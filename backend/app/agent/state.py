"""Mutable state passed between LangGraph nodes."""

from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages

from ..models import Brief, ModuleResult, Question, ResearchPlan


def _merge_results(
    left: list[ModuleResult] | None,
    right: list[ModuleResult] | None,
) -> list[ModuleResult]:
    """Reducer for module results — concatenates parallel branch outputs."""
    return (left or []) + (right or [])


class AgentState(TypedDict, total=False):
    """All fields are optional; nodes fill them in order."""

    question: Question
    plan: ResearchPlan
    results: Annotated[list[ModuleResult], _merge_results]
    brief: Brief
    messages: Annotated[list, add_messages]  # LLM trace, if used
    # Refine-loop bookkeeping (plan §5.1). Incremented by refine_plan_node;
    # capped by the conditional edge so we never loop more than twice.
    iterations: int
    refine_notes: list[str]
