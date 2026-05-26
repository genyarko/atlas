"""Exposure — credentials, PII, and exec-doxx surface across the open web.

Day-7 deliverable (per implementation plan §4.6):

1. Targeted SERP dorks: paste sites, public code search, breach
   aggregators, exec-doxx queries — built from the subject's canonical
   domain via ``build_dorks()``.
2. Web Unlocker fetches candidate pages (paste sites + code archives
   often block conventional clients; SERP returns the URL, Web Unlocker
   lands the body).
3. LLM extracts structured ``LeakRecord``s: credential patterns, API
   key shapes, webhook URLs, persistent identifiers, infra topology.
4. Severity classification on the Atlas scale (critical/high/notable/info).

The mock path runs the controlled-target catalog (``demo/exposure/``)
through the *same* ``LeakRecord → Finding`` pipeline as the live path,
so the brief shape is identical whether or not Bright Data + Claude are
wired up.

Mode labels
-----------
* ``live``    — every emitted leak came from a real LLM extraction over
                fetched content. Controlled fixtures may still be
                included (they're real declared leaks, not synthesis).
* ``partial`` — at least one candidate produced a live leak; some
                candidates dropped (fetch failed or LLM unparseable)
                or fell back to declared records.
* ``mock``    — no live extractions; brief is fully synthesized from
                the controlled-target catalog.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from typing import Any, Iterable

from ..brightdata import record_simulated, serp, web_unlocker
from ..models import Finding, ModuleResult, Source
from ._fixtures import infer_subject, subject_domain
from .base import IntelligenceModule
from .exposure_data import (
    ControlledExposureTarget,
    ExposureScan,
    LeakRecord,
    SerpCandidate,
    Severity,
    build_dorks,
    coerce_kind,
    coerce_severity,
    filter_candidates,
    get_controlled_target,
    has_credential_shape,
    severity_rank,
)

log = logging.getLogger(__name__)


# Cap on how many SERP-discovered candidates we'll actually fetch +
# extract per run. Web Unlocker + LLM extraction is the expensive step;
# 6 is enough breadth without blowing the demo budget.
_MAX_CANDIDATES = 6


class ExposureModule(IntelligenceModule):
    name = "exposure"
    live_ready = True

    async def execute(self, params: dict[str, Any]) -> ModuleResult:
        subject, domain = _resolve_subject(params)
        controlled = get_controlled_target(subject)

        dorks = build_dorks(
            domain=domain, custom=params.get("dorks") or None,
        )

        candidates = await _run_dorks(dorks)

        live_records: list[LeakRecord] = []
        dropped: list[str] = []
        for candidate in candidates[:_MAX_CANDIDATES]:
            extracted = await _extract_candidate(
                candidate=candidate, subject=subject, domain=domain,
            )
            if extracted is None:
                dropped.append(candidate.url)
                continue
            live_records.extend(extracted)

        # Always include the controlled-target declared records — they're
        # the demo guarantee. Filtered to avoid double-counting URLs the
        # live extraction already covered.
        controlled_records = _controlled_records(
            controlled=controlled, already_seen={r.location_url for r in live_records},
        )

        records = list(live_records) + controlled_records
        if not records:
            log.info("Exposure live: no leaks found for %s; mock fallback", subject)
            return await self.mock(params)

        mode = _resolve_mode(
            live_records=live_records,
            controlled_records=controlled_records,
            dropped=dropped,
        )
        scan = ExposureScan(
            subject=subject,
            domain=domain,
            dorks=tuple(dorks),
            candidates=tuple(candidates),
            records=tuple(records),
            dropped=tuple(dropped),
        )
        return _build_result(scan=scan, mode=mode)

    async def mock(self, params: dict[str, Any]) -> ModuleResult:
        subject, domain = _resolve_subject(params)
        controlled = get_controlled_target(subject)
        dorks = build_dorks(
            domain=domain, custom=params.get("dorks") or None,
        )
        records: tuple[LeakRecord, ...]
        if controlled is not None:
            records = tuple(
                rec for leak in controlled.leaks for rec in leak.records
            )
        else:
            records = _synthetic_records(subject=subject, domain=domain)
        await _emit_simulated_trace(dorks=dorks, records=records)
        scan = ExposureScan(
            subject=subject,
            domain=domain,
            dorks=tuple(dorks),
            candidates=(),
            records=records,
        )
        return _build_result(scan=scan, mode="mock")


async def _emit_simulated_trace(
    *, dorks: list[str], records: tuple[LeakRecord, ...]
) -> None:
    """Declare SERP dorks + Web Unlocker fetches the live path would make."""
    # Cap dork emissions so the rail stays focused — pick the first
    # four, which is the meaningful breadth (paste/code/breach/exec).
    for dork in dorks[:4]:
        await record_simulated(
            tool="search_engine",
            args={"query": dork, "num_results": 8},
        )
    # Then a Web Unlocker fetch per declared leak's location URL — these
    # mirror what _extract_candidate() would do in the live path.
    seen: set[str] = set()
    for record in records[:3]:
        url = getattr(record, "location_url", "") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        await record_simulated(
            tool="scrape_as_markdown",
            args={"url": url},
        )


# ── Input resolution ───────────────────────────────────────────────


def _resolve_subject(params: dict[str, Any]) -> tuple[str, str]:
    subject = params.get("subject") or infer_subject(params.get("query", ""))
    domain = params.get("domain") or subject_domain(subject)
    return subject, domain


# ── SERP discovery ─────────────────────────────────────────────────


async def _run_dorks(dorks: list[str]) -> list[SerpCandidate]:
    """Fan out the dork list to SERP and return de-duped candidates."""
    if not dorks:
        return []
    results = await asyncio.gather(
        *[serp.search(q, num=8) for q in dorks],
        return_exceptions=True,
    )
    candidates: list[SerpCandidate] = []
    seen: set[str] = set()
    for query, result in zip(dorks, results):
        if isinstance(result, Exception) or not result:
            continue
        tagged = [_tag(row, query=query) for row in result if isinstance(row, dict)]
        for c in filter_candidates(tagged, discovery_query=query):
            if c.url in seen:
                continue
            seen.add(c.url)
            candidates.append(c)
    return candidates


def _tag(row: dict[str, Any], *, query: str) -> dict[str, Any]:
    out = dict(row)
    out.setdefault("_query", query)
    return out


# ── Candidate extraction ───────────────────────────────────────────


async def _extract_candidate(
    *, candidate: SerpCandidate, subject: str, domain: str,
) -> list[LeakRecord] | None:
    """Fetch one candidate via Web Unlocker and run LLM extraction.

    Returns the list of extracted records (possibly empty if the page
    held no creds), or None when the fetch failed entirely. Empty list
    vs None is a meaningful distinction for the mode calculation."""
    body = await _fetch(candidate.url)
    if body is None:
        return None

    # Cheap pre-flight: if there's nothing credential-shaped in the
    # body AND no shape in the SERP snippet either, skip the LLM call.
    body_text = _strip_html(body)
    if not has_credential_shape(body_text) and not has_credential_shape(candidate.snippet):
        # Page exists but doesn't smell like a leak — treat as an empty
        # extraction (still counts as a successful fetch).
        return []

    extracted = await _llm_extract(
        candidate=candidate, subject=subject, domain=domain, body=body_text,
    )
    if extracted is None:
        return None
    return extracted


async def _fetch(url: str, *, timeout_s: float = 20.0) -> str | None:
    if not url:
        return None
    try:
        return await asyncio.wait_for(web_unlocker.fetch(url), timeout=timeout_s)
    except asyncio.TimeoutError:
        log.warning("Exposure: Web Unlocker timed out for %s", url)
        return None
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("Exposure: Web Unlocker failed for %s: %s", url, exc)
        return None


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(body: str) -> str:
    text = _TAG_RE.sub(" ", body)
    # Code-search and breach hosts render bodies as HTML — `&lt;`-encoded
    # credential lines must round-trip through unescape() or the pre-flight
    # regex misses them and the LLM also sees a degraded excerpt.
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


# ── LLM extraction ─────────────────────────────────────────────────


_EXTRACT_SYSTEM = """You are the Atlas Exposure analyst.

