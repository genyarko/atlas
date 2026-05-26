"""Exposure-module-specific tests — Day 7 acceptance criterion.

Acceptance: "Exposure flags your controlled credential leak."

Coverage:
  * Pure data layer — dork templates, host classifier, severity coercion,
    controlled-target catalog, credential-shape pre-flight regex.
  * Mock path — AcmeCorp controlled target surfaces ≥1 critical finding
    pointing at the paste-bin URL.
  * Live path — SERP + Web Unlocker + LLM stubbed via monkeypatch.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.brightdata import serp, web_unlocker
from app.modules import MODULES
from app.modules.exposure import (
    ExposureModule,
    _parse_llm_extract,
    _resolve_mode,
)
from app.modules.exposure_data import (
    ACMECORP_EXPOSURE_TARGET,
    ACMECORP_GITHUB_LEAK,
    ACMECORP_PASTEBIN_LEAK,
    BREACH_HOSTS,
    CODE_HOSTS,
    CONTROLLED_TARGETS,
    DEFAULT_DORKS,
    PASTE_HOSTS,
    SerpCandidate,
    build_dorks,
    classify_channel,
    coerce_channel,
    coerce_kind,
    coerce_severity,
    filter_candidates,
    get_controlled_target,
    has_credential_shape,
    severity_rank,
)


# ── Pure data layer — controlled-target catalog ────────────────────


def test_controlled_target_catalog_has_acmecorp_with_two_leaks():
    """Day-7 deliverable: controlled paste-bin entry created for demo."""
    assert "AcmeCorp" in CONTROLLED_TARGETS
    target = CONTROLLED_TARGETS["AcmeCorp"]
    assert len(target.leaks) == 2
    slugs = {leak.slug for leak in target.leaks}
    assert slugs == {
        "acmecorp-pastebin-ci-bootstrap",
        "acmecorp-github-seed-env",
    }


def test_controlled_pastebin_carries_critical_credential_record():
    """The paste leak is the headline — must have ≥1 critical credential."""
    severities = {r.severity for r in ACMECORP_PASTEBIN_LEAK.records}
    assert "critical" in severities
    kinds = {r.kind for r in ACMECORP_PASTEBIN_LEAK.records}
    assert "credential" in kinds
    assert "api_key" in kinds


def test_controlled_pastebin_text_file_exists_on_disk():
    """The paste-bin text file must be tracked alongside the catalog."""
    assert ACMECORP_PASTEBIN_LEAK.text_path.exists(), (
        f"missing controlled paste-bin file: {ACMECORP_PASTEBIN_LEAK.text_path}"
    )
    text = ACMECORP_PASTEBIN_LEAK.text_path.read_text(encoding="utf-8")
    # Pre-flight regex must trip on it — same gate the live path uses.
    assert has_credential_shape(text), (
        "controlled paste-bin should look credential-shaped to the live pipeline"
    )


def test_controlled_github_text_file_exists_on_disk():
    assert ACMECORP_GITHUB_LEAK.text_path.exists()
    text = ACMECORP_GITHUB_LEAK.text_path.read_text(encoding="utf-8")
    assert has_credential_shape(text)


def test_controlled_target_domain_is_reserved_tld():
    """acmecorp-demo.test sits under the RFC 6761 reserved `.test` TLD —
    must never resolve to a real host even if someone accidentally curls it."""
    assert ACMECORP_EXPOSURE_TARGET.domain.endswith(".test")


# ── Pure data layer — dork templates ────────────────────────────────


def test_build_dorks_fills_domain_placeholder():
    dorks = build_dorks(domain="acmecorp-demo.test")
    assert dorks  # non-empty
    assert all("acmecorp-demo.test" in d or "{domain}" not in d for d in dorks)
    # Specific dorks the implementation plan §4.6 calls out.
    assert any("site:pastebin.com" in d for d in dorks)
    assert any("site:github.com" in d for d in dorks)


def test_build_dorks_supports_custom_templates():
    dorks = build_dorks(
        domain="acmecorp-demo.test",
        custom=['site:rentry.co "{domain}"', "no-placeholder query"],
    )
    assert dorks == [
        'site:rentry.co "acmecorp-demo.test"',
        "no-placeholder query",
    ]


def test_default_dorks_cover_paste_code_and_keyword_channels():
    """The default dork set must touch all three discovery channels."""
    joined = " ".join(DEFAULT_DORKS)
    assert "pastebin.com" in joined
    assert "github.com" in joined
    # Keyword channel — generic credential dorks not tied to a host.
    assert any("password" in d.lower() or "credentials" in d.lower() for d in DEFAULT_DORKS)


# ── Pure data layer — host classifier ───────────────────────────────


@pytest.mark.parametrize("url,expected", [
    ("https://pastebin.com/raw/abc123", "paste"),
    ("https://ghostbin.co/x", "paste"),
    ("https://github.com/acmecorp/repo", "code"),
    ("https://gist.github.com/abc/def", "code"),
    ("https://raw.githubusercontent.com/acmecorp/repo/main/seed.env", "code"),
    ("https://gitlab.com/acmecorp/proj", "code"),
    ("https://haveibeenpwned.com/account/foo", "breach"),
    ("https://dehashed.com/search", "breach"),
    ("https://example.com/random", None),       # unknown host → None
    ("not a url", None),                         # unparseable → None
])
def test_classify_channel(url, expected):
    assert classify_channel(url) == expected


def test_paste_code_breach_sets_are_disjoint():
    """A host can't belong to two channels at once — classifier needs unambiguity."""
    assert not (PASTE_HOSTS & CODE_HOSTS)
    assert not (PASTE_HOSTS & BREACH_HOSTS)
    assert not (CODE_HOSTS & BREACH_HOSTS)


