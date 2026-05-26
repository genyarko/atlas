"""Investor data layer — VC firm normalization, clustering, and demo fixtures.

The Investor module's ``subject`` parameter is a *sector* (e.g. "edtech",
"fintech", "climate tech"), not a company. The live path feeds raw rows
from the Bright Data LinkedIn Companies + People datasets through
``normalize`` → ``cluster``; the mock path swaps in fixtures with the
same shape. Both paths exercise the same synthesis pipeline so the
mock brief is structurally identical to a live one.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


# ── Sector inference ─────────────────────────────────────────────────
#
# The planner's generic ``infer_subject`` looks for capitalized brand
# tokens, which fails for lowercase sector terms like "edtech". This
# helper owns sector inference for the Investor module.

_SECTOR_PATTERNS: list[tuple[str, list[str]]] = [
    ("edtech",       ["edtech", "ed-tech", "ed tech", "education tech", "learning tech"]),
    ("fintech",      ["fintech", "fin-tech", "financial tech", "neobank"]),
    ("climate tech", ["climate tech", "climatetech", "cleantech", "clean tech",
                      "climate", "decarbonization", "carbon capture"]),
    ("healthtech",   ["healthtech", "health tech", "digital health", "medtech"]),
    ("ai",           ["ai/ml", "artificial intelligence", "machine learning",
                      " ai ", "genai", "generative ai", "foundation model"]),
    ("dev tools",    ["dev tools", "developer tools", "devtools", "devops"]),
    ("security",     ["cybersecurity", "infosec", "appsec", "cloud security"]),
    ("biotech",      ["biotech", "bio tech", "life sciences", "pharma"]),
]


def infer_sector(query: str, *, fallback: str = "edtech") -> str:
    q = f" {query.lower()} "
    for canonical, hints in _SECTOR_PATTERNS:
        if any(h in q for h in hints):
            return canonical
    return fallback


# ── Stage normalization ──────────────────────────────────────────────

_STAGE_TOKENS: dict[str, list[str]] = {
    "seed":     ["seed", "pre-seed", "preseed", "angel"],
    "series-a": ["series a", "series-a", "early stage", "early-stage"],
    "series-b": ["series b", "series-b"],
    "series-c": ["series c", "series-c"],
    "growth":   ["growth", "late stage", "late-stage", "series d", "series e",
                 "pre-ipo", "crossover"],
}


def normalize_stage(text: str) -> list[str]:
    """Extract stage labels from a firm's stage_focus / about text."""
    t = text.lower() if text else ""
    out: list[str] = []
    for canonical, tokens in _STAGE_TOKENS.items():
        if any(tok in t for tok in tokens):
            out.append(canonical)
    return out


# ── Geo normalization ────────────────────────────────────────────────

_GEO_HUBS: dict[str, list[str]] = {
    "USA":  ["united states", "usa", "u.s.", "u.s", "san francisco", "new york",
             "boston", "palo alto", "menlo park", "ny,", "ca,", "ma,"],
    "UK":   ["united kingdom", "london", "uk,", " uk "],
    "EU":   ["france", "paris", "berlin", "germany", "amsterdam", "stockholm",
             "ireland", "dublin", "munich"],
    "APAC": ["singapore", "tokyo", "japan", "hong kong", "sydney", "india",
             "bangalore", "shanghai", "beijing"],
    "MENA": ["dubai", "uae", "riyadh", "saudi"],
    "LATAM": ["são paulo", "sao paulo", "brazil", "mexico", "buenos aires"],
}


def normalize_country(text: str) -> str:
    t = f" {text.lower()} " if text else " "
    for canonical, hints in _GEO_HUBS.items():
        if any(h in t for h in hints):
            return canonical
    return "Other"


# ── Dataclasses ──────────────────────────────────────────────────────


@dataclass
class Partner:
    """A single decision-maker at a VC firm."""
    title: str
    profile_url: str
    name: str = ""  # populated from live dataset; blank in fixtures
    location: str = ""


@dataclass
class VCFirmSignal:
    firm_name: str
    linkedin_url: str
    hq_country: str
    stage_focus: list[str]
    focus_sectors: list[str]
    partners: list[Partner]
    recent_signal: str           # one-line description of the most recent activity
    signal_url: str              # press release / changelog / news URL backing the signal
    portfolio_callouts: list[str]  # named portfolio companies in this sector