You will receive an excerpt from a web page surfaced by an exposure-scan \
dork (paste site, public code search, or breach aggregator). Identify \
material leak observations relevant to the named subject organization.

Output STRICT JSON. No markdown fences. No commentary outside the JSON.

Schema:
{
  "summary": "<one sentence overall observation>",
  "records": [
    {
      "kind": "credential" | "api_key" | "webhook" | "pii" | "infra" | "mention",
      "severity": "info" | "notable" | "high" | "critical",
      "excerpt": "<short redacted excerpt drawn from the page (≤200 chars)>",
      "rationale": "<one sentence: why this matters for the subject org>"
    }
  ]
}

Severity rubric:
- critical: a live-shaped credential (password line, PAT/secret token, \
working webhook URL) tied to the subject's domain.
- high: persistent identifier paired with a secret (email+password \
fixture), deploy key without secret body, or webhook URL.
- notable: org-internal hostnames, infra topology, deploy-key references \
without bodies.
- info: incidental brand mention without any credential or PII shape.

Rules:
- Cite ONLY content visible in the supplied excerpt. Do not invent.
- Redact passwords in the excerpt: keep the FIRST 4 chars then `…`.
- Redact tokens to the FIRST 12 chars then `…`.
- Produce 0-6 records. Empty records array means "no material leak".
- If the page does not reference the subject's domain at all, return \
empty records and severity "info" in summary."""


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