# ── Pure data layer — SERP row filtering ────────────────────────────


def test_filter_candidates_keeps_known_channels_only():
    rows = [
        {"link": "https://pastebin.com/raw/aaa", "title": "paste 1",
         "snippet": "ACMECORP password leak"},
        {"link": "https://github.com/x/y", "title": "code"},
        {"link": "https://example.com/random", "title": "junk"},  # unknown host
        {"link": "", "title": "empty url"},
        "not a dict",
    ]
    candidates = filter_candidates(rows, discovery_query="probe")
    urls = [c.url for c in candidates]
    assert urls == [
        "https://pastebin.com/raw/aaa",
        "https://github.com/x/y",
    ]
    assert candidates[0].channel == "paste"
    assert candidates[0].snippet == "ACMECORP password leak"
    assert candidates[1].channel == "code"


def test_filter_candidates_dedupes_by_url():
    rows = [
        {"link": "https://pastebin.com/raw/a", "title": "one"},
        {"link": "https://pastebin.com/raw/a", "title": "duplicate"},
    ]
    assert len(filter_candidates(rows)) == 1


def test_filter_candidates_carries_discovery_query():
    rows = [
        {"link": "https://pastebin.com/raw/a", "title": "x", "_query": 'site:pastebin.com "acme"'},
        {"link": "https://github.com/x/y", "title": "z"},
    ]
    candidates = filter_candidates(rows, discovery_query="fallback")
    by_url = {c.url: c.discovery_query for c in candidates}
    assert by_url["https://pastebin.com/raw/a"] == 'site:pastebin.com "acme"'
    assert by_url["https://github.com/x/y"] == "fallback"


# ── Pure data layer — credential-shape pre-flight ───────────────────


@pytest.mark.parametrize("text,expected", [
    ("password=Hunter2-staging-bootstrap-2026", True),
    ("PASSWD: longenough123", True),
    ("api_key: acmecorp_pat_live_4f9c8e1a2b3d5f6e7a8b9c0d1e2f3a4b", True),
    ("API_KEY=" + ("x" * 32), True),
    ("https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXX", True),
    ("acmecorp_pat_live_4f9c8e1a2b3d5f6e7a8b9c0d1e2f3a4b", True),
    ("just a brand mention with no creds", False),
    ("password=short", False),                       # below the 6-char body bar
    ("", False),
])
def test_has_credential_shape(text, expected):
    assert has_credential_shape(text) is expected


