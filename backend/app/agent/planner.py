"""Planner node — turns a Question into a ResearchPlan.

Two paths:
  • LLM (Claude) when ANTHROPIC_API_KEY is set
  • Heuristic keyword router otherwise

Both produce the same Pydantic ResearchPlan, so downstream nodes are agnostic.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..models import Intent, ModuleInvocation, Question, ResearchPlan
from ..modules import MODULES, MODULE_CATALOG
from ..modules._fixtures import infer_subject
from .llm import get_llm
from .state import AgentState

log = logging.getLogger(__name__)


# ── Heuristic router ──────────────────────────────────────────────────

_INTENT_KEYWORDS: dict[Intent, list[str]] = {
    "competitive": ["competitive", "competitor", "evaluate", "pricing", "compare", "battlecard"],
    "financial":   ["earnings", "filing", "investor", "10-k", "10-q", "8-k", "material", "sec"],
    "security":    ["phishing", "impersonation", "lookalike", "credential", "leak", "exposure", "doxx", "brand"],
}

_MODULE_HINTS: dict[str, list[str]] = {
    "trueprice": ["price", "pricing", "cost", "checkout", "tier", "plan"],
    "signal":    ["hiring", "jobs", "exec", "leadership", "strategy", "roadmap", "intent", "earnings", "competitive"],
    "filing":    ["filing", "sec", "10-k", "10-q", "8-k", "regulatory", "patent"],
    "altdata":   ["review", "sentiment", "glassdoor", "g2", "trustpilot", "earnings", "competitive"],
    "visual":    ["impersonation", "lookalike", "phish", "brand", "spoof"],
    "exposure":  ["leak", "credential", "exposure", "paste", "doxx", "breach"],
    "investor":  ["venture", "vc firm", "vc fund", "seed round", "series a", "series b", "series c",
                  "raising fund", "fund close", "limited partner", " lp ", "investing in",
                  "investors in", "portfolio company", "portfolio companies"],
}


def _classify_intent(query: str) -> Intent:
    q = query.lower()
    scores: dict[Intent, int] = {"competitive": 0, "financial": 0, "security": 0}
    for intent, keywords in _INTENT_KEYWORDS.items():
        scores[intent] = sum(1 for k in keywords if k in q)
    top = max(scores.values())
    if top == 0:
        return "mixed"
    winners = [k for k, v in scores.items() if v == top]
    return winners[0] if len(winners) == 1 else "mixed"


_INTENT_DEFAULTS: dict[Intent, list[str]] = {
    "competitive": ["trueprice", "signal", "altdata"],
    "financial":   ["filing", "signal", "altdata"],
    "security":    ["visual", "exposure"],
    "mixed":       ["signal", "altdata", "filing"],
}


def _select_modules(query: str, intent: Intent) -> list[str]:
    q = query.lower()
    keyword_scores: dict[str, int] = {}
    for module, hints in _MODULE_HINTS.items():
        score = sum(1 for h in hints if h in q)
        if score:
            keyword_scores[module] = score

    # Start with the intent-defaults so a well-classified intent always
    # produces the demo-expected module set. Keyword hits boost the ranking
    # of any module already in the default set and pull in extras.
    selected: dict[str, int] = {m: 1 for m in _INTENT_DEFAULTS[intent]}
    for module, score in keyword_scores.items():
        selected[module] = selected.get(module, 0) + score

    ranked = sorted(selected.items(), key=lambda kv: kv[1], reverse=True)
    return [m for m, _ in ranked[:4]]


def _heuristic_plan(question: Question) -> ResearchPlan:
    intent = _classify_intent(question.text)
    module_names = _select_modules(question.text, intent)
    subject = infer_subject(question.text)
    invocations = [
        ModuleInvocation(
            module=name,
            params={"subject": subject, "query": question.text},
            priority=3,
            rationale=f"Selected by heuristic router for intent={intent}.",
        )
        for name in module_names
    ]
    return ResearchPlan(
        question_id=question.id,
        intent=intent,
        modules_to_invoke=invocations,
        reasoning=(
            f"Heuristic planner classified intent as '{intent}'. "
            f"Selected {len(invocations)} module(s) by keyword match: "
            f"{', '.join(module_names)}."
        ),
    )


# ── LLM planner ───────────────────────────────────────────────────────

_PLANNER_SYSTEM = """You are the Atlas Planner. Decompose the user's question \
into a research plan that selects from the available intelligence modules.

Modules available:
{catalog}

Output STRICT JSON only (no markdown fence, no commentary), matching:
{{
  "intent": "competitive" | "financial" | "security" | "mixed",
  "modules": [
    {{"module": "<name>", "subject": "<entity>", "rationale": "<one line>", "priority": 1-5}}
  ],
  "reasoning": "<1-2 sentences>"
}}