async def _llm_extract(
    *,
    candidate: SerpCandidate,
    subject: str,
    domain: str,
    body: str,
) -> list[LeakRecord] | None:
    """Run the LLM extraction on one candidate page.

    Returns ``None`` when the LLM isn't available or the response was
    unparseable — callers treat that as a partial fetch (count it
    against ``dropped``). Returns ``[]`` when the LLM found nothing
    material — a valid signal that the page wasn't a leak."""
    from ..agent.llm import get_llm  # local import — avoids cycle

    llm = get_llm()
    if llm is None:
        return None

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
    except ImportError:  # pragma: no cover
        return None

    prompt = {
        "subject": subject,
        "domain": domain,
        "discovery_query": candidate.discovery_query,
        "candidate": {
            "url": candidate.url,
            "title": candidate.title,
            "channel": candidate.channel,
        },
        "excerpt": body[:14_000],
    }
    try:
        response = await llm.ainvoke([
            SystemMessage(content=_EXTRACT_SYSTEM),
            HumanMessage(content=json.dumps(prompt, indent=2)),
        ])
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("Exposure LLM extract failed (%s); dropping %s", exc, candidate.url)
        return None
    text = response.content if isinstance(response.content, str) else str(response.content)
    return _parse_llm_extract(text, candidate=candidate)


def _parse_llm_extract(
    raw: str, *, candidate: SerpCandidate,
) -> list[LeakRecord] | None:
    """Parse the LLM JSON; return ``None`` on hard parse failure, ``[]`` on
    a structurally-valid empty extraction."""
    cleaned = _FENCE_RE.sub("", raw.strip()).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        log.warning("Exposure LLM returned non-JSON for %s", candidate.url)
        return None
    if not isinstance(data, dict):
        return None
    raw_records = data.get("records")
    if not isinstance(raw_records, list):
        return []

    via = "web_unlocker" if candidate.channel in ("paste", "breach") else "serp_api"
    out: list[LeakRecord] = []
    for entry in raw_records:
        if not isinstance(entry, dict):
            continue
        excerpt = (entry.get("excerpt") or "").strip()
        rationale = (entry.get("rationale") or "").strip()
        if not excerpt:
            continue
        out.append(LeakRecord(
            channel=candidate.channel,
            kind=coerce_kind(entry.get("kind")),
            severity=coerce_severity(entry.get("severity")),
            location_url=candidate.url,
            location_title=candidate.title or candidate.url,
            excerpt=excerpt[:200],
            rationale=rationale[:300],
            via=via,
        ))
    return out


