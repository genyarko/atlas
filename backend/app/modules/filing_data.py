"""Filing data layer — EDGAR parsing, risk-factor extraction, diff fixtures.

Import-side-effect free and deterministic. The live path in ``filing.py``
fetches EDGAR pages via Web Unlocker and parses them through this
module; the mock path skips the network and feeds the same downstream
``FilingDiff → Finding`` pipeline pre-built fixtures, so the brief
output shape is identical regardless of whether MCP is wired up.

Design notes
------------
* The Filing module is a *supporting cast* member (per implementation
  plan day 6 note: "polish ceiling lower than first 3 modules"), so
  this layer is intentionally tighter than ``signal_data`` or
  ``trueprice_data``. It does enough to parse EDGAR's atom feeds and
  excerpt risk factors; deeper SEC-document parsing isn't worth the
  hackathon budget.
* Materiality is on the 1-5 scale from the implementation plan
  (§4.3). We map each FilingDiff's max-materiality change to a
  ``Severity`` for the Finding the synthesizer emits.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Iterable, Literal

from ..models import Severity


# ── Types ──────────────────────────────────────────────────────────


FilingType = Literal["10-K", "10-Q", "8-K"]


# Tracked filing types in priority order — 10-Q is the highest-signal
# diff target for a pre-earnings scan (the demo §8.2 query), 10-K is
# the annual full-text reset, 8-K covers material-event disclosures
# (no risk-factor section but we still surface them in the brief).
TRACKED_FILING_TYPES: tuple[FilingType, ...] = ("10-Q", "10-K", "8-K")


@dataclass(frozen=True)
class Filing:
    """One filing entry as returned from EDGAR or pre-built fixture."""

    accession_no: str        # e.g. "0001561550-26-000017"
    filing_type: FilingType  # "10-Q", "10-K", "8-K"
    filed_at: str            # ISO date string (YYYY-MM-DD)
    fiscal_period: str       # "Q1 2026", "FY 2025", or "" for 8-K
    url: str                 # document URL (EDGAR Archives)
    index_url: str           # filing index page (Web Unlocker fetches this)

    @property
    def short(self) -> str:
        """One-line citation form, e.g. '10-Q Q1 2026 (filed 2026-04-30)'."""
        if self.fiscal_period:
            return f"{self.filing_type} {self.fiscal_period} (filed {self.filed_at})"
        return f"{self.filing_type} (filed {self.filed_at})"


ChangeKind = Literal["added", "removed", "modified"]


@dataclass(frozen=True)
class RiskFactorChange:
    """A single material change between current and prior risk factors.

    Materiality is 1-5 per the implementation plan rubric:
        1 = boilerplate / pro-forma update
        2 = expanded language on existing risk
        3 = notable new risk worth flagging
        4 = high — new risk factor materially shifts the disclosure surface
        5 = critical — new risk factor implies near-term operational impact
    """

    kind: ChangeKind
    headline: str            # short title for the risk factor
    excerpt: str             # 1-3 sentence excerpt from the filing
    materiality: int         # 1-5
    rationale: str           # one-sentence "why this matters"

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "headline": self.headline,
            "excerpt": self.excerpt,
            "materiality": self.materiality,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class FilingDiff:
    """Diff between a current filing and its prior comparable filing."""

    current: Filing
    prior: Filing | None
    changes: tuple[RiskFactorChange, ...]
    summary: str = ""

    @property
    def max_materiality(self) -> int:
        return max((c.materiality for c in self.changes), default=0)

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)

    def to_raw(self) -> dict[str, object]:
        return {
            "current": _filing_to_dict(self.current),
            "prior": _filing_to_dict(self.prior) if self.prior else None,
            "changes": [c.to_dict() for c in self.changes],
            "max_materiality": self.max_materiality,
            "summary": self.summary,
        }


def _filing_to_dict(f: Filing) -> dict[str, str]:
    return {
        "accession_no": f.accession_no,
        "filing_type": f.filing_type,
        "filed_at": f.filed_at,
        "fiscal_period": f.fiscal_period,
        "url": f.url,
        "index_url": f.index_url,
    }


# ── Materiality → Severity ─────────────────────────────────────────


def materiality_to_severity(score: int) -> Severity:
    """Map the 1-5 materiality rubric onto the Atlas Finding severity scale."""
    if score >= 5:
        return "critical"
    if score >= 4:
        return "high"
    if score >= 2:
        return "notable"
    return "info"


# ── Known CIKs (avoids EDGAR ticker lookup roundtrip) ──────────────


# Padded to the 10-digit form EDGAR's JSON API uses. Keeping this map
# small and explicit: any unknown subject falls through to the mock
# fixture path, so we never burn Web Unlocker credits on guessed CIKs.
KNOWN_CIKS: dict[str, str] = {
    "Datadog": "0001561550",
    "Snowflake": "0001640147",
    "MongoDB": "0001441816",
    "CrowdStrike": "0001535527",
    "Cloudflare": "0001477333",
    "HashiCorp": "0001720671",
    "Atlassian": "0001650372",
    "GitLab": "0001653482",
    "Confluent": "0001699838",
    "Elastic": "0001707753",
}


# Fiscal year-end month (1-12) for companies on a non-calendar fiscal
# year. Calendar-year companies (December) don't need entries — they
# default to month 12 in ``_fiscal_period_label``.
#
# Keyed by CIK so the lookup is unambiguous regardless of how the
# subject string is normalized.
FISCAL_YEAR_END_MONTH: dict[str, int] = {
    "0001441816": 1,   # MongoDB — FY ends late January
    "0001720671": 1,   # HashiCorp — FY ends late January
    "0001650372": 6,   # Atlassian — FY ends June
    "0001535527": 1,   # CrowdStrike — FY ends late January
    "0001640147": 1,   # Snowflake — FY ends late January
}


def cik_for(subject: str) -> str | None:
    """Return a 10-digit CIK for known public-company subjects."""
    return KNOWN_CIKS.get(subject.strip())


def edgar_submissions_url(cik: str) -> str:
    """JSON endpoint listing a company's recent filings."""
    return f"https://data.sec.gov/submissions/CIK{cik}.json"


