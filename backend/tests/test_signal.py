"""Signal-module-specific tests — Day 3 acceptance criterion.

Acceptance: Real Signal output for "Linear" includes ≥3 concrete
inferences with source URLs.

These tests exercise both the mock path (deterministic) and the live
path (forced via stubbing the Bright Data layer)."""

from __future__ import annotations

import pytest

from app.brightdata import serp, web_scraper_api
from app.models import MODULE_NAMES
from app.modules import MODULES
from app.modules.signal import SignalModule
from app.modules.signal_data import (
    cluster,
    fixture_for,
    normalize,
    role_family,
    region,
    seniority,
)


# ── Classification tests ─────────────────────────────────────────────


@pytest.mark.parametrize("title,expected", [
    ("Enterprise Account Executive, DACH", "sales-enterprise"),
    ("Strategic Account Executive, EMEA", "sales-enterprise"),
    ("Senior GDPR Compliance Engineer", "compliance"),
    ("Privacy Engineer (GDPR / EU Data Residency)", "compliance"),
    ("Head of Revenue Operations, EMEA", "revops"),
    ("VP of Sales, EMEA", "sales-leadership"),
    ("Sales Development Representative — DACH", "sdr"),
    ("Solutions Engineer, EMEA", "solutions"),
    ("Senior Software Engineer, Platform", "engineering"),
    ("Senior Security Engineer, Cloud SIEM", "security-eng"),
])
def test_role_family_classification(title, expected):
    assert role_family(title) == expected


@pytest.mark.parametrize("location,expected", [
    ("Berlin, Germany", "EMEA"),
    ("London, United Kingdom", "EMEA"),
    ("San Francisco, CA", "AMER"),
    ("Washington, DC", "AMER"),
    ("Singapore", "APAC"),
    ("Remote — EMEA", "EMEA"),
])
def test_region_classification(location, expected):
    assert region(location) == expected


@pytest.mark.parametrize("title,expected", [
    ("VP of Sales, EMEA", "executive"),
    ("Head of Revenue Operations, EMEA", "executive"),
    ("Staff Product Manager, Integrations", "lead"),
    ("Senior Software Engineer", "senior"),
    ("Account Executive", "ic"),
    ("Junior Recruiter", "junior"),
])
def test_seniority_classification(title, expected):
    assert seniority(title) == expected


# ── Cluster math ─────────────────────────────────────────────────────


def test_linear_fixture_clusters_into_emea_signal():
    postings = normalize(fixture_for("Linear"), company="Linear")
    summary = cluster(postings, "Linear")
    # The Linear fixture is GTM-heavy in EMEA — assert the shape, not exact numbers.
    assert summary.total >= 15
    assert summary.by_region.get("EMEA", 0) >= 8
    assert summary.by_family.get("sales-enterprise", 0) >= 5
    assert summary.by_family.get("compliance", 0) >= 2
    assert summary.by_seniority.get("executive", 0) >= 1
    # Recent ≫ older, so velocity should clearly be above 1.0
    assert summary.velocity_ratio > 1.5


# ── Acceptance: Linear mock produces ≥3 findings with sources ────────


async def test_signal_linear_meets_day3_acceptance():
    """Real Signal output for 'Linear' includes ≥3 concrete inferences with source URLs."""
    module = MODULES["signal"]
    result = await module.run({"query": "Run Signal on Linear", "subject": "Linear"})
    assert result.module == "signal"
    assert len(result.findings) >= 3, f"need ≥3 findings, got {len(result.findings)}"
    # Each finding must cite at least one URL — "concrete inferences with source URLs".
    for f in result.findings:
        assert f.evidence, f"finding without evidence: {f.statement!r}"
        for url in f.evidence:
            assert url.startswith("http"), f"non-URL evidence: {url!r}"
    # At least one inference must be about the EMEA expansion (the demo headline).
    text = " ".join(f.statement.lower() for f in result.findings)
    assert "emea" in text or "european" in text or "europe" in text or "eu" in text
    # Sources span both LinkedIn dataset and the job evidence
    vias = {s.via for s in result.sources}
    assert "web_scraper_api" in vias