# ── Controlled-record handling ─────────────────────────────────────


def _controlled_records(
    *,
    controlled: ControlledExposureTarget | None,
    already_seen: set[str],
) -> list[LeakRecord]:
    """Flatten controlled-target declared leaks, skipping URLs already
    surfaced by the live extraction."""
    if controlled is None:
        return []
    out: list[LeakRecord] = []
    for leak in controlled.leaks:
        if leak.claimed_url in already_seen:
            continue
        out.extend(leak.records)
    return out


def _resolve_mode(
    *,
    live_records: list[LeakRecord],
    controlled_records: list[LeakRecord],
    dropped: list[str],
) -> str:
    """Pick the mode label based on what the run produced."""
    if not live_records:
        return "mock"
    if controlled_records or dropped:
        return "partial"
    return "live"


# ── Synthetic records (fallback for unknown subjects) ──────────────


def _synthetic_records(*, subject: str, domain: str) -> tuple[LeakRecord, ...]:
    """When no controlled target on file, emit a low-confidence synthetic
    leak so the brief still has shape. Severity capped at ``notable``
    because we never invent a critical leak."""
    paste_url = f"https://pastebin.com/raw/{subject.lower()}-DEMO-1"
    github_url = f"https://github.com/search?q={domain}+password&type=code"
    return (
        LeakRecord(
            channel="paste",
            kind="mention",
            severity="notable",
            location_url=paste_url,
            location_title=f"Paste-site mention of {domain}",
            excerpt=f"Brand mention of {domain} surfaced on paste-site search.",
            rationale=(
                "Synthetic placeholder for an unknown subject — re-run with a "
                "controlled target or live SERP credentials for material leaks."
            ),
            via="web_unlocker",
        ),
        LeakRecord(
            channel="code",
            kind="mention",
            severity="info",
            location_url=github_url,
            location_title="GitHub code search",
            excerpt=f"Code-search results referencing `{domain}`.",
            rationale=(
                "Synthetic placeholder; not a verified leak. Use live mode to "
                "extract actual credential patterns."
            ),
            via="serp_api",
        ),
    )


# ── Result assembly ───────────────────────────────────────────────


def _build_result(*, scan: ExposureScan, mode: str) -> ModuleResult:
    sorted_records = sorted(
        scan.records,
        key=lambda r: severity_rank(r.severity),
        reverse=True,
    )
    findings = _build_findings(scan=scan, records=sorted_records)
    sources = _build_sources(records=sorted_records)

    raw_data: dict[str, Any] = {
        "subject": scan.subject,
        "domain": scan.domain,
        "mode": mode,
        "exposure_scan": scan.to_raw(),
        "max_severity": scan.max_severity,
        "critical_count": scan.critical_count,
        "channels": list(scan.channels_hit),
    }

    status = "partial" if mode == "partial" else "success"
    if not sorted_records or all(r.severity == "info" for r in sorted_records):
        status = "partial"
    return ModuleResult(
        module="exposure",
        status=status,
        findings=findings,
        sources=sources,
        raw_data=raw_data,
        confidence=_confidence_score(scan=scan, mode=mode),
    )