# ── Pure data layer — coercion helpers (LLM output sanitization) ────


@pytest.mark.parametrize("raw,expected", [
    ("critical", "critical"),
    ("HIGH", "high"),
    (" notable ", "notable"),
    ("info", "info"),
    ("low", "info"),
    ("medium", "notable"),
    ("moderate", "notable"),
    ("severe", "high"),
    ("garbage", "notable"),                          # default
    (None, "notable"),
])
def test_coerce_severity(raw, expected):
    assert coerce_severity(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("credential", "credential"),
    ("credentials", "credential"),
    ("password", "credential"),
    ("api_key", "api_key"),
    ("token", "api_key"),
    ("PAT", "api_key"),
    ("webhook", "webhook"),
    ("slack_webhook", "webhook"),
    ("pii", "pii"),
    ("email", "pii"),
    ("infra", "infra"),
    ("hostname", "infra"),
    ("garbage", "mention"),                          # default
])
def test_coerce_kind(raw, expected):
    assert coerce_kind(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("paste", "paste"),
    ("code", "code"),
    ("breach", "breach"),
    ("doxx", "doxx"),
    ("github", "code"),
    ("source_code", "code"),
    ("pastebin", "paste"),
    ("garbage", "paste"),                            # default
])
def test_coerce_channel(raw, expected):
    assert coerce_channel(raw) == expected


@pytest.mark.parametrize("severity,rank", [
    ("info", 1), ("notable", 2), ("high", 3), ("critical", 4),
])
def test_severity_rank_ordering(severity, rank):
    assert severity_rank(severity) == rank


# ── LLM extract parser (trust boundary with Claude) ────────────────


def _candidate(url: str = "https://pastebin.com/raw/aaa") -> SerpCandidate:
    return SerpCandidate(
        url=url, title="paste", snippet="snippet", channel="paste",
        discovery_query='site:pastebin.com "acme"',
    )


def test_parse_llm_extract_happy_path():
    payload = json.dumps({
        "summary": "One credential pair, one PAT.",
        "records": [
            {"kind": "credential", "severity": "critical",
             "excerpt": "DB password leaked", "rationale": "rotate immediately"},
            {"kind": "api_key", "severity": "high",
             "excerpt": "PAT token", "rationale": "revoke"},
        ],
    })
    records = _parse_llm_extract(payload, candidate=_candidate())
    assert records is not None
    assert len(records) == 2
    assert records[0].kind == "credential"
    assert records[0].severity == "critical"
    assert records[0].channel == "paste"
    assert records[0].via == "web_unlocker"
    assert records[1].kind == "api_key"


def test_parse_llm_extract_strips_markdown_fences():
    fenced = "```json\n" + json.dumps({
        "summary": "x",
        "records": [{"kind": "credential", "severity": "high",
                     "excerpt": "e", "rationale": "r"}],
    }) + "\n```"
    records = _parse_llm_extract(fenced, candidate=_candidate())
    assert records is not None
    assert len(records) == 1


def test_parse_llm_extract_empty_records_is_valid_signal():
    """Structurally-valid but empty extraction means 'page exists, no leak'."""
    payload = json.dumps({"summary": "no leak", "records": []})
    records = _parse_llm_extract(payload, candidate=_candidate())
    assert records == []


def test_parse_llm_extract_drops_records_without_excerpt():
    payload = json.dumps({
        "summary": "",
        "records": [
            {"kind": "credential", "severity": "critical", "excerpt": "", "rationale": "r"},
            {"kind": "credential", "severity": "critical", "rationale": "r"},   # missing excerpt
            {"kind": "credential", "severity": "high", "excerpt": "ok", "rationale": "r"},
        ],
    })
    records = _parse_llm_extract(payload, candidate=_candidate())
    assert records is not None
    assert len(records) == 1
    assert records[0].excerpt == "ok"