def edgar_filing_index_url(cik: str, accession_no: str) -> str:
    """Filing index page URL — Web Unlocker fetches this to discover documents."""
    clean = accession_no.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{clean}/"
        f"{accession_no}-index.htm"
    )


# ── EDGAR JSON parsing ─────────────────────────────────────────────


def parse_edgar_submissions(
    payload: dict[str, object], *, cik: str, limit: int = 20,
) -> list[Filing]:
    """Parse the ``recent`` block of EDGAR's submissions JSON.

    Returns Filings ordered newest-first, filtered to TRACKED_FILING_TYPES.
    Empty list on any structural mismatch — the live path treats this as
    "couldn't read the response" and falls back to the mock.
    """
    filings_root = payload.get("filings")
    if not isinstance(filings_root, dict):
        return []
    recent = filings_root.get("recent")
    if not isinstance(recent, dict):
        return []

    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accessions = recent.get("accessionNumber") or []
    primary_docs = recent.get("primaryDocument") or []
    periods = recent.get("periodOfReport") or []
    if not (
        isinstance(forms, list)
        and isinstance(dates, list)
        and isinstance(accessions, list)
        and isinstance(primary_docs, list)
    ):
        return []

    out: list[Filing] = []
    n = min(len(forms), len(dates), len(accessions), len(primary_docs))
    for i in range(n):
        form = str(forms[i]).strip()
        if form not in TRACKED_FILING_TYPES:
            continue
        accession = str(accessions[i]).strip()
        if not accession:
            continue
        period = str(periods[i]) if i < len(periods) else ""
        out.append(Filing(
            accession_no=accession,
            filing_type=form,  # type: ignore[arg-type]
            filed_at=str(dates[i]).strip(),
            fiscal_period=_fiscal_period_label(form, period, cik=cik),
            url=(
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{int(cik)}/{accession.replace('-', '')}/{primary_docs[i]}"
            ),
            index_url=edgar_filing_index_url(cik, accession),
        ))
        if len(out) >= limit:
            break
    return out


