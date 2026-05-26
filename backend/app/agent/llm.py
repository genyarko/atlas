"""Lazy LLM accessor — returns a configured Claude client or None."""

from __future__ import annotations

import logging

from .. import config

log = logging.getLogger(__name__)

_client = None


def get_llm():
    """Return a `ChatAnthropic` instance, or None if not configured."""
    global _client
    if not config.has_llm():
        return None
    if _client is not None:
        return _client
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError:  # pragma: no cover
        log.warning("langchain-anthropic not installed; LLM nodes disabled")
        return None
    _client = ChatAnthropic(
        model=config.LLM_MODEL,
        api_key=config.ANTHROPIC_API_KEY,
        temperature=0.2,
        max_tokens=2000,
    )
    return _client