@dataclass
class InvestorCluster:
    sector: str
    total_firms: int
    by_stage: dict[str, int]
    by_country: dict[str, int]
    partner_count: int
    active_signals_count: int    # firms with a recent_signal
    top_firms: list[dict[str, Any]]
    top_partners: list[dict[str, str]]

    def to_prompt_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Normalization: raw rows → VCFirmSignal ───────────────────────────


def _coerce_partners(raw: Any) -> list[Partner]:
    if not isinstance(raw, list):
        return []
    out: list[Partner] = []
    for p in raw:
        if not isinstance(p, dict):
            continue
        out.append(Partner(
            title=str(p.get("title") or p.get("headline") or "").strip(),
            profile_url=str(p.get("profile_url") or p.get("url") or "").strip(),
            name=str(p.get("name") or "").strip(),
            location=str(p.get("location") or "").strip(),
        ))
    return out


def normalize(
    company_rows: Iterable[dict[str, Any]],
    people_rows: Iterable[dict[str, Any]] | None = None,
    *,
    sector: str,
) -> list[VCFirmSignal]:
    """Convert raw dataset rows to ``VCFirmSignal`` instances.

    ``company_rows`` come from the LinkedIn Companies dataset (or fixture).
    ``people_rows``, if provided, come from the LinkedIn People dataset
    and are grouped by ``current_company`` and attached to the matching
    firm record. People without a matching firm are dropped — the
    Investor module is firm-centric.
    """
    firms: list[VCFirmSignal] = []
    firm_by_name: dict[str, VCFirmSignal] = {}

    for row in company_rows:
        if not isinstance(row, dict):
            continue
        name = (
            row.get("firm_name")
            or row.get("name")
            or row.get("company_name")
            or ""
        ).strip()
        if not name:
            continue
        linkedin_url = (
            row.get("linkedin_url")
            or row.get("url")
            or f"https://www.linkedin.com/company/{name.lower().replace(' ', '-')}/"
        )
        stages_raw = row.get("stage_focus") or row.get("about") or ""
        if isinstance(stages_raw, list):
            stages = [str(s).lower() for s in stages_raw]
        else:
            stages = normalize_stage(str(stages_raw))
        focus = row.get("focus_sectors") or [sector]
        if isinstance(focus, str):
            focus = [focus]
        firm = VCFirmSignal(
            firm_name=name,
            linkedin_url=linkedin_url,
            hq_country=normalize_country(
                str(row.get("hq_country") or row.get("headquarters") or row.get("location") or "")
            ),
            stage_focus=stages,
            focus_sectors=[str(f) for f in focus],
            partners=_coerce_partners(row.get("partners")),
            recent_signal=str(row.get("recent_signal") or "").strip(),
            signal_url=str(row.get("signal_url") or "").strip(),
            portfolio_callouts=[
                str(c) for c in (row.get("portfolio_callouts") or [])
            ],
        )
        firms.append(firm)
        firm_by_name[name.lower()] = firm

    # Attach people to firms (live-mode enrichment).
    if people_rows:
        for prow in people_rows:
            if not isinstance(prow, dict):
                continue
            current = (
                prow.get("current_company")
                or prow.get("company")
                or ""
            )
            firm = firm_by_name.get(str(current).lower())
            if firm is None:
                continue
            firm.partners.append(Partner(
                title=str(prow.get("headline") or prow.get("title") or "").strip(),
                profile_url=str(prow.get("profile_url") or prow.get("url") or "").strip(),
                name=str(prow.get("name") or "").strip(),
                location=str(prow.get("location") or "").strip(),
            ))

    return firms


# ── Clustering: VCFirmSignal list → InvestorCluster ──────────────────


