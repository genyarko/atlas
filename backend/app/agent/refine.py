"""Evaluate + refine_plan nodes — the agentic depth loop from plan §5.1.

After ``execute`` returns, ``evaluate_node`` inspects the results and
decides whether the brief is good enough to synthesize, or whether the
plan needs another pass with complementary modules.

Triggers a refine when, within the iteration cap (max 2 refinements):
  • composite confidence across non-failed modules is below 0.5, OR
  • any module status is ``failed``, OR
  • ≥2 modules are degraded (partial, failed, or fell back to mock when
    the global mode was live).

``refine_plan_node`` then picks 1–2 modules that the plan hasn't already
invoked, ranks them by keyword affinity to the original question, appends
them to the plan, and hands control back to ``execute``. The executor
skips modules already in ``state["results"]`` so the loop only spends
work on the new invocations.
"""

from __future__ import annotations

import logging
from typing import Literal

from .. import config
from ..models import ModuleInvocation, ModuleResult
from ..modules import MODULE_CATALOG, MODULES
from ..modules._fixtures import infer_subject
from ..brightdata.mcp_client import _emit_event
from .planner import _MODULE_HINTS
from .state import AgentState

log = logging.getLogger(__name__)


MAX_ITERATIONS = 2


# ── Quality signals ───────────────────────────────────────────────────


def _avg_confidence(results: list[ModuleResult]) -> float:
    scores = [r.confidence for r in results if r.status != "failed"]
    return round(sum(scores) / len(scores), 2) if scores else 0.0


def _degraded_count(results: list[ModuleResult]) -> int:
    """Modules that didn't deliver what we asked for.

    In live mode that includes any module that quietly fell back to its
    mock fixture. In global mock mode those fallbacks are the *intended*
    path, so we only count outright partial/failed statuses."""
    intended_mock = config.is_mock_mode()
    count = 0
    for r in results:
        if r.status in ("partial", "failed"):
            count += 1
            continue
        if not intended_mock and r.raw_data.get("mode") == "mock":
            count += 1
    return count


# ── Evaluate node ─────────────────────────────────────────────────────


async def evaluate_node(state: AgentState) -> AgentState:
    results = state.get("results", [])
    iterations = state.get("iterations", 0)

    avg = _avg_confidence(results)
    degraded = _degraded_count(results)
    failed = sum(1 for r in results if r.status == "failed")

    needs_refine = (avg < 0.5) or (degraded >= 2) or (failed >= 1)
    can_refine = iterations < MAX_ITERATIONS

    decision: Literal["refine", "synthesize"] = (
        "refine" if (needs_refine and can_refine) else "synthesize"
    )

    log.info(
        "Evaluate: iter=%d avg_conf=%.2f degraded=%d failed=%d → %s",
        iterations, avg, degraded, failed, decision,
    )
    _emit_event("evaluate", {
        "iteration": iterations,
        "avg_confidence": avg,
        "degraded": degraded,
        "failed": failed,
        "decision": decision,
    })
    # Read-only node; the decision routes via the conditional edge.
    return {}


def decide_after_evaluate(state: AgentState) -> Literal["refine_plan", "synthesize"]:
    results = state.get("results", [])
    iterations = state.get("iterations", 0)
    if iterations >= MAX_ITERATIONS:
        return "synthesize"
    avg = _avg_confidence(results)
    degraded = _degraded_count(results)
    failed = sum(1 for r in results if r.status == "failed")
    if (avg < 0.5) or (degraded >= 2) or (failed >= 1):
        return "refine_plan"
    return "synthesize"


# ── Refine-plan node ──────────────────────────────────────────────────


def _candidate_modules(
    query: str, already_invoked: set[str],
) -> list[str]:
    """Modules not yet invoked, ranked by keyword affinity to the query.

    Falls back to the catalog order when nothing matches so we still add
    *something* on a refine pass (rather than no-oping)."""
    q = query.lower()
    scored: list[tuple[str, int]] = []
    for name in MODULES.keys():
        if name in already_invoked:
            continue
        hints = _MODULE_HINTS.get(name, [])
        score = sum(1 for h in hints if h in q)
        scored.append((name, score))

    scored.sort(key=lambda kv: kv[1], reverse=True)
    return [name for name, _ in scored]


async def refine_plan_node(state: AgentState) -> AgentState:
    plan = state["plan"]
    question = state["question"]
    results = state.get("results", [])
    iterations = state.get("iterations", 0)

    already = {inv.module for inv in plan.modules_to_invoke}
    candidates = _candidate_modules(question.text, already)
    # One module per refine pass keeps blast radius small and gives the
    # next evaluate a clean signal about whether the addition helped.
    to_add = candidates[:1]

    if not to_add:
        log.info("Refine: no candidate modules left; deferring to synthesize")
        _emit_event("refine_plan", {
            "iteration": iterations + 1,
            "added": [],
            "reason": "no candidates",
        })
        return {"iterations": iterations + 1}

    subject = (
        plan.modules_to_invoke[0].params.get("subject")
        if plan.modules_to_invoke else None
    ) or infer_subject(question.text)

    avg = _avg_confidence(results)
    degraded = _degraded_count(results)
    reason = (
        f"avg confidence {avg:.2f} below 0.5"
        if avg < 0.5 else
        f"{degraded} module(s) degraded — adding complementary coverage"
    )

    new_invocations = [
        ModuleInvocation(
            module=name,
            params={"subject": subject, "query": question.text},
            priority=2,
            rationale=f"Added on refine pass {iterations + 1}: {reason}.",
        )
        for name in to_add
    ]

    updated_plan = plan.model_copy(update={
        "modules_to_invoke": plan.modules_to_invoke + new_invocations,
        "reasoning": (
            f"{plan.reasoning} Refine pass {iterations + 1} added "
            f"{', '.join(to_add)} ({reason})."
        ).strip(),
    })

    note = (
        f"iter={iterations + 1} added={','.join(to_add)} "
        f"avg_conf={avg:.2f} degraded={degraded}"
    )
    log.info("Refine: %s", note)
    _emit_event("refine_plan", {
        "iteration": iterations + 1,
        "added": [
            {
                "module": inv.module,
                "title": MODULE_CATALOG.get(inv.module, {}).get("title", inv.module),
                "rationale": inv.rationale,
            }
            for inv in new_invocations
        ],
        "reason": reason,
    })

    notes = list(state.get("refine_notes", []))
    notes.append(note)
    return {
        "plan": updated_plan,
        "iterations": iterations + 1,
        "refine_notes": notes,
    }