Rules:
- Pick 2-4 modules. Only invoke all 6 if the question genuinely spans all 3 tracks.
- The "module" field MUST be one of: {names}.
- "subject" should be a single canonical entity (company or brand).
"""


def _catalog_for_prompt() -> str:
    return "\n".join(
        f"- {key}: {info['title']} ({info['track']}) — {info['purpose']}"
        for key, info in MODULE_CATALOG.items()
    )


def _parse_llm_plan(raw: str, question: Question) -> ResearchPlan | None:
    # Tolerate stray code fences.
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        data: dict[str, Any] = json.loads(cleaned)
    except json.JSONDecodeError as e:
        log.warning("Planner LLM returned non-JSON (%s); falling back", e)
        return None

    valid_names = set(MODULES.keys())
    proposed = data.get("modules", []) or []
    invocations: list[ModuleInvocation] = []
    dropped: list[str] = []
    for entry in proposed:
        name = entry.get("module")
        if name not in valid_names:
            dropped.append(str(name))
            continue
        subject = entry.get("subject") or infer_subject(question.text)
        invocations.append(ModuleInvocation(
            module=name,
            params={"subject": subject, "query": question.text},
            priority=int(entry.get("priority", 3)),
            rationale=entry.get("rationale", ""),
        ))

    if dropped:
        log.warning(
            "Planner LLM proposed %d invalid module name(s) (dropped): %s; kept %d valid",
            len(dropped), dropped, len(invocations),
        )

    if not invocations:
        log.warning("Planner LLM produced no valid modules; falling back to heuristic")
        return None

    # If the LLM hallucinated more module names than it got right, the
    # remaining plan is unlikely to reflect the user's question — treat
    # this as a signal to fall back to the heuristic router rather than
    # silently shipping a thin brief.
    if len(dropped) > len(invocations) and len(proposed) >= 2:
        log.warning(
            "Planner LLM kept only %d of %d proposed modules; falling back to heuristic",
            len(invocations), len(proposed),
        )
        return None

    intent_value = data.get("intent", "mixed")
    if intent_value not in ("competitive", "financial", "security", "mixed"):
        intent_value = "mixed"

    return ResearchPlan(
        question_id=question.id,
        intent=intent_value,
        modules_to_invoke=invocations,
        reasoning=data.get("reasoning", ""),
    )


async def _llm_plan(question: Question) -> ResearchPlan | None:
    llm = get_llm()
    if llm is None:
        return None
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
    except ImportError:  # pragma: no cover
        return None

    system = _PLANNER_SYSTEM.format(
        catalog=_catalog_for_prompt(),
        names=", ".join(MODULES.keys()),
    )
    messages = [
        SystemMessage(content=system),
        HumanMessage(content=f"Question: {question.text}"),
    ]
    try:
        response = await llm.ainvoke(messages)
    except Exception as e:  # pragma: no cover — defensive
        log.warning("Planner LLM call failed (%s); falling back", e)
        return None
    text = response.content if isinstance(response.content, str) else str(response.content)
    return _parse_llm_plan(text, question)


# ── LLM subject extractor ─────────────────────────────────────────────
#
# When the heuristic router decides modules (no LLM key, or LLM planner
# bailed), the regex-based ``infer_subject`` is the only thing picking
# the subject — fine for the 5 demo companies, brittle for anything else.
# This helper runs a single short LLM call whose only job is to return
# the canonical entity. It's cheap and lets the heuristic path produce
# correct subjects for unfamiliar brands when an LLM is available.


_SUBJECT_SYSTEM = """You extract the single canonical company or brand the user is asking about.

Return PLAIN TEXT only — just the entity name, no punctuation, no explanation.
If no entity is identifiable, return exactly: UNKNOWN
"""


async def _llm_extract_subject(query: str) -> str | None:
    llm = get_llm()
    if llm is None:
        return None
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
    except ImportError:  # pragma: no cover
        return None
    try:
        response = await llm.ainvoke([
            SystemMessage(content=_SUBJECT_SYSTEM),
            HumanMessage(content=query),
        ])
    except Exception as e:  # pragma: no cover — defensive
        log.warning("Subject-extractor LLM call failed (%s)", e)
        return None
    text = response.content if isinstance(response.content, str) else str(response.content)
    name = text.strip().splitlines()[0].strip(" .\"'`") if text.strip() else ""
    # Reject blanks, sentinel, and absurd lengths (LLM ignored the instruction).
    if not name or name.upper() == "UNKNOWN" or len(name) > 60:
        return None
    return name


async def _resolve_subject(question: Question) -> str:
    """Pick a subject for the heuristic plan path.

    Try the LLM extractor first (only fires when an API key is set); fall
    back to the regex-driven ``infer_subject`` if the LLM is unavailable
    or returns nothing usable."""
    llm_subject = await _llm_extract_subject(question.text)
    if llm_subject:
        return llm_subject
    return infer_subject(question.text)


# ── Graph node ────────────────────────────────────────────────────────


async def plan_node(state: AgentState) -> AgentState:
    # Skip re-planning when the caller pre-seeded a plan (e.g. full-module test).
    if state.get("plan") is not None:
        log.info("Planner: using pre-seeded plan with %d modules", len(state["plan"].modules_to_invoke))
        return {}

    question = state["question"]
    plan = await _llm_plan(question)
    if plan is None:
        log.info("Planner: using heuristic router")
        plan = _heuristic_plan(question)
        # If an LLM is available, replace the regex-derived subject on every
        # invocation with the LLM-extracted one. This is the "have the
        # planner call an LLM purely for subject extraction" path from the
        # improvements list — it kicks in exactly when the heuristic router
        # decided modules.
        llm_subject = await _llm_extract_subject(question.text)
        if llm_subject:
            for inv in plan.modules_to_invoke:
                inv.params["subject"] = llm_subject
            log.info("Planner: heuristic modules + LLM subject='%s'", llm_subject)
    else:
        log.info("Planner: LLM produced plan with %d modules", len(plan.modules_to_invoke))
    return {"plan": plan}
