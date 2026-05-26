"""Filing-module-specific tests — Day 6 acceptance criterion.

Acceptance: "Filing returns useful output for at least one demo target.
Polish ceiling lower than first 3 modules — supporting cast."

Coverage:
  * Pure data layer — EDGAR JSON parsing, risk-factor extraction,
    materiality→severity mapping, fixture catalog.
  * Mock path — Datadog produces materially-scored findings; private
    company (Linear) degrades cleanly with an explanatory finding.
  * Live path — Web Unlocker + LLM stubbed via monkeypatch.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.brightdata import web_unlocker
from app.modules import MODULES
from app.modules.filing import FilingModule, _parse_llm_diff
from app.modules.filing_data import (
    KNOWN_CIKS,
    Filing,
    FilingDiff,
    RiskFactorChange,
    TRACKED_FILING_TYPES,
    cik_for,
    edgar_filing_index_url,
    edgar_submissions_url,
    extract_risk_factors,
    fixture_diff_for,
    materiality_to_severity,
    parse_edgar_submissions,
    pick_diff_pair,
)


# ── Pure data layer ─────────────────────────────────────────────────


def test_known_ciks_are_10_digits():
    """EDGAR JSON paths use the zero-padded 10-digit CIK form."""
    for subject, cik in KNOWN_CIKS.items():
        assert len(cik) == 10, f"{subject} CIK {cik} not zero-padded"
        assert cik.isdigit(), f"{subject} CIK {cik} not numeric"


def test_cik_lookup_for_known_and_unknown():
    assert cik_for("Datadog") == "0001561550"
    assert cik_for("NotAPublicCo") is None


def test_edgar_url_helpers():
    """EDGAR URL builders return well-formed paths the live path will fetch."""
    sub = edgar_submissions_url("0001561550")
    assert sub.startswith("https://data.sec.gov/submissions/CIK")
    assert "0001561550" in sub
    idx = edgar_filing_index_url("0001561550", "0001561550-26-000017")
    # CIK strips leading zeros in the Archives path; accession is dash-stripped
    # in the directory name but retained in the filename.
    assert "1561550/000156155026000017/" in idx
    assert "0001561550-26-000017-index.htm" in idx


def test_tracked_filing_types_includes_10q():
    """10-Q is the demo headline — must be in the tracked set."""
    assert "10-Q" in TRACKED_FILING_TYPES


@pytest.mark.parametrize("score,expected", [
    (5, "critical"),
    (4, "high"),
    (3, "notable"),
    (2, "notable"),
    (1, "info"),
    (0, "info"),
])
def test_materiality_to_severity_rubric(score, expected):
    assert materiality_to_severity(score) == expected


def test_parse_edgar_submissions_picks_tracked_forms_only():
    """The parser drops Form 4, S-3, ARS, etc. — only tracked forms survive."""
    payload = {
        "filings": {"recent": {
            "form":             ["10-Q", "8-K", "4", "10-K", "S-3"],
            "filingDate":       ["2026-04-30", "2026-04-15", "2026-04-10", "2026-02-20", "2026-01-15"],
            "accessionNumber":  ["a1", "a2", "a3", "a4", "a5"],
            "primaryDocument":  ["q1.htm", "8k.htm", "form4.htm", "10k.htm", "s3.htm"],
            "periodOfReport":   ["2026-03-31", "2026-04-15", "", "2025-12-31", ""],
        }},
    }
    filings = parse_edgar_submissions(payload, cik="0001561550")
    forms = [f.filing_type for f in filings]
    assert forms == ["10-Q", "8-K", "10-K"]
    assert filings[0].fiscal_period == "Q1 2026"
    assert filings[2].fiscal_period == "FY 2025"
    # URL points into EDGAR Archives, with CIK leading-zeros stripped.
    assert "1561550/" in filings[0].url
    assert filings[0].url.endswith("q1.htm")


def test_parse_edgar_submissions_returns_empty_on_malformed():
    assert parse_edgar_submissions({"filings": "nope"}, cik="0001561550") == []
    assert parse_edgar_submissions({}, cik="0001561550") == []
    assert parse_edgar_submissions(
        {"filings": {"recent": {"form": "not-a-list"}}}, cik="0001561550"
    ) == []


def test_pick_diff_pair_picks_most_recent_and_prior():
    a = Filing("a1", "10-Q", "2026-04-30", "Q1 2026", "u1", "i1")
    b = Filing("b1", "10-Q", "2026-02-13", "Q4 2025", "u2", "i2")
    c = Filing("c1", "10-K", "2026-02-25", "FY 2025", "u3", "i3")
    pair = pick_diff_pair([c, a, b], filing_type="10-Q")
    assert pair is not None
    current, prior = pair
    assert current.accession_no == "a1"
    assert prior.accession_no == "b1"


def test_pick_diff_pair_no_prior_when_only_one_match():
    a = Filing("a1", "10-K", "2026-02-25", "FY 2025", "u1", "i1")
    pair = pick_diff_pair([a], filing_type="10-K")
    assert pair is not None
    current, prior = pair
    assert current is a
    assert prior is None


def test_pick_diff_pair_returns_none_when_no_match():
    a = Filing("a1", "10-Q", "2026-04-30", "Q1 2026", "u1", "i1")
    assert pick_diff_pair([a], filing_type="10-K") is None


def test_extract_risk_factors_slices_between_items():
    body = (
        "<html><body>"
        "Item 1. Business. We do things.\n"
        "Item 1A. Risk Factors\n"
        "We may face customer concentration risk in AI workloads.\n"
        "Our suppliers could become unreliable.\n"
        "Item 1B. Unresolved Staff Comments\n"
        "None.</body></html>"
    )
    section = extract_risk_factors(body)
    assert "customer concentration" in section.lower()
    assert "unresolved staff comments" not in section.lower()
    assert "we do things" not in section.lower()


def test_extract_risk_factors_falls_back_when_no_marker():
    body = "<html><body>Plain text with no Item markers, just risk language.</body></html>"
    section = extract_risk_factors(body, max_chars=200)
    # Without a marker we return the first max_chars of text — must still
    # return *something* so the LLM diff doesn't see empty input.
    assert "risk language" in section
    assert len(section) <= 200


def test_fixture_diff_for_datadog_has_high_materiality_diff():
    diff = fixture_diff_for("Datadog")
    assert diff.has_changes
    assert diff.max_materiality >= 4
    kinds = {c.kind for c in diff.changes}
    assert "added" in kinds  # the headline AI-workload risk factor
    assert diff.prior is not None  # there's a comparable for the diff


def test_fixture_diff_for_linear_is_empty_and_says_private():
    diff = fixture_diff_for("Linear")
    assert not diff.has_changes
    assert "privately" in diff.summary.lower() or "private" in diff.summary.lower()


def test_fixture_diff_for_unknown_public_company_marks_no_scouted_data():
    diff = fixture_diff_for("Cloudflare")  # in KNOWN_CIKS but no fixture
    assert not diff.has_changes
    assert "scouted" in diff.summary.lower() or "live mode" in diff.summary.lower()


# ── LLM diff parser (trust boundary with Claude) ────────────────────


def test_parse_llm_diff_happy_path():
    payload = json.dumps({
        "summary": "Two added risks, one modified.",
        "changes": [
            {"kind": "added", "headline": "New AI workload risk",
             "excerpt": "We disclose increasing concentration on AI workloads.",
             "materiality": 4, "rationale": "Net-new disclosure."},
            {"kind": "modified", "headline": "Exec comp",
             "excerpt": "Vesting tied to FCF.",
             "materiality": 3, "rationale": "First FCF-linked PSU."},
        ],
    })
    changes, summary = _parse_llm_diff(payload)
    assert summary.startswith("Two added")
    assert len(changes) == 2
    assert changes[0].kind == "added"
    assert changes[0].materiality == 4
    assert changes[1].kind == "modified"


def test_parse_llm_diff_strips_markdown_fences():
    fenced = "```json\n" + json.dumps({
        "summary": "ok",
        "changes": [
            {"kind": "added", "headline": "h", "excerpt": "e",
             "materiality": 3, "rationale": "r"},
        ],
    }) + "\n```"
    changes, summary = _parse_llm_diff(fenced)
    assert summary == "ok"
    assert len(changes) == 1


def test_parse_llm_diff_clamps_materiality():
    payload = json.dumps({
        "summary": "",
        "changes": [
            {"kind": "added", "headline": "h1", "excerpt": "e",
             "materiality": 99, "rationale": "r"},
            {"kind": "removed", "headline": "h2", "excerpt": "e",
             "materiality": -3, "rationale": "r"},
        ],
    })
    changes, _ = _parse_llm_diff(payload)
    assert changes[0].materiality == 5
    assert changes[1].materiality == 1


def test_parse_llm_diff_drops_invalid_kinds_and_empty_fields():
    payload = json.dumps({
        "summary": "",
        "changes": [
            {"kind": "INVALID", "headline": "h", "excerpt": "e",
             "materiality": 3, "rationale": "r"},     # invalid kind
            {"kind": "added", "headline": "", "excerpt": "e",
             "materiality": 3, "rationale": "r"},     # empty headline
            {"kind": "added", "headline": "h", "excerpt": "",
             "materiality": 3, "rationale": "r"},     # empty excerpt
            {"kind": "added", "headline": "ok", "excerpt": "ok",
             "materiality": 3, "rationale": "r"},     # valid
        ],
    })
    changes, _ = _parse_llm_diff(payload)
    assert len(changes) == 1
    assert changes[0].headline == "ok"


@pytest.mark.parametrize("payload", [
    "not json",
    "{ invalid",
    "[1, 2]",                          # top-level list — drop
    json.dumps({"changes": "not-a-list"}),
])
def test_parse_llm_diff_returns_empty_on_malformed(payload):
    changes, summary = _parse_llm_diff(payload)
    assert changes == []


# ── Mock acceptance: Datadog produces material findings ─────────────


async def test_filing_datadog_meets_day6_acceptance():
    """Day-6 acceptance: 'returns useful output for at least one demo target'.

    Datadog 10-Q mock surfaces ≥1 finding at severity ≥ high with an
    EDGAR source URL — that's the supporting-cast demo target.
    """
    result = await MODULES["filing"].run(
        {"query": "Pre-earnings scan on Datadog", "subject": "Datadog"},
    )
    assert result.module == "filing"
    assert result.findings
    # Material content: at least one HIGH or CRITICAL finding.
    severities = {f.severity for f in result.findings}
    assert severities & {"high", "critical"}, (
        f"expected ≥1 high/critical finding, got severities {severities}"
    )
    # Each finding cites an EDGAR URL.
    for f in result.findings:
        assert f.evidence, f"finding without evidence: {f.statement!r}"
        for url in f.evidence:
            assert url.startswith("http"), f"non-URL evidence: {url!r}"
            assert "sec.gov" in url


async def test_filing_datadog_raw_data_shape():
    """Renderer needs the full filing_diff structure in raw_data."""
    result = await MODULES["filing"].run({"subject": "Datadog"})
    rd = result.raw_data
    assert rd["subject"] == "Datadog"
    assert rd["max_materiality"] >= 4
    diff = rd["filing_diff"]
    assert diff["current"]["filing_type"] == "10-Q"
    assert diff["prior"] is not None
    assert isinstance(diff["changes"], list) and diff["changes"]


async def test_filing_private_company_finding_explains_gap():
    """Private companies should produce a clean explanatory finding, not crash."""
    result = await MODULES["filing"].run({"subject": "Linear"})
    assert result.module == "filing"
    assert result.findings
    statement = result.findings[0].statement.lower()
    assert "private" in statement or "no sec" in statement or "skipped" in statement


async def test_filing_unknown_public_company_returns_partial():
    """A public company we know but didn't scout — partial status, honest finding."""
    result = await MODULES["filing"].run({"subject": "Cloudflare"})
    assert result.status in ("partial", "success")
    assert result.findings
    text = result.findings[0].statement.lower()
    assert "scouted" in text or "live" in text or "edgar" in text


