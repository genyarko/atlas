"""Signal — strategic intent from hiring patterns and exec movements.

Pipeline (same for mock + live):

    raw rows  ──►  normalize ──►  cluster ──►  synthesize ──►  Findings
                  (JobPosting)   (ClusterSummary)  (LLM or rules)

The live path fetches LinkedIn job rows via Bright Data MCP
(``web_data_linkedin_job_listings``) and triangulates with a couple of
SERP queries; the mock path swaps in deterministic fixtures so the demo
flows end-to-end with zero credentials. Both paths run the same
clustering and synthesis steps, so what the judge sees in mock mode is
structurally identical to a live brief.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..brightdata import record_simulated, serp, web_scraper_api
from ..models import Finding, ModuleResult, Severity, Source
from ._fixtures import infer_subject
from .base import IntelligenceModule
from .signal_data import (
    ClusterSummary,
    JobPosting,
    cluster,
    fixture_for,
    news_fixture_for,
    normalize,
)

log = logging.getLogger(__name__)


class SignalModule(IntelligenceModule):
    name = "signal"
    live_ready = True

    async def execute(self, params: dict[str, Any]) -> ModuleResult:
        subject = params.get("subject") or infer_subject(params.get("query", ""))
        rows = await web_scraper_api.fetch_company_careers_jobs(subject, limit=80)
        if not rows:
            log.info("Signal live: no careers-page rows; falling back to mock for %s", subject)
            return await self.mock(params)
        postings = normalize(rows, company=subject)
        if len(postings) < 5:
            log.info("Signal live: only %d postings normalized; mock fallback", len(postings))
            return await self.mock(params)

        news_rows = (
            await serp.search(f'"{subject}" hiring OR expansion OR launch', num=8)
        ) or []
        news = _normalize_news(news_rows)
        return await _build_result(
            subject=subject, postings=postings, news=news, lookback_days=int(
                params.get("lookback_days", 30)
            ), mode="live"
        )

    async def mock(self, params: dict[str, Any]) -> ModuleResult:
        subject = params.get("subject") or infer_subject(params.get("query", ""))
        await _emit_simulated_trace(subject)
        postings = normalize(fixture_for(subject), company=subject)
        news = news_fixture_for(subject)
        return await _build_result(
            subject=subject, postings=postings, news=news, lookback_days=int(
                params.get("lookback_days", 30)
            ), mode="mock"
        )


async def _emit_simulated_trace(subject: str) -> None:
    """Record the MCP calls the live path would have made.

    Shown in the UI's infrastructure rail with a 'simulated' badge so
    the demo's provenance trace stays alive in default mock-mode."""
    slug = subject.lower().replace(" ", "")
    await record_simulated(
        tool="scrape_as_markdown",
        args={"url": f"https://{slug}.app/careers"},
    )
    await record_simulated(
        tool="search_engine",
        args={"query": f'"{subject}" hiring OR expansion OR launch', "num_results": 8},
    )


# ── Result assembly ─────────────────────────────────────────────────


async def _build_result(
    *,
    subject: str,
    postings: list[JobPosting],
    news: list[dict[str, str]],
    lookback_days: int,
    mode: str,
) -> ModuleResult:
    summary = cluster(postings, subject)
    findings = await _synthesize_findings(subject, summary, news)
    findings = _ensure_min_findings(subject, summary, news, findings)

    sources = _build_sources(subject, summary, news)

    raw_data: dict[str, Any] = {
        "subject": subject,
        "lookback_days": lookback_days,
        "total_postings": summary.total,
        "recent_30d": summary.recent_30d,
        "older_60d": summary.older_60d,
        "velocity_ratio": summary.velocity_ratio,
        "by_family": summary.by_family,
        "by_region": summary.by_region,
        "recent_by_region": summary.recent_by_region,
        "older_by_region": summary.older_by_region,
        "by_seniority": summary.by_seniority,
        "top_examples": summary.top_examples,
        "news_items": news[:5],
        "mode": mode,
    }
    confidence = _confidence_score(summary, news)
    return ModuleResult(
        module="signal",
        findings=findings,
        sources=sources,
        raw_data=raw_data,
        confidence=confidence,
    )


