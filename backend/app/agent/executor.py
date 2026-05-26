"""Executor node — runs the modules in the plan in parallel.

Each dispatched module runs inside an ``active_module`` context so every
MCP call it makes is tagged with the owning module in the transcript.
We also emit ``module_start`` / ``module_done`` events on the active SSE
queue so the frontend rail can show per-module progress alongside the
individual tool calls.
"""

from __future__ import annotations

import asyncio
import logging

from ..brightdata.mcp_client import _emit_event, active_module
from ..modules import MODULE_CATALOG, MODULES
from .state import AgentState

log = logging.getLogger(__name__)


async def execute_node(state: AgentState) -> AgentState:
    plan = state["plan"]
    # On a refine pass the plan grows; skip any module whose result is
    # already in state so we don't re-pay for the same call. The state
    # reducer concatenates the new results onto the existing ones.
    already_done = {r.module for r in state.get("results", [])}
    invocations = [inv for inv in plan.modules_to_invoke if inv.module not in already_done]

    if not invocations:
        log.info("Executor: nothing new to dispatch (all %d module(s) already executed)",
                 len(plan.modules_to_invoke))
        return {"results": []}

    async def _run(inv):
        meta = MODULE_CATALOG.get(inv.module, {})
        module = MODULES[inv.module]
        log.info("Executor: dispatching %s (priority=%d)", inv.module, inv.priority)
        _emit_event("module_start", {
            "module": inv.module,
            "title": meta.get("title", inv.module),
            "track": meta.get("track", ""),
            "rationale": inv.rationale,
        })
        with active_module(inv.module):
            result = await module.run(inv.params)
        _emit_event("module_done", {
            "module": inv.module,
            "status": result.status,
            "findings": len(result.findings),
            "confidence": result.confidence,
            "duration_ms": result.duration_ms,
            "error": result.error,
        })
        return result

    results = await asyncio.gather(*[_run(inv) for inv in invocations])
    log.info("Executor: %d module(s) completed", len(results))
    return {"results": list(results)}
