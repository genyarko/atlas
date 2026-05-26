"""TruePrice-specific tests — Day 4 acceptance criterion.

Acceptance: TruePrice produces a 3-region comparison table for the
demo target.

These cover the pure data layer (regions, FX, parse_checkout_extract),
the mock path (deterministic 3-region table from the data layer), and
the live path (Scraping Browser stubbed via monkeypatch)."""

from __future__ import annotations

import asyncio

import pytest

from app.brief import render_html, render_markdown
from app.brightdata import scraping_browser
from app.models import Brief, BriefSection, Question, ResearchPlan
from app.modules import MODULES
from app.modules.trueprice import TruePriceModule
from app.modules.trueprice_data import (
    DEMO_REGIONS,
    DE,
    GB,
    US,
    annotate_deltas,
    apply_local_taxes,
    make_quote,
    resolve_regions,
    to_usd,
)
from app.modules.trueprice_targets import (
    LINEAR_TARGET,
    NOTION_TARGET,
    get_target,
    parse_checkout_extract,
)


# ── Pure data layer ─────────────────────────────────────────────────


def test_demo_regions_are_three_with_us_baseline_first():
    assert len(DEMO_REGIONS) == 3
    assert DEMO_REGIONS[0].is_baseline
    codes = [r.code for r in DEMO_REGIONS]
    assert codes == ["US", "GB", "DE"]


def test_resolve_regions_pins_us_first_and_drops_unknown():
    resolved = resolve_regions(["DE", "ZZ", "GB"])
    codes = [r.code for r in resolved]
    assert codes[0] == "US"
    assert "DE" in codes and "GB" in codes
    assert "ZZ" not in codes


def test_resolve_regions_defaults_to_demo_trio_when_empty():
    resolved = resolve_regions(None)
    assert [r.code for r in resolved] == ["US", "GB", "DE"]


@pytest.mark.parametrize("currency,amount,expected", [
    ("USD", 100.0, 100.0),
    ("GBP", 100.0, 126.0),
    ("EUR", 100.0, 108.0),
])
def test_fx_conversion(currency, amount, expected):
    assert to_usd(amount, currency) == expected


def test_apply_local_taxes_gb_adds_20pct_vat():
    true_local, breakdown = apply_local_taxes(100.0, GB)
    assert true_local == 120.0
    kinds = [b["kind"] for b in breakdown]
    assert kinds == ["sticker", "tax", "total"]
    assert breakdown[1]["label"] == "VAT"
    assert breakdown[1]["amount"] == 20.0


def test_apply_local_taxes_us_baseline_no_tax_line():
    true_local, breakdown = apply_local_taxes(8.0, US)
    assert true_local == 8.0
    kinds = [b["kind"] for b in breakdown]
    assert kinds == ["sticker", "total"]  # no tax line at 0%


def test_annotate_deltas_assigns_us_baseline_zero():
    quotes = [
        make_quote(region=US, plan_id="standard", plan_label="Standard",
                   sticker_local=8.0),
        make_quote(region=GB, plan_id="standard", plan_label="Standard",
                   sticker_local=8.0),
        make_quote(region=DE, plan_id="standard", plan_label="Standard",
                   sticker_local=8.0),
    ]
    annotate_deltas(quotes)
    assert quotes[0].delta_pct == 0.0
    assert quotes[1].delta_pct > 0   # +VAT in GBP
    assert quotes[2].delta_pct > 0   # +VAT in EUR + FX


def test_make_quote_infers_source_from_inputs():
    """Synthesizing from sticker → baseline_tax; passing in cart numbers → cart_extract."""
    q1 = make_quote(region=GB, plan_id="standard", plan_label="Standard",
                    sticker_local=8.0)
    assert q1.source == "baseline_tax"

    q2 = make_quote(
        region=GB, plan_id="standard", plan_label="Standard",
        sticker_local=8.0, true_local=9.6,
        breakdown=[{"label": "total", "amount": 9.6, "currency": "GBP", "kind": "total"}],
    )
    assert q2.source == "cart_extract"


# ── Target configs ──────────────────────────────────────────────────


def test_linear_target_has_executable_interaction_script():
    actions = [step["action"] for step in LINEAR_TARGET.interaction_script]
    assert "goto" in actions
    assert "click" in actions
    assert "wait_selector" in actions
    assert "extract" in actions
    # Extract step must list the price fields we depend on downstream.
    extract = next(s for s in LINEAR_TARGET.interaction_script if s["action"] == "extract")
    selectors = extract["selectors"]
    for key in ("list_price", "tax_amount", "total", "currency"):
        assert key in selectors


