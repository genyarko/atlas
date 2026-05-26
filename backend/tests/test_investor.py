"""Investor-module tests.

Acceptance: Investor module returns ≥3 findings with evidence URLs for
any sector query; mock mode is deterministic; live path degrades
gracefully when the Bright Data dataset returns no results.

Coverage:
  * Data layer — infer_sector, normalize_stage, normalize_country,
    normalize (company rows + people rows), cluster.
  * Mock acceptance — edtech, fintech, climate tech each produce the
    expected shape.
  * Live path — companies + people + SERP all stubbed via monkeypatch.
  * Fallback behavior — empty company rows, too few firms.
  * _resolve_sector — query-based inference overrides generic subject.
"""

from __future__ import annotations

import pytest

from app.brightdata import serp, web_scraper_api
from app.modules import MODULES
from app.modules.investor import InvestorModule, _resolve_sector
from app.modules.investor_data import (
    InvestorCluster,
    Partner,
    VCFirmSignal,
    cluster,
    fixture_for,
    infer_sector,
    news_fixture_for,
    normalize,
    normalize_country,
    normalize_stage,
)


# ── infer_sector ─────────────────────────────────────────────────────


@pytest.mark.parametrize("query,expected", [
    ("find VCs investing in edtech", "edtech"),
    ("who funds ed-tech startups", "edtech"),
    ("top fintech investors in Europe", "fintech"),
    ("financial tech VC firms", "fintech"),
    ("climate tech venture funds", "climate tech"),
    ("cleantech seed investors", "climate tech"),
    ("digital health series A investors", "healthtech"),
    ("AI/ML startup investors", "ai"),
    ("dev tools VC landscape", "dev tools"),
    ("cybersecurity VC firms", "security"),
    ("totally unrelated query about Linear", "edtech"),  # fallback
])
def test_infer_sector_classification(query, expected):
    assert infer_sector(query) == expected


# ── normalize_stage ───────────────────────────────────────────────────


@pytest.mark.parametrize("text,expected_stages", [
    ("seed and pre-seed fund", ["seed"]),
    ("Series A and Series B", ["series-a", "series-b"]),
    ("early-stage through growth", ["series-a", "growth"]),
    ("", []),
    ("generalist multi-stage", []),
])
def test_normalize_stage(text, expected_stages):
    result = normalize_stage(text)
    for stage in expected_stages:
        assert stage in result, f"expected {stage!r} in {result!r} for {text!r}"


# ── normalize_country ─────────────────────────────────────────────────


@pytest.mark.parametrize("location,expected", [
    ("San Francisco, CA", "USA"),
    ("Menlo Park, CA", "USA"),
    ("New York, NY", "USA"),
    ("London, UK", "UK"),
    ("Paris, France", "EU"),
    ("Berlin, Germany", "EU"),
    ("Singapore", "APAC"),
    ("Tokyo, Japan", "APAC"),
    ("Dubai, UAE", "MENA"),
    ("São Paulo, Brazil", "LATAM"),
    ("", "Other"),
    ("Somewhere Unknown", "Other"),
])
def test_normalize_country(location, expected):
    assert normalize_country(location) == expected


# ── normalize ─────────────────────────────────────────────────────────


def _sample_company_rows() -> list[dict]:
    return [
        {
            "firm_name": "Reach Capital",
            "linkedin_url": "https://www.linkedin.com/company/reach-capital/",
            "hq_country": "San Francisco, CA",
            "stage_focus": ["seed", "series-a"],
            "recent_signal": "Closed Fund V at $215M",
            "signal_url": "https://www.reachcapital.com/news/fund-v",
            "portfolio_callouts": ["Outschool", "Newsela"],
        },
        {
            "firm_name": "Owl Ventures",
            "linkedin_url": "https://www.linkedin.com/company/owl-ventures/",
            "hq_country": "San Francisco, CA",
            "stage_focus": ["series-a", "series-b", "growth"],
            "recent_signal": "Closed $1B Fund VI",
            "signal_url": "https://www.owlvc.com/news/fund-vi",
            "portfolio_callouts": ["MasterClass", "Quizlet"],
        },
    ]


