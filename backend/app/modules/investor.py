"""Investor — active VC firms and partners investing in a target sector.

Pipeline (same shape as Signal):

    raw rows  ──►  normalize ──►  cluster ──►  synthesize ──►  Findings
                  (VCFirmSignal)  (InvestorCluster)  (LLM or rules)

The Investor module's ``subject`` is a *sector* (e.g. "edtech"), not a
company. The live path runs three parallel SERP queries:

1. site:linkedin.com/company to discover VC firm pages for the sector
2. site:linkedin.com/in to discover partner / principal profiles
3. General news SERP for fund-close / portfolio signals

SERP results are parsed directly — no LinkedIn dataset subscription
required. If a dataset is later activated, web_scraper_api exposes
fetch_linkedin_company_profile() and fetch_linkedin_person() as
optional per-URL enrichment helpers.

Mock mode swaps in pre-shaped fixtures. Both paths run the same
clustering and synthesis pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from ..brightdata import record_simulated, serp
from ..models import Finding, ModuleResult, Severity, Source
from .base import IntelligenceModule
from .investor_data import (
    InvestorCluster,
    VCFirmSignal,
    cluster,
    fixture_for,
    infer_sector,
    news_fixture_for,
    normalize,
)

log = logging.getLogger(__name__)

# LinkedIn SERP title suffix to strip
_LI_SUFFIX = re.compile(r"\s*\|\s*LinkedIn\s*$", re.I)
# Split on " - " or " – " (em dash) to decompose person titles
_TITLE_SEP = re.compile(r"\s*[-–]\s*")


class InvestorModule(IntelligenceModule):
    name = "investor"
    live_ready = True

    async def execute(self, params: dict[str, Any]) -> ModuleResult:
        sector = _resolve_sector(params)

        # Run all three SERP queries in parallel
        results = await asyncio.gather(
            serp.search(
                f'site:linkedin.com/company "venture capital" "{sector}"',
                num=12,
            ),
            serp.search(
                f'site:linkedin.com/in "venture capital" "{sector}" partner OR principal',
                num=15,
            ),
            serp.search(
                f'"{sector}" venture capital fund OR portfolio',
                num=8,
            ),
            return_exceptions=True,
        )

        firm_serp  = results[0] if isinstance(results[0], list) else []
        people_serp = results[1] if isinstance(results[1], list) else []
        news_serp  = results[2] if isinstance(results[2], list) else []

        company_rows = _parse_firms_from_serp(firm_serp, sector=sector)
        people_rows  = _parse_people_from_serp(people_serp)
        news         = _normalize_news(news_serp)

        if not company_rows:
            log.info("Investor live: no VC firms from SERP for %r; mock fallback", sector)
            return await self.mock(params)

        firms = normalize(company_rows, people_rows, sector=sector)
        if len(firms) < 2:
            log.info("Investor live: only %d firms parsed; mock fallback", len(firms))
            return await self.mock(params)

        return await _build_result(sector=sector, firms=firms, news=news, mode="live")

    async def mock(self, params: dict[str, Any]) -> ModuleResult:
        sector = _resolve_sector(params)
        await _emit_simulated_trace(sector)
        firms = normalize(fixture_for(sector), sector=sector)
        news = news_fixture_for(sector)
        return await _build_result(sector=sector, firms=firms, news=news, mode="mock")


def _resolve_sector(params: dict[str, Any]) -> str:
    """Pull a sector from params.

    The planner sets ``subject`` from a generic brand extractor, so for a
    query like "find VCs in edtech" it returns ``"the target"``. We
    prefer an explicit ``sector`` param, then re-infer from ``query``,
    then fall back to ``subject`` only if it isn't the generic sentinel.
    """
    if isinstance(params.get("sector"), str) and params["sector"].strip():
        return params["sector"].strip().lower()
    inferred = infer_sector(params.get("query", "") or "")
    if inferred != "edtech" or "edtech" in (params.get("query", "") or "").lower():
        return inferred
    subject = params.get("subject")
    if isinstance(subject, str) and subject.strip() and subject.lower() != "the target":
        # Use the brand subject as a sector only when no sector hint is found.
        return subject.strip().lower()
    return inferred  # default "edtech" — matches fixture coverage


async def _emit_simulated_trace(sector: str) -> None:
    """Record the SERP calls the live path would make.

    Surfaced in the UI's infrastructure rail with a 'simulated' badge."""
    await record_simulated(
        tool="search_engine",
        args={"query": f'site:linkedin.com/company "venture capital" "{sector}"',
              "num_results": 12},
    )
    await record_simulated(
        tool="search_engine",
        args={"query": f'site:linkedin.com/in "venture capital" "{sector}" partner OR principal',
              "num_results": 15},
    )
    await record_simulated(
        tool="search_engine",
        args={"query": f'"{sector}" venture capital fund OR portfolio',
              "num_results": 8},
    )


