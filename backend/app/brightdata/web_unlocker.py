"""Web Unlocker helper — fetches pages that bot-block conventional clients."""

from __future__ import annotations

from .mcp_client import BrightDataMCPClient, MCPNotAvailable


async def fetch(url: str) -> str | None:
    """Return the page content for ``url`` as markdown, or None on failure."""
    try:
        result = await BrightDataMCPClient.get().call(
            "scrape_as_markdown",
            {"url": url},
        )
    except MCPNotAvailable:
        return None
    content = getattr(result, "content", None)
    if isinstance(content, list):
        for part in content:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                return text
    return None