def _build_findings(
    *, scan: ExposureScan, records: list[LeakRecord],
) -> list[Finding]:
    if not records:
        return [Finding(
            statement=(
                f"No exposure surface detected for {scan.subject} ({scan.domain}) "
                "across paste sites, public code search, or breach aggregators."
            ),
            severity="info",
            evidence=[],
        )]

    findings: list[Finding] = []
    top = records[0]
    findings.append(Finding(
        statement=_top_statement(scan=scan, record=top),
        severity=top.severity,
        evidence=_evidence_for(top),
    ))

    # Aggregator second — ≥2 critical hits means coordinated exposure, and
    # we want it to survive the secondary-tier cap.
    critical_records = [r for r in records if r.severity == "critical"]
    if len(critical_records) >= 2:
        urls = list({r.location_url for r in critical_records})[:3]
        # Count distinct channels among the *critical* records, not the
        # whole record set — the headline aggregates criticals only.
        critical_channels = sorted({r.channel for r in critical_records})
        findings.append(Finding(
            statement=(
                f"Multiple live-shaped credential leaks for {scan.subject} "
                f"({len(critical_records)} critical hits across "
                f"{len(critical_channels)} channel(s)) — "
                "treat as coordinated exposure; rotate every cited secret."
            ),
            severity="critical",
            evidence=urls,
        ))

    for record in records[1:]:
        if record.severity == "info":
            # Skip incidental mentions in the secondary tier unless they're
            # all we have (handled above).
            continue
        findings.append(Finding(
            statement=_secondary_statement(record=record),
            severity=record.severity,
            evidence=_evidence_for(record),
        ))

    return findings[:6]


def _top_statement(*, scan: ExposureScan, record: LeakRecord) -> str:
    channel_label = _channel_label(record.channel)
    kind_phrase = _kind_phrase(record.kind)
    return (
        f"{_severity_phrase(record.severity)} {scan.subject} {kind_phrase} "
        f"on {channel_label} at {record.location_url} — {record.rationale}"
    )


def _secondary_statement(*, record: LeakRecord) -> str:
    channel_label = _channel_label(record.channel)
    kind_phrase = _kind_phrase(record.kind)
    return (
        f"Secondary {kind_phrase} on {channel_label} "
        f"({record.location_url}) — {record.rationale}"
    )


def _channel_label(channel: str) -> str:
    return {
        "paste": "paste site",
        "code": "public code search",
        "breach": "breach aggregator",
        "doxx": "personal-info surface",
    }.get(channel, channel)


def _kind_phrase(kind: str) -> str:
    return {
        "credential": "credential pair",
        "api_key": "API key / token",
        "webhook": "webhook URL",
        "pii": "PII",
        "infra": "infra detail",
        "mention": "brand mention",
    }.get(kind, kind)


def _severity_phrase(s: Severity) -> str:
    return {
        "critical": "Critical-severity",
        "high": "High-severity",
        "notable": "Notable",
        "info": "Low-severity",
    }.get(s, "Notable")


def _evidence_for(record: LeakRecord) -> list[str]:
    return [record.location_url] if record.location_url else []


def _build_sources(*, records: Iterable[LeakRecord]) -> list[Source]:
    seen: set[str] = set()
    sources: list[Source] = []
    for r in records:
        if not r.location_url or r.location_url in seen:
            continue
        seen.add(r.location_url)
        sources.append(Source(
            url=r.location_url,
            title=r.location_title or r.location_url,
            via=r.via,
        ))
    return sources


def _confidence_score(*, scan: ExposureScan, mode: str) -> float:
    score = 0.66
    if scan.critical_count >= 1:
        score += 0.08
    if scan.critical_count >= 2:
        score += 0.04
    if len(scan.channels_hit) >= 2:
        score += 0.04
    if mode == "live":
        score += 0.06
    elif mode == "partial":
        score += 0.02
    elif mode == "mock":
        score -= 0.02
    return round(max(0.4, min(0.92, score)), 2)


__all__ = ["ExposureModule"]