async def test_filing_via_attribution_is_web_unlocker():
    """Implementation plan §4.3 calls Web Unlocker — every Filing source
    should be attributed to it (visible in the brief footer)."""
    result = await MODULES["filing"].run({"subject": "Datadog"})
    assert result.sources
    for s in result.sources:
        assert s.via == "web_unlocker"


# ── Live path stubbed via monkeypatch ───────────────────────────────


def _fake_submissions_json(*, cik: str, items: list[dict]) -> str:
    """Build a tiny EDGAR submissions JSON body for monkeypatch tests."""
    forms = [it["form"] for it in items]
    dates = [it["date"] for it in items]
    accessions = [it["accession"] for it in items]
    docs = [it["primary"] for it in items]
    periods = [it["period"] for it in items]
    return json.dumps({
        "cik": int(cik),
        "filings": {"recent": {
            "form": forms,
            "filingDate": dates,
            "accessionNumber": accessions,
            "primaryDocument": docs,
            "periodOfReport": periods,
        }},
    })


class _FakeLLM:
    def __init__(self, response: SimpleNamespace) -> None:
        self.response = response
        self.calls: list[list] = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return self.response


async def test_filing_live_uses_web_unlocker_and_llm(monkeypatch):
    """End-to-end live path: EDGAR JSON → document fetch → LLM diff."""

    fetch_calls: list[str] = []

    submissions_body = _fake_submissions_json(
        cik="0001561550",
        items=[
            {"form": "10-Q", "date": "2026-04-30",
             "accession": "0001561550-26-000017",
             "primary": "ddog-20260331.htm",
             "period": "2026-03-31"},
            {"form": "10-Q", "date": "2026-02-13",
             "accession": "0001561550-26-000003",
             "primary": "ddog-20251231.htm",
             "period": "2025-12-31"},
            {"form": "10-K", "date": "2026-02-25",
             "accession": "0001561550-26-000005",
             "primary": "ddog-2025-10k.htm",
             "period": "2025-12-31"},
        ],
    )

    current_doc = (
        "<html>Item 1A. Risk Factors\n"
        "Customer concentration in AI workloads has grown. We are exposed.\n"
        "Item 1B. Staff Comments None.</html>"
    )
    prior_doc = (
        "<html>Item 1A. Risk Factors\n"
        "Cloud market dynamics continue to evolve.\n"
        "Item 1B. Staff Comments None.</html>"
    )

    async def fake_fetch(url: str):
        fetch_calls.append(url)
        if url.startswith("https://data.sec.gov/submissions"):
            return submissions_body
        if "20260331" in url:
            return current_doc
        if "20251231" in url:
            return prior_doc
        return None

    fake_llm = _FakeLLM(SimpleNamespace(content=json.dumps({
        "summary": "Adds AI-workload concentration risk.",
        "changes": [{
            "kind": "added",
            "headline": "Customer concentration in AI workloads",
            "excerpt": "Customer concentration in AI workloads has grown.",
            "materiality": 4,
            "rationale": "Net-new risk factor, not present in prior 10-Q.",
        }],
    })))

    monkeypatch.setattr(web_unlocker, "fetch", fake_fetch)
    import app.agent.llm as llm_mod
    monkeypatch.setattr(llm_mod, "get_llm", lambda: fake_llm)

    result = await FilingModule().execute(
        {"query": "Datadog 10-Q scan", "subject": "Datadog"}
    )

    # EDGAR submissions JSON was fetched.
    assert any(u.startswith("https://data.sec.gov/submissions") for u in fetch_calls)
    # Both current and prior documents were fetched.
    assert any("20260331" in u for u in fetch_calls)
    assert any("20251231" in u for u in fetch_calls)
    # LLM was called exactly once.
    assert len(fake_llm.calls) == 1
    # Live result has the LLM-extracted change.
    assert result.raw_data["mode"] == "live"
    diff = result.raw_data["filing_diff"]
    assert diff["max_materiality"] == 4
    assert any("AI workload" in c["headline"] for c in diff["changes"])