def test_get_target_returns_pre_validated_configs():
    assert get_target("Linear") is LINEAR_TARGET
    assert get_target("Notion") is NOTION_TARGET


def test_get_target_synthesizes_generic_for_unknown_subject():
    """Unknown subjects get a placeholder config — never Linear's URLs."""
    from app.modules.trueprice_targets import is_pre_validated

    t = get_target("ZZ Unknown")
    assert t.name == "ZZ Unknown"
    assert "linear" not in t.pricing_url.lower()
    # Empty interaction script signals the live path to skip.
    assert t.interaction_script == []
    assert not is_pre_validated("ZZ Unknown")
    assert is_pre_validated("Linear")


def test_parse_checkout_extract_handles_currency_strings():
    parsed = parse_checkout_extract(
        {
            "list_price": "$8.00",
            "tax_amount": "$1.60",
            "total": "$9.60",
            "currency": "USD",
        },
        target=LINEAR_TARGET,
        region=GB,
    )
    assert parsed is not None
    assert parsed["sticker_local"] == 8.0
    assert parsed["true_local"] == 9.6
    kinds = [b["kind"] for b in parsed["breakdown"]]
    assert kinds == ["sticker", "tax", "total"]


def test_parse_checkout_extract_returns_none_when_empty():
    assert parse_checkout_extract({}, target=LINEAR_TARGET, region=GB) is None


# ── Acceptance: 3-region comparison table on demo target ───────────


async def test_trueprice_linear_produces_3_region_table():
    module = MODULES["trueprice"]
    result = await module.run({"query": "TruePrice Linear", "subject": "Linear"})
    assert result.module == "trueprice"
    table = result.raw_data.get("regions")
    assert isinstance(table, list)
    assert len(table) == 3, f"expected 3 regions, got {len(table)}"
    codes = [row["region"] for row in table]
    assert codes == ["US", "GB", "DE"]
    # US is the baseline row
    assert table[0]["delta_pct"] == 0.0
    # Other regions show a positive delta vs US (VAT + FX)
    for row in table[1:]:
        assert row["delta_pct"] > 0, f"{row['region']} should be > US baseline"
    # Findings must cite the source URL
    assert result.findings
    for f in result.findings:
        for url in f.evidence:
            assert url.startswith("http")


async def test_trueprice_linear_headline_finding_mentions_eu_region():
    module = MODULES["trueprice"]
    result = await module.run({"query": "TruePrice Linear", "subject": "Linear"})
    headline = result.findings[0].statement.lower()
    assert any(token in headline for token in ("germany", "united kingdom", "vat"))


async def test_trueprice_brief_section_carries_table_data():
    """The synthesizer must surface the regions table on the BriefSection."""
    from app.modules._fixtures import infer_subject

    module = MODULES["trueprice"]
    r = await module.run({"query": "TruePrice Linear", "subject": "Linear"})
    section = BriefSection(
        module=r.module,
        title="TruePrice",
        summary=r.findings[0].statement,
        findings=r.findings,
        sources=r.sources,
        confidence=r.confidence,
        data=r.raw_data,
    )
    assert section.data["regions"]
    assert section.data["fx_snapshot_date"]


# ── Renderer integration: table must appear in HTML + markdown ─────


def _brief_for_section(section: BriefSection) -> Brief:
    q = Question(text="Run TruePrice on Linear")
    plan = ResearchPlan(question_id=q.id, intent="competitive", modules_to_invoke=[])
    return Brief(
        question=q,
        plan=plan,
        subject="Linear",
        executive_summary="3-region cart comparison.",
        sections=[section],
    )


async def test_html_brief_renders_regions_table():
    r = await MODULES["trueprice"].run({"query": "TruePrice Linear", "subject": "Linear"})
    section = BriefSection(
        module=r.module, title="TruePrice", summary="ok",
        findings=r.findings, sources=r.sources, confidence=r.confidence,
        data=r.raw_data,
    )
    html = render_html(_brief_for_section(section))
    assert "price-table" in html
    assert "United Kingdom" in html
    assert "Germany" in html
    assert "baseline" in html.lower()


