"""Synthesizer node — turns ModuleResults into a Brief.

Two paths, same output:
  • LLM (Claude) writes a polished executive summary
  • Template fallback composes a competent one from finding statements
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from ..models import Brief, BriefSection, Finding, ModuleResult, Severity
from ..modules import MODULE_CATALOG
from ..modules._fixtures import infer_subject
from .. import config
from .llm import get_llm
from .state import AgentState

log = logging.getLogger(__name__)


_SEVERITY_RANK: dict[Severity, int] = {"critical": 4, "high": 3, "notable": 2, "info": 1}


def _top_findings(results: list[ModuleResult], limit: int = 5) -> list[Finding]:
    all_findings: list[Finding] = []
    for r in results:
        all_findings.extend(r.findings)
    all_findings.sort(key=lambda f: _SEVERITY_RANK.get(f.severity, 0), reverse=True)
    return all_findings[:limit]


def _composite_confidence(results: Iterable[ModuleResult]) -> float:
    scores = [r.confidence for r in results if r.status != "failed"]
    return round(sum(scores) / len(scores), 2) if scores else 0.0


def _build_sections(results: list[ModuleResult]) -> list[BriefSection]:
    sections: list[BriefSection] = []
    for r in results:
        meta = MODULE_CATALOG[r.module]
        summary = (
            r.findings[0].statement
            if r.findings
            else f"No findings surfaced by {meta['title']}."
        )
        sections.append(BriefSection(
            module=r.module,
            title=meta["title"],
            summary=summary,
            findings=r.findings,
            sources=r.sources,
            confidence=r.confidence,
            data=r.raw_data,
        ))
    return sections


def _template_summary(subject: str, results: list[ModuleResult]) -> str:
    parts: list[str] = []
    for r in results:
        meta = MODULE_CATALOG[r.module]
        if r.findings:
            parts.append(f"{meta['title']}: {r.findings[0].statement}")
    if not parts:
        return f"No material signals surfaced for {subject} across the invoked modules."
    head = (
        f"Ground Truth Brief on {subject}. "
        f"{len(results)} module(s) invoked; "
        f"{sum(len(r.findings) for r in results)} finding(s) total."
    )
    return head + " " + " ".join(parts[:3])


_SYNTH_SYSTEM = """You are the Atlas Synthesizer. Produce an executive intelligence brief.

Style:
- Institutional research analyst. Direct. No marketing language.
- Cite every claim implicitly (sources are listed separately).
- Lead with the "so what".

Return PLAIN TEXT only — 3-4 sentences, no headings, no bullet points.
This text becomes the brief's executive summary. Do NOT restate the question."""


