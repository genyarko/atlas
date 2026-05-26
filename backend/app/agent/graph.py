"""LangGraph state machine wiring (plan §5.1).

    START → plan → execute → evaluate ─┬─► refine_plan → execute (loop)
                                       └─► synthesize → END

After the first execute pass, evaluate inspects confidence and
degraded-module counts. If quality is below threshold and we're under
the iteration cap (``MAX_ITERATIONS`` in ``refine.py``), refine_plan
appends a complementary module and execute runs again — the executor
skips anything already in ``state["results"]`` so the loop only pays
for the new work.
"""

from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from ..models import Brief, Question
from .executor import execute_node
from .planner import plan_node
from .refine import decide_after_evaluate, evaluate_node, refine_plan_node
from .state import AgentState
from .synthesizer import synthesize_node

log = logging.getLogger(__name__)


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("plan", plan_node)
    g.add_node("execute", execute_node)
    g.add_node("evaluate", evaluate_node)
    g.add_node("refine_plan", refine_plan_node)
    g.add_node("synthesize", synthesize_node)

    g.add_edge(START, "plan")
    g.add_edge("plan", "execute")
    g.add_edge("execute", "evaluate")
    g.add_conditional_edges(
        "evaluate",
        decide_after_evaluate,
        {"refine_plan": "refine_plan", "synthesize": "synthesize"},
    )
    g.add_edge("refine_plan", "execute")
    g.add_edge("synthesize", END)
    return g.compile()


_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


async def run_agent(question: Question) -> Brief:
    """Run the full pipeline for a single question. Returns the final Brief."""
    log.info("Agent: question=%s", question.text)
    graph = _get_graph()
    final_state = await graph.ainvoke({"question": question, "iterations": 0})
    brief = final_state["brief"]
    log.info(
        "Agent: brief generated id=%s modules=%d findings=%d confidence=%.2f iterations=%d",
        brief.id,
        len(brief.sections),
        sum(len(s.findings) for s in brief.sections),
        brief.confidence_score,
        final_state.get("iterations", 0),
    )
    return brief