# ── SERP parsing helpers ─────────────────────────────────────────────


def _parse_firms_from_serp(
    rows: list[dict[str, Any]], *, sector: str
) -> list[dict[str, Any]]:
    """Convert site:linkedin.com/company SERP results into firm dicts
    that ``normalize()`` can consume."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = (row.get("link") or row.get("url") or "").strip()
        if not url or "linkedin.com/company" not in url:
            continue
        # Normalise: drop query params, ensure trailing slash
        base_url = url.split("?")[0].rstrip("/") + "/"
        if base_url in seen:
            continue
        seen.add(base_url)
        title = row.get("title") or ""
        snippet = row.get("snippet") or row.get("description") or ""
        firm_name = _LI_SUFFIX.sub("", title).strip()
        if not firm_name:
            continue
        out.append({
            "firm_name": firm_name,
            "linkedin_url": base_url,
            "hq_country": "",
            "stage_focus": [],
            "focus_sectors": [sector],
            "recent_signal": snippet[:200] if snippet else "",
            "signal_url": base_url,
            "portfolio_callouts": [],
        })
    return out


def _parse_people_from_serp(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert site:linkedin.com/in SERP results into people dicts.

    LinkedIn person SERP titles follow a few patterns:
    - "Name - Title at Company | LinkedIn"
    - "Name - Title - Company | LinkedIn"
    We parse name, title, and employer from whichever pattern applies.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = (row.get("link") or row.get("url") or "").strip()
        if not url or "linkedin.com/in" not in url:
            continue
        base_url = url.split("?")[0].rstrip("/")
        if base_url in seen:
            continue
        seen.add(base_url)
        title = row.get("title") or ""
        clean = _LI_SUFFIX.sub("", title).strip()
        # Split "Name - Title at Company" into parts
        parts = [p.strip() for p in _TITLE_SEP.split(clean, maxsplit=2)]
        name = parts[0] if parts else ""
        if not name:
            continue
        headline = parts[1] if len(parts) > 1 else ""
        # "Title at Company" → extract company
        company = ""
        if " at " in headline:
            company = headline.split(" at ", 1)[-1].strip()
        elif len(parts) > 2:
            company = parts[-1]
        out.append({
            "name": name,
            "headline": headline,
            "current_company": company,
            "profile_url": base_url,
            "location": "",
        })
    return out


# ── Result assembly ─────────────────────────────────────────────────


async def _build_result(
    *,
    sector: str,
    firms: list[VCFirmSignal],
    news: list[dict[str, str]],
    mode: str,
) -> ModuleResult:
    summary = cluster(firms, sector)
    findings = await _synthesize_findings(sector, summary, news)
    findings = _ensure_min_findings(sector, summary, news, findings)

    sources = _build_sources(sector, summary, news)

    raw_data: dict[str, Any] = {
        "sector": sector,
        "total_firms": summary.total_firms,
        "by_stage": summary.by_stage,
        "by_country": summary.by_country,
        "partner_count": summary.partner_count,
        "active_signals_count": summary.active_signals_count,
        "top_firms": summary.top_firms,
        "top_partners": summary.top_partners,
        "news_items": news[:5],
        "mode": mode,
    }
    confidence = _confidence_score(summary, news)
    return ModuleResult(
        module="investor",
        findings=findings,
        sources=sources,
        raw_data=raw_data,
        confidence=confidence,
    )


def _build_sources(
    sector: str,
    summary: InvestorCluster,
    news: list[dict[str, str]],
) -> list[Source]:
    """Compose the source list from URLs that were actually queried.

    Each top firm contributes its LinkedIn company URL + its signal URL
    (if present). News URLs come from the SERP triangulation."""
    seen: set[str] = set()
    sources: list[Source] = []

    def _add(url: str, title: str, via: str) -> None:
        if not url or url in seen:
            return
        seen.add(url)
        sources.append(Source(url=url, title=title, via=via))

    for firm in summary.top_firms[:5]:
        _add(firm["linkedin_url"], f"{firm['firm_name']} — LinkedIn", "web_scraper_api")
        signal_url = firm.get("signal_url") or ""
        if signal_url:
            _add(signal_url, f"{firm['firm_name']} — recent activity", "web_scraper_api")

    for item in news[:3]:
        url = item.get("url", "")
        if url:
            _add(url, item.get("title", f"{sector} VC news"), "serp_api")

    return sources


def _confidence_score(summary: InvestorCluster, news: list[dict[str, str]]) -> float:
    score = 0.55
    if summary.total_firms >= 4:
        score += 0.1
    if summary.total_firms >= 8:
        score += 0.05
    if summary.active_signals_count >= 3:
        score += 0.07
    if summary.partner_count >= 6:
        score += 0.05
    if news:
        score += 0.05
    return round(min(score, 0.92), 2)


# ── Synthesis: LLM with deterministic fallback ──────────────────────


_SYNTH_SYSTEM = """You are the Atlas Investor synthesizer.