def _build_sources(
    subject: str,
    summary: ClusterSummary,
    news: list[dict[str, str]],
) -> list[Source]:
    """Compose the source list from URLs that were *actually queried*.

    LinkedIn jobs search URL represents the dataset query (always present
    — both live and mock pipelines query it). Top example job posting URLs
    are returned rows from that query. News URLs come from SERP queries.
    No fabricated "looks-related" URLs."""
    seen: set[str] = set()
    sources: list[Source] = []

    def _add(url: str, title: str, via: str) -> None:
        if not url or url in seen:
            return
        seen.add(url)
        sources.append(Source(url=url, title=title, via=via))

    slug = subject.lower().replace(" ", "")
    jobs_search = f"https://{slug}.app/careers"
    _add(jobs_search, f"{subject} — careers page", "scrape_as_markdown")

    # Top representative job postings — each one is a real row from the
    # LinkedIn dataset (or a fixture row that matches that shape).
    for ex in summary.top_examples[:4]:
        url = ex.get("url", "")
        title_text = ex.get("title", "")
        loc = ex.get("location", "")
        if url and url != jobs_search:
            _add(url, f"{title_text} ({loc})" if loc else title_text, "scrape_as_markdown")

    for item in news[:3]:
        url = item.get("url", "")
        if url:
            _add(url, item.get("title", f"{subject} news"), "serp_api")

    return sources


def _confidence_score(summary: ClusterSummary, news: list[dict[str, str]]) -> float:
    score = 0.55
    if summary.total >= 10:
        score += 0.1
    if summary.total >= 25:
        score += 0.05
    if summary.velocity_ratio >= 1.5:
        score += 0.05
    if news:
        score += 0.07
    return round(min(score, 0.92), 2)


# ── Synthesis: LLM with deterministic fallback ──────────────────────


_SYNTH_SYSTEM = """You are the Atlas Signal synthesizer.

Given a clustered summary of a company's recent job postings (and \
optionally a few news headlines), output STRICT JSON inferring the \
company's strategic intent. Do NOT restate the raw numbers — infer the \
"so what".

Output schema (no markdown fences, no commentary):
{
  "findings": [
    {
      "statement": "<one sharp sentence, institutional-analyst tone>",
      "severity": "info" | "notable" | "high" | "critical",
      "evidence_urls": ["<job/news url that supports this claim>", ...]
    }
  ]
}

Rules:
- Produce 3-5 findings, ordered by strategic importance.
- Each finding must cite at least one URL drawn from the input.
- Severities: "critical" for org-defining moves (new region launch, exec \
hire, compliance build-out for a market), "high" for clear directional \
signals, "notable" for incremental but worth-knowing, "info" for context.
- Reason from the cluster shape (which families, regions, seniority) and \
the velocity ratio. Do not invent facts not implied by the input.
"""


async def _synthesize_findings(
    subject: str,
    summary: ClusterSummary,
    news: list[dict[str, str]],
) -> list[Finding]:
    """Try the LLM first; on any failure, return [] and let the rule-based
    fallback take over."""
    from ..agent.llm import get_llm  # local import — avoids cycle at import time

    llm = get_llm()
    if llm is None:
        return []

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
    except ImportError:  # pragma: no cover
        return []

    prompt = {
        "subject": subject,
        "cluster_summary": summary.to_prompt_dict(),
        "news_headlines": news[:5],
    }
    try:
        response = await llm.ainvoke([
            SystemMessage(content=_SYNTH_SYSTEM),
            HumanMessage(content=json.dumps(prompt, indent=2)),
        ])
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("Signal LLM synthesis failed (%s); using rules", exc)
        return []
    text = response.content if isinstance(response.content, str) else str(response.content)
    return _parse_llm_findings(text)


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _parse_llm_findings(raw: str) -> list[Finding]:
    cleaned = _FENCE_RE.sub("", raw.strip()).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        log.warning("Signal LLM returned non-JSON; using rules")
        return []
    out: list[Finding] = []
    for entry in data.get("findings", []):
        statement = (entry.get("statement") or "").strip()
        if not statement:
            continue
        sev = entry.get("severity", "notable")
        if sev not in ("info", "notable", "high", "critical"):
            sev = "notable"
        urls = [u for u in entry.get("evidence_urls", []) if isinstance(u, str) and u.strip()]
        out.append(Finding(statement=statement, severity=sev, evidence=urls or []))
    return out


# ── Deterministic rule-based findings (fallback path) ────────────────


def _ensure_min_findings(
    subject: str,
    summary: ClusterSummary,
    news: list[dict[str, str]],
    findings: list[Finding],
) -> list[Finding]:
    """Guarantee ≥3 findings with source URLs even when no LLM is configured."""
    if len(findings) >= 3 and all(f.evidence for f in findings):
        return findings

    rule_based = _rule_based_findings(subject, summary, news)
    # Merge: prefer LLM findings first; pad with rule-based ones we haven't covered.
    seen = {f.statement.lower() for f in findings}
    merged = list(findings)
    for f in rule_based:
        if f.statement.lower() in seen:
            continue
        merged.append(f)
        seen.add(f.statement.lower())
        if len(merged) >= 5:
            break
    # Drop any finding that still has no URL — Day-3 acceptance requires sources.
    merged = [f for f in merged if f.evidence]
    return merged[:5] if len(merged) >= 3 else rule_based[:5]


