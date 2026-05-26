"""AltData — composite distress/momentum score from review signals.

Day-6 deliverable (per implementation plan §4.4):

1. Glassdoor: pull recent employee reviews via Bright Data Web Scraper API.
2. G2: pull recent product reviews via Bright Data Web Scraper API.
3. Compute the headline trend (rating delta, review velocity, complaint
   clustering) over a recent-30d vs prior-30-60d window for each source.
4. Blend the per-source trends into one momentum/distress composite
   score, with named drivers.

Polish ceiling is intentionally lower than Signal / TruePrice / Visual
(per day-6 brief). The synthesizer is rule-based first; LLM is a polish
layer that can subsume the rule-based output when configured. The
findings shape is identical either way so the brief looks the same in
mock mode and live mode.

Mode labels
-----------
* ``live``    — at least one source returned real reviews from Bright Data.
* ``partial`` — one source live, the other fell back to fixtures.
* ``mock``    — both sources used fixtures (no MCP creds / unavailable).
"""

from __future__ import annotations

import logging
from typing import Any

from ..brightdata import record_simulated, web_scraper_api
from ..models import Finding, ModuleResult, Severity, Source
from ._fixtures import infer_subject
from .altdata_data import (
    CompositeScore,
    Review,
    ReviewSource,
    ReviewSummary,
    composite_score,
    fixture_for,
    g2_product_url,
    glassdoor_search_url,
    normalize,
    summarize_trend,
)
from .base import IntelligenceModule

log = logging.getLogger(__name__)


class AltDataModule(IntelligenceModule):
    name = "altdata"
    live_ready = True

    async def execute(self, params: dict[str, Any]) -> ModuleResult:
        subject = params.get("subject") or infer_subject(params.get("query", ""))
        limit = int(params.get("limit", 80))

        gd_reviews, gd_mode = await _fetch_reviews(
            subject=subject, source="glassdoor", limit=limit,
        )
        g2_reviews, g2_mode = await _fetch_reviews(
            subject=subject, source="g2", limit=limit,
        )

        mode = _combine_mode(gd_mode, g2_mode)
        if mode == "mock" and not gd_reviews and not g2_reviews:
            log.info("AltData live: no reviews or fixtures for %s; emitting no-data brief", subject)
        return _build_result(
            subject=subject,
            glassdoor=gd_reviews,
            g2=g2_reviews,
            mode=mode,
        )

    async def mock(self, params: dict[str, Any]) -> ModuleResult:
        subject = params.get("subject") or infer_subject(params.get("query", ""))
        await _emit_simulated_trace(subject)
        gd = normalize(fixture_for(subject, "glassdoor"), source="glassdoor")
        g2 = normalize(fixture_for(subject, "g2"), source="g2")
        return _build_result(
            subject=subject,
            glassdoor=gd,
            g2=g2,
            mode="mock",
        )


async def _emit_simulated_trace(subject: str) -> None:
    """Declare the SERP + Web Unlocker calls the live path would make.

    Both review sources are now web_unlocker-fetched after a SERP discovery
    step, so the mock trace mirrors that pattern."""
    await record_simulated(
        tool="search_engine",
        args={"query": f'site:glassdoor.com/Reviews "{subject}" software reviews',
              "num_results": 5},
    )
    await record_simulated(
        tool="scrape_as_markdown",
        args={"url": glassdoor_search_url(subject)},
    )
    await record_simulated(
        tool="scrape_as_markdown",
        args={"url": g2_product_url(subject)},
    )


# ── Fetch + normalize one source ───────────────────────────────────


