"""Signal data layer — job normalization, clustering, and demo fixtures.

This module is import-side-effect free and deterministic. The live path in
``signal.py`` feeds it dicts returned by Bright Data's LinkedIn job
listings tool; the mock path feeds it the fixtures below. Both shapes go
through the same ``normalize`` → ``cluster`` pipeline so the downstream
LLM synthesizer sees identical input.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


# ── Role family taxonomy ─────────────────────────────────────────────

# Order matters: first hit wins, so engineering-specific titles (e.g.
# "GDPR engineer") aren't classified into the broader "engineering" bucket
# until after the compliance check.
_ROLE_FAMILIES: list[tuple[str, list[str]]] = [
    ("compliance",     ["gdpr", "compliance", "privacy", "trust & safety", "trust and safety", "risk officer"]),
    ("revops",         ["revenue operations", "revops", "deal desk", "sales operations"]),
    ("sales-leadership", ["vp of sales", "head of sales", "head of revenue", "chief revenue", "vp sales"]),
    ("sales-enterprise", ["enterprise account executive", "enterprise ae", "strategic account executive",
                          "enterprise sales", "named account"]),
    ("sales-mm",       ["mid-market account executive", "mid market ae", "smb account executive",
                        "account executive", " ae "]),
    ("sdr",            ["sdr", "sales development", "bdr", "business development representative"]),
    ("solutions",      ["solutions engineer", "sales engineer", "solutions architect", "presales"]),
    ("customer-success", ["customer success", "csm ", "implementation manager", "onboarding manager"]),
    ("marketing",      ["marketing", "growth", "demand gen", "content strategist", "brand designer"]),
    ("product",        ["product manager", "product lead", "head of product", " pm "]),
    ("design",         ["designer", "design lead", "ux ", "ui "]),
    ("data",            ["data scientist", "data engineer", "analytics engineer", "ml engineer",
                         "machine learning"]),
    ("security-eng",   ["security engineer", "appsec", "application security", "platform security",
                        "detection engineer"]),
    ("engineering",    ["engineer", "developer", "swe", "sre", "devops", "infrastructure"]),
    ("recruiting",     ["recruiter", "talent partner", "sourcer"]),
    ("operations",     ["operations", "program manager", "chief of staff", "business operations"]),
    ("support",        ["support", "technical support", "helpdesk"]),
]


def role_family(title: str) -> str:
    t = f" {title.lower()} "
    for family, keywords in _ROLE_FAMILIES:
        if any(k in t for k in keywords):
            return family
    return "other"


# ── Seniority detection ──────────────────────────────────────────────

_EXECUTIVE_TOKENS = ("chief ", "cxo", "ceo", "cto", "cfo", "cro", "cmo",
                     "vp ", "vp of", "vice president", "head of", "president")
_LEAD_TOKENS = ("director", "lead", "principal", "staff", "manager",
                "managerial")
_SENIOR_TOKENS = ("senior", "sr.", "sr ", "experienced")
_JUNIOR_TOKENS = ("junior", "jr.", "jr ", "associate", "graduate", "intern", "entry")


def seniority(title: str) -> str:
    t = f" {title.lower()} "
    if any(tok in t for tok in _EXECUTIVE_TOKENS):
        return "executive"
    if any(tok in t for tok in _LEAD_TOKENS):
        return "lead"
    if any(tok in t for tok in _SENIOR_TOKENS):
        return "senior"
    if any(tok in t for tok in _JUNIOR_TOKENS):
        return "junior"
    return "ic"


# ── Region detection ─────────────────────────────────────────────────

_REGION_MAP: dict[str, list[str]] = {
    "EMEA": [
        "london", "uk", "united kingdom", "england", "ireland", "dublin",
        "germany", "berlin", "munich", "frankfurt", "hamburg",
        "france", "paris", "amsterdam", "netherlands", "spain", "madrid",
        "italy", "rome", "milan", "stockholm", "copenhagen", "oslo",
        "warsaw", "poland", "lisbon", "portugal", "vienna", "austria",
        "zurich", "geneva", "switzerland", "europe", "emea", "remote — emea",
    ],
    "APAC": [
        "singapore", "tokyo", "japan", "seoul", "korea", "sydney", "australia",
        "melbourne", "auckland", "new zealand", "hong kong", "bangalore",
        "india", "mumbai", "delhi", "manila", "philippines", "jakarta",
        "indonesia", "shanghai", "beijing", "china", "apac",
    ],
    "LATAM": [
        "são paulo", "sao paulo", "brazil", "mexico city", "mexico",
        "buenos aires", "argentina", "santiago", "chile", "bogota",
        "colombia", "latam", "latin america",
    ],
    "AMER": [
        "new york", "nyc", "san francisco", "sf", "los angeles", "la,",
        "seattle", "boston", "austin", "denver", "chicago", "atlanta",
        "miami", "dallas", "houston", "washington dc", " dc ", "d.c.",
        "toronto", "ontario", "vancouver", "canada", "montréal",
        "united states", "usa", "u.s.", " us ", "remote — us",
        "remote (us)", "remote, us",
    ],
}


def region(location: str) -> str:
    loc = f" {location.lower()} "
    for reg, hints in _REGION_MAP.items():
        if any(h in loc for h in hints):
            return reg
    if "remote" in loc:
        return "Remote"
    return "Other"


# ── JobPosting + clustering ──────────────────────────────────────────


@dataclass
class JobPosting:
    title: str
    location: str
    url: str
    posted_days_ago: int
    company: str
    seniority: str = field(default="ic")
    family: str = field(default="other")
    region: str = field(default="Other")

    def classify(self) -> None:
        self.seniority = seniority(self.title)
        self.family = role_family(self.title)
        self.region = region(self.location)


def normalize(rows: Iterable[dict[str, Any]], *, company: str) -> list[JobPosting]:
    """Convert raw Bright Data rows to ``JobPosting`` instances.

    The Bright Data LinkedIn dataset returns rows with stable-ish keys
    (``job_title``, ``job_location``, ``job_posting_url``, ``job_posting_date``),
    but a few aliases show up across runs — we accept the common ones."""
    out: list[JobPosting] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = (
            row.get("job_title")
            or row.get("title")
            or row.get("position")
            or ""
        ).strip()
        if not title:
            continue
        loc = (
            row.get("job_location")
            or row.get("location")
            or row.get("city")
            or ""
        ).strip()
        url = (
            row.get("job_posting_url")
            or row.get("url")
            or row.get("link")
            or ""
        ).strip()
        days_ago = row.get("days_ago")
        if not isinstance(days_ago, int):
            posted = row.get("job_posting_date") or row.get("posted_at") or ""
            days_ago = _parse_days_ago(str(posted))
        posting = JobPosting(
            title=title,
            location=loc or "Unknown",
            url=url or f"https://www.linkedin.com/jobs/search/?keywords={company}",
            posted_days_ago=int(days_ago) if isinstance(days_ago, int) else 30,
            company=company,
        )
        posting.classify()
        out.append(posting)
    return out


_REL_DATE = re.compile(r"(\d+)\s*(day|week|month)s?\s*ago", re.I)


def _parse_days_ago(text: str) -> int:
    m = _REL_DATE.search(text)
    if not m:
        return 30
    n = int(m.group(1))
    unit = m.group(2).lower()
    return n if unit == "day" else n * 7 if unit == "week" else n * 30


_FEDERAL_TOKENS = (
    "federal", "public sector", "gov ", "government", "dod ",
    "department of defense", "fedramp", "il5", "il6",
)


def is_federal_title(title: str) -> bool:
    t = f" {title.lower()} "
    return any(tok in t for tok in _FEDERAL_TOKENS)


@dataclass
class ClusterSummary:
    company: str
    total: int
    by_family: dict[str, int]
    by_region: dict[str, int]
    by_seniority: dict[str, int]
    recent_30d: int
    older_60d: int
    recent_by_region: dict[str, int]  # last-30-day region tally
    older_by_region: dict[str, int]   # 30-90-day region tally (baseline)
    velocity_ratio: float  # last-30-day count / trailing-60-day baseline (per-30d)
    top_examples: list[dict[str, str]]  # representative {title, location, url, family}
    federal_count: int = 0  # postings whose title carries federal/gov tokens
    federal_example: dict[str, str] = field(default_factory=dict)

    def to_prompt_dict(self) -> dict[str, Any]:
        return asdict(self)


def cluster(postings: list[JobPosting], company: str) -> ClusterSummary:
    by_family = Counter(p.family for p in postings)
    by_region = Counter(p.region for p in postings)
    by_seniority = Counter(p.seniority for p in postings)
    recent = [p for p in postings if p.posted_days_ago <= 30]
    older = [p for p in postings if 30 < p.posted_days_ago <= 90]
    recent_30d = len(recent)
    older_60d = len(older)
    recent_by_region = dict(Counter(p.region for p in recent))
    older_by_region = dict(Counter(p.region for p in older))
    baseline_per_30d = (older_60d / 2.0) if older_60d else 1.0
    velocity_ratio = round(recent_30d / baseline_per_30d, 2) if baseline_per_30d else 0.0

    # Representative examples: one per non-other family, EMEA-first.
    # Include the canonical family so the rule layer doesn't have to re-classify.
    seen_families: set[str] = set()
    examples: list[dict[str, str]] = []
    for p in sorted(postings, key=lambda p: (p.region != "EMEA", p.posted_days_ago)):
        if p.family in seen_families or p.family == "other":
            continue
        seen_families.add(p.family)
        examples.append({
            "title": p.title,
            "location": p.location,
            "url": p.url,
            "family": p.family,
            "seniority": p.seniority,
            "region": p.region,
        })
        if len(examples) >= 6:
            break

    federal_postings = [p for p in postings if is_federal_title(p.title)]
    federal_example: dict[str, str] = {}
    if federal_postings:
        f = federal_postings[0]
        federal_example = {
            "title": f.title, "location": f.location, "url": f.url,
        }

    return ClusterSummary(
        company=company,
        total=len(postings),
        by_family=dict(by_family),
        by_region=dict(by_region),
        by_seniority=dict(by_seniority),
        recent_30d=recent_30d,
        older_60d=older_60d,
        recent_by_region=recent_by_region,
        older_by_region=older_by_region,
        velocity_ratio=velocity_ratio,
        top_examples=examples,
        federal_count=len(federal_postings),
        federal_example=federal_example,
    )


# ── Fixtures ─────────────────────────────────────────────────────────

# Realistic-shape job-posting fixtures used by the mock path. Each fixture
# is shaped exactly like the rows the LinkedIn dataset returns, so the
# normalize/cluster pipeline below exercises the same code in mock mode.

_LINEAR_FIXTURE: list[dict[str, Any]] = [
    # EU enterprise GTM build-out — the headline signal.
    {"job_title": "Enterprise Account Executive, DACH",
     "job_location": "Berlin, Germany",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3987121401",
     "job_posting_date": "5 days ago"},
    {"job_title": "Enterprise Account Executive, UK&I",
     "job_location": "London, United Kingdom",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3987121402",
     "job_posting_date": "9 days ago"},
    {"job_title": "Enterprise Account Executive, France",
     "job_location": "Paris, France",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3987121403",
     "job_posting_date": "11 days ago"},
    {"job_title": "Enterprise Account Executive, Nordics",
     "job_location": "Stockholm, Sweden",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3987121404",
     "job_posting_date": "14 days ago"},
    {"job_title": "Enterprise Account Executive, Benelux",
     "job_location": "Amsterdam, Netherlands",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3987121405",
     "job_posting_date": "16 days ago"},
    {"job_title": "Strategic Account Executive, EMEA",
     "job_location": "Remote — EMEA",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3987121406",
     "job_posting_date": "21 days ago"},
    {"job_title": "Strategic Account Executive, EMEA",
     "job_location": "London, United Kingdom",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3987121407",
     "job_posting_date": "23 days ago"},
    {"job_title": "Enterprise Account Executive, Germany",
     "job_location": "Munich, Germany",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3987121408",
     "job_posting_date": "27 days ago"},
    # Leadership commitment
    {"job_title": "Head of Revenue Operations, EMEA",
     "job_location": "London, United Kingdom",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3987121409",
     "job_posting_date": "18 days ago"},
    {"job_title": "VP of Sales, EMEA",
     "job_location": "London, United Kingdom",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3987121410",
     "job_posting_date": "12 days ago"},
    # Compliance build-out — supports EU thesis
    {"job_title": "Senior GDPR Compliance Engineer",
     "job_location": "Dublin, Ireland",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3987121411",
     "job_posting_date": "6 days ago"},
    {"job_title": "Privacy Engineer (GDPR / EU Data Residency)",
     "job_location": "Berlin, Germany",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3987121412",
     "job_posting_date": "10 days ago"},
    {"job_title": "Data Protection Officer",
     "job_location": "Dublin, Ireland",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3987121413",
     "job_posting_date": "25 days ago"},
    # SDR base under the AE layer
    {"job_title": "Sales Development Representative — DACH",
     "job_location": "Frankfurt, Germany",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3987121414",
     "job_posting_date": "4 days ago"},
    {"job_title": "Sales Development Representative — UK&I",
     "job_location": "London, United Kingdom",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3987121415",
     "job_posting_date": "8 days ago"},
    {"job_title": "Solutions Engineer, EMEA",
     "job_location": "London, United Kingdom",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3987121416",
     "job_posting_date": "20 days ago"},
    # US presence continuing but flat
    {"job_title": "Enterprise Account Executive",
     "job_location": "New York, NY",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3987121417",
     "job_posting_date": "22 days ago"},
    {"job_title": "Senior Software Engineer, Platform",
     "job_location": "San Francisco, CA",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3987121418",
     "job_posting_date": "26 days ago"},
    {"job_title": "Staff Product Manager, Integrations",
     "job_location": "San Francisco, CA",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3987121419",
     "job_posting_date": "29 days ago"},
    # Baseline (older) postings — used to compute velocity ratio
    {"job_title": "Account Executive",
     "job_location": "New York, NY",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3987121420",
     "job_posting_date": "55 days ago"},
    {"job_title": "Senior Software Engineer",
     "job_location": "Remote — US",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3987121421",
     "job_posting_date": "62 days ago"},
    {"job_title": "Product Designer",
     "job_location": "San Francisco, CA",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3987121422",
     "job_posting_date": "70 days ago"},
    {"job_title": "Customer Success Manager",
     "job_location": "Remote — US",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3987121423",
     "job_posting_date": "78 days ago"},
]

_DATADOG_FIXTURE: list[dict[str, Any]] = [
    {"job_title": "Senior Security Engineer, Cloud SIEM",
     "job_location": "New York, NY",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3990201501",
     "job_posting_date": "3 days ago"},
    {"job_title": "Detection Engineer, CSPM",
     "job_location": "Boston, MA",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3990201502",
     "job_posting_date": "7 days ago"},
    {"job_title": "Federal Solutions Architect",
     "job_location": "Washington, DC",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3990201503",
     "job_posting_date": "10 days ago"},
    {"job_title": "Federal Solutions Architect — DoD",
     "job_location": "Washington, DC",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3990201504",
     "job_posting_date": "15 days ago"},
    {"job_title": "Senior Product Manager, Cloud Security",
     "job_location": "New York, NY",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3990201505",
     "job_posting_date": "12 days ago"},
    {"job_title": "Application Security Engineer",
     "job_location": "Paris, France",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3990201506",
     "job_posting_date": "20 days ago"},
    {"job_title": "Senior Site Reliability Engineer",
     "job_location": "Remote — US",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3990201507",
     "job_posting_date": "28 days ago"},
    # Older baseline
    {"job_title": "Software Engineer, Observability Pipelines",
     "job_location": "New York, NY",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3990201508",
     "job_posting_date": "48 days ago"},
    {"job_title": "Software Engineer",
     "job_location": "Boston, MA",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3990201509",
     "job_posting_date": "60 days ago"},
    {"job_title": "Account Executive, Mid-Market",
     "job_location": "Austin, TX",
     "job_posting_url": "https://www.linkedin.com/jobs/view/3990201510",
     "job_posting_date": "72 days ago"},
]


def fixture_for(company: str) -> list[dict[str, Any]]:
    """Return a realistic, pre-shaped job-listings fixture for ``company``.

    Anything not specifically pre-seeded gets a small generic set so the
    mock path still produces a non-empty cluster summary."""
    key = company.strip().lower()
    if key == "linear":
        return _LINEAR_FIXTURE
    if key == "datadog":
        return _DATADOG_FIXTURE
    return [
        {"job_title": "Senior Account Executive",
         "job_location": "New York, NY",
         "job_posting_url": f"https://www.linkedin.com/jobs/search/?keywords={company}",
         "job_posting_date": "8 days ago"},
        {"job_title": "Staff Software Engineer",
         "job_location": "Remote — US",
         "job_posting_url": f"https://www.linkedin.com/jobs/search/?keywords={company}",
         "job_posting_date": "14 days ago"},
        {"job_title": "Product Manager",
         "job_location": "San Francisco, CA",
         "job_posting_url": f"https://www.linkedin.com/jobs/search/?keywords={company}",
         "job_posting_date": "22 days ago"},
        {"job_title": "Account Executive",
         "job_location": "London, United Kingdom",
         "job_posting_url": f"https://www.linkedin.com/jobs/search/?keywords={company}",
         "job_posting_date": "55 days ago"},
        {"job_title": "Software Engineer",
         "job_location": "Austin, TX",
         "job_posting_url": f"https://www.linkedin.com/jobs/search/?keywords={company}",
         "job_posting_date": "70 days ago"},
    ]


# ── News / SERP triangulation fixture ───────────────────────────────


_NEWS_FIXTURE: dict[str, list[dict[str, str]]] = {
    "linear": [
        {"title": "Linear opens London office, names EMEA sales lead",
         "url": "https://techcrunch.com/2026/05/02/linear-emea-london-office/",
         "snippet": "Linear, the issue-tracking startup, has opened its first European office in London..."},
        {"title": "Linear adds EU data residency for enterprise customers",
         "url": "https://www.linear.app/changelog/eu-data-residency",
         "snippet": "Customers on the Enterprise plan can now elect to store their data in the EU."},
        {"title": "Linear hires former Stripe exec to lead European expansion",
         "url": "https://www.theinformation.com/articles/linear-emea-hire",
         "snippet": "Linear has hired a former Stripe revenue leader to head its European push."},
    ],
    "datadog": [
        {"title": "Datadog expands security suite with new CSPM tier",
         "url": "https://www.datadoghq.com/blog/cspm-tier-announcement/",
         "snippet": "Datadog announced a new CSPM tier targeting cloud security teams."},
        {"title": "Datadog wins FedRAMP High authorization",
         "url": "https://www.fedscoop.com/datadog-fedramp-high",
         "snippet": "The observability company received FedRAMP High, unlocking broader federal deployment."},
    ],
}


def news_fixture_for(company: str) -> list[dict[str, str]]:
    return _NEWS_FIXTURE.get(company.strip().lower(), [])