def test_parse_llm_extract_clamps_excerpt_length():
    long_excerpt = "x" * 500
    payload = json.dumps({
        "summary": "",
        "records": [{"kind": "credential", "severity": "high",
                     "excerpt": long_excerpt, "rationale": "r"}],
    })
    records = _parse_llm_extract(payload, candidate=_candidate())
    assert records is not None
    assert len(records[0].excerpt) <= 200


def test_parse_llm_extract_assigns_via_by_channel():
    """Code-channel records get via=serp_api; paste/breach → web_unlocker."""
    payload = json.dumps({
        "records": [{"kind": "api_key", "severity": "high",
                     "excerpt": "token", "rationale": "r"}],
    })
    code_records = _parse_llm_extract(payload, candidate=SerpCandidate(
        url="https://github.com/x/y", title="t", snippet="",
        channel="code", discovery_query="q",
    ))
    paste_records = _parse_llm_extract(payload, candidate=_candidate())
    assert code_records is not None
    assert paste_records is not None
    assert code_records[0].via == "serp_api"
    assert paste_records[0].via == "web_unlocker"


@pytest.mark.parametrize("payload", [
    "not json",
    "{ invalid",
    "[1, 2]",                                       # list at top level → None
])
def test_parse_llm_extract_returns_none_on_malformed(payload):
    assert _parse_llm_extract(payload, candidate=_candidate()) is None


def test_parse_llm_extract_returns_empty_when_records_not_a_list():
    """``records`` field present but wrong type — treated as empty extraction."""
    payload = json.dumps({"records": "not a list"})
    assert _parse_llm_extract(payload, candidate=_candidate()) == []


# ── Mode resolver ──────────────────────────────────────────────────


def test_resolve_mode_live_when_only_live_records():
    mode = _resolve_mode(
        live_records=[_pasted_record()], controlled_records=[], dropped=[],
    )
    assert mode == "live"


def test_resolve_mode_partial_when_some_controlled_records_present():
    mode = _resolve_mode(
        live_records=[_pasted_record()],
        controlled_records=[_pasted_record(url="https://example.com")],
        dropped=[],
    )
    assert mode == "partial"


def test_resolve_mode_partial_when_some_dropped():
    mode = _resolve_mode(
        live_records=[_pasted_record()],
        controlled_records=[],
        dropped=["https://x.test/y"],
    )
    assert mode == "partial"


def test_resolve_mode_mock_when_no_live_records():
    mode = _resolve_mode(
        live_records=[], controlled_records=[_pasted_record()], dropped=[],
    )
    assert mode == "mock"


def _pasted_record(url: str = "https://pastebin.com/raw/aaa"):
    """Tiny LeakRecord factory for mode-resolver tests."""
    from app.modules.exposure_data import LeakRecord
    return LeakRecord(
        channel="paste", kind="credential", severity="critical",
        location_url=url, location_title="t", excerpt="e",
        rationale="r", via="web_unlocker",
    )


# ── Mock acceptance: AcmeCorp flags the controlled leak ────────────


async def test_exposure_acmecorp_meets_day7_acceptance():
    """Day-7 acceptance: 'Exposure flags your controlled credential leak'.

    The mock path must surface at least one critical finding pointing at
    the controlled paste-bin URL, with concrete credential rationale."""
    result = await MODULES["exposure"].run(
        {"query": "Scan AcmeCorp for exposure", "subject": "AcmeCorp"},
    )
    assert result.module == "exposure"
    assert result.findings
    severities = {f.severity for f in result.findings}
    assert "critical" in severities, (
        f"expected ≥1 critical finding, got severities {severities}"
    )
    # Headline finding cites the controlled paste-bin URL.
    headline = result.findings[0]
    assert headline.severity == "critical"
    assert ACMECORP_PASTEBIN_LEAK.claimed_url in headline.evidence