def cluster(firms: list[VCFirmSignal], sector: str) -> InvestorCluster:
    by_stage: Counter[str] = Counter()
    for f in firms:
        for s in f.stage_focus:
            by_stage[s] += 1
    by_country = Counter(f.hq_country for f in firms)
    partner_count = sum(len(f.partners) for f in firms)
    active_signals = sum(1 for f in firms if f.recent_signal)

    # Top firms: prefer ones with a recent signal, then ones with named
    # portfolio callouts, then ones with the most partners.
    def firm_score(f: VCFirmSignal) -> tuple[int, int, int]:
        return (
            1 if f.recent_signal else 0,
            len(f.portfolio_callouts),
            len(f.partners),
        )

    ordered = sorted(firms, key=firm_score, reverse=True)
    top_firms = [
        {
            "firm_name": f.firm_name,
            "linkedin_url": f.linkedin_url,
            "hq_country": f.hq_country,
            "stage_focus": f.stage_focus,
            "recent_signal": f.recent_signal,
            "signal_url": f.signal_url,
            "portfolio_callouts": f.portfolio_callouts,
            "partner_count": len(f.partners),
        }
        for f in ordered[:6]
    ]

    # Top partners: at most one per firm, prefer those with a profile URL.
    top_partners: list[dict[str, str]] = []
    for f in ordered:
        for p in f.partners:
            if not p.profile_url:
                continue
            top_partners.append({
                "name": p.name,
                "title": p.title,
                "profile_url": p.profile_url,
                "firm_name": f.firm_name,
            })
            break
        if len(top_partners) >= 8:
            break

    return InvestorCluster(
        sector=sector,
        total_firms=len(firms),
        by_stage=dict(by_stage),
        by_country=dict(by_country),
        partner_count=partner_count,
        active_signals_count=active_signals,
        top_firms=top_firms,
        top_partners=top_partners,
    )


# ── Fixtures ─────────────────────────────────────────────────────────
#
# Pre-shaped fixtures for each demo sector. Firm names are real public
# entities (well-known VC firms); partner names are intentionally blank
# in fixtures so the brief shows the "live LinkedIn dataset returns
# real partner records" affordance without fabricating identities.

_EDTECH_FIXTURE: list[dict[str, Any]] = [
    {
        "firm_name": "Reach Capital",
        "linkedin_url": "https://www.linkedin.com/company/reach-capital/",
        "hq_country": "San Francisco, CA",
        "stage_focus": ["seed", "series-a"],
        "focus_sectors": ["edtech", "future of work"],
        "partners": [
            {"title": "Founding Partner",
             "profile_url": "https://www.linkedin.com/search/results/people/?currentCompany=reach-capital"},
            {"title": "Partner",
             "profile_url": "https://www.linkedin.com/search/results/people/?currentCompany=reach-capital&keywords=partner"},
        ],
        "recent_signal": "Closed Fund V at $215M targeting early-stage edtech",
        "signal_url": "https://www.reachcapital.com/news/fund-v-close",
        "portfolio_callouts": ["Outschool", "Newsela", "Handshake"],
    },
    {
        "firm_name": "GSV Ventures",
        "linkedin_url": "https://www.linkedin.com/company/gsv-ventures/",
        "hq_country": "Menlo Park, CA",
        "stage_focus": ["series-a", "series-b", "growth"],
        "focus_sectors": ["edtech"],
        "partners": [
            {"title": "Managing Partner",
             "profile_url": "https://www.linkedin.com/search/results/people/?currentCompany=gsv-ventures"},
        ],
        "recent_signal": "Led $40M Series B in workforce-skilling platform Guild",
        "signal_url": "https://www.gsv.com/news/guild-series-b",
        "portfolio_callouts": ["Guild", "Coursera", "Class Technologies"],
    },
    {
        "firm_name": "Owl Ventures",
        "linkedin_url": "https://www.linkedin.com/company/owl-ventures/",
        "hq_country": "San Francisco, CA",
        "stage_focus": ["series-a", "series-b", "growth"],
        "focus_sectors": ["edtech"],
        "partners": [
            {"title": "Co-founder & Managing Director",
             "profile_url": "https://www.linkedin.com/search/results/people/?currentCompany=owl-ventures"},
            {"title": "Partner",
             "profile_url": "https://www.linkedin.com/search/results/people/?currentCompany=owl-ventures&keywords=partner"},
        ],
        "recent_signal": "Announced $1B Fund VI — the largest dedicated edtech fund to date",
        "signal_url": "https://www.owlvc.com/news/fund-vi",
        "portfolio_callouts": ["Byju's", "MasterClass", "Quizlet"],
    },
    {
        "firm_name": "Brighteye Ventures",
        "linkedin_url": "https://www.linkedin.com/company/brighteye-ventures/",
        "hq_country": "Paris, France",
        "stage_focus": ["seed", "series-a"],
        "focus_sectors": ["edtech"],
        "partners": [
            {"title": "General Partner",
             "profile_url": "https://www.linkedin.com/search/results/people/?currentCompany=brighteye-ventures"},
        ],
        "recent_signal": "EU edtech round — Series A in language-learning platform Lingumi",
        "signal_url": "https://www.brighteyevc.com/news/lingumi-series-a",
        "portfolio_callouts": ["Lingumi", "Aula", "GoStudent"],
    },
    {
        "firm_name": "New Markets Venture Partners",
        "linkedin_url": "https://www.linkedin.com/company/new-markets-venture-partners/",
        "hq_country": "Bethesda, MD",
        "stage_focus": ["series-a", "series-b"],
        "focus_sectors": ["edtech", "workforce"],
        "partners": [
            {"title": "Managing Partner",
             "profile_url": "https://www.linkedin.com/search/results/people/?currentCompany=new-markets-venture-partners"},
        ],
        "recent_signal": "",
        "signal_url": "",
        "portfolio_callouts": ["Credly", "Mursion"],
    },
    {
        "firm_name": "Learn Capital",
        "linkedin_url": "https://www.linkedin.com/company/learn-capital/",
        "hq_country": "San Mateo, CA",
        "stage_focus": ["seed", "series-a"],
        "focus_sectors": ["edtech"],
        "partners": [
            {"title": "General Partner",
             "profile_url": "https://www.linkedin.com/search/results/people/?currentCompany=learn-capital"},
        ],
        "recent_signal": "Seed round in AI-tutor startup announced this month",
        "signal_url": "https://www.learncapital.com/news/ai-tutor-seed",
        "portfolio_callouts": ["Brilliant", "Udemy", "Andela"],
    },
]


