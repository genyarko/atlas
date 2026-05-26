"""SERP API helper — Bright Data ``search_engine`` tool wrapper."""

from __future__ import annotations

import logging
from typing import Any

from .mcp_client import BrightDataMCPClient, MCPNotAvailable

log = logging.getLogger(__name__)


def _coerce_results(result: Any) -> list[dict[str, Any]] | None:
    if result is None:
        return None
    items = getattr(result, "results", None)
    if isinstance(items, list):
        return [r for r in items if isinstance(r, dict)]
    content = getattr(result, "content", None)
    if isinstance(content, list):
        import json

        out: list[dict[str, Any]] = []
        for part in content:
            text = getattr(part, "text", None)
            if not isinstance(text, str):
                continue
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError):
                continue
            organic = parsed.get("organic") if isinstance(parsed, dict) else None
            if isinstance(organic, list):
                out.extend(r for r in organic if isinstance(r, dict))
            elif isinstance(parsed, list):
                out.extend(r for r in parsed if isinstance(r, dict))
        if out:
            return out
    return None


async def search(query: str, *, num: int = 10) -> list[dict[str, Any]] | None:
    """Run a SERP query via the MCP ``search_engine`` tool."""
    try:
        result = await BrightDataMCPClient.get().call(
            "search_engine",
            {"query": query, "num_results": num},
        )
    except MCPNotAvailable:
        return None
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("SERP search failed: %s", exc)
        return None
    return _coerce_results(result)