async def test_exposure_acmecorp_raw_data_shape():
    """Renderer needs the full exposure_scan structure in raw_data."""
    result = await MODULES["exposure"].run({"subject": "AcmeCorp"})
    rd = result.raw_data
    assert rd["subject"] == "AcmeCorp"
    assert rd["domain"] == "acmecorp-demo.test"
    assert rd["max_severity"] == "critical"
    assert rd["critical_count"] >= 1
    # Both controlled channels (paste + code) surface.
    assert set(rd["channels"]) == {"paste", "code"}
    scan = rd["exposure_scan"]
    assert scan["record_count"] >= 4               # paste leak has 4 records
    assert scan["records"]


async def test_exposure_acmecorp_aggregate_finding_fires_when_multiple_critical():
    """≥2 critical hits → emit the coordinated-exposure aggregator."""
    result = await MODULES["exposure"].run({"subject": "AcmeCorp"})
    aggregate = [
        f for f in result.findings if "coordinated exposure" in f.statement.lower()
    ]
    if result.raw_data["critical_count"] >= 2:
        assert aggregate, (
            "aggregate coordinated-exposure finding should fire when ≥2 criticals"
        )
        assert aggregate[0].severity == "critical"


async def test_exposure_acmecorp_sources_attribute_via_bright_data_tools():
    """Implementation plan §4.6 calls Web Unlocker + SERP API.

    Paste-site records should be via=web_unlocker (the page-fetch tool),
    code-search records should be via=serp_api (the discovery tool)."""
    result = await MODULES["exposure"].run({"subject": "AcmeCorp"})
    by_via = {s.via for s in result.sources}
    assert "web_unlocker" in by_via
    assert "serp_api" in by_via


async def test_exposure_acmecorp_finding_calls_out_pastebin_specifically():
    result = await MODULES["exposure"].run({"subject": "AcmeCorp"})
    text = " ".join(f.statement for f in result.findings).lower()
    assert "pastebin.com" in text or "paste-site" in text or "paste site" in text


async def test_exposure_unknown_subject_falls_back_to_synthetic_records():
    """No controlled target → still produces shape-bearing findings.

    Synthetic records are capped at 'notable' severity; we never invent
    a critical leak for a brand we have no real signal on."""
    result = await MODULES["exposure"].run(
        {"query": "exposure scan for SomeUnknownBrand", "subject": "SomeUnknownBrand"}
    )
    assert result.findings
    severities = {f.severity for f in result.findings}
    assert "critical" not in severities


# ── Live path stubbed via monkeypatch ──────────────────────────────


class _FakeLLM:
    def __init__(self, responses: list) -> None:
        # List of SimpleNamespace(content=...) returned one per call.
        self.responses = list(responses)
        self.calls: list[list] = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if not self.responses:
            raise RuntimeError("no more fake LLM responses")
        return self.responses.pop(0)


