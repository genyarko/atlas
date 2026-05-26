"""Planner unit tests.

Focuses on the LLM JSON parser edge cases that the heuristic-only smoke
tests can't cover: code fences, missing keys, invalid module names, and
the "mostly hallucinated" fallback signal. Also pins the heuristic
router's intent classifier and module selection so a regression there
shows up as a test failure rather than a thinner brief.
"""

from __future__ import annotations

import pytest

from app.agent.planner import (
    _classify_intent,
    _heuristic_plan,
    _parse_llm_plan,
    _select_modules,
)
from app.models import Question


# ── Heuristic router ──────────────────────────────────────────────────


def test_classify_intent_security():
    assert _classify_intent("Scan AcmeCorp for credential leaks and impersonation") == "security"


def test_classify_intent_financial():
    assert _classify_intent("Pull the latest 10-K filings for Datadog") == "financial"


def test_classify_intent_competitive():
    assert _classify_intent("Run a competitive pricing brief on Linear") == "competitive"


def test_classify_intent_unmatched_is_mixed():
    assert _classify_intent("Tell me about the company") == "mixed"


def test_classify_intent_tie_is_mixed():
    # One competitive keyword + one financial keyword → tie → mixed.
    assert _classify_intent("Compare earnings disclosures") == "mixed"


def test_select_modules_competitive_includes_gtm_stack():
    modules = _select_modules("Run a competitive brief on Linear", "competitive")
    assert {"trueprice", "signal", "altdata"} <= set(modules)


def test_select_modules_security_includes_visual_and_exposure():
    modules = _select_modules(
        "Scan for brand impersonation and credential leaks", "security",
    )
    assert "visual" in modules
    assert "exposure" in modules


def test_select_modules_caps_at_four():
    modules = _select_modules(
        "pricing hiring earnings filings reviews impersonation leaks", "mixed",
    )
    assert len(modules) <= 4


def test_heuristic_plan_emits_subject_in_params():
    plan = _heuristic_plan(Question(text="Run a competitive brief on Linear"))
    assert plan.modules_to_invoke
    for inv in plan.modules_to_invoke:
        assert inv.params.get("subject") == "Linear"
        assert inv.params.get("query")


# ── LLM JSON parser ───────────────────────────────────────────────────


_GOOD_JSON = """{
  "intent": "competitive",
  "modules": [
    {"module": "trueprice", "subject": "Linear", "rationale": "pricing diff", "priority": 1},
    {"module": "signal",    "subject": "Linear", "rationale": "hiring",       "priority": 2}
  ],
  "reasoning": "Competitive intent — pricing + signal."
}"""


def test_parse_llm_plan_happy_path():
    plan = _parse_llm_plan(_GOOD_JSON, Question(text="Brief on Linear"))
    assert plan is not None
    assert plan.intent == "competitive"
    assert {inv.module for inv in plan.modules_to_invoke} == {"trueprice", "signal"}
    assert plan.modules_to_invoke[0].params["subject"] == "Linear"


def test_parse_llm_plan_strips_code_fence():
    fenced = "```json\n" + _GOOD_JSON + "\n```"
    plan = _parse_llm_plan(fenced, Question(text="Brief on Linear"))
    assert plan is not None
    assert plan.modules_to_invoke


def test_parse_llm_plan_strips_bare_fence():
    fenced = "```\n" + _GOOD_JSON + "\n```"
    plan = _parse_llm_plan(fenced, Question(text="Brief on Linear"))
    assert plan is not None
    assert plan.modules_to_invoke


def test_parse_llm_plan_invalid_json_returns_none():
    plan = _parse_llm_plan("not json at all", Question(text="x"))
    assert plan is None


def test_parse_llm_plan_drops_invalid_module_names():
    raw = """{
      "intent": "competitive",
      "modules": [
        {"module": "trueprice",     "subject": "Linear", "rationale": "ok",  "priority": 1},
        {"module": "made_up_module","subject": "Linear", "rationale": "bad", "priority": 1}
      ],
      "reasoning": "test"
    }"""
    plan = _parse_llm_plan(raw, Question(text="x"))
    assert plan is not None
    names = [inv.module for inv in plan.modules_to_invoke]
    assert names == ["trueprice"]


def test_parse_llm_plan_all_invalid_returns_none():
    raw = """{
      "intent": "competitive",
      "modules": [
        {"module": "fake_a", "subject": "X", "rationale": "", "priority": 1},
        {"module": "fake_b", "subject": "X", "rationale": "", "priority": 1}
      ],
      "reasoning": "test"
    }"""
    plan = _parse_llm_plan(raw, Question(text="x"))
    assert plan is None


def test_parse_llm_plan_mostly_invalid_falls_back():
    # 3 hallucinated names, 1 valid — improvement #8: don't ship a thin
    # 1-module brief silently; bail and let the heuristic router decide.
    raw = """{
      "intent": "competitive",
      "modules": [
        {"module": "fake_a",    "subject": "X", "rationale": "", "priority": 1},
        {"module": "fake_b",    "subject": "X", "rationale": "", "priority": 1},
        {"module": "fake_c",    "subject": "X", "rationale": "", "priority": 1},
        {"module": "trueprice", "subject": "X", "rationale": "", "priority": 1}
      ],
      "reasoning": "test"
    }"""
    plan = _parse_llm_plan(raw, Question(text="x"))
    assert plan is None


def test_parse_llm_plan_unknown_intent_defaults_to_mixed():
    raw = """{
      "intent": "weather",
      "modules": [
        {"module": "signal", "subject": "X", "rationale": "", "priority": 3}
      ],
      "reasoning": ""
    }"""
    plan = _parse_llm_plan(raw, Question(text="x"))
    assert plan is not None
    assert plan.intent == "mixed"


def test_parse_llm_plan_missing_subject_falls_back_to_infer():
    raw = """{
      "intent": "competitive",
      "modules": [
        {"module": "signal", "rationale": "no subject field", "priority": 2}
      ],
      "reasoning": ""
    }"""
    plan = _parse_llm_plan(raw, Question(text="Run a brief on Linear"))
    assert plan is not None
    # infer_subject should have filled it in from the question text.
    assert plan.modules_to_invoke[0].params["subject"] == "Linear"


def test_parse_llm_plan_priority_default_when_missing():
    raw = """{
      "intent": "mixed",
      "modules": [
        {"module": "signal", "subject": "Linear", "rationale": ""}
      ],
      "reasoning": ""
    }"""
    plan = _parse_llm_plan(raw, Question(text="x"))
    assert plan is not None
    assert plan.modules_to_invoke[0].priority == 3


def test_parse_llm_plan_empty_modules_returns_none():
    raw = '{"intent": "mixed", "modules": [], "reasoning": ""}'
    plan = _parse_llm_plan(raw, Question(text="x"))
    assert plan is None


def test_parse_llm_plan_missing_modules_key_returns_none():
    raw = '{"intent": "mixed", "reasoning": "no modules key"}'
    plan = _parse_llm_plan(raw, Question(text="x"))
    assert plan is None
