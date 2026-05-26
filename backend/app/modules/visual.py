"""Visual — brand-impersonation detection via vision diff.

Day-5 deliverable (per implementation plan §4.5):

1. SERP queries for ``"<brand>" login``, ``"<brand>" signin`` etc. —
   collect candidate URLs.
2. Filter to non-canonical, non-social hosts.
3. For each suspect, Scraping Browser captures a full-page screenshot
   alongside the same shot for the legitimate brand page.
4. Claude vision: side-by-side comparison → similarity score + listed
   visual anomalies (off-brand colors, mistranscribed CTAs, off-canonical
   forms, etc.).
5. Rank suspects by suspicion verdict and surface the top N as findings.

The mock path runs the controlled-lookalike catalog through the *same*
``VisionDiff → Finding`` pipeline as the live path, so the brief shape
is identical whether or not Bright Data + Claude are wired up.

Mode labels
-----------
* ``live``    — every diff came from a real Claude vision call.
* ``partial`` — at least one diff came from a vision call; the rest
                fell through to declared-anomaly synthesis (controlled
                fallback) or were dropped (screenshot failures).
* ``mock``    — no live vision calls succeeded; the brief is fully
                synthesized from the controlled-target catalog.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from typing import Any, Iterable

from ..brightdata import record_simulated, scraping_browser, serp
from ..models import Finding, ModuleResult, Severity, Source
from ._fixtures import infer_subject, subject_domain
from .base import IntelligenceModule
from .visual_data import (
    ControlledTarget,
    SuspectCandidate,
    VisionAnomaly,
    VisionDiff,
    diff_from_declared,
    filter_candidates,
    get_controlled_target,
    verdict_for,
    verdict_to_severity,
)

log = logging.getLogger(__name__)


# Cap on candidates we'll actually screenshot + diff per run. Vision
# calls are the expensive step; 4 is enough for a demo without
# blowing the Bright Data + Claude budget mid-Q&A.
_MAX_SUSPECTS = 4


class VisualModule(IntelligenceModule):
    name = "visual"
    live_ready = True

    async def execute(self, params: dict[str, Any]) -> ModuleResult:
        subject, brand_url = _resolve_subject(params)
        controlled = get_controlled_target(subject)
        if controlled is not None:
            # Prefer the controlled target's canonical legit URL (which
            # carries a path) over the bare-domain synthesis — keeps
            # source attribution and diff.legit_url consistent.
            brand_url = controlled.legit_url

        candidates = await _discover_candidates(
            subject=subject, brand_url=brand_url, controlled=controlled,
        )
        candidates = filter_candidates(candidates, brand_url=brand_url)[:_MAX_SUSPECTS]

        if not candidates:
            log.info("Visual live: no candidates after filtering for %s; falling back to mock", subject)
            return await self.mock(params)

        legit_shot = await _screenshot(brand_url)

        diffs: list[VisionDiff] = []
        live_diffs = 0
        dropped: list[str] = []
        for candidate in candidates:
            diff, kind = await _diff_candidate(
                candidate=candidate,
                brand_url=brand_url,
                legit_shot=legit_shot,
                controlled=controlled,
            )
            if diff is None:
                dropped.append(candidate.url)
                continue
            diffs.append(diff)
            if kind == "live":
                live_diffs += 1

        if not diffs:
            log.info("Visual live: every candidate dropped for %s; falling back to mock", subject)
            return await self.mock(params)

        if live_diffs == 0:
            # Every suspect fell through to declared-anomaly synthesis —
            # not a live call in any meaningful sense.
            mode = "mock"
        elif live_diffs == len(diffs) and not dropped:
            mode = "live"
        else:
            mode = "partial"

        return _build_result(
            subject=subject,
            brand_url=brand_url,
            diffs=diffs,
            mode=mode,
            dropped=dropped,
            controlled=controlled,
        )

    async def mock(self, params: dict[str, Any]) -> ModuleResult:
        subject, brand_url = _resolve_subject(params)
        controlled = get_controlled_target(subject)

        if controlled is not None:
            brand_url = controlled.legit_url
            diffs = [
                diff_from_declared(la, legit_url=controlled.legit_url)
                for la in controlled.lookalikes
            ]
        else:
            diffs = _synthetic_diffs(subject=subject, brand_url=brand_url)

        await _emit_simulated_trace(subject=subject, brand_url=brand_url, diffs=diffs)

        return _build_result(
            subject=subject,
            brand_url=brand_url,
            diffs=diffs,
            mode="mock",
            dropped=[],
            controlled=controlled,
        )


async def _emit_simulated_trace(
    *, subject: str, brand_url: str, diffs: list[VisionDiff]
) -> None:
    """Declare the SERP + Scraping Browser calls the live path would make."""
    for term in ("login", "signin", "support"):
        await record_simulated(
            tool="search_engine",
            args={"query": f'"{subject}" {term}', "num_results": 8},
        )
    # Brand baseline screenshot
    await record_simulated(
        tool="scraping_browser_navigate",
        args={"url": brand_url},
    )
    await record_simulated(
        tool="scraping_browser_screenshot",
        args={"target": brand_url, "full_page": True},
    )
    # Per-suspect navigate + screenshot, capped to keep the trace tight
    for diff in diffs[:3]:
        await record_simulated(
            tool="scraping_browser_navigate",
            args={"url": diff.suspect_url},
        )
        await record_simulated(
            tool="scraping_browser_screenshot",
            args={"target": diff.suspect_url, "full_page": True},
        )


# ── Input resolution ───────────────────────────────────────────────


def _resolve_subject(params: dict[str, Any]) -> tuple[str, str]:
    subject = params.get("subject") or infer_subject(params.get("query", ""))
    brand_url = params.get("brand_url") or f"https://{subject_domain(subject)}"
    return subject, brand_url


# ── Candidate discovery ────────────────────────────────────────────


# Default SERP terms. Caller can override with params["search_terms"].
_DEFAULT_TERMS: tuple[str, ...] = (
    "login", "signin", "sign in", "support", "verify account",
)


async def _discover_candidates(
    *,
    subject: str,
    brand_url: str,
    controlled: ControlledTarget | None,
) -> list[SuspectCandidate]:
    """Build the candidate pool from controlled targets + SERP results.

    Controlled lookalikes always come first — they're the guaranteed
    demo material. SERP candidates pad the list for unknown brands or
    when a judge asks an ad-hoc question."""
    out: list[SuspectCandidate] = []

    if controlled is not None:
        for la in controlled.lookalikes:
            out.append(SuspectCandidate(
                url=la.url,
                title=la.slug,
                source="controlled",
                discovery_query="controlled-target",
            ))

    serp_terms = controlled.serp_terms if controlled else _DEFAULT_TERMS
    serp_rows = await _serp_for_terms(subject=subject, terms=serp_terms)
    for row in serp_rows:
        url = row.get("link") or row.get("url") or ""
        title = row.get("title") or ""
        query = row.get("_query", "")
        if not url:
            continue
        out.append(SuspectCandidate(
            url=url, title=title, source="serp", discovery_query=query,
        ))
    return out


async def _serp_for_terms(*, subject: str, terms: Iterable[str]) -> list[dict[str, Any]]:
    """Run a fan-out of SERP queries; tag each row with the source query."""
    queries = [f'"{subject}" {term}' for term in terms]
    rows = await asyncio.gather(
        *[serp.search(q, num=6) for q in queries],
        return_exceptions=True,
    )
    out: list[dict[str, Any]] = []
    for query, result in zip(queries, rows):
        if isinstance(result, Exception) or not result:
            continue
        for row in result:
            if not isinstance(row, dict):
                continue
            tagged = dict(row)
            tagged["_query"] = query
            out.append(tagged)
    return out


# ── Per-candidate diff ─────────────────────────────────────────────


async def _diff_candidate(
    *,
    candidate: SuspectCandidate,
    brand_url: str,
    legit_shot: bytes | None,
    controlled: ControlledTarget | None,
) -> tuple[VisionDiff | None, str]:
    """Run the vision diff on one candidate.

    Returns ``(diff, kind)`` where ``kind`` is:
      * ``"live"``      — Claude vision call returned a real anomaly list.
      * ``"declared"``  — controlled lookalike; we used declared anomalies.
      * ``"none"``      — failed entirely; caller drops the candidate.
    """
    declared_fallback: VisionDiff | None = None
    if candidate.source == "controlled" and controlled is not None:
        # Build the declared-anomaly diff up-front; we'll use it as the
        # safety net if the live vision call fails or is unavailable.
        for la in controlled.lookalikes:
            if la.url == candidate.url:
                declared_fallback = diff_from_declared(la, legit_url=controlled.legit_url)
                break

    suspect_shot = await _screenshot(candidate.url)
    if suspect_shot is None:
        if declared_fallback is not None:
            return declared_fallback, "declared"
        return None, "none"

    diff = await _vision_diff(
        suspect_url=candidate.url,
        suspect_title=candidate.title,
        legit_url=brand_url,
        legit_shot=legit_shot,
        suspect_shot=suspect_shot,
    )
    if diff is not None:
        return diff, "live"

    # Live call unavailable / parsing failed → fall back to declared
    # anomalies if this was a controlled candidate, otherwise drop.
    if declared_fallback is not None:
        return declared_fallback, "declared"
    return None, "none"


async def _screenshot(url: str) -> bytes | None:
    try:
        return await asyncio.wait_for(
            scraping_browser.screenshot(url),
            timeout=20.0,
        )
    except asyncio.TimeoutError:
        log.warning("Visual: screenshot timed out for %s", url)
        return None
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("Visual: screenshot failed for %s: %s", url, exc)
        return None


# ── Claude vision call ─────────────────────────────────────────────


_VISION_SYSTEM = """You are the Atlas Visual analyst.