async def test_exposure_live_uses_serp_unlocker_and_llm(monkeypatch):
    """End-to-end live path: SERP dorks → Web Unlocker → LLM extraction."""

    serp_calls: list[str] = []
    fetch_calls: list[str] = []

    async def fake_search(query, *, num=10):
        serp_calls.append(query)
        # Only the pastebin dork returns a hit; the rest empty so we
        # focus on the live extraction path for one candidate.
        if "pastebin.com" in query:
            return [{
                "link": "https://pastebin.com/raw/live-1",
                "title": "anonymous paste",
                "snippet": "ACMECORP_DB_PASSWORD=Hunter2-live-shape-2026",
                "_query": query,
            }]
        return []

    async def fake_fetch(url):
        fetch_calls.append(url)
        if "pastebin.com/raw/live-1" in url:
            return (
                "ACMECORP_DB_USER=ci-bootstrap\n"
                "ACMECORP_DB_PASSWORD=Hunter2-live-shape-2026\n"
                "ACMECORP_CI_TOKEN=acmecorp_pat_live_4f9c8e1a2b3d5f6e7a8b9c0d1e2f3a4b\n"
            )
        return None

    fake_llm = _FakeLLM([SimpleNamespace(content=json.dumps({
        "summary": "Live-shaped DB credential and CI PAT.",
        "records": [
            {"kind": "credential", "severity": "critical",
             "excerpt": "ACMECORP_DB_PASSWORD=Hunt…",
             "rationale": "Live-shaped DB password — rotate immediately."},
            {"kind": "api_key", "severity": "critical",
             "excerpt": "ACMECORP_CI_TOKEN=acmecorp_pat…",
             "rationale": "PAT with `_live_` channel — revoke at issuer."},
        ],
    }))])

    monkeypatch.setattr(serp, "search", fake_search)
    monkeypatch.setattr(web_unlocker, "fetch", fake_fetch)
    import app.agent.llm as llm_mod
    monkeypatch.setattr(llm_mod, "get_llm", lambda: fake_llm)

    result = await ExposureModule().execute(
        {"query": "AcmeCorp exposure", "subject": "AcmeCorp"}
    )

    # SERP fan-out happened.
    assert any("pastebin.com" in q for q in serp_calls)
    # Web Unlocker fetched the live candidate.
    assert any("pastebin.com/raw/live-1" in u for u in fetch_calls)
    # LLM was called exactly once (one candidate).
    assert len(fake_llm.calls) == 1
    # Live records merged with controlled records → mode=partial.
    assert result.raw_data["mode"] == "partial"
    # The live URL appears alongside the controlled paste URL.
    scan_records = result.raw_data["exposure_scan"]["records"]
    location_urls = {r["location_url"] for r in scan_records}
    assert "https://pastebin.com/raw/live-1" in location_urls
    assert ACMECORP_PASTEBIN_LEAK.claimed_url in location_urls


async def test_exposure_live_falls_back_to_mock_when_no_candidates(monkeypatch):
    """SERP returns nothing for an unknown brand → mock fallback."""

    async def fake_search(query, *, num=10):
        return []

    async def fake_fetch(url):
        raise RuntimeError("should not be called when SERP returns nothing")

    monkeypatch.setattr(serp, "search", fake_search)
    monkeypatch.setattr(web_unlocker, "fetch", fake_fetch)

    result = await ExposureModule().execute(
        {"query": "scan UnknownBrand", "subject": "UnknownBrand"}
    )
    # Unknown subject + no candidates → mock fallback with synthetic records.
    assert result.raw_data["mode"] == "mock"
    assert result.findings


async def test_exposure_live_skips_llm_when_no_credential_shape(monkeypatch):
    """A candidate whose body has no credential shape skips the LLM
    extraction call entirely — the cheap pre-flight guard."""

    async def fake_search(query, *, num=10):
        if "pastebin.com" in query:
            return [{
                "link": "https://pastebin.com/raw/clean-page",
                "title": "no creds here",
                "snippet": "just a brand mention",
            }]
        return []

    async def fake_fetch(url):
        return "This page just mentions AcmeCorp in passing. No secrets."

    fake_llm = _FakeLLM([])  # zero responses — must not be invoked

    monkeypatch.setattr(serp, "search", fake_search)
    monkeypatch.setattr(web_unlocker, "fetch", fake_fetch)
    import app.agent.llm as llm_mod
    monkeypatch.setattr(llm_mod, "get_llm", lambda: fake_llm)

    result = await ExposureModule().execute({"subject": "AcmeCorp"})
    # LLM never called (zero responses pre-loaded → would have raised).
    assert fake_llm.calls == []
    # Controlled fixtures still surface; mode stays mock because no live
    # extractions succeeded (the page existed but held no leak).
    assert result.raw_data["mode"] == "mock"
    assert result.raw_data["critical_count"] >= 1   # controlled paste still there


async def test_exposure_live_drops_candidates_when_llm_unparseable(monkeypatch):
    """LLM returns garbage → candidate ends up in the dropped list."""

    async def fake_search(query, *, num=10):
        if "pastebin.com" in query:
            return [{
                "link": "https://pastebin.com/raw/llm-broken",
                "snippet": "ACMECORP_API_KEY=" + ("x" * 32),
            }]
        return []

    async def fake_fetch(url):
        return "ACMECORP_API_KEY=" + ("x" * 32)

    fake_llm = _FakeLLM([SimpleNamespace(content="not json at all")])

    monkeypatch.setattr(serp, "search", fake_search)
    monkeypatch.setattr(web_unlocker, "fetch", fake_fetch)
    import app.agent.llm as llm_mod
    monkeypatch.setattr(llm_mod, "get_llm", lambda: fake_llm)

    result = await ExposureModule().execute({"subject": "AcmeCorp"})
    dropped = result.raw_data["exposure_scan"]["dropped"]
    assert "https://pastebin.com/raw/llm-broken" in dropped