_FINTECH_FIXTURE: list[dict[str, Any]] = [
    {
        "firm_name": "Ribbit Capital",
        "linkedin_url": "https://www.linkedin.com/company/ribbit-capital/",
        "hq_country": "Palo Alto, CA",
        "stage_focus": ["series-a", "series-b", "growth"],
        "focus_sectors": ["fintech"],
        "partners": [
            {"title": "Partner",
             "profile_url": "https://www.linkedin.com/search/results/people/?currentCompany=ribbit-capital"},
        ],
        "recent_signal": "Led $100M Series C in cross-border payments startup",
        "signal_url": "https://www.ribbitcap.com/news/payments-series-c",
        "portfolio_callouts": ["Robinhood", "Nubank", "Coinbase"],
    },
    {
        "firm_name": "QED Investors",
        "linkedin_url": "https://www.linkedin.com/company/qed-investors/",
        "hq_country": "Alexandria, VA",
        "stage_focus": ["seed", "series-a", "series-b"],
        "focus_sectors": ["fintech"],
        "partners": [
            {"title": "Managing Partner",
             "profile_url": "https://www.linkedin.com/search/results/people/?currentCompany=qed-investors"},
            {"title": "Partner",
             "profile_url": "https://www.linkedin.com/search/results/people/?currentCompany=qed-investors&keywords=partner"},
        ],
        "recent_signal": "Closed Fund VIII at $925M — largest dedicated fintech fund of the year",
        "signal_url": "https://www.qedinvestors.com/news/fund-viii",
        "portfolio_callouts": ["Nubank", "Klarna", "Credit Karma"],
    },
    {
        "firm_name": "Index Ventures (Fintech)",
        "linkedin_url": "https://www.linkedin.com/company/index-ventures/",
        "hq_country": "London, UK",
        "stage_focus": ["series-a", "series-b", "growth"],
        "focus_sectors": ["fintech", "saas"],
        "partners": [
            {"title": "Partner — Fintech",
             "profile_url": "https://www.linkedin.com/search/results/people/?currentCompany=index-ventures&keywords=fintech"},
        ],
        "recent_signal": "EU fintech Series B — embedded-finance platform led by Index",
        "signal_url": "https://www.indexventures.com/news/embedded-finance-series-b",
        "portfolio_callouts": ["Revolut", "Wise", "Adyen"],
    },
    {
        "firm_name": "Anthemis Group",
        "linkedin_url": "https://www.linkedin.com/company/anthemis-group/",
        "hq_country": "London, UK",
        "stage_focus": ["seed", "series-a"],
        "focus_sectors": ["fintech", "insurtech"],
        "partners": [
            {"title": "Managing Partner",
             "profile_url": "https://www.linkedin.com/search/results/people/?currentCompany=anthemis-group"},
        ],
        "recent_signal": "",
        "signal_url": "",
        "portfolio_callouts": ["Pendo", "Trov", "Currencycloud"],
    },
    {
        "firm_name": "Nyca Partners",
        "linkedin_url": "https://www.linkedin.com/company/nyca-partners/",
        "hq_country": "New York, NY",
        "stage_focus": ["seed", "series-a", "series-b"],
        "focus_sectors": ["fintech"],
        "partners": [
            {"title": "Managing Partner",
             "profile_url": "https://www.linkedin.com/search/results/people/?currentCompany=nyca-partners"},
        ],
        "recent_signal": "Series A in B2B treasury-automation startup announced",
        "signal_url": "https://www.nyca.com/news/treasury-series-a",
        "portfolio_callouts": ["Acorns", "Affirm", "Payoneer"],
    },
]