Given a clustered summary of VC firms active in a sector (and \
optionally a few news headlines), output STRICT JSON inferring the \
investor landscape. Do NOT restate raw counts — infer the "so what".

Output schema (no markdown fences, no commentary):
{
  "findings": [
    {
      "statement": "<one sharp sentence, institutional-analyst tone>",
      "severity": "info" | "notable" | "high" | "critical",
      "evidence_urls": ["<firm/news url that supports this claim>", ...]
    }
  ]
}

Rules:
- Produce 3-5 findings, ordered by strategic importance.
- Each finding must cite at least one URL drawn from the input.
- Severities: "critical" for landscape-defining events (mega-fund \
close, named decision-maker movements), "high" for clear directional \
signals, "notable" for incremental but worth-knowing, "info" for context.
- Reason from cluster shape (which stages, which geos, partner density, \
recent signals concentration) — do not invent facts not implied by input.
- A founder reading this should know: who to talk to first, why, and \
what stage-specific motion to use.
"""


async def _synthesize_findings(
    sector: str,
    summary: InvestorCluster,
    news: list[dict[str, str]],
) -> list[Finding]:
    """Try the LLM first; on any failure, return [] and let the rule-based
    fallback take over."""
    from ..agent.llm import get_llm

    llm = get_llm()
    if llm is None:
        return []

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
    except ImportError:  # pragma: no cover
        return []

    prompt = {
        "sector": sector,
        "cluster_summary": summary.to_prompt_dict(),
        "news_headlines": news[:5],
    }
    try:
        response = await llm.ainvoke([
            SystemMessage(content=_SYNTH_SYSTEM),
            HumanMessage(content=json.dumps(prompt, indent=2)),
        ])
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("Investor LLM synthesis failed (%s); using rules", exc)
        return []
    text = response.content if isinstance(response.content, str) else str(response.content)
    return _parse_llm_findings(text)


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _parse_llm_findings(raw: str) -> list[Finding]:
    cleaned = _FENCE_RE.sub("", raw.strip()).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        log.warning("Investor LLM returned non-JSON; using rules")
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
    sector: str,
    summary: InvestorCluster,
    news: list[dict[str, str]],
    findings: list[Finding],
) -> list[Finding]:
    """Guarantee ≥3 findings with source URLs even when no LLM is configured."""
    if len(findings) >= 3 and all(f.evidence for f in findings):
        return findings

    rule_based = _rule_based_findings(sector, summary, news)
    seen = {f.statement.lower() for f in findings}
    merged = list(findings)
    for f in rule_based:
        if f.statement.lower() in seen:
            continue
        merged.append(f)
        seen.add(f.statement.lower())
        if len(merged) >= 5:
            break
    merged = [f for f in merged if f.evidence]
    return merged[:5] if len(merged) >= 3 else rule_based[:5]


def _rule_based_findings(
    sector: str,
    summary: InvestorCluster,
    news: list[dict[str, str]],
) -> list[Finding]:
    """Compute findings without an LLM. Mirrors what an analyst would
    write up given the same firm/partner/news table."""
    findings: list[Finding] = []

    # 1. Lead firm — highest-signal firm by recent activity + partner depth.
    if summary.top_firms:
        lead = summary.top_firms[0]
        lead_url = lead.get("signal_url") or lead.get("linkedin_url", "")
        signal_clause = (
            f" — {lead['recent_signal']}" if lead.get("recent_signal") else ""
        )
        portfolio_clause = (
            f"; portfolio includes {', '.join(lead['portfolio_callouts'][:3])}"
            if lead.get("portfolio_callouts") else ""
        )
        sev: Severity = "critical" if lead.get("recent_signal") and lead.get("portfolio_callouts") else "high"
        findings.append(Finding(
            statement=(
                f"{lead['firm_name']} is the strongest current entry point into "
                f"{sector}{signal_clause}{portfolio_clause}."
            ),
            severity=sev,
            evidence=[u for u in [lead_url] if u],
        ))

    # 2. Stage concentration — which stage the sector is actually getting funded at.
    if summary.by_stage:
        ordered_stages = sorted(summary.by_stage.items(), key=lambda kv: kv[1], reverse=True)
        top_stage, top_count = ordered_stages[0]
        stage_total = sum(summary.by_stage.values())
        share = top_count / stage_total if stage_total else 0
        # Use top firm's URL as evidence for this aggregate finding.
        evidence_url = (
            summary.top_firms[0]["linkedin_url"] if summary.top_firms else ""
        )
        if share >= 0.4 and top_count >= 3:
            findings.append(Finding(
                statement=(
                    f"{sector} capital is concentrated at {top_stage} stage "
                    f"({top_count} of the active firms list this stage explicitly) — "
                    "a founder pitching outside this stage will need to either "
                    "look beyond the top sector-active firms or reframe their round."
                ),
                severity="high",
                evidence=[evidence_url] if evidence_url else [],
            ))

    # 3. Geo signal — non-US presence as a directional indicator.
    if summary.by_country:
        non_us_count = sum(c for k, c in summary.by_country.items() if k not in ("USA", "Other"))
        total = sum(summary.by_country.values())
        if total >= 3 and non_us_count >= 2:
            non_us_geos = [k for k in summary.by_country if k not in ("USA", "Other")]
            geo_url = ""
            for firm in summary.top_firms:
                if firm.get("hq_country") not in ("USA", "Other"):
                    geo_url = firm.get("linkedin_url", "")
                    break
            findings.append(Finding(
                statement=(
                    f"International coverage is real, not token: "
                    f"{non_us_count} of {total} active firms are based outside the US "
                    f"({', '.join(non_us_geos)}) — viable path for non-US founders."
                ),
                severity="notable",
                evidence=[geo_url] if geo_url else [],
            ))

    # 4. Recent activity density — how alive the sector is for fundraising.
    if summary.active_signals_count >= 3:
        # Find a firm with a non-empty signal URL to cite.
        signal_url = next(
            (f.get("signal_url", "") for f in summary.top_firms if f.get("signal_url")),
            "",
        )
        findings.append(Finding(
            statement=(
                f"{summary.active_signals_count} of {summary.total_firms} sector-active "
                f"firms have made a recent public move (fund close, lead investment, "
                f"or named portfolio expansion) — the window for warm intros is open now."
            ),
            severity="high",
            evidence=[signal_url] if signal_url else [],
        ))

    # 5. Decision-maker reachability — partner density and named contacts.
    if summary.partner_count >= 5 and summary.top_partners:
        partner = summary.top_partners[0]
        findings.append(Finding(
            statement=(
                f"{summary.partner_count} reachable partners surfaced across the firm list — "
                f"start with {partner['title']} at {partner['firm_name']} for the "
                "highest-signal first conversation."
            ),
            severity="notable",
            evidence=[partner["profile_url"]] if partner.get("profile_url") else [],
        ))

    # Fallback: at least name the sector and its firm count.
    if not findings and summary.top_firms:
        findings.append(Finding(
            statement=(
                f"{summary.total_firms} VC firm(s) currently active in {sector} — "
                f"led by {summary.top_firms[0]['firm_name']}."
            ),
            severity="notable",
            evidence=[summary.top_firms[0].get("linkedin_url", "")],
        ))

    return findings


# ── Small helpers ────────────────────────────────────────────────────


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