async def test_exposure_live_unescapes_html_entities_before_preflight(monkeypatch):
    """Code-search hosts often render credential lines as HTML-encoded text
    (e.g. ``&lt;br&gt;ACME_API_KEY=...``). The strip-HTML helper must
    round-trip through ``html.unescape`` or the pre-flight regex misses
    those rows and the LLM never gets called."""

    encoded_body = (
        "<pre>&lt;br&gt;ACMECORP_CI_TOKEN="
        "acmecorp_pat_live_4f9c8e1a2b3d5f6e7a8b9c0d1e2f3a4b&lt;/br&gt;</pre>"
    )

    async def fake_search(query, *, num=10):
        if "github.com" in query:
            return [{
                "link": "https://github.com/x/y",
                "title": "code",
                "snippet": "no creds in snippet itself",
            }]
        return []

    async def fake_fetch(url):
        return encoded_body

    fake_llm = _FakeLLM([SimpleNamespace(content=json.dumps({
        "records": [{"kind": "api_key", "severity": "critical",
                     "excerpt": "ACMECORP_CI_TOKEN=acmecorp_pat…",
                     "rationale": "PAT exposed in committed code."}],
    }))])

    monkeypatch.setattr(serp, "search", fake_search)
    monkeypatch.setattr(web_unlocker, "fetch", fake_fetch)
    import app.agent.llm as llm_mod
    monkeypatch.setattr(llm_mod, "get_llm", lambda: fake_llm)

    result = await ExposureModule().execute({"subject": "AcmeCorp"})
    # LLM was invoked because the unescaped body matched the pre-flight regex.
    assert len(fake_llm.calls) == 1
    scan_records = result.raw_data["exposure_scan"]["records"]
    assert any(r["location_url"] == "https://github.com/x/y" for r in scan_records)


async def test_exposure_live_dedupes_live_against_controlled(monkeypatch):
    """If the live extraction happens to land on the same URL as a
    controlled leak, controlled is suppressed for that URL — no double-counting."""

    controlled_url = ACMECORP_PASTEBIN_LEAK.claimed_url

    async def fake_search(query, *, num=10):
        if "pastebin.com" in query:
            return [{
                "link": controlled_url,
                "snippet": "ACMECORP_DB_PASSWORD=live-shape-2026",
            }]
        return []

    async def fake_fetch(url):
        return "ACMECORP_DB_PASSWORD=live-shape-2026"

    fake_llm = _FakeLLM([SimpleNamespace(content=json.dumps({
        "records": [{"kind": "credential", "severity": "critical",
                     "excerpt": "ACMECORP_DB_PASSWORD=live…",
                     "rationale": "Live extraction of the same URL."}],
    }))])

    monkeypatch.setattr(serp, "search", fake_search)
    monkeypatch.setattr(web_unlocker, "fetch", fake_fetch)
    import app.agent.llm as llm_mod
    monkeypatch.setattr(llm_mod, "get_llm", lambda: fake_llm)

    result = await ExposureModule().execute({"subject": "AcmeCorp"})
    scan_records = result.raw_data["exposure_scan"]["records"]
    # The paste-bin URL appears, but NOT both as a live record AND as the
    # full set of declared paste records — the four declared paste records
    # should be suppressed since the live extraction owns the URL.
    paste_url_records = [
        r for r in scan_records if r["location_url"] == controlled_url
    ]
    # Exactly the one live record (no controlled paste records re-added).
    assert len(paste_url_records) == 1
    assert paste_url_records[0]["rationale"].startswith("Live extraction")