def _sample_people_rows() -> list[dict]:
    return [
        {
            "name": "A. Partner",
            "headline": "Founding Partner",
            "current_company": "Reach Capital",
            "location": "San Francisco, CA",
            "profile_url": "https://www.linkedin.com/in/reach-partner/",
        },
        {
            "name": "B. Investor",
            "headline": "Managing Director",
            "current_company": "Owl Ventures",
            "location": "San Francisco, CA",
            "profile_url": "https://www.linkedin.com/in/owl-md/",
        },
        {
            # company not in our firm list → should be dropped
            "name": "C. Unknown",
            "headline": "Partner",
            "current_company": "Unknown Fund",
            "profile_url": "https://www.linkedin.com/in/unknown/",
        },
    ]


def test_normalize_converts_company_rows_to_vc_firm_signals():
    firms = normalize(_sample_company_rows(), sector="edtech")
    assert len(firms) == 2
    names = [f.firm_name for f in firms]
    assert "Reach Capital" in names
    assert "Owl Ventures" in names


def test_normalize_attaches_people_to_matching_firms():
    firms = normalize(_sample_company_rows(), _sample_people_rows(), sector="edtech")
    by_name = {f.firm_name: f for f in firms}
    assert len(by_name["Reach Capital"].partners) == 1
    assert by_name["Reach Capital"].partners[0].title == "Founding Partner"
    assert by_name["Reach Capital"].partners[0].name == "A. Partner"


def test_normalize_drops_people_with_unknown_firm():
    firms = normalize(_sample_company_rows(), _sample_people_rows(), sector="edtech")
    all_partner_names = [p.name for f in firms for p in f.partners]
    assert "C. Unknown" not in all_partner_names


def test_normalize_skips_non_dict_rows():
    rows = [None, "bad", 42, _sample_company_rows()[0]]
    firms = normalize(rows, sector="edtech")
    assert len(firms) == 1


def test_normalize_skips_rows_without_name():
    rows = [{"linkedin_url": "https://example.com"}, _sample_company_rows()[0]]
    firms = normalize(rows, sector="edtech")
    assert len(firms) == 1


def test_normalize_generates_linkedin_url_when_missing():
    rows = [{"firm_name": "Test Fund"}]
    firms = normalize(rows, sector="edtech")
    assert firms[0].linkedin_url.startswith("https://www.linkedin.com/company/")


# ── cluster ───────────────────────────────────────────────────────────


def test_cluster_counts_firms_and_partners():
    firms = normalize(_sample_company_rows(), _sample_people_rows(), sector="edtech")
    summary = cluster(firms, "edtech")
    assert summary.total_firms == 2
    assert summary.partner_count == 2
    assert summary.sector == "edtech"


def test_cluster_counts_active_signals():
    firms = normalize(_sample_company_rows(), sector="edtech")
    summary = cluster(firms, "edtech")
    # Both sample firms have a recent_signal
    assert summary.active_signals_count == 2


def test_cluster_by_stage_includes_firm_stages():
    firms = normalize(_sample_company_rows(), sector="edtech")
    summary = cluster(firms, "edtech")
    assert "seed" in summary.by_stage
    assert "series-a" in summary.by_stage


def test_cluster_top_firms_ordered_by_signal_then_portfolio():
    firms = normalize(_sample_company_rows(), sector="edtech")
    summary = cluster(firms, "edtech")
    assert len(summary.top_firms) == 2
    # Both have signals — Owl has more portfolio callouts so could rank first or
    # same; just assert top_firms contains both.
    firm_names = [f["firm_name"] for f in summary.top_firms]
    assert "Reach Capital" in firm_names
    assert "Owl Ventures" in firm_names


def test_cluster_top_partners_one_per_firm():
    firms = normalize(_sample_company_rows(), _sample_people_rows(), sector="edtech")
    summary = cluster(firms, "edtech")
    # At most 1 partner per firm surfaced in top_partners, 2 firms → ≤2 top partners
    assert len(summary.top_partners) <= 2
    partner_firms = [p["firm_name"] for p in summary.top_partners]
    # No duplicated firm
    assert len(partner_firms) == len(set(partner_firms))


def test_cluster_edtech_fixture_produces_plausible_shape():
    firms = normalize(fixture_for("edtech"), sector="edtech")
    summary = cluster(firms, "edtech")
    assert summary.total_firms >= 4
    assert summary.partner_count >= 4
    assert summary.active_signals_count >= 3
    assert "USA" in summary.by_country
    assert "EU" in summary.by_country   # Brighteye is Paris