async def test_markdown_brief_renders_regions_table():
    r = await MODULES["trueprice"].run({"query": "TruePrice Linear", "subject": "Linear"})
    section = BriefSection(
        module=r.module, title="TruePrice", summary="ok",
        findings=r.findings, sources=r.sources, confidence=r.confidence,
        data=r.raw_data,
    )
    md = render_markdown(_brief_for_section(section))
    assert "| Region | Plan |" in md
    assert "US — United States" in md
    assert "GB — United Kingdom" in md
    assert "DE — Germany" in md


# ── Live path with Scraping Browser stubbed ────────────────────────


async def test_trueprice_live_uses_scraping_browser(monkeypatch):
    """When the Scraping Browser returns extracts, they feed the table verbatim."""

    calls: list[dict] = []

    async def fake_session(*, target_url, region_country, script, locale=None, timeout_ms=45000):
        calls.append({"target_url": target_url, "region": region_country, "script": script})
        # Per-region fake checkout extracts.
        if region_country == "us":
            return {"list_price": "8.00", "tax_amount": "0.00", "total": "8.00",
                    "currency": "USD", "billing_country": "US"}
        if region_country == "gb":
            return {"list_price": "8.00", "tax_amount": "1.60", "total": "9.60",
                    "currency": "GBP", "billing_country": "GB", "tax_label": "VAT"}
        if region_country == "de":
            return {"list_price": "8.00", "tax_amount": "1.52", "total": "9.52",
                    "currency": "EUR", "billing_country": "DE", "tax_label": "VAT"}
        return None

    monkeypatch.setattr(scraping_browser, "checkout_session", fake_session)

    module = TruePriceModule()
    result = await module.execute({"query": "Live", "subject": "Linear"})
    assert result.raw_data["mode"] == "live"
    assert len(calls) == 3
    # Routed through three distinct residential proxies
    assert {c["region"] for c in calls} == {"us", "gb", "de"}
    # The extracts surfaced verbatim into the table
    by_code = {row["region"]: row for row in result.raw_data["regions"]}
    assert by_code["US"]["true_local"] == 8.0
    assert by_code["GB"]["true_local"] == 9.6
    assert by_code["DE"]["true_local"] == 9.52
    # And the script we sent is the Linear interaction script
    sent_script = calls[0]["script"]
    assert any(step["action"] == "extract" for step in sent_script)


async def test_trueprice_live_falls_back_to_mock_when_mcp_unavailable(monkeypatch):
    async def fake_session(*, target_url, region_country, script, locale=None, timeout_ms=45000):
        return None  # MCP returns nothing — treat as unavailable

    monkeypatch.setattr(scraping_browser, "checkout_session", fake_session)

    module = TruePriceModule()
    result = await module.execute({"query": "Live", "subject": "Linear"})
    # Falls back to mock — still produces a 3-region table.
    assert result.raw_data["mode"] == "mock"
    assert len(result.raw_data["regions"]) == 3


async def test_trueprice_live_partial_when_one_region_misses_extract(monkeypatch):
    """DE session runs but extracts nothing → DE row is baseline_tax, mode=partial."""

    async def fake_session(*, target_url, region_country, script, locale=None, timeout_ms=45000):
        if region_country == "us":
            return {"list_price": "8.00", "total": "8.00", "currency": "USD"}
        if region_country == "gb":
            return {"list_price": "8.00", "tax_amount": "1.60", "total": "9.60",
                    "currency": "GBP", "tax_label": "VAT"}
        if region_country == "de":
            return {"billing_country": "DE"}  # no usable price fields
        return None

    monkeypatch.setattr(scraping_browser, "checkout_session", fake_session)

    result = await TruePriceModule().execute({"query": "Live", "subject": "Linear"})
    assert result.raw_data["mode"] == "partial"
    assert result.status == "partial"
    # Two cart extracts (US, GB), one baseline-tax (DE) — no dropped regions.
    assert result.raw_data["cart_extracts"] == 2
    assert result.raw_data["failed_regions"] == []
    by_code = {r["region"]: r for r in result.raw_data["regions"]}
    assert by_code["US"]["source"] == "cart_extract"
    assert by_code["GB"]["source"] == "cart_extract"
    assert by_code["DE"]["source"] == "baseline_tax"