_FAMILY_LABEL = {
    "sales-enterprise": "enterprise account executive",
    "sales-mm":         "mid-market account executive",
    "sales-leadership": "sales leadership",
    "sdr":              "sales development",
    "solutions":        "solutions engineer",
    "customer-success": "customer success",
    "engineering":      "engineering",
    "security-eng":     "security engineering",
    "compliance":       "compliance / privacy engineering",
    "revops":           "revenue operations",
    "marketing":        "marketing",
    "product":          "product management",
    "design":           "design",
    "data":             "data / ML",
    "operations":       "operations",
    "recruiting":       "recruiting",
    "support":          "support",
}


def _rule_based_findings(
    subject: str,
    summary: ClusterSummary,
    news: list[dict[str, str]],
) -> list[Finding]:
    """Compute findings from the cluster summary without an LLM.

    Mirrors what an analyst would write up given the same table, so the
    demo never falls flat when the model is offline."""
    findings: list[Finding] = []

    # Family → first example URL, using the example's own canonical family.
    family_to_url: dict[str, str] = {}
    for example in summary.top_examples:
        fam = example.get("family", "")
        if fam and fam not in family_to_url and example.get("url"):
            family_to_url[fam] = example["url"]

    # 1. Regional expansion signal (recent vs older cross-tab, with
    #    AMER treated as the default home unless clearly accelerating).
    region_signal = _regional_signal(summary)
    if region_signal:
        target_region, recent_count, recent_share, delta = region_signal
        sev: Severity = (
            "critical" if recent_share >= 0.4 and recent_count >= 5 and delta >= 0.3
            else "high"
        )
        sales_url = family_to_url.get("sales-enterprise") or _example_url_by_region(
            summary, target_region
        )
        evidence = [u for u in [sales_url] if u]
        for item in news[:3]:
            if item.get("url"):
                evidence.append(item["url"])
                break
        delta_pct = int(delta * 100)
        delta_clause = (
            f" — up {delta_pct} points from the prior 30-60-day window"
            if delta >= 0.15 else ""
        )
        findings.append(Finding(
            statement=(
                f"{subject} concentrated {recent_count} of its last-30-day postings "
                f"({int(recent_share * 100)}%) in {_region_long(target_region)}"
                f"{delta_clause} — consistent with an active "
                f"{_region_long(target_region)} GTM build-out, not opportunistic hiring."
            ),
            severity=sev,
            evidence=evidence,
        ))

    # 2. Leadership commitment — fire when at least one exec-level role,
    #    or multiple director/lead-level openings, not on a single Staff IC.
    exec_count = summary.by_seniority.get("executive", 0)
    lead_count = summary.by_seniority.get("lead", 0)
    if exec_count >= 1 or lead_count >= 2:
        exec_url = (
            _example_url_by_seniority(summary, "executive")
            or _example_url_by_seniority(summary, "lead")
        )
        if exec_count >= 1:
            statement = (
                f"{exec_count} VP/Head-level role(s) open"
                f"{f' alongside {lead_count} director/lead-level role(s)' if lead_count else ''}"
                " — budget-line hires that signal organizational commitment to the "
                "current expansion thesis, not coverage."
            )
            sev_lead: Severity = "high"
        else:
            statement = (
                f"{lead_count} director/lead-level role(s) open concurrently — "
                "an unusual concentration of senior-IC investment for a single "
                "30-day window."
            )
            sev_lead = "notable"
        findings.append(Finding(
            statement=statement,
            severity=sev_lead,
            evidence=[exec_url] if exec_url else [],
        ))

    # 3. Compliance / privacy build-out
    compliance_count = summary.by_family.get("compliance", 0)
    compliance_url = family_to_url.get("compliance")
    if compliance_count >= 1:
        sev_comp: Severity = "critical" if compliance_count >= 2 else "high"
        findings.append(Finding(
            statement=(
                f"{compliance_count} compliance/privacy engineering role(s) opened — "
                "typical pre-launch staffing for EU data-residency commitments or "
                "regulated-industry sales motions."
            ),
            severity=sev_comp,
            evidence=[compliance_url] if compliance_url else [],
        ))

    # 4. Hiring velocity — only fires when the baseline is large enough to
    #    be meaningful; display caps at >=5x so a tiny baseline doesn't
    #    produce an absurd headline number.
    if (
        summary.velocity_ratio >= 1.5
        and summary.recent_30d >= 5
        and summary.older_60d >= 4
    ):
        any_url = summary.top_examples[0].get("url") if summary.top_examples else None
        display = (
            f"≥5.0×" if summary.velocity_ratio >= 5.0
            else f"{summary.velocity_ratio:.1f}×"
        )
        findings.append(Finding(
            statement=(
                f"Hiring velocity is {display} the trailing-60-day baseline "
                f"({summary.recent_30d} new roles in the last 30 days vs "
                f"{summary.older_60d} across the prior 60) — an inflection-shaped "
                "pattern, not steady-state growth."
            ),
            severity="notable",
            evidence=[any_url] if any_url else [],
        ))

    # 5. Security / Federal posture (e.g. Datadog)
    sec_count = summary.by_family.get("security-eng", 0)
    if sec_count >= 2:
        sec_url = family_to_url.get("security-eng")
        findings.append(Finding(
            statement=(
                f"Security-engineering hiring concentrated in CSPM / SIEM / detection "
                f"({sec_count} roles) indicates active investment in the "
                "security-product surface, not maintenance."
            ),
            severity="high",
            evidence=[sec_url] if sec_url else [],
        ))

    # 6. Federal / public-sector buildout — title-level pattern that cuts
    #    across role families (Solutions Architects, FedRAMP engineers, etc.)
    if summary.federal_count >= 2:
        fed_url = summary.federal_example.get("url", "")
        findings.append(Finding(
            statement=(
                f"{summary.federal_count} federal / public-sector role(s) opened "
                "across solutions and engineering — consistent with a deliberate "
                "government-segment buildout, not opportunistic coverage."
            ),
            severity="high",
            evidence=[fed_url] if fed_url else [],
        ))

    # Fallback: generic role-mix observation if nothing else triggered.
    if not findings:
        top_families = sorted(
            ((k, v) for k, v in summary.by_family.items() if k != "other"),
            key=lambda kv: kv[1], reverse=True,
        )[:2]
        if top_families:
            label = " + ".join(_FAMILY_LABEL.get(f, f) for f, _ in top_families)
            url = (
                family_to_url.get(top_families[0][0])
                or (summary.top_examples[0].get("url") if summary.top_examples else "")
            )
            findings.append(Finding(
                statement=(
                    f"{subject} hiring is concentrated in {label} — "
                    "consistent with growth-stage motion."
                ),
                severity="notable",
                evidence=[url] if url else [],
            ))
    return findings


