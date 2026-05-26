# Architecture

This document is a runtime view of the foundation. The strategic view lives in [`implementation plan.md`](../implementation%20plan.md).

## Process model

```
┌──────────────┐    HTTP/JSON     ┌────────────────────────────────────┐
│  Next.js UI  │ ───────────────► │  FastAPI (backend/app/main.py)     │
│  (frontend/) │ ◄─────────────── │   • POST /api/ask                  │
└──────────────┘    Brief JSON    │   • GET  /api/briefs/{id}.html     │
                                  └────────────────┬───────────────────┘
                                                   │ asyncio
                                                   ▼
                                  ┌────────────────────────────────────┐
                                  │  LangGraph (agent/graph.py)        │
                                  │   plan → execute → synthesize      │
                                  └────────────────┬───────────────────┘
                                                   │
                                                   ▼
                                  ┌────────────────────────────────────┐
                                  │  Modules (modules/*.py)            │
                                  │   trueprice / signal / filing /    │
                                  │   altdata / visual / exposure      │
                                  └────────────────┬───────────────────┘
                                                   │ async (Day 3+)
                                                   ▼
                                  ┌────────────────────────────────────┐
                                  │  Bright Data MCP Server (stdio)    │
                                  │  (brightdata/mcp_client.py)        │
                                  └────────────────────────────────────┘
```

## Why these choices for the foundation

- **LangGraph over CrewAI.** We need conditional routing and parallel module execution. CrewAI's role-based crews fight that pattern.
- **Pydantic v2 everywhere.** The Brief schema is the contract between executor, synthesizer, and renderer. Validation at the boundary keeps the renderer template dumb.
- **Heuristic fallbacks.** Day 1-2 must run with no API keys. The planner has a keyword-router fallback; the synthesizer has a template fallback. As soon as `ANTHROPIC_API_KEY` is set, both nodes upgrade to Claude.
- **MCP wrapper is stdio-based.** Bright Data ships `@brightdata/mcp` via npx; we spawn it under the FastAPI process and speak MCP over stdio. The wrapper is a single connection pool, opened lazily on the first tool call.

## Brief flow contract

```
Question (str) ──► AgentState ──► Brief (Pydantic) ──► HTML (Jinja2) + JSON
```

Every node mutates `AgentState`. The `Brief` model is the rendering input; if it serializes, it renders.

## What's *not* in the foundation

- Persistence (no DB; briefs live in memory + disk under `backend/runtime/briefs/`)
- Authentication
- Streaming responses to the UI (the chat UI shows a spinner, then the whole brief)
- WeasyPrint PDF export (Day 8)
- Real MCP calls (stubs only; the wrapper exists but every module is gated on `ATLAS_MODE=mock` by default)