def _module_evidence(r: ModuleResult) -> str:
    """One-line snapshot of the load-bearing numbers in r.raw_data.

    Each module owns its raw_data shape, so dispatch by module and surface
    only the metrics an analyst would quote in a paragraph. Findings carry
    the prose; this carries the digits behind them."""
    rd = r.raw_data or {}

    def _fmt(v: Any, spec: str = "") -> str:
        try:
            return format(v, spec) if spec else str(v)
        except (TypeError, ValueError):
            return str(v)

    if r.module == "trueprice":
        regions = rd.get("regions") or []
        bits: list[str] = []
        if rd.get("plan_label") or rd.get("plan_id"):
            bits.append(f"plan={rd.get('plan_label') or rd.get('plan_id')}")
        non_base = [reg for reg in regions if reg.get("region") != "US"]
        if non_base:
            top = max(non_base, key=lambda x: x.get("delta_pct", 0))
            bits.append(
                f"max_delta=+{_fmt(top.get('delta_pct', 0), '.0f')}% "
                f"({top.get('region', '?')}: "
                f"${_fmt(top.get('true_usd', 0), '.2f')} vs "
                f"${_fmt(next((r2.get('true_usd', 0) for r2 in regions if r2.get('region') == 'US'), 0), '.2f')} US)"
            )
        if regions:
            bits.append(f"cart_extracts={rd.get('cart_extracts', 0)}/{len(regions)}")
        if rd.get("mode"):
            bits.append(f"mode={rd['mode']}")
        return ", ".join(bits)

    if r.module == "signal":
        bits = []
        if (v := rd.get("velocity_ratio")) is not None:
            bits.append(f"velocity={_fmt(v, '.1f')}×")
        if (n := rd.get("recent_30d")) is not None:
            bits.append(f"recent_30d={n}")
        if (n := rd.get("older_60d")) is not None:
            bits.append(f"prior_60d={n}")
        by_family = rd.get("by_family") or {}
        if by_family:
            top_fam = sorted(
                ((k, v) for k, v in by_family.items() if k != "other"),
                key=lambda kv: kv[1], reverse=True,
            )[:3]
            if top_fam:
                bits.append("families=" + ",".join(f"{k}:{v}" for k, v in top_fam))
        by_region = rd.get("recent_by_region") or {}
        if by_region:
            top_reg = sorted(by_region.items(), key=lambda kv: kv[1], reverse=True)[:2]
            bits.append("recent_regions=" + ",".join(f"{k}:{v}" for k, v in top_reg))
        return ", ".join(bits)

    if r.module == "altdata":
        bits = []
        if (cs := rd.get("composite_score")) is not None:
            bits.append(f"composite={_fmt(cs, '.2f')} ({rd.get('composite_label', '?')})")
        for src_name, sd in (rd.get("sources") or {}).items():
            sd_bits = [f"n={sd.get('recent_30d', 0)}"]
            if (d := sd.get("rating_delta")) is not None:
                sd_bits.append(f"Δrating={_fmt(d, '+.2f')}")
            if (vr := sd.get("velocity_ratio")) is not None:
                sd_bits.append(f"velocity={_fmt(vr, '.1f')}×")
            if sd.get("top_complaint"):
                sd_bits.append(f"complaint={sd['top_complaint']}")
            bits.append(f"{src_name}({'/'.join(sd_bits)})")
        return ", ".join(bits)

    if r.module == "exposure":
        bits = []
        if rd.get("max_severity"):
            bits.append(f"max_sev={rd['max_severity']}")
        if (n := rd.get("critical_count")) is not None:
            bits.append(f"critical={n}")
        channels = rd.get("channels") or []
        if channels:
            bits.append(f"channels={','.join(str(c) for c in channels)}")
        return ", ".join(bits)

    if r.module == "filing":
        bits = []
        if (n := rd.get("change_count")) is not None:
            bits.append(f"changes={n}")
        if rd.get("max_materiality"):
            bits.append(f"max_materiality={rd['max_materiality']}")
        if rd.get("mode"):
            bits.append(f"mode={rd['mode']}")
        return ", ".join(bits)

    if r.module == "visual":
        bits = []
        if (n := rd.get("suspect_count")) is not None:
            bits.append(f"suspects={n}")
        if (n := rd.get("high_count")) is not None:
            bits.append(f"high_or_critical={n}")
        suspects = rd.get("suspects") or []
        if suspects:
            top = suspects[0]
            bits.append(
                f"top={top.get('verdict', '?')}@sim={_fmt(top.get('similarity', 0), '.2f')}"
            )
        return ", ".join(bits)

    return ""


def _findings_prompt_block(results: list[ModuleResult]) -> str:
    """Group findings by module with a metrics line so the LLM sees the
    numbers behind each section, not just the prose statements."""
    sections: list[str] = []
    for r in results:
        if not r.findings:
            continue
        meta = MODULE_CATALOG.get(r.module, {"title": r.module})
        header = f"[{r.module}] {meta.get('title', r.module)}"
        metrics = _module_evidence(r)
        if metrics:
            header += f" — metrics: {metrics}"
        lines = [header]
        for f in r.findings:
            lines.append(f"  - [{f.severity}] {f.statement}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections) or "(no findings)"


async def _llm_summary(subject: str, query: str, results: list[ModuleResult]) -> str | None:
    llm = get_llm()
    if llm is None:
        return None
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
    except ImportError:  # pragma: no cover
        return None

    findings_block = _findings_prompt_block(results)

    human = (
        f"Subject: {subject}\n"
        f"Original question: {query}\n\n"
        f"Module findings (with supporting metrics):\n{findings_block}\n\n"
        f"Quote at least one specific number from the metrics lines in your summary."
    )
    try:
        response = await llm.ainvoke([
            SystemMessage(content=_SYNTH_SYSTEM),
            HumanMessage(content=human),
        ])
    except Exception as e:  # pragma: no cover — defensive
        log.warning("Synthesizer LLM call failed (%s); falling back", e)
        return None
    text = response.content if isinstance(response.content, str) else str(response.content)
    return text.strip() or None


async def synthesize_node(state: AgentState) -> AgentState:
    question = state["question"]
    plan = state["plan"]
    results = state["results"]

    # All invocations were primed with the same subject; reuse it.
    subject = (
        plan.modules_to_invoke[0].params.get("subject")
        if plan.modules_to_invoke else None
    ) or infer_subject(question.text)

    summary = await _llm_summary(subject, question.text, results)
    if summary is None:
        log.info("Synthesizer: using template fallback")
        summary = _template_summary(subject, results)
    else:
        log.info("Synthesizer: LLM produced executive summary")

    brief = Brief(
        question=question,
        plan=plan,
        subject=subject,
        executive_summary=summary,
        key_findings=_top_findings(results),
        sections=_build_sections(results),
        confidence_score=_composite_confidence(results),
        mode="mock" if config.is_mock_mode() else "live",
    )
    return {"brief": brief}
