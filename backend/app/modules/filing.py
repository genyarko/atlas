"""Filing — materiality-scored diffs of regulatory filings.

Day-6 deliverable (per implementation plan §4.3):

1. Fetch recent filings via Web Unlocker — the Bright Data product that
   handles regional blocks on regulator sites and lands a clean
   "blocked → unblocked" side-by-side for the demo.
2. Diff against prior filings — focus on the Item 1A "Risk Factors"
   section of 10-Q / 10-K filings.
3. LLM pass: rate materiality on a 1-5 scale, summarize each change in
   one sentence with a "why this matters" rationale.

Polish ceiling is intentionally lower than Signal / TruePrice / Visual
(per day-6 brief: "polish ceiling lower than first 3 modules — these
are supporting cast"), so this orchestration stays tight: one filing
type per run (10-Q for the pre-earnings demo, configurable), one
LLM diff call, deterministic fixture fallback.

Mode labels
-----------
* ``live``    — the diff came from a live LLM call over fetched filings.
* ``mock``    — the diff is the pre-built fixture (no LLM call).
* ``partial`` — Web Unlocker returned a current filing but we couldn't
                build the diff (no prior on file, or the LLM failed
                to surface anything material). The brief still renders;
                we just say what we couldn't compute.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from ..brightdata import record_simulated, web_unlocker
from ..models import Finding, ModuleResult, Source
from ._fixtures import infer_subject
from .base import IntelligenceModule
from .filing_data import (
    Filing,
    FilingDiff,
    FilingType,
    RiskFactorChange,
    TRACKED_FILING_TYPES,
    cik_for,
    edgar_submissions_url,
    extract_risk_factors,
    fixture_diff_for,
    materiality_to_severity,
    parse_edgar_submissions,
    pick_diff_pair,
)

log = logging.getLogger(__name__)


class FilingModule(IntelligenceModule):
    name = "filing"
    live_ready = True

    async def execute(self, params: dict[str, Any]) -> ModuleResult:
        subject = params.get("subject") or infer_subject(params.get("query", ""))
        cik = cik_for(subject)
        if cik is None:
            log.info("Filing live: no CIK for %s; falling back to mock", subject)
            return await self.mock(params)

        filing_type = _resolve_filing_type(params)
        filings = await _fetch_filings(cik=cik)
        if not filings:
            log.info("Filing live: empty EDGAR submissions for %s; mock fallback", subject)
            return await self.mock(params)

        pair = pick_diff_pair(filings, filing_type=filing_type)
        if pair is None:
            log.info(
                "Filing live: no %s on file for %s in recent window; mock fallback",
                filing_type, subject,
            )
            return await self.mock(params)
        current, prior = pair

        # Fetch current + prior documents through Web Unlocker.
        # Concurrent fetches — the MCP layer serializes if it needs to.
        bodies = await asyncio.gather(
            _fetch_document(current.url),
            _fetch_document(prior.url) if prior is not None else _noop(),
            return_exceptions=False,
        )
        current_body, prior_body = bodies[0], bodies[1]

        if current_body is None:
            log.info("Filing live: current filing fetch failed for %s; mock fallback", subject)
            return await self.mock(params)

        current_rf = extract_risk_factors(current_body)
        prior_rf = extract_risk_factors(prior_body) if prior_body else None

        changes, summary = await _llm_diff(
            subject=subject,
            current=current,
            prior=prior,
            current_text=current_rf,
            prior_text=prior_rf,
        )

        if not changes and prior is not None:
            # We had a comparable but the LLM didn't surface anything
            # material. That's a valid signal too — emit a non-empty
            # brief that says so explicitly.
            mode = "partial"
        else:
            mode = "live"

        diff = FilingDiff(
            current=current, prior=prior, changes=tuple(changes), summary=summary,
        )
        return _build_result(subject=subject, diff=diff, mode=mode)

    async def mock(self, params: dict[str, Any]) -> ModuleResult:
        subject = params.get("subject") or infer_subject(params.get("query", ""))
        diff = fixture_diff_for(subject)
        await _emit_simulated_trace(subject, diff)
        return _build_result(subject=subject, diff=diff, mode="mock")


async def _emit_simulated_trace(subject: str, diff: FilingDiff) -> None:
    """Declare the Web Unlocker fetches the live path would have made.

    Filing routes everything through scrape_as_markdown (the demo's
    'blocked vs unblocked' lever) — EDGAR submissions index + current
    filing + prior filing."""
    cik = cik_for(subject) or "0000000000"
    await record_simulated(
        tool="scrape_as_markdown",
        args={"url": edgar_submissions_url(cik)},
    )
    if diff.current is not None:
        await record_simulated(
            tool="scrape_as_markdown",
            args={"url": diff.current.url},
        )
    if diff.prior is not None:
        await record_simulated(
            tool="scrape_as_markdown",
            args={"url": diff.prior.url},
        )


async def _noop() -> None:
    return None


# ── Input resolution ───────────────────────────────────────────────


def _resolve_filing_type(params: dict[str, Any]) -> FilingType:
    """Pick the filing type to diff on. Default 10-Q (the demo headline)."""
    requested = params.get("filing_types") or params.get("filing_type")
    if isinstance(requested, str):
        candidates = [requested]
    elif isinstance(requested, list):
        candidates = [str(t) for t in requested if isinstance(t, str)]
    else:
        candidates = []
    for cand in candidates:
        if cand in TRACKED_FILING_TYPES:
            return cand  # type: ignore[return-value]
    return "10-Q"


# ── EDGAR fetches ──────────────────────────────────────────────────


async def _fetch_filings(*, cik: str, timeout_s: float = 20.0) -> list[Filing]:
    """Fetch the recent-submissions JSON for a CIK and parse it."""
    url = edgar_submissions_url(cik)
    try:
        body = await asyncio.wait_for(web_unlocker.fetch(url), timeout=timeout_s)
    except asyncio.TimeoutError:
        log.warning("Filing: EDGAR submissions fetch timed out for cik=%s", cik)
        return []
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("Filing: EDGAR submissions fetch failed: %s", exc)
        return []
    if not body:
        return []
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        log.warning("Filing: EDGAR submissions returned non-JSON for cik=%s", cik)
        return []
    if not isinstance(payload, dict):
        return []
    return parse_edgar_submissions(payload, cik=cik)


async def _fetch_document(url: str, *, timeout_s: float = 25.0) -> str | None:
    if not url:
        return None
    try:
        return await asyncio.wait_for(web_unlocker.fetch(url), timeout=timeout_s)
    except asyncio.TimeoutError:
        log.warning("Filing: document fetch timed out for %s", url)
        return None
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("Filing: document fetch failed for %s: %s", url, exc)
        return None


# ── LLM diff (the materiality-scoring step) ────────────────────────


_DIFF_SYSTEM = """You are the Atlas Filing analyst.