def _fiscal_period_label(form: str, period: str, *, cik: str | None = None) -> str:
    """Convert EDGAR's ``periodOfReport`` (YYYY-MM-DD) into a human label.

    Honors per-company fiscal-year-end months (MongoDB, HashiCorp,
    Atlassian, etc. don't end their FY in December), looked up via CIK
    in ``FISCAL_YEAR_END_MONTH``. Subjects without an entry default to
    calendar-year (December).
    """
    if not period or len(period) < 7:
        return ""
    try:
        year = int(period[:4])
        month = int(period[5:7])
    except ValueError:
        return ""
    fy_end_month = FISCAL_YEAR_END_MONTH.get(cik or "", 12)
    # FY label: when the period sits past the fiscal year-end month,
    # we're in the *next* fiscal year. e.g. Atlassian FY ends June, so
    # period 2026-09-30 belongs to FY 2027.
    fy_year = year + 1 if month > fy_end_month else year
    if form == "10-K":
        return f"FY {fy_year}"
    if form == "10-Q":
        # Quarter index relative to fiscal-year start (month after FY end).
        months_in = ((month - (fy_end_month + 1)) % 12) + 1
        quarter = (months_in - 1) // 3 + 1
        return f"Q{quarter} {fy_year}"
    return ""


def pick_diff_pair(
    filings: Iterable[Filing], *, filing_type: FilingType,
) -> tuple[Filing, Filing | None] | None:
    """Pick the most recent filing of ``filing_type`` and its prior comparable.

    Returns ``(current, prior)`` where ``prior`` may be None when only
    one filing of that type appears in the recent window. Returns None
    when nothing of that type is present at all.
    """
    matching = [f for f in filings if f.filing_type == filing_type]
    if not matching:
        return None
    matching.sort(key=lambda f: f.filed_at, reverse=True)
    if len(matching) == 1:
        return matching[0], None
    return matching[0], matching[1]


# ── Risk-factor extraction from a filing HTML body ─────────────────


# "Item 1A" (risk factors) → "Item 1B" / "Item 2". Permissive: SEC
# filings vary wildly in formatting. We slice raw text between markers
# and hand the slice to the LLM — perfect parsing isn't necessary
# because the diff prompt is the actual judge.
_ITEM_1A_RE = re.compile(
    r"item\s*1a[.\s]*risk\s+factors", re.IGNORECASE
)
_ITEM_1B_RE = re.compile(
    r"item\s*(?:1b|2)[.\s]+", re.IGNORECASE
)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(body: str) -> str:
    text = _TAG_RE.sub(" ", body)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def extract_risk_factors(body: str, *, max_chars: int = 18_000) -> str:
    """Return the Item 1A "Risk Factors" section as flattened plain text.

    Slices between "Item 1A" and the next item header. Falls back to
    the first ``max_chars`` of text if no markers are found (rare but
    possible on filings that embed risk factors as exhibits).
    """
    text = _strip_html(body)
    match = _ITEM_1A_RE.search(text)
    if not match:
        return text[:max_chars]
    start = match.end()
    end_match = _ITEM_1B_RE.search(text, start)
    end = end_match.start() if end_match else min(start + max_chars, len(text))
    section = text[start:end].strip()
    return section[:max_chars]


# ── Fixtures (mock-mode FilingDiffs) ───────────────────────────────


# Datadog fixture — the marquee demo target for §8.2 (pre-earnings
# scan). Mirrors the existing mock fixture from the foundation stub
# but expands it into the structured FilingDiff shape so the renderer
# can lay out specifics. Numbers and language are illustrative, not
# real EDGAR content — for a real demo you'd swap in scouted text.
_DATADOG_CURRENT_10Q = Filing(
    accession_no="0001561550-26-000017",
    filing_type="10-Q",
    filed_at="2026-04-30",
    fiscal_period="Q1 2026",
    url=(
        "https://www.sec.gov/Archives/edgar/data/1561550/"
        "000156155026000017/ddog-20260331.htm"
    ),
    index_url=edgar_filing_index_url("0001561550", "0001561550-26-000017"),
)


_DATADOG_PRIOR_10Q = Filing(
    accession_no="0001561550-26-000003",
    filing_type="10-Q",
    filed_at="2026-02-13",
    fiscal_period="Q4 2025",
    url=(
        "https://www.sec.gov/Archives/edgar/data/1561550/"
        "000156155026000003/ddog-20251231.htm"
    ),
    index_url=edgar_filing_index_url("0001561550", "0001561550-26-000003"),
)