async def test_filing_live_falls_back_to_mock_when_no_filings(monkeypatch):
    """No filings in the recent window → mock fallback so the brief renders."""

    async def fake_fetch(url: str):
        if url.startswith("https://data.sec.gov/submissions"):
            return json.dumps({"filings": {"recent": {
                "form": [], "filingDate": [], "accessionNumber": [],
                "primaryDocument": [], "periodOfReport": [],
            }}})
        return None

    monkeypatch.setattr(web_unlocker, "fetch", fake_fetch)
    result = await FilingModule().execute({"subject": "Datadog"})
    # Mock fixture surfaces — the AI-workload findings from the fixture.
    assert result.raw_data["mode"] == "mock"


async def test_filing_live_no_cik_falls_back_to_mock(monkeypatch):
    """Subjects not in KNOWN_CIKS short-circuit live and use the mock fixture."""

    async def fake_fetch(url: str):
        raise RuntimeError("should never be called for an unknown subject")

    monkeypatch.setattr(web_unlocker, "fetch", fake_fetch)
    result = await FilingModule().execute({"subject": "Linear"})
    assert result.raw_data["mode"] == "mock"


async def test_filing_live_partial_when_llm_returns_no_changes(monkeypatch):
    """Live filings fetched but LLM finds no material delta → partial mode."""

    submissions_body = _fake_submissions_json(
        cik="0001561550",
        items=[
            {"form": "10-Q", "date": "2026-04-30",
             "accession": "0001561550-26-000017",
             "primary": "q1.htm", "period": "2026-03-31"},
            {"form": "10-Q", "date": "2026-02-13",
             "accession": "0001561550-26-000003",
             "primary": "q4.htm", "period": "2025-12-31"},
        ],
    )

    async def fake_fetch(url: str):
        if "submissions" in url:
            return submissions_body
        return "<html>Item 1A. Risk Factors\nBoilerplate.\nItem 1B.</html>"

    fake_llm = _FakeLLM(SimpleNamespace(content=json.dumps({
        "summary": "No material changes.", "changes": [],
    })))

    monkeypatch.setattr(web_unlocker, "fetch", fake_fetch)
    import app.agent.llm as llm_mod
    monkeypatch.setattr(llm_mod, "get_llm", lambda: fake_llm)

    result = await FilingModule().execute({"subject": "Datadog"})
    assert result.raw_data["mode"] == "partial"
    assert result.status == "partial"
    # Honest finding when nothing material is found.
    assert result.findings
    assert result.findings[0].severity == "info"


