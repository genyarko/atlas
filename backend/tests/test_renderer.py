"""Renderer unit tests.

Cover the HTML, Markdown, and chart-spec layers directly so a template
regression or a math error in the bar/meter geometry surfaces here
instead of in the demo. PDF rendering is exercised only behind a
guard — xhtml2pdf is an optional dep and the test must still pass
without it installed.
"""

from __future__ import annotations

import pytest

from app.brief import chart_specs
from app.brief.renderer import (
    _build_chart_specs,
    _severity_class,
    _severity_label,
    render_html,
    render_markdown,
    render_pdf,
)
from app.models import (
    Brief,
    BriefSection,
    Finding,
    ModuleInvocation,
    Question,
    ResearchPlan,
    Source,
)


# ── Brief fixture ─────────────────────────────────────────────────────


def _brief(*, sections=None, key_findings=None, subject="Linear") -> Brief:
    question = Question(text="Run a competitive brief on Linear")
    plan = ResearchPlan(
        question_id=question.id,
        intent="competitive",
        modules_to_invoke=[
            ModuleInvocation(module="trueprice", params={"subject": subject}),
        ],
    )
    return Brief(
        question=question,
        plan=plan,
        subject=subject,
        executive_summary=f"{subject} shows pricing arbitrage across regions.",
        key_findings=key_findings or [
            Finding(statement="DE pays +23% over US baseline", severity="high"),
        ],
        sections=sections or [
            BriefSection(
                module="trueprice",
                title="TruePrice — Geo Pricing",
                summary="DE pays $9.84 vs $8.00 US.",
                findings=[
                    Finding(statement="DE +23% delta", severity="high"),
                ],
                sources=[
                    Source(url="https://linear.app/pricing", title="Pricing", via="scraping_browser"),
                ],
                confidence=0.85,
                data={
                    "regions": [
                        {"region": "US", "region_name": "United States", "plan": "Standard",
                         "sticker_local": 8.0, "true_local": 8.0, "true_usd": 8.0,
                         "delta_pct": 0.0, "currency": "USD", "notes": "baseline"},
                        {"region": "DE", "region_name": "Germany", "plan": "Standard",
                         "sticker_local": 8.0, "true_local": 9.84, "true_usd": 9.84,
                         "delta_pct": 23.0, "currency": "EUR", "notes": "VAT"},
                    ],
                    "fx_snapshot_date": "2026-05-20",
                    "mode": "mock",
                },
            )
        ],
        confidence_score=0.78,
    )


# ── Severity filters ──────────────────────────────────────────────────


def test_severity_class_maps_to_css_class():
    assert _severity_class("critical") == "sev-critical"
    assert _severity_class("info") == "sev-info"


def test_severity_label_upcases():
    assert _severity_label("high") == "HIGH"


# ── render_html ───────────────────────────────────────────────────────


def test_render_html_emits_doctype_and_subject():
    html = render_html(_brief())
    low = html.lower()
    assert "<html" in low
    assert "Linear" in html


def test_render_html_includes_executive_summary():
    html = render_html(_brief())
    assert "shows pricing arbitrage" in html


def test_render_html_includes_section_title_and_finding():
    html = render_html(_brief())
    assert "TruePrice" in html
    assert "+23" in html or "23%" in html


def test_render_html_severity_class_appears():
    html = render_html(_brief())
    assert "sev-high" in html


def test_render_html_escapes_user_input():
    # Subject is injected; ensure Jinja autoescape catches an HTML payload.
    brief = _brief(subject="<script>alert(1)</script>")
    html = render_html(brief)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# ── render_markdown ───────────────────────────────────────────────────


def test_render_markdown_has_brief_header():
    md = render_markdown(_brief())
    assert "# Atlas — Ground Truth Brief" in md
    assert "**Subject:** Linear" in md


def test_render_markdown_includes_key_findings_section():
    md = render_markdown(_brief())
    assert "## Key Findings" in md
    assert "[HIGH]" in md


def test_render_markdown_emits_trueprice_table():
    md = render_markdown(_brief())
    # Header row of the price table
    assert "| Region | Plan |" in md
    # Body row contains a delta cell with the formatted sign.
    assert "+23.0%" in md or "+23.0" in md
    # Sources block
    assert "**Sources**" in md
    assert "scraping_browser" in md


def test_render_markdown_visual_table_includes_verdict():
    section = BriefSection(
        module="visual",
        title="Visual",
        summary="Suspect detected",
        findings=[Finding(statement="One suspect", severity="high")],
        data={
            "suspects": [
                {"suspect_url": "https://lookalike.example",
                 "verdict": "high", "similarity": 0.87, "anomaly_count": 4,
                 "anomalies": [{"description": "logo color mismatch"}]},
            ],
            "mode": "mock",
        },
        confidence=0.8,
    )
    md = render_markdown(_brief(sections=[section]))
    assert "| Suspect | Verdict |" in md
    assert "HIGH" in md
    assert "0.87" in md
    assert "logo color mismatch" in md


# ── _build_chart_specs ────────────────────────────────────────────────


def test_chart_specs_emits_trueprice_when_regions_present():
    specs = _build_chart_specs(_brief())
    assert "trueprice" in specs
    bars = specs["trueprice"]["delta_bars"]["bars"]
    assert len(bars) == 2
    de_bar = next(b for b in bars if b["label"] == "DE")
    assert de_bar["polarity"] == "up"
    assert de_bar["delta"] == 23.0
    us_bar = next(b for b in bars if b["label"] == "US")
    assert us_bar["is_baseline"] is True


def test_chart_specs_signal_family_bars_sorted_desc():
    spec = chart_specs.signal_family_bars({"gtm": 3, "eng": 9, "ops": 1})
    labels = [b["label"] for b in spec["bars"]]
    assert labels == ["eng", "gtm", "ops"]
    # Top bar fills the full track.
    assert spec["bars"][0]["width"] == spec["track"]


def test_chart_specs_altdata_meter_clamps_score():
    low = chart_specs.altdata_score_meter(-0.5)
    high = chart_specs.altdata_score_meter(2.0)
    assert low["score"] == 0.0
    assert high["score"] == 1.0
    mid = chart_specs.altdata_score_meter(0.5)
    assert mid["pct"] == 50


def test_chart_specs_trueprice_handles_empty_regions():
    spec = chart_specs.trueprice_delta_bars([])
    assert spec["bars"] == []


# ── render_pdf ────────────────────────────────────────────────────────


def test_render_pdf_returns_bytes_or_raises_runtime_error():
    """xhtml2pdf is an optional dep — accept either a PDF byte string
    or the documented RuntimeError if the pipeline isn't installed."""
    try:
        out = render_pdf(_brief())
    except RuntimeError as exc:
        # Acceptable when the optional [pdf] extra isn't installed.
        assert "xhtml2pdf" in str(exc) or "PDF pipeline" in str(exc)
        pytest.skip("PDF extra not installed")
    else:
        assert isinstance(out, bytes)
        assert out.startswith(b"%PDF")