You will receive two excerpts from a public company's SEC filings: the \
RISK FACTORS section from a CURRENT filing and the same section from a \
PRIOR filing. Identify material changes — new risk factors, removed \
risk factors, or substantively modified language. Skip boilerplate.

Output STRICT JSON. No markdown fences. No commentary outside the JSON.

Schema:
{
  "summary": "<one or two sentences summarizing the net change>",
  "changes": [
    {
      "kind": "added" | "removed" | "modified",
      "headline": "<short title for the risk factor, ≤80 chars>",
      "excerpt": "<1-3 sentence excerpt drawn from the filing text>",
      "materiality": 1-5,
      "rationale": "<one sentence: why this matters for an analyst>"
    }
  ]
}

Materiality rubric (1-5):
- 1: boilerplate / pro-forma update (date refresh, legal language)
- 2: expanded language on an existing risk
- 3: notable new risk worth flagging
- 4: high — new risk factor materially shifts the disclosure surface
- 5: critical — implies near-term operational impact

Rules:
- Cite ONLY language present in the supplied excerpts. Do not invent.
- Produce 0-4 changes. Empty changes array means "no material delta".
- Reserve materiality ≥4 for risks that would change an analyst's model.
- If the PRIOR excerpt is missing, you may still emit `added` changes \
  for risks the CURRENT filing names, but cap materiality at 3 since \
  you cannot prove novelty."""


async def _llm_diff(
    *,
    subject: str,
    current: Filing,
    prior: Filing | None,
    current_text: str,
    prior_text: str | None,
) -> tuple[list[RiskFactorChange], str]:
    """Return ``(changes, summary)``. On any LLM failure returns ``([], "")``.

    Callers treat an empty changes list as "no material delta surfaced".
    """
    from ..agent.llm import get_llm  # local import — avoids cycle at import time

    llm = get_llm()
    if llm is None:
        return [], ""

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
    except ImportError:  # pragma: no cover
        return [], ""

    prompt = {
        "subject": subject,
        "current_filing": {
            "type": current.filing_type,
            "fiscal_period": current.fiscal_period,
            "filed_at": current.filed_at,
            "risk_factors_excerpt": current_text[:14_000],
        },
        "prior_filing": (
            {
                "type": prior.filing_type,
                "fiscal_period": prior.fiscal_period,
                "filed_at": prior.filed_at,
                "risk_factors_excerpt": (prior_text or "")[:14_000],
            }
            if prior is not None else None
        ),
    }
    try:
        response = await llm.ainvoke([
            SystemMessage(content=_DIFF_SYSTEM),
            HumanMessage(content=json.dumps(prompt, indent=2)),
        ])
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("Filing LLM diff failed (%s); skipping live changes", exc)
        return [], ""
    text = response.content if isinstance(response.content, str) else str(response.content)
    return _parse_llm_diff(text)


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_ALLOWED_KINDS: frozenset[str] = frozenset({"added", "removed", "modified"})


def _parse_llm_diff(raw: str) -> tuple[list[RiskFactorChange], str]:
    cleaned = _FENCE_RE.sub("", raw.strip()).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        log.warning("Filing LLM returned non-JSON; using empty diff")
        return [], ""
    if not isinstance(data, dict):
        return [], ""
    summary = data.get("summary", "")
    if not isinstance(summary, str):
        summary = ""

    changes: list[RiskFactorChange] = []
    raw_changes = data.get("changes") or []
    if not isinstance(raw_changes, list):
        return [], summary.strip()

    for entry in raw_changes:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind", "")
        if not isinstance(kind, str) or kind not in _ALLOWED_KINDS:
            continue
        headline = (entry.get("headline") or "").strip()
        excerpt = (entry.get("excerpt") or "").strip()
        if not headline or not excerpt:
            continue
        try:
            materiality = int(entry.get("materiality", 0))
        except (TypeError, ValueError):
            materiality = 0
        materiality = max(1, min(5, materiality))
        rationale = (entry.get("rationale") or "").strip()
        changes.append(RiskFactorChange(
            kind=kind,  # type: ignore[arg-type]
            headline=headline[:200],
            excerpt=excerpt[:800],
            materiality=materiality,
            rationale=rationale[:300],
        ))
    return changes, summary.strip()


# ── Result assembly ───────────────────────────────────────────────


def _build_result(*, subject: str, diff: FilingDiff, mode: str) -> ModuleResult:
    findings = _build_findings(subject=subject, diff=diff)
    sources = _build_sources(diff=diff)
    raw_data: dict[str, Any] = {
        "subject": subject,
        "mode": mode,
        "filing_diff": diff.to_raw(),
        "max_materiality": diff.max_materiality,
        "change_count": len(diff.changes),
    }
    status = "partial" if (mode == "partial" or not diff.has_changes) else "success"
    return ModuleResult(
        module="filing",
        status=status,
        findings=findings,
        sources=sources,
        raw_data=raw_data,
        confidence=_confidence_score(diff=diff, mode=mode),
    )


def _build_findings(*, subject: str, diff: FilingDiff) -> list[Finding]:
    if not diff.has_changes:
        evidence = [diff.current.url] if diff.current.url else []
        return [Finding(
            statement=(
                diff.summary
                or f"No material risk-factor changes detected in {subject}'s most recent filings."
            ),
            severity="info",
            evidence=evidence,
        )]

    findings: list[Finding] = []
    # Headline = highest-materiality change.
    sorted_changes = sorted(diff.changes, key=lambda c: c.materiality, reverse=True)
    top = sorted_changes[0]
    findings.append(Finding(
        statement=(
            f"{subject} {diff.current.short}: "
            f"{_kind_verb(top.kind)} risk factor '{top.headline}' "
            f"(materiality {top.materiality}/5) — {top.rationale}"
        ),
        severity=materiality_to_severity(top.materiality),
        evidence=_evidence(diff),
    ))
    for change in sorted_changes[1:]:
        findings.append(Finding(
            statement=(
                f"{_kind_verb(change.kind).capitalize()} "
                f"'{change.headline}' (materiality {change.materiality}/5) — "
                f"{change.rationale}"
            ),
            severity=materiality_to_severity(change.materiality),
            evidence=_evidence(diff),
        ))
    return findings


def _kind_verb(kind: str) -> str:
    return {
        "added": "adds",
        "removed": "removes",
        "modified": "modifies",
    }.get(kind, "changes")


def _evidence(diff: FilingDiff) -> list[str]:
    urls: list[str] = []
    if diff.current.url:
        urls.append(diff.current.url)
    if diff.prior is not None and diff.prior.url and diff.prior.url not in urls:
        urls.append(diff.prior.url)
    return urls


def _build_sources(*, diff: FilingDiff) -> list[Source]:
    seen: set[str] = set()
    sources: list[Source] = []

    def _add(url: str, title: str) -> None:
        if not url or url in seen:
            return
        seen.add(url)
        sources.append(Source(url=url, title=title, via="web_unlocker"))

    if diff.current.url:
        _add(diff.current.url, f"SEC EDGAR — {diff.current.short}")
    if diff.prior is not None and diff.prior.url:
        _add(diff.prior.url, f"SEC EDGAR — {diff.prior.short}")
    return sources


def _confidence_score(*, diff: FilingDiff, mode: str) -> float:
    score = 0.68
    if diff.has_changes:
        score += 0.06
    if diff.max_materiality >= 4:
        score += 0.06
    if diff.prior is not None:
        score += 0.04
    if mode == "live":
        score += 0.04
    elif mode == "partial":
        score -= 0.04
    elif mode == "mock":
        score -= 0.02
    return round(max(0.4, min(0.92, score)), 2)


__all__ = ["FilingModule"]