async def test_signal_datadog_returns_findings_with_sources():
    module = MODULES["signal"]
    result = await module.run({"query": "Datadog pre-earnings scan", "subject": "Datadog"})
    assert len(result.findings) >= 2
    for f in result.findings:
        assert f.evidence


# ── Live path stubbed via monkeypatch ────────────────────────────────


async def test_signal_live_execute_uses_brightdata_then_synthesizes(monkeypatch):
    """When the live path returns rows, the pipeline normalizes + clusters them."""
    fake_rows = [
        {"job_title": "Enterprise Account Executive, DACH",
         "job_location": "Berlin, Germany",
         "job_posting_url": "https://example.com/jobs/1",
         "job_posting_date": "5 days ago"},
        {"job_title": "Enterprise Account Executive, UK&I",
         "job_location": "London, United Kingdom",
         "job_posting_url": "https://example.com/jobs/2",
         "job_posting_date": "9 days ago"},
        {"job_title": "Senior GDPR Compliance Engineer",
         "job_location": "Dublin, Ireland",
         "job_posting_url": "https://example.com/jobs/3",
         "job_posting_date": "6 days ago"},
        {"job_title": "VP of Sales, EMEA",
         "job_location": "London, United Kingdom",
         "job_posting_url": "https://example.com/jobs/4",
         "job_posting_date": "12 days ago"},
        {"job_title": "Sales Development Representative — DACH",
         "job_location": "Frankfurt, Germany",
         "job_posting_url": "https://example.com/jobs/5",
         "job_posting_date": "4 days ago"},
        {"job_title": "Account Executive",
         "job_location": "New York, NY",
         "job_posting_url": "https://example.com/jobs/old1",
         "job_posting_date": "60 days ago"},
        {"job_title": "Software Engineer",
         "job_location": "Remote — US",
         "job_posting_url": "https://example.com/jobs/old2",
         "job_posting_date": "75 days ago"},
    ]

    async def fake_fetch(company, *, location=None, limit=50):
        return fake_rows

    async def fake_search(query, *, num=10):
        return [
            {"title": "Linear opens London office",
             "url": "https://example.com/news/london",
             "snippet": "Linear has opened a London office..."},
        ]

    monkeypatch.setattr(web_scraper_api, "fetch_company_careers_jobs", fake_fetch)
    monkeypatch.setattr(serp, "search", fake_search)

    module = SignalModule()
    result = await module.execute({"query": "Live signal on Linear", "subject": "Linear"})
    assert result.module == "signal"
    assert result.raw_data["mode"] == "live"
    assert len(result.findings) >= 3
    assert any("EMEA" in f.statement or "European" in f.statement or "europe" in f.statement.lower()
               for f in result.findings)
    # SERP triangulation surfaced as a source
    urls = {s.url for s in result.sources}
    assert "https://example.com/news/london" in urls


async def test_signal_live_falls_back_when_rows_empty(monkeypatch):
    async def fake_fetch(company, *, location=None, limit=50):
        return None

    monkeypatch.setattr(web_scraper_api, "fetch_company_careers_jobs", fake_fetch)
    module = SignalModule()
    result = await module.execute({"query": "Live signal on Linear", "subject": "Linear"})
    # Falls back to mock — mode reads 'mock' and we still get our acceptance shape.
    assert result.raw_data["mode"] == "mock"
    assert len(result.findings) >= 3


# ── Regression: every module still passes its own contract ──────────


@pytest.mark.parametrize("name", MODULE_NAMES)
async def test_modules_all_have_minimum_shape(name):
    result = await MODULES[name].mock({"query": "Test on AcmeCorp", "subject": "AcmeCorp"})
    assert result.findings
    assert 0.0 <= result.confidence <= 1.0