You will receive two screenshots: the LEGITIMATE brand reference, then a \
SUSPECT page. Compare them and decide whether the suspect is a brand \
impersonation attempt.

Output STRICT JSON. No markdown fences. No commentary outside the JSON.

Schema:
{
  "similarity": 0.0-1.0,        // overall visual similarity to the legit page
  "anomalies": [
    {
      "kind": "logo" | "color" | "copy" | "form" | "footer" | "layout" | "stale",
      "description": "<one concrete observation tied to the screenshots>"
    }
  ],
  "reasoning": "<one sentence summary of why you scored as you did>"
}

Rules:
- Only list anomalies you can directly observe in the screenshots.
- Be specific: "primary CTA reads 'Login' not 'Sign in to AcmeCorp'", \
not "CTA differs".
- Do not list more than 6 anomalies.
- ``similarity`` is the visual similarity to the legit page (1.0 == \
indistinguishable). It is NOT a suspicion score.
- If you cannot see one of the screenshots clearly, set similarity to 0 \
and anomalies to []."""


_ALLOWED_KINDS: frozenset[str] = frozenset({
    "logo", "color", "copy", "form", "footer", "layout", "stale",
})

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


async def _vision_diff(
    *,
    suspect_url: str,
    suspect_title: str,
    legit_url: str,
    legit_shot: bytes | None,
    suspect_shot: bytes,
) -> VisionDiff | None:
    """Ask Claude vision to compare the two screenshots.

    Returns None when the LLM isn't configured or the response was
    unparseable — callers fall back to declared-anomaly synthesis or
    drop the candidate."""
    from ..agent.llm import get_llm  # local import — avoids import cycle

    llm = get_llm()
    if llm is None:
        return None

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
    except ImportError:  # pragma: no cover
        return None

    parts: list[dict[str, Any]] = []
    if legit_shot is not None:
        parts.append(_text_part(f"LEGITIMATE brand reference for {legit_url}:"))
        parts.append(_image_part(legit_shot))
    else:
        parts.append(_text_part(
            f"LEGITIMATE brand reference URL: {legit_url} "
            "(screenshot unavailable; compare against your prior knowledge of the brand)."
        ))
    parts.append(_text_part(f"SUSPECT page at {suspect_url}:"))
    parts.append(_image_part(suspect_shot))

    try:
        response = await llm.ainvoke([
            SystemMessage(content=_VISION_SYSTEM),
            HumanMessage(content=parts),
        ])
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("Visual: vision LLM call failed for %s (%s)", suspect_url, exc)
        return None

    text = response.content if isinstance(response.content, str) else str(response.content)
    parsed = _parse_vision_response(text)
    if parsed is None:
        log.warning("Visual: vision LLM returned unparseable JSON for %s", suspect_url)
        return None

    similarity = parsed["similarity"]
    anomalies = parsed["anomalies"]
    has_form = any(a.kind == "form" for a in anomalies)
    verdict = verdict_for(
        anomaly_count=len(anomalies),
        similarity=similarity,
        has_form_anomaly=has_form,
    )
    return VisionDiff(
        suspect_url=suspect_url,
        suspect_title=suspect_title,
        similarity=similarity,
        anomalies=tuple(anomalies),
        verdict=verdict,
        reasoning=parsed["reasoning"],
        legit_url=legit_url,
    )


def _text_part(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _image_part(image_bytes: bytes) -> dict[str, Any]:
    """Pack a PNG into the Anthropic multimodal block format LangChain expects."""
    data = base64.b64encode(image_bytes).decode("ascii")
    return {
        "type": "image",
        "source_type": "base64",
        "mime_type": "image/png",
        "data": data,
    }


def _parse_vision_response(raw: str) -> dict[str, Any] | None:
    cleaned = _FENCE_RE.sub("", raw.strip()).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    sim = data.get("similarity")
    if not isinstance(sim, (int, float)):
        return None
    similarity = max(0.0, min(1.0, float(sim)))

    anomalies: list[VisionAnomaly] = []
    for entry in data.get("anomalies") or []:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind", "")
        desc = entry.get("description", "")
        if not isinstance(kind, str) or kind not in _ALLOWED_KINDS:
            continue
        if not isinstance(desc, str) or not desc.strip():
            continue
        anomalies.append(VisionAnomaly(kind=kind, description=desc.strip()))  # type: ignore[arg-type]

    reasoning = data.get("reasoning", "")
    if not isinstance(reasoning, str):
        reasoning = ""
    return {"similarity": similarity, "anomalies": anomalies, "reasoning": reasoning}


# ── Synthetic diffs for unknown brands (mock fallback) ─────────────


def _synthetic_diffs(*, subject: str, brand_url: str) -> list[VisionDiff]:
    """When we have no controlled target, generate two plausible
    typosquat diffs from the subject name so the brief still has
    something to render."""
    slug = subject.lower().replace(" ", "")
    typo_url = f"https://{slug}-secure-login.test"
    color_url = f"https://app-{slug}.test"

    typo_anoms: tuple[VisionAnomaly, ...] = (
        VisionAnomaly("logo", f"Logo wordmark misshapen — aspect ratio off ~6% vs canonical {subject}"),
        VisionAnomaly("copy", "Primary CTA copy mistranscribed"),
        VisionAnomaly("footer", "Footer links resolve to non-canonical domains"),
    )
    color_anoms: tuple[VisionAnomaly, ...] = (
        VisionAnomaly("color", "Brand primary off-hue versus canonical palette"),
        VisionAnomaly("stale", "Outdated marketing copy from prior site version"),
    )

    return [
        VisionDiff(
            suspect_url=typo_url,
            suspect_title=f"{subject} typosquat",
            similarity=0.91,
            anomalies=typo_anoms,
            verdict=verdict_for(anomaly_count=len(typo_anoms), similarity=0.91),
            reasoning=f"Synthesized typosquat profile for {subject} (no controlled target on file).",
            legit_url=brand_url,
        ),
        VisionDiff(
            suspect_url=color_url,
            suspect_title=f"{subject} color-swap",
            similarity=0.84,
            anomalies=color_anoms,
            verdict=verdict_for(anomaly_count=len(color_anoms), similarity=0.84),
            reasoning=f"Synthesized color-swap profile for {subject}.",
            legit_url=brand_url,
        ),
    ]


# ── Result assembly ───────────────────────────────────────────────


_VERDICT_RANK: dict[str, int] = {"critical": 3, "high": 2, "notable": 1, "low": 0}


def _build_result(
    *,
    subject: str,
    brand_url: str,
    diffs: list[VisionDiff],
    mode: str,
    dropped: list[str],
    controlled: ControlledTarget | None,
) -> ModuleResult:
    diffs_sorted = sorted(
        diffs,
        key=lambda d: (_VERDICT_RANK.get(d.verdict, 0), d.similarity),
        reverse=True,
    )

    findings = _build_findings(subject=subject, diffs=diffs_sorted)
    sources = _build_sources(brand_url=brand_url, diffs=diffs_sorted)

    raw_data: dict[str, Any] = {
        "subject": subject,
        "brand_url": brand_url,
        "controlled": controlled is not None,
        "mode": mode,
        "suspects": [d.to_raw() for d in diffs_sorted],
        "suspect_count": len(diffs_sorted),
        "high_count": sum(
            1 for d in diffs_sorted if d.verdict in ("high", "critical")
        ),
        "dropped": dropped,
    }

    status = "partial" if (mode == "partial" or dropped) else "success"
    return ModuleResult(
        module="visual",
        status=status,
        findings=findings,
        sources=sources,
        raw_data=raw_data,
        confidence=_confidence_score(diffs_sorted, mode, dropped),
    )


def _confidence_score(
    diffs: list[VisionDiff], mode: str, dropped: list[str],
) -> float:
    score = 0.74
    if any(d.verdict == "critical" for d in diffs):
        score += 0.08
    if any(d.verdict in ("high", "critical") for d in diffs):
        score += 0.04
    if mode == "live":
        score += 0.04
    elif mode == "partial":
        score -= 0.04 * (len(dropped) / max(len(diffs) + len(dropped), 1))
    return round(max(0.4, min(0.94, score)), 2)


def _build_findings(*, subject: str, diffs: list[VisionDiff]) -> list[Finding]:
    if not diffs:
        return [Finding(
            statement=(
                f"No impersonation candidates surfaced for {subject} in this run. "
                "Re-scan with broader SERP terms or controlled targets."
            ),
            severity="info",
            evidence=[],
        )]

    findings: list[Finding] = []
    top = diffs[0]
    sev_top: Severity = verdict_to_severity(top.verdict)
    findings.append(Finding(
        statement=(
            f"{_verdict_phrase(top.verdict)} {subject} impersonation candidate at "
            f"{top.suspect_url}: {len(top.anomalies)} "
            f"{_pluralize(len(top.anomalies), 'visual anomaly', 'visual anomalies')} "
            f"at similarity {top.similarity:.2f} — "
            f"{_top_anomaly_summary(top.anomalies)}."
        ),
        severity=sev_top,
        evidence=_evidence_for(top),
    ))

    for diff in diffs[1:]:
        sev: Severity = verdict_to_severity(diff.verdict)
        if sev == "info":
            # Skip uninteresting hits unless we'd otherwise emit zero
            # follow-up findings.
            continue
        findings.append(Finding(
            statement=(
                f"Secondary candidate {diff.suspect_url}: "
                f"{len(diff.anomalies)} "
                f"{_pluralize(len(diff.anomalies), 'anomaly', 'anomalies')} "
                f"at similarity {diff.similarity:.2f} — "
                f"{_top_anomaly_summary(diff.anomalies)}."
            ),
            severity=sev,
            evidence=_evidence_for(diff),
        ))

    high_or_crit = [d for d in diffs if d.verdict in ("high", "critical")]
    if len(high_or_crit) >= 2:
        findings.append(Finding(
            statement=(
                f"Multiple high-suspicion lookalikes detected for {subject} "
                f"({len(high_or_crit)} candidates at verdict ≥ high) — recommend "
                "domain takedown workflow plus DMCA filing on the typosquat host."
            ),
            severity="high",
            evidence=[d.suspect_url for d in high_or_crit[:3]],
        ))

    return findings


def _pluralize(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def _verdict_phrase(v: str) -> str:
    return {
        "critical": "Critical-confidence",
        "high": "High-confidence",
        "notable": "Notable",
        "low": "Low-confidence",
    }.get(v, "Notable")


def _top_anomaly_summary(anomalies: tuple[VisionAnomaly, ...]) -> str:
    if not anomalies:
        return "no anomaly details available"
    primary = anomalies[0].description
    extra = len(anomalies) - 1
    if extra <= 0:
        return primary
    return f"{primary} (+{extra} more)"


def _evidence_for(diff: VisionDiff) -> list[str]:
    urls = [diff.suspect_url]
    if diff.legit_url and diff.legit_url not in urls:
        urls.append(diff.legit_url)
    return urls[:2]


def _build_sources(*, brand_url: str, diffs: list[VisionDiff]) -> list[Source]:
    seen: set[str] = set()
    sources: list[Source] = []

    def _add(url: str, title: str, via: str) -> None:
        if not url or url in seen:
            return
        seen.add(url)
        sources.append(Source(url=url, title=title, via=via))

    _add(brand_url, "Legitimate brand reference", "scraping_browser")
    for diff in diffs:
        _add(diff.suspect_url, diff.suspect_title or "Suspect lookalike", "scraping_browser")
    return sources


__all__ = ["VisualModule"]
