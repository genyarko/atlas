"""End-to-end smoke test on the 3 demo queries.

This is the Day 2 acceptance criterion: Question → planner → executor
(mocks) → synthesizer → HTML brief, end-to-end. Brief is ugly but flows.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent import run_agent
from app.brief import render_html, render_markdown
from app.models import Question

_DEMO_QUERIES = json.loads(
    (Path(__file__).resolve().parent.parent.parent / "demo" / "queries.json").read_text(
        encoding="utf-8"
    )
)["queries"]


@pytest.mark.parametrize("entry", _DEMO_QUERIES, ids=[q["id"] for q in _DEMO_QUERIES])
async def test_demo_pipeline(entry):
    q = Question(text=entry["query"])
    brief = await run_agent(q)

    # Plan picked at least one module
    assert brief.plan.modules_to_invoke, f"planner produced no modules for {entry['id']}"
    # Execution produced sections for every planned module
    assert len(brief.sections) == len(brief.plan.modules_to_invoke)
    # The demo plan's expected modules must all appear
    selected = {inv.module for inv in brief.plan.modules_to_invoke}
    expected = set(entry["expected_modules"])
    missing = expected - selected
    assert not missing, f"{entry['id']} expected {expected}, got {selected} (missing {missing})"
    # Subject inferred correctly
    assert brief.subject == entry["subject"]
    # At least one finding total
    total_findings = sum(len(s.findings) for s in brief.sections)
    assert total_findings >= 1
    # Renderers don't throw and produce non-empty output
    html = render_html(brief)
    md = render_markdown(brief)
    assert "<html" in html.lower() and brief.subject in html
    assert "Ground Truth Brief" in md


async def test_planner_routes_security_to_visual_and_exposure():
    q = Question(text="Scan for brand impersonation and credential leaks on AcmeCorp.")
    brief = await run_agent(q)
    selected = {inv.module for inv in brief.plan.modules_to_invoke}
    assert "visual" in selected
    assert "exposure" in selected


async def test_planner_routes_competitive_to_gtm_stack():
    q = Question(text="Run a competitive pricing brief on Linear.")
    brief = await run_agent(q)
    selected = {inv.module for inv in brief.plan.modules_to_invoke}
    # Competitive intent should at minimum pull in pricing (trueprice) or signal/altdata.
    assert selected & {"trueprice", "signal", "altdata"}