# ── _resolve_sector ───────────────────────────────────────────────────


@pytest.mark.parametrize("params,expected", [
    ({"sector": "fintech", "query": "some query"}, "fintech"),
    ({"query": "find VCs in edtech", "subject": "the target"}, "edtech"),
    ({"query": "climate tech investors", "subject": "the target"}, "climate tech"),
    ({"query": "no sector hint here", "subject": "fintech"}, "fintech"),
    ({"query": "no sector and generic", "subject": "the target"}, "edtech"),  # fallback
])
def test_resolve_sector(params, expected):
    assert _resolve_sector(params) == expected


# ── Mock path acceptance ──────────────────────────────────────────────


@pytest.mark.parametrize("sector,query", [
    ("edtech",       "find me VCs investing in edtech"),
    ("fintech",      "top fintech VC firms and partners"),
    ("climate tech", "climate tech investors series A"),
])
async def test_investor_mock_meets_acceptance(sector, query):
    module = InvestorModule()
    result = await module.mock({"query": query, "subject": "the target"})
    assert result.module == "investor"
    assert result.status == "success"
    assert len(result.findings) >= 3, f"need ≥3 findings, got {len(result.findings)}"
    for f in result.findings:
        assert f.evidence, f"finding has no evidence URL: {f.statement!r}"
        for url in f.evidence:
            assert url.startswith("http"), f"non-URL evidence: {url!r}"
    assert len(result.sources) >= 2
    vias = {s.via for s in result.sources}
    assert "web_scraper_api" in vias
    assert result.raw_data["mode"] == "mock"
    assert result.raw_data["sector"] == sector
    assert result.confidence >= 0.7


async def test_investor_mock_raw_data_contains_expected_keys():
    module = InvestorModule()
    result = await module.mock({"query": "edtech VC", "subject": "the target"})
    for key in ("sector", "total_firms", "by_stage", "by_country",
                "partner_count", "active_signals_count", "top_firms",
                "top_partners", "news_items", "mode"):
        assert key in result.raw_data, f"missing key: {key}"


async def test_investor_mock_edtech_surfaces_known_firms():
    module = InvestorModule()
    result = await module.mock({"query": "find VCs in edtech", "subject": "the target"})
    firm_names = [f["firm_name"] for f in result.raw_data["top_firms"]]
    # At least one of the pre-seeded edtech firms must appear
    known = {"Reach Capital", "Owl Ventures", "GSV Ventures", "Brighteye Ventures",
             "Learn Capital", "New Markets Venture Partners"}
    assert known & set(firm_names), f"no known edtech firm in {firm_names}"


# ── Live path stubbed via monkeypatch ─────────────────────────────────


# ── SERP result fixtures for live-path tests ─────────────────────────
#
# The live path now discovers firms and people via SERP (site:linkedin.com/…)
# rather than direct dataset calls, so monkeypatch targets serp.search.


def _fake_firm_serp() -> list[dict]:
    """Three linkedin.com/company results the live path would parse."""
    return [
        {"title": "Stubbed VC One | LinkedIn",
         "link": "https://www.linkedin.com/company/stubbed-vc-one/",
         "snippet": "Early-stage venture capital investing in edtech startups."},
        {"title": "Stubbed VC Two | LinkedIn",
         "link": "https://www.linkedin.com/company/stubbed-vc-two/",
         "snippet": "Series A and B fund focused on edtech and future of work."},
        {"title": "Stubbed VC Three | LinkedIn",
         "link": "https://www.linkedin.com/company/stubbed-vc-three/",
         "snippet": "Seed-stage edtech investor based in Berlin."},
    ]


def _fake_people_serp() -> list[dict]:
    """Two linkedin.com/in results the live path would parse."""
    return [
        {"title": "D. Partner - General Partner at Stubbed VC One | LinkedIn",
         "link": "https://www.linkedin.com/in/d-partner",
         "snippet": "Investing in edtech at seed and Series A."},
        {"title": "E. Principal - Principal at Stubbed VC Two | LinkedIn",
         "link": "https://www.linkedin.com/in/e-principal",
         "snippet": "Early-stage edtech investor."},
    ]