async def _fetch_reviews(
    *, subject: str, source: ReviewSource, limit: int,
) -> tuple[list[Review], str]:
    """Fetch reviews for one source. Returns ``(reviews, mode)``.

    ``mode`` is "live" when MCP returned rows we could normalize, or
    "mock" when we fell back to fixtures. A 20s timeout prevents a slow
    scrape from blocking the rest of the brief.
    """
    import asyncio

    rows: list[dict[str, Any]] | None
    # Both sources now use the web-unlocker (scrape_as_markdown) as primary.
    # Glassdoor: SERP (~4s) + unlocker scrape (~40s) — allow 60s.
    # G2: unlocker scrape alone can take ~65s — allow 75s.
    timeout = 60.0 if source == "glassdoor" else 75.0
    try:
        if source == "glassdoor":
            rows = await asyncio.wait_for(
                web_scraper_api.fetch_glassdoor_reviews(subject, limit=limit),
                timeout=timeout,
            )
        else:
            rows = await asyncio.wait_for(
                web_scraper_api.fetch_g2_reviews(subject, limit=limit),
                timeout=timeout,
            )
    except asyncio.TimeoutError:
        log.warning("AltData: %s scrape timed out for %s; using fixture", source, subject)
        rows = None

    if rows:
        reviews = normalize(rows, source=source)
        if len(reviews) >= 3:
            return reviews, "live"
        log.info(
            "AltData live: only %d %s rows normalized for %s; using fixture",
            len(reviews), source, subject,
        )

    return normalize(fixture_for(subject, source), source=source), "mock"


def _combine_mode(gd_mode: str, g2_mode: str) -> str:
    if gd_mode == "live" and g2_mode == "live":
        return "live"
    if gd_mode == "live" or g2_mode == "live":
        return "partial"
    return "mock"


# ── Result assembly ───────────────────────────────────────────────


def _build_result(
    *,
    subject: str,
    glassdoor: list[Review],
    g2: list[Review],
    mode: str,
) -> ModuleResult:
    summaries: list[ReviewSummary] = []
    if glassdoor:
        summaries.append(summarize_trend(glassdoor, subject=subject))
    if g2:
        summaries.append(summarize_trend(g2, subject=subject))

    composite = composite_score(summaries)

    findings = _build_findings(subject=subject, summaries=summaries, composite=composite)
    sources = _build_sources(subject=subject, summaries=summaries)

    raw_data: dict[str, Any] = {
        "subject": subject,
        "mode": mode,
        "composite_score": composite.score,
        "composite_label": composite.label,
        "drivers": composite.drivers,
        "sources": {s.source: _summary_to_raw(s) for s in summaries},
    }

    status = "partial" if mode == "partial" else "success"
    if not findings:
        status = "partial"
    return ModuleResult(
        module="altdata",
        status=status,
        findings=findings,
        sources=sources,
        raw_data=raw_data,
        confidence=_confidence_score(summaries=summaries, mode=mode),
    )


def _summary_to_raw(s: ReviewSummary) -> dict[str, Any]:
    return {
        "total": s.total,
        "recent_30d": s.recent_30d,
        "prior_30_60d": s.prior_30_60d,
        "avg_rating_recent": s.avg_rating_recent,
        "avg_rating_prior": s.avg_rating_prior,
        "rating_delta": s.rating_delta,
        "velocity_ratio": s.velocity_ratio,
        "complaint_clusters": s.complaint_clusters,
        "top_complaint": s.top_complaint,
        "representative_urls": s.representative_urls,
    }


# ── Findings (rule-based; sharp enough for the supporting-cast slot) ──


def _build_findings(
    *,
    subject: str,
    summaries: list[ReviewSummary],
    composite: CompositeScore,
) -> list[Finding]:
    if not summaries:
        return [Finding(
            statement=(
                f"No review data available for {subject} across Glassdoor or G2 — "
                "treat any alt-data inference as low confidence."
            ),
            severity="info",
            evidence=[],
        )]

    findings: list[Finding] = []

    # 1. Headline composite score finding (always emitted).
    sev_headline: Severity = (
        "high" if composite.label == "distress" and composite.score <= 0.40
        else "notable" if composite.label != "neutral"
        else "info"
    )
    drivers_clause = f" Drivers: {'; '.join(composite.drivers[:3])}." if composite.drivers else ""
    findings.append(Finding(
        statement=(
            f"{subject} composite alt-data score "
            f"{composite.score:.2f} ({composite.label}) — "
            f"blended across Glassdoor + G2 trend signals.{drivers_clause}"
        ),
        severity=sev_headline,
        evidence=_aggregate_urls(summaries),
    ))

    # 2. Per-source trend findings (only when material).
    for s in summaries:
        findings.extend(_per_source_findings(s))

    # Trim to a reasonable headline-set so the brief stays scannable.
    return findings[:6]


