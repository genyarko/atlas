"""Bright Data MCP Server client wrapper.

Connects to the Bright Data MCP server over stdio (the `@brightdata/mcp`
npm package launched via npx), exposes a small async API for tool calls,
and degrades gracefully in mock mode when credentials are absent.

This is the *foundation* wrapper: every module dispatches through here,
but in `ATLAS_MODE=mock` no actual MCP process is spawned. Live calls
get wired in as each module is promoted from stub to real (Days 3+).
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import random
import time
from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Iterator
from uuid import uuid4

from .. import config

log = logging.getLogger(__name__)


class MCPNotAvailable(RuntimeError):
    """Raised when an MCP call is attempted in mock mode or without creds."""


@dataclass
class MCPToolCall:
    """Record of a single MCP tool invocation. Surfaced in the UI's
    'Live infrastructure trace' rail and in the per-brief transcript
    endpoint — the architectural commitment from implementation plan §5.4."""

    tool: str
    args: dict[str, Any]
    ok: bool
    duration_ms: int
    result_preview: str = ""
    error: str | None = None
    # Augmentations for the browser-rail surface:
    id: str = field(default_factory=lambda: uuid4().hex[:10])
    module: str | None = None
    simulated: bool = False  # True ⇒ mock-mode "what we would have called"
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_event(self) -> dict[str, Any]:
        """Compact dict suitable for SSE payload + frontend keying."""
        return asdict(self)


# ── Per-request capture ───────────────────────────────────────────
#
# The MCP client itself is a process-wide singleton, but each /api/ask
# request needs its own transcript so concurrent calls don't bleed into
# one another and so each Brief can ship its own provenance trace.
#
# We use ``contextvars`` because asyncio tasks inherit them on creation,
# so anything ``asyncio.gather``'d inside ``run_agent`` picks up the
# right transcript automatically.

_active_transcript: contextvars.ContextVar[list[MCPToolCall] | None] = contextvars.ContextVar(
    "atlas_mcp_active_transcript", default=None,
)
_active_module: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "atlas_mcp_active_module", default=None,
)
_active_event_queue: contextvars.ContextVar["asyncio.Queue | None"] = contextvars.ContextVar(
    "atlas_mcp_active_event_queue", default=None,
)


@contextlib.asynccontextmanager
async def capture_transcript(
    event_queue: "asyncio.Queue | None" = None,
) -> "AsyncIterator[list[MCPToolCall]]":
    """Bind a fresh transcript (and optional SSE queue) to the current task tree.

    Use this around ``run_agent`` so every MCP call made by the planner /
    executor / modules during that request lands in the returned list.
    The optional ``event_queue`` is used by the streaming endpoint to
    push each call to the browser as it happens.
    """
    transcript: list[MCPToolCall] = []
    t_token = _active_transcript.set(transcript)
    q_token = _active_event_queue.set(event_queue)
    try:
        yield transcript
    finally:
        _active_transcript.reset(t_token)
        _active_event_queue.reset(q_token)


@contextlib.contextmanager
def active_module(name: str | None) -> Iterator[None]:
    """Tag every MCP call inside the ``with`` block with the owning module."""
    token = _active_module.set(name)
    try:
        yield
    finally:
        _active_module.reset(token)


def _emit_event(event: str, payload: Any) -> None:
    queue = _active_event_queue.get()
    if queue is None:
        return
    try:
        queue.put_nowait((event, payload))
    except asyncio.QueueFull:  # pragma: no cover — defensive
        log.warning("MCP event queue full; dropping %s", event)


def _append_record(record: MCPToolCall) -> None:
    """Append to the singleton transcript AND the active per-request one."""
    BrightDataMCPClient.get().transcript.append(record)
    active = _active_transcript.get()
    if active is not None:
        active.append(record)
    _emit_event("mcp", record.to_event())


async def record_simulated(
    *,
    tool: str,
    args: dict[str, Any],
    duration_ms: int | None = None,
    module: str | None = None,
    result_preview: str = "",
    stagger: bool = True,
) -> MCPToolCall:
    """Module mock paths call this to declare the call the live path would make.

    The entry is marked ``simulated=True`` so the UI can render it with a
    muted/dotted style. We jitter a tiny sleep so the rail animates in
    mock mode at a watchable rate (matches how the live path naturally
    paces itself behind real network latency).
    """
    if stagger:
        await asyncio.sleep(random.uniform(0.04, 0.16))
    record = MCPToolCall(
        tool=tool,
        args=args,
        ok=True,
        duration_ms=duration_ms if duration_ms is not None else random.randint(420, 2_300),
        simulated=True,
        module=module or _active_module.get(),
        result_preview=result_preview,
    )
    _append_record(record)
    return record


@dataclass
class _Connection:
    """Lazy holder for the live MCP stdio session.

    In foundation/mock mode this stays unused. Real wiring lands in Day 3+.
    Keeping it as a separate dataclass makes the mock path obvious and
    keeps the optional mcp/stdio_client imports lazy.
    """

    session: Any = None
    stack: AsyncExitStack | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Serialises all browser navigate→get_text/screenshot sequences so
    # concurrent modules don't clobber the single stateful browser session.
    browser_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class BrightDataMCPClient:
    """Singleton-ish wrapper used by all modules.

    Usage:
        client = BrightDataMCPClient.get()
        result = await client.call("serp_search", {"query": "linear"})

    In mock mode, ``call()`` raises ``MCPNotAvailable``; callers MUST
    catch it and return their fixture data. This keeps the contract
    explicit at the module boundary.
    """

    _instance: BrightDataMCPClient | None = None

    def __init__(self) -> None:
        self._conn = _Connection()
        self.transcript: list[MCPToolCall] = []
        self._mock = config.is_mock_mode() or not config.has_brightdata_creds()

    @classmethod
    def get(cls) -> BrightDataMCPClient:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def mock(self) -> bool:
        return self._mock

    @property
    def browser_lock(self) -> asyncio.Lock:
        """Lock that serialises all browser navigate→extract sequences."""
        return self._conn.browser_lock

    async def _connect(self) -> None:
        """Spawn the Bright Data MCP server over stdio. Lazy."""
        if self._mock:
            raise MCPNotAvailable("MCP client running in mock mode")

        async with self._conn.lock:
            if self._conn.session is not None:
                return
            try:
                # Imported lazily so the foundation install can run without
                # the mcp package fully wired up in CI.
                from mcp import ClientSession, StdioServerParameters
                from mcp.client.stdio import stdio_client
            except ImportError as e:  # pragma: no cover — defensive
                raise MCPNotAvailable(f"mcp package not installed: {e}") from e

            env = {
                "API_TOKEN": config.BRIGHTDATA_API_TOKEN or "",
                "WEB_UNLOCKER_ZONE": config.BRIGHTDATA_WEB_UNLOCKER_ZONE or "",
                "BROWSER_ZONE": config.BRIGHTDATA_SCRAPING_BROWSER_ZONE or "",
                "GROUPS": config.MCP_GROUPS,
            }
            params = StdioServerParameters(
                command=config.MCP_COMMAND,
                args=config.MCP_ARGS,
                env=env,
            )
            stack = AsyncExitStack()
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()

            self._conn.session = session
            self._conn.stack = stack
            log.info("Bright Data MCP session initialized")

    async def call(self, tool: str, args: dict[str, Any] | None = None) -> Any:
        """Invoke an MCP tool. Raises MCPNotAvailable in mock mode.

        Every call — successful, failed, or mock-mode-skipped — is recorded
        into the per-request transcript (when one is bound via
        ``capture_transcript``) and the singleton's running list, and is
        pushed to the SSE event queue if one is bound. Failures and
        mock-mode skips still emit so the rail honestly reflects what the
        agent attempted, not just what succeeded.
        """
        args = args or {}
        module = _active_module.get()
        start = time.perf_counter()

        if self._mock:
            # In mock mode the live path is unreachable. We DON'T emit
            # this as a UI event — the module's own ``record_simulated``
            # calls produce the visible trace. This branch is kept for
            # debugging (anything that bypasses ``record_simulated`` and
            # tries ``call()`` directly will at least leave a trail).
            self.transcript.append(MCPToolCall(
                tool=tool, args=args, ok=False, duration_ms=0,
                error="mock mode", module=module,
            ))
            raise MCPNotAvailable(f"mock mode — cannot call {tool}")

        await self._connect()
        assert self._conn.session is not None

        try:
            result = await self._conn.session.call_tool(tool, args)
            duration_ms = int((time.perf_counter() - start) * 1000)
            preview = str(result)[:200]
            _append_record(MCPToolCall(
                tool=tool, args=args, ok=True,
                duration_ms=duration_ms, result_preview=preview,
                module=module,
            ))
            return result
        except Exception as e:
            duration_ms = int((time.perf_counter() - start) * 1000)
            _append_record(MCPToolCall(
                tool=tool, args=args, ok=False,
                duration_ms=duration_ms, error=str(e),
                module=module,
            ))
            raise

    async def list_tools(self) -> list[str]:
        """Return the tool names exposed by the MCP server. Empty in mock mode."""
        if self._mock:
            return []
        await self._connect()
        assert self._conn.session is not None
        listing = await self._conn.session.list_tools()
        return [t.name for t in getattr(listing, "tools", [])]

    async def aclose(self) -> None:
        if self._conn.stack is not None:
            await self._conn.stack.aclose()
            self._conn.stack = None
            self._conn.session = None
