"""Synthesizer unit tests.

Pins the pure pieces that the smoke test exercises only implicitly:
  • _module_evidence — formats the right metric snapshot per module
  • _top_findings    — sorts by severity, respects limit
  • _composite_confidence — averages non-failed module scores
  • _findings_prompt_block — produces the metrics-laced LLM prompt block
  • _template_summary — fallback prose composition

The smoke test already covers the async ``synthesize_node`` end-to-end.
"""

from __future__ import annotations

from app.agent.synthesizer import (
    _build_sections,
    _composite_confidence,
    _findings_prompt_block,
    _module_evidence,
    _template_summary,
    _top_findings,
)
from app.models import Finding, ModuleResult


def _result(module, *, findings=None, confidence=0.7, status="success", raw_data=None):
    return ModuleResult(
        module=module,
        status=status,
        confidence=confidence,
        findings=findings or [],
        raw_data=raw_data or {},
    )


# ── _module_evidence ──────────────────────────────────────────────────


def test_evidence_trueprice_emits_plan_and_delta():
    r = _result("trueprice", raw_data={
        "plan_label": "Standard",
        "cart_extracts": 3,
        "mode": "live",
        "regions": [
            {"region": "US", "true_usd": 8.00, "delta_pct": 0.0},
            {"region": "DE", "true_usd": 9.84, "delta_pct": 23.0},
            {"region": "GB", "true_usd": 9.60, "delta_pct": 20.0},
        ],
    })
    text = _module_evidence(r)
    assert "plan=Standard" in text
    assert "max_delta=+23%" in text
    assert "DE" in text
    assert "cart_extracts=3/3" in text
    assert "mode=live" in text


def test_evidence_signal_emits_velocity_and_families():
    r = _result("signal", raw_data={
        "velocity_ratio": 2.4,
        "recent_30d": 12,
        "older_60d": 5,
        "by_family": {"eng": 8, "gtm": 3, "other": 1},
        "recent_by_region": {"US": 9, "EU": 3},
    })
    text = _module_evidence(r)
    assert "velocity=2.4×" in text
    assert "recent_30d=12" in text
    assert "eng:8" in text
    assert "other" not in text  # excluded from top-family slice
    assert "US:9" in text


def test_evidence_altdata_emits_composite_and_per_source():
    r = _result("altdata", raw_data={
        "composite_score": 0.42,
        "composite_label": "deteriorating",
        "sources": {
            "g2":  {"recent_30d": 14, "rating_delta": -0.3, "velocity_ratio": 1.8,
                    "top_complaint": "slow support"},
            "glassdoor": {"recent_30d": 6, "rating_delta": 0.1},
        },
    })
    text = _module_evidence(r)
    assert "composite=0.42" in text
    assert "deteriorating" in text
    assert "g2" in text and "n=14" in text
    assert "Δrating=-0.30" in text
    assert "complaint=slow support" in text


def test_evidence_exposure_lists_channels():
    r = _result("exposure", raw_data={
        "max_severity": "critical",
        "critical_count": 2,
        "channels": ["paste", "github"],
    })
    text = _module_evidence(r)
    assert "max_sev=critical" in text
    assert "critical=2" in text
    assert "paste,github" in text


def test_evidence_filing_emits_change_count():
    r = _result("filing", raw_data={
        "change_count": 4,
        "max_materiality": "high",
        "mode": "mock",
    })
    text = _module_evidence(r)
    assert "changes=4" in text
    assert "max_materiality=high" in text
    assert "mode=mock" in text


def test_evidence_visual_highlights_top_suspect():
    r = _result("visual", raw_data={
        "suspect_count": 5,
        "high_count": 2,
        "suspects": [
            {"verdict": "high", "similarity": 0.91},
            {"verdict": "low", "similarity": 0.55},
        ],
    })
    text = _module_evidence(r)
    assert "suspects=5" in text
    assert "high_or_critical=2" in text
    assert "top=high@sim=0.91" in text


def test_evidence_empty_raw_data_returns_empty_string():
    assert _module_evidence(_result("trueprice")) == ""


# ── _top_findings ─────────────────────────────────────────────────────


def test_top_findings_sorts_by_severity_and_limits():
    results = [
        _result("signal", findings=[
            Finding(statement="A", severity="info"),
            Finding(statement="B", severity="critical"),
        ]),
        _result("altdata", findings=[
            Finding(statement="C", severity="high"),
            Finding(statement="D", severity="notable"),
        ]),
    ]
    top = _top_findings(results, limit=3)
    severities = [f.severity for f in top]
    assert severities == ["critical", "high", "notable"]
    assert len(top) == 3


def test_top_findings_empty_when_no_findings():
    assert _top_findings([_result("signal")]) == []


# ── _composite_confidence ─────────────────────────────────────────────


def test_composite_confidence_ignores_failed_modules():
    results = [
        _result("signal", confidence=0.9),
        _result("altdata", confidence=0.5),
        _result("filing", confidence=0.0, status="failed"),
    ]
    assert _composite_confidence(results) == 0.7


def test_composite_confidence_zero_when_all_failed():
    results = [
        _result("signal", confidence=0.0, status="failed"),
        _result("altdata", confidence=0.0, status="failed"),
    ]
    assert _composite_confidence(results) == 0.0


# ── _findings_prompt_block ────────────────────────────────────────────


def test_findings_prompt_block_includes_metrics_header():
    results = [
        _result("trueprice",
                findings=[Finding(statement="DE +23% over US", severity="high")],
                raw_data={
                    "regions": [
                        {"region": "US", "true_usd": 8.0, "delta_pct": 0.0},
                        {"region": "DE", "true_usd": 9.84, "delta_pct": 23.0},
                    ],
                }),
    ]
    block = _findings_prompt_block(results)
    assert "[trueprice]" in block
    assert "metrics:" in block
    assert "max_delta=+23%" in block
    assert "- [high] DE +23% over US" in block


def test_findings_prompt_block_skips_findingless_modules():
    results = [_result("signal"), _result("altdata", findings=[Finding(statement="X")])]
    block = _findings_prompt_block(results)
    assert "[altdata]" in block
    assert "[signal]" not in block


def test_findings_prompt_block_handles_zero_findings():
    assert _findings_prompt_block([]) == "(no findings)"


# ── _template_summary ─────────────────────────────────────────────────


def test_template_summary_uses_subject_and_finding_count():
    results = [
        _result("signal", findings=[Finding(statement="Hiring spiked 2.4× in 30d")]),
        _result("altdata", findings=[Finding(statement="G2 rating dropped 0.3 pts")]),
    ]
    text = _template_summary("Linear", results)
    assert "Linear" in text
    assert "2 module" in text
    assert "Hiring spiked 2.4× in 30d" in text


def test_template_summary_no_findings_message():
    text = _template_summary("Linear", [_result("signal")])
    assert "No material signals" in text
    assert "Linear" in text


# ── _build_sections ───────────────────────────────────────────────────


def test_build_sections_carries_raw_data_through_to_section():
    results = [_result("trueprice",
                       findings=[Finding(statement="DE +23%")],
                       raw_data={"regions": [{"region": "DE"}]})]
    sections = _build_sections(results)
    assert len(sections) == 1
    assert sections[0].module == "trueprice"
    assert sections[0].data["regions"] == [{"region": "DE"}]
    assert sections[0].summary == "DE +23%"


def test_build_sections_findingless_falls_back_to_placeholder():
    sections = _build_sections([_result("signal")])
    assert "No findings surfaced" in sections[0].summary