def _per_source_findings(s: ReviewSummary) -> list[Finding]:
    out: list[Finding] = []
    source_label = "Glassdoor" if s.source == "glassdoor" else "G2"
    citation = s.representative_urls[:2] or _fallback_citation(s)

    # Rating shift — the headline trend per source.
    if s.rating_delta >= 0.30 and s.avg_rating_prior > 0:
        out.append(Finding(
            statement=(
                f"{source_label} sentiment improved "
                f"+{s.rating_delta:.2f} stars QoQ "
                f"({s.avg_rating_prior:.2f} → {s.avg_rating_recent:.2f}) "
                f"across {s.recent_30d} recent reviews — reverses the prior 30-60-day window."
            ),
            severity="high",
            evidence=citation,
        ))
    elif s.rating_delta <= -0.30 and s.avg_rating_prior > 0:
        out.append(Finding(
            statement=(
                f"{source_label} sentiment {s.rating_delta:+.2f} stars QoQ "
                f"({s.avg_rating_prior:.2f} → {s.avg_rating_recent:.2f}) "
                f"across {s.recent_30d} recent reviews — material decline."
            ),
            severity="high",
            evidence=citation,
        ))
    elif abs(s.rating_delta) >= 0.10 and s.avg_rating_prior > 0:
        out.append(Finding(
            statement=(
                f"{source_label} sentiment {s.rating_delta:+.2f} stars QoQ "
                f"({s.avg_rating_prior:.2f} → {s.avg_rating_recent:.2f}) — "
                "directional but within noise."
            ),
            severity="notable",
            evidence=citation,
        ))

    # Velocity shift — secondary signal, only fires when the baseline
    # is large enough for the ratio to be meaningful.
    if (
        s.velocity_ratio >= 2.0
        and s.recent_30d >= 5
        and s.prior_30_60d >= 3
    ):
        out.append(Finding(
            statement=(
                f"{source_label} review velocity {s.velocity_ratio:.1f}× "
                f"the prior 30-day window ({s.recent_30d} vs {s.prior_30_60d}) — "
                "attention spike, not steady-state."
            ),
            severity="notable",
            evidence=citation,
        ))

    # Complaint cluster — surface only when it carries weight.
    if s.top_complaint and s.complaint_clusters.get(s.top_complaint, 0) >= 2:
        cluster_count = s.complaint_clusters[s.top_complaint]
        sev: Severity = (
            "high" if s.top_complaint in ("stability", "leadership") and cluster_count >= 3
            else "notable"
        )
        out.append(Finding(
            statement=(
                f"{source_label} complaints cluster on '{s.top_complaint}' "
                f"({cluster_count} of {s.recent_30d} recent reviews) — "
                "not diffuse; thematic."
            ),
            severity=sev,
            evidence=citation,
        ))

    return out


def _fallback_citation(s: ReviewSummary) -> list[str]:
    """When a source has no per-review URLs, cite the search landing page."""
    if s.source == "glassdoor":
        return [glassdoor_search_url(s.subject)]
    return [g2_product_url(s.subject)]


def _aggregate_urls(summaries: list[ReviewSummary]) -> list[str]:
    urls: list[str] = []
    for s in summaries:
        urls.extend(s.representative_urls[:1])
    for s in summaries:
        if not s.representative_urls:
            urls.extend(_fallback_citation(s))
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out[:3]


def _build_sources(*, subject: str, summaries: list[ReviewSummary]) -> list[Source]:
    seen: set[str] = set()
    sources: list[Source] = []

    def _add(url: str, title: str) -> None:
        if not url or url in seen:
            return
        seen.add(url)
        sources.append(Source(url=url, title=title, via="web_scraper_api"))

    for s in summaries:
        if s.source == "glassdoor":
            _add(glassdoor_search_url(subject), f"Glassdoor — {subject} reviews (search)")
        else:
            _add(g2_product_url(subject), f"G2 — {subject} reviews")
        for url in s.representative_urls[:2]:
            _add(url, f"{s.source.capitalize()} review")
    return sources


def _confidence_score(*, summaries: list[ReviewSummary], mode: str) -> float:
    score = 0.62
    total_recent = sum(s.recent_30d for s in summaries)
    if total_recent >= 5:
        score += 0.06
    if total_recent >= 10:
        score += 0.04
    if len(summaries) >= 2:
        score += 0.04
    if mode == "live":
        score += 0.06
    elif mode == "partial":
        score += 0.02
    elif mode == "mock":
        score -= 0.02
    return round(max(0.4, min(0.9, score)), 2)


__all__ = ["AltDataModule"]