async def test_trueprice_live_partial_when_one_region_drops(monkeypatch):
    """A region that times out is dropped from the table; mode=partial."""

    async def fake_session(*, target_url, region_country, script, locale=None, timeout_ms=45000):
        if region_country == "de":
            raise asyncio.TimeoutError()
        return {"list_price": "8.00", "tax_amount": "0.00", "total": "8.00",
                "currency": region_country.upper()}

    monkeypatch.setattr(scraping_browser, "checkout_session", fake_session)

    result = await TruePriceModule().execute({"query": "Live", "subject": "Linear"})
    assert result.raw_data["mode"] == "partial"
    assert result.status == "partial"
    assert result.raw_data["failed_regions"] == ["DE"]
    codes = [r["region"] for r in result.raw_data["regions"]]
    assert codes == ["US", "GB"]  # DE dropped


async def test_trueprice_live_falls_back_to_mock_when_all_extracts_empty(monkeypatch):
    """If sessions run but no region surfaces usable fields, that's not live."""

    async def fake_session(*, target_url, region_country, script, locale=None, timeout_ms=45000):
        return {"billing_country": region_country.upper()}  # garbage for all regions

    monkeypatch.setattr(scraping_browser, "checkout_session", fake_session)

    result = await TruePriceModule().execute({"query": "Live", "subject": "Linear"})
    assert result.raw_data["mode"] == "mock"
    assert result.raw_data["cart_extracts"] == 0


async def test_trueprice_live_runs_regions_in_parallel(monkeypatch):
    """Per-region sessions must overlap; sequential would multiply total wall time."""
    import time

    started: list[float] = []
    finished: list[float] = []

    async def slow_session(*, target_url, region_country, script, locale=None, timeout_ms=45000):
        started.append(time.perf_counter())
        await asyncio.sleep(0.15)
        finished.append(time.perf_counter())
        return {"list_price": "8.00", "tax_amount": "0.00", "total": "8.00",
                "currency": region_country.upper()}

    monkeypatch.setattr(scraping_browser, "checkout_session", slow_session)

    t0 = time.perf_counter()
    result = await TruePriceModule().execute({"query": "Live", "subject": "Linear"})
    elapsed = time.perf_counter() - t0
    assert len(started) == 3
    # Sequential would be ≥0.45s; parallel should be well under 0.3s even
    # with scheduling slop.
    assert elapsed < 0.3, f"regions ran sequentially (elapsed={elapsed:.3f}s)"
    assert result.raw_data["mode"] == "live"


# ── Findings: target-specific paths ─────────────────────────────────


async def test_trueprice_notion_emits_localized_sticker_finding():
    """Notion lists distinct GBP/EUR stickers — the localized-sticker
    finding should fire (and stay quiet for Linear)."""
    r = await MODULES["trueprice"].run({"query": "TruePrice Notion", "subject": "Notion"})
    statements = " ".join(f.statement.lower() for f in r.findings)
    assert "localized sticker" in statements

    # Same module on Linear (USD-canonical) should NOT emit that finding.
    r2 = await MODULES["trueprice"].run({"query": "TruePrice Linear", "subject": "Linear"})
    s2 = " ".join(f.statement.lower() for f in r2.findings)
    assert "localized sticker" not in s2


# ── Unknown subject: skip live path, lead with caveat ──────────────


async def test_trueprice_unknown_subject_skips_live_and_flags_caveat(monkeypatch):
    """An unknown subject must not spend Scraping Browser sessions; the
    brief must lead with a note that the target isn't pre-validated."""

    calls: list[str] = []

    async def fake_session(*, target_url, region_country, script, locale=None, timeout_ms=45000):
        calls.append(region_country)
        return {"list_price": "8.00", "total": "8.00", "currency": "USD"}

    monkeypatch.setattr(scraping_browser, "checkout_session", fake_session)

    r = await TruePriceModule().execute({"query": "TruePrice ZZZ", "subject": "ZZZ"})
    assert calls == [], f"unknown subject hit Scraping Browser: {calls}"
    assert r.raw_data["mode"] == "mock"
    assert r.raw_data["pre_validated"] is False
    assert r.findings[0].severity == "info"
    assert "not in" in r.findings[0].statement.lower() and "pre-validated" in r.findings[0].statement.lower()
    # Pricing URL must not be misattributed to Linear's domain.
    assert all("linear.app" not in s.url for s in r.sources)


# ── plan_tier fallback ─────────────────────────────────────────────


async def test_trueprice_unknown_plan_tier_falls_back_to_default():
    r = await MODULES["trueprice"].run({
        "query": "TruePrice Linear", "subject": "Linear",
        "plan_tier": "enterprise-bogus",
    })
    # Falls back to Linear's default plan instead of crashing.
    assert r.raw_data["plan_id"] == "standard"
    assert len(r.raw_data["regions"]) == 3