_CLIMATE_FIXTURE: list[dict[str, Any]] = [
    {
        "firm_name": "Breakthrough Energy Ventures",
        "linkedin_url": "https://www.linkedin.com/company/breakthrough-energy/",
        "hq_country": "Kirkland, WA",
        "stage_focus": ["series-a", "series-b", "growth"],
        "focus_sectors": ["climate tech"],
        "partners": [
            {"title": "Partner",
             "profile_url": "https://www.linkedin.com/search/results/people/?currentCompany=breakthrough-energy"},
        ],
        "recent_signal": "Announced new $1.5B fund for climate-tech scale-ups",
        "signal_url": "https://breakthroughenergy.org/news/new-fund",
        "portfolio_callouts": ["Form Energy", "Boston Metal", "Pivot Bio"],
    },
    {
        "firm_name": "Lowercarbon Capital",
        "linkedin_url": "https://www.linkedin.com/company/lowercarbon-capital/",
        "hq_country": "San Francisco, CA",
        "stage_focus": ["seed", "series-a"],
        "focus_sectors": ["climate tech"],
        "partners": [
            {"title": "Founding Partner",
             "profile_url": "https://www.linkedin.com/search/results/people/?currentCompany=lowercarbon-capital"},
        ],
        "recent_signal": "Led seed round in direct-air-capture startup",
        "signal_url": "https://lowercarbon.com/news/dac-seed",
        "portfolio_callouts": ["Twelve", "Living Carbon", "Charm Industrial"],
    },
    {
        "firm_name": "Energy Impact Partners",
        "linkedin_url": "https://www.linkedin.com/company/energy-impact-partners/",
        "hq_country": "New York, NY",
        "stage_focus": ["series-b", "growth"],
        "focus_sectors": ["climate tech", "energy"],
        "partners": [
            {"title": "Managing Partner",
             "profile_url": "https://www.linkedin.com/search/results/people/?currentCompany=energy-impact-partners"},
        ],
        "recent_signal": "",
        "signal_url": "",
        "portfolio_callouts": ["Arcadia", "Aurora Solar"],
    },
    {
        "firm_name": "Pale Blue Dot",
        "linkedin_url": "https://www.linkedin.com/company/pale-blue-dot-vc/",
        "hq_country": "Malmö, Sweden",
        "stage_focus": ["seed", "series-a"],
        "focus_sectors": ["climate tech"],
        "partners": [
            {"title": "General Partner",
             "profile_url": "https://www.linkedin.com/search/results/people/?currentCompany=pale-blue-dot-vc"},
        ],
        "recent_signal": "Closed Fund II at €100M — European seed leader for climate",
        "signal_url": "https://paleblue.vc/news/fund-ii",
        "portfolio_callouts": ["Patch", "Phytoform", "Climatiq"],
    },
]