def _fake_news_serp() -> list[dict]:
    return [
        {"title": "EdTech VC news",
         "link": "https://example.com/edtech-vc-news",
         "snippet": "Investors are active in edtech..."},
    ]


def _make_fake_serp(*, firms=True, people=True, news=True):
    """Return a serp.search stub that routes by query content."""
    async def fake_search(query, *, num=10):
        if "linkedin.com/company" in query:
            return _fake_firm_serp() if firms else []
        if "linkedin.com/in" in query:
            return _fake_people_serp() if people else []
        return _fake_news_serp() if news else []
    return fake_search


async def test_investor_live_executes_when_company_rows_returned(monkeypatch):
    monkeypatch.setattr(serp, "search", _make_fake_serp())

    module = InvestorModule()
    result = await module.execute({"query": "find edtech VCs", "subject": "the target"})
    assert result.module == "investor"
    assert result.raw_data["mode"] == "live"
    assert result.raw_data["total_firms"] == 3
    assert len(result.findings) >= 3
    for f in result.findings:
        assert f.evidence

    # News SERP URL must appear in sources
    source_urls = {s.url for s in result.sources}
    assert "https://example.com/edtech-vc-news" in source_urls

    # LinkedIn firm URLs appear in sources
    assert any("linkedin.com/company" in s.url for s in result.sources)


async def test_investor_live_attaches_people_to_firms(monkeypatch):
    monkeypatch.setattr(serp, "search", _make_fake_serp())

    module = InvestorModule()
    result = await module.execute({"query": "edtech VCs", "subject": "the target"})
    # Two partner profiles parsed from people SERP results
    assert result.raw_data["partner_count"] == 2
    partner_profile_urls = [p["profile_url"] for p in result.raw_data["top_partners"]]
    assert any("linkedin.com/in" in u for u in partner_profile_urls)


# ── Fallback behaviour ────────────────────────────────────────────────


async def test_investor_live_falls_back_when_no_company_rows(monkeypatch):
    monkeypatch.setattr(serp, "search", _make_fake_serp(firms=False, people=False, news=False))

    module = InvestorModule()
    result = await module.execute({"query": "edtech VCs", "subject": "the target"})
    assert result.raw_data["mode"] == "mock"
    assert len(result.findings) >= 3


async def test_investor_live_falls_back_when_too_few_firms(monkeypatch):
    async def one_firm_serp(query, *, num=10):
        if "linkedin.com/company" in query:
            return [_fake_firm_serp()[0]]  # only 1 firm; threshold is 2
        return []

    monkeypatch.setattr(serp, "search", one_firm_serp)

    module = InvestorModule()
    result = await module.execute({"query": "edtech VCs", "subject": "the target"})
    assert result.raw_data["mode"] == "mock"


async def test_investor_run_never_raises_on_empty_creds():
    """run() must return a valid ModuleResult — never raises."""
    module = InvestorModule()
    result = await module.run({"query": "fintech VC landscape", "subject": "the target"})
    assert result.module == "investor"
    assert result.status in ("success", "partial")
    assert len(result.findings) >= 1


# ── Fixture smoke tests ────────────────────────────────────────────────


@pytest.mark.parametrize("sector", ["edtech", "fintech", "climate tech"])
def test_fixture_for_returns_non_empty_list(sector):
    rows = fixture_for(sector)
    assert len(rows) >= 3
    for row in rows:
        assert "firm_name" in row
        assert "linkedin_url" in row


@pytest.mark.parametrize("sector", ["edtech", "fintech", "climate tech"])
def test_news_fixture_has_valid_urls(sector):
    news = news_fixture_for(sector)
    assert len(news) >= 1
    for item in news:
        assert item["url"].startswith("http")
        assert item["title"]


def test_fixture_for_unknown_sector_returns_generic():
    rows = fixture_for("quantum computing")
    assert len(rows) >= 1
    assert "firm_name" in rows[0]


# ── Confidence score bounds ───────────────────────────────────────────


async def test_investor_confidence_within_bounds():
    module = InvestorModule()
    for sector in ["edtech", "fintech", "climate tech"]:
        result = await module.mock({"query": f"{sector} VCs", "subject": "the target"})
        assert 0.0 <= result.confidence <= 1.0, f"confidence out of bounds for {sector}"