_DATADOG_FILING_DIFF = FilingDiff(
    current=_DATADOG_CURRENT_10Q,
    prior=_DATADOG_PRIOR_10Q,
    changes=(
        RiskFactorChange(
            kind="added",
            headline="Customer concentration in AI-driven workloads",
            excerpt=(
                "A growing portion of our revenue is concentrated in customers "
                "deploying observability for AI-driven workloads. A pullback in "
                "generative-AI infrastructure spend, or migration of these "
                "workloads to in-house observability stacks, could materially "
                "reduce growth in this segment."
            ),
            materiality=4,
            rationale=(
                "Net-new risk factor — not present in the prior 10-Q. Signals "
                "exposure to the AI-infrastructure capex cycle that the news has "
                "not connected to Datadog yet."
            ),
        ),
        RiskFactorChange(
            kind="modified",
            headline="Executive compensation — PSU vesting tied to FCF margin",
            excerpt=(
                "Performance Share Units granted to executive officers in Q1 "
                "include accelerated vesting tranches tied to non-GAAP free cash "
                "flow margin reaching ≥31% over the trailing four quarters."
            ),
            materiality=3,
            rationale=(
                "First time FCF margin is named in PSU triggers. Implies a board "
                "shift toward near-term efficiency over reinvestment."
            ),
        ),
    ),
    summary=(
        "Q1 2026 10-Q adds one materially new risk factor (AI-workload "
        "concentration) and modifies executive PSU vesting to tie to FCF "
        "margin — both consistent with a board-level efficiency pivot."
    ),
)


_LINEAR_PLACEHOLDER_CURRENT = Filing(
    accession_no="",
    filing_type="10-Q",
    filed_at="",
    fiscal_period="",
    url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=Linear",
    index_url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=Linear",
)


_LINEAR_FILING_DIFF = FilingDiff(
    current=_LINEAR_PLACEHOLDER_CURRENT,
    prior=None,
    changes=(),
    summary=(
        "Linear is privately held — no SEC filings on file. Module skipped "
        "for this target."
    ),
)


_FIXTURES: dict[str, FilingDiff] = {
    "Datadog": _DATADOG_FILING_DIFF,
    "Linear": _LINEAR_FILING_DIFF,
}


def fixture_diff_for(subject: str) -> FilingDiff:
    """Return a deterministic FilingDiff for the mock path.

    Anything not pre-seeded gets a "no material change" filler so the
    brief still renders. Public companies without seed data fall into
    this generic bucket; the brief reflects the gap honestly.
    """
    diff = _FIXTURES.get(subject)
    if diff is not None:
        return diff
    is_public = subject in KNOWN_CIKS
    if is_public:
        # Public company we know about but didn't scout — emit a placeholder
        # noting we don't have a scouted diff on file.
        placeholder = Filing(
            accession_no="",
            filing_type="10-Q",
            filed_at="",
            fiscal_period="",
            url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={KNOWN_CIKS[subject]}",
            index_url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={KNOWN_CIKS[subject]}",
        )
        return FilingDiff(
            current=placeholder, prior=None, changes=(),
            summary=(
                f"{subject} is on file with EDGAR but no scouted diff is "
                "available; live mode required for material changes."
            ),
        )
    # Truly unknown subject — likely private. Surface that fact.
    return FilingDiff(
        current=Filing(
            accession_no="", filing_type="10-Q", filed_at="",
            fiscal_period="",
            url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={subject}",
            index_url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={subject}",
        ),
        prior=None,
        changes=(),
        summary=(
            f"{subject} does not appear on the SEC EDGAR roster — likely "
            "privately held or pre-IPO."
        ),
    )


__all__ = [
    "Filing",
    "FilingDiff",
    "FilingType",
    "RiskFactorChange",
    "ChangeKind",
    "TRACKED_FILING_TYPES",
    "KNOWN_CIKS",
    "cik_for",
    "edgar_submissions_url",
    "edgar_filing_index_url",
    "parse_edgar_submissions",
    "pick_diff_pair",
    "extract_risk_factors",
    "materiality_to_severity",
    "fixture_diff_for",
]