# ── Filing-type override ────────────────────────────────────────────


async def test_filing_respects_filing_type_param(monkeypatch):
    """When the planner passes ``filing_types=['10-K']``, we diff that form."""

    submissions_body = _fake_submissions_json(
        cik="0001561550",
        items=[
            {"form": "10-Q", "date": "2026-04-30",
             "accession": "a1", "primary": "q.htm", "period": "2026-03-31"},
            {"form": "10-K", "date": "2026-02-25",
             "accession": "k1", "primary": "k1.htm", "period": "2025-12-31"},
            {"form": "10-K", "date": "2025-02-25",
             "accession": "k0", "primary": "k0.htm", "period": "2024-12-31"},
        ],
    )

    seen: list[str] = []

    async def fake_fetch(url: str):
        seen.append(url)
        if "submissions" in url:
            return submissions_body
        return "<html>Item 1A. Risk Factors\nA risk.\nItem 1B.</html>"

    fake_llm = _FakeLLM(SimpleNamespace(content=json.dumps({
        "summary": "", "changes": [{
            "kind": "added", "headline": "X", "excerpt": "e",
            "materiality": 2, "rationale": "r",
        }],
    })))

    monkeypatch.setattr(web_unlocker, "fetch", fake_fetch)
    import app.agent.llm as llm_mod
    monkeypatch.setattr(llm_mod, "get_llm", lambda: fake_llm)

    result = await FilingModule().execute(
        {"subject": "Datadog", "filing_types": ["10-K"]}
    )
    diff = result.raw_data["filing_diff"]
    assert diff["current"]["filing_type"] == "10-K"
    # 10-K documents were fetched, not the 10-Q.
    assert any("k1.htm" in u for u in seen)
    assert all("q.htm" not in u for u in seen)