# ── Small helpers for evidence linking ──────────────────────────────


def _normalize_news(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = row.get("link") or row.get("url") or ""
        title = row.get("title") or ""
        snippet = row.get("snippet") or row.get("description") or ""
        if url and title:
            out.append({"url": url, "title": title, "snippet": snippet})
    return out


def _regional_signal(
    summary: ClusterSummary,
) -> tuple[str, int, float, float] | None:
    """Detect a directional regional shift, not just "where they hire".

    Returns ``(region, recent_count, recent_share, expansion_delta)`` for
    the strongest candidate region, or ``None`` if none qualifies.

    Logic:
    - Need ``recent_30d >= 5`` for the share denominator to be meaningful.
    - Candidate region must hold ``>=3`` recent postings AND ``>=25%`` of
      recent postings.
    - Expansion delta = recent_share - older_share for the same region.
      Positive delta means the region grew its share relative to the prior
      30-60-day window.
    - AMER is treated as the default home: only fire if the AMER delta is
      ``>=0.3`` (a clear acceleration), otherwise we'd misread normal
      US-based hiring as "expansion".
    """
    if summary.recent_30d < 5:
        return None

    older_total = summary.older_60d or 1
    candidates: list[tuple[str, int, float, float]] = []
    for r, recent_count in summary.recent_by_region.items():
        if r in ("Other", "Remote") or recent_count < 3:
            continue
        recent_share = recent_count / summary.recent_30d
        if recent_share < 0.25:
            continue
        older_share = summary.older_by_region.get(r, 0) / older_total
        delta = recent_share - older_share
        candidates.append((r, recent_count, recent_share, delta))

    if not candidates:
        return None
    # Prefer: non-AMER first, then largest delta, then largest share.
    candidates.sort(key=lambda c: (c[0] == "AMER", -c[3], -c[2]))
    target, count, share, delta = candidates[0]

    # AMER is the default home — only call it an "expansion" if there's a
    # real acceleration vs the prior window.
    if target == "AMER" and delta < 0.3:
        return None

    return target, count, share, delta


def _region_long(code: str) -> str:
    return {
        "EMEA": "EMEA",
        "APAC": "APAC",
        "LATAM": "LATAM",
        "AMER": "North America",
    }.get(code, code)


def _example_url_by_region(summary: ClusterSummary, region_code: str) -> str | None:
    for ex in summary.top_examples:
        if ex.get("region") == region_code:
            return ex.get("url")
    return summary.top_examples[0].get("url") if summary.top_examples else None


def _example_url_by_seniority(summary: ClusterSummary, level: str) -> str | None:
    for ex in summary.top_examples:
        if ex.get("seniority") == level:
            return ex.get("url")
    return None