_GENERIC_FIXTURE: list[dict[str, Any]] = [
    {
        "firm_name": "Sequoia Capital",
        "linkedin_url": "https://www.linkedin.com/company/sequoia-capital/",
        "hq_country": "Menlo Park, CA",
        "stage_focus": ["seed", "series-a", "series-b", "growth"],
        "focus_sectors": ["generalist"],
        "partners": [
            {"title": "Partner",
             "profile_url": "https://www.linkedin.com/search/results/people/?currentCompany=sequoia-capital"},
        ],
        "recent_signal": "Generalist mandate — no sector-specific announcement",
        "signal_url": "https://www.sequoiacap.com/companies",
        "portfolio_callouts": [],
    },
    {
        "firm_name": "Andreessen Horowitz",
        "linkedin_url": "https://www.linkedin.com/company/andreessen-horowitz/",
        "hq_country": "Menlo Park, CA",
        "stage_focus": ["seed", "series-a", "series-b", "growth"],
        "focus_sectors": ["generalist"],
        "partners": [
            {"title": "General Partner",
             "profile_url": "https://www.linkedin.com/search/results/people/?currentCompany=andreessen-horowitz"},
        ],
        "recent_signal": "",
        "signal_url": "",
        "portfolio_callouts": [],
    },
    {
        "firm_name": "Accel",
        "linkedin_url": "https://www.linkedin.com/company/accel-vc/",
        "hq_country": "Palo Alto, CA",
        "stage_focus": ["series-a", "series-b"],
        "focus_sectors": ["generalist"],
        "partners": [
            {"title": "Partner",
             "profile_url": "https://www.linkedin.com/search/results/people/?currentCompany=accel-vc"},
        ],
        "recent_signal": "",
        "signal_url": "",
        "portfolio_callouts": [],
    },
]


_FIXTURES: dict[str, list[dict[str, Any]]] = {
    "edtech":       _EDTECH_FIXTURE,
    "fintech":      _FINTECH_FIXTURE,
    "climate tech": _CLIMATE_FIXTURE,
}


def fixture_for(sector: str) -> list[dict[str, Any]]:
    return _FIXTURES.get(sector.strip().lower(), _GENERIC_FIXTURE)


# ── News / SERP triangulation fixture ───────────────────────────────


_NEWS_FIXTURE: dict[str, list[dict[str, str]]] = {
    "edtech": [
        {"title": "Owl Ventures closes record $1B edtech fund",
         "url": "https://techcrunch.com/2026/04/12/owl-ventures-1b-fund/",
         "snippet": "Owl Ventures, the largest dedicated edtech investor, has closed Fund VI at $1B..."},
        {"title": "Reach Capital raises $215M Fund V for early-stage edtech",
         "url": "https://www.edsurge.com/news/reach-capital-fund-v",
         "snippet": "Reach Capital, an early-stage education-tech VC, announced its fifth fund..."},
        {"title": "EU edtech Series A — Brighteye leads round in language learning",
         "url": "https://sifted.eu/articles/brighteye-lingumi-series-a",
         "snippet": "Paris-based Brighteye Ventures led a Series A in language-learning startup..."},
    ],
    "fintech": [
        {"title": "QED Investors closes $925M Fund VIII for fintech",
         "url": "https://www.fintechnews.com/qed-fund-viii-close",
         "snippet": "QED Investors, the fintech-dedicated VC, has closed its eighth fund..."},
        {"title": "Index Ventures leads Series B in embedded-finance platform",
         "url": "https://techcrunch.com/2026/05/01/index-embedded-finance-series-b/",
         "snippet": "Index Ventures led a Series B funding round in a UK-based embedded-finance..."},
    ],
    "climate tech": [
        {"title": "Breakthrough Energy Ventures announces $1.5B climate fund",
         "url": "https://www.canarymedia.com/articles/bev-1-5b-fund",
         "snippet": "Breakthrough Energy Ventures announced a new $1.5B fund focused on climate scale-ups..."},
        {"title": "Pale Blue Dot closes €100M Fund II for European climate seed",
         "url": "https://sifted.eu/articles/pale-blue-dot-fund-ii",
         "snippet": "Swedish climate-tech VC Pale Blue Dot closed Fund II at €100M..."},
    ],
}


def news_fixture_for(sector: str) -> list[dict[str, str]]:
    return _NEWS_FIXTURE.get(sector.strip().lower(), [])
