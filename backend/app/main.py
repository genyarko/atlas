"""FastAPI entrypoint for Atlas."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from . import config
from .agent import run_agent
from .brief import render_html, render_markdown, render_pdf, write_html
from .brightdata import MCPToolCall, capture_transcript
from .models import Brief, Question
from .modules import MODULE_CATALOG

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

app = FastAPI(title="Atlas API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory store for the foundation. Day 8+ swaps in SQLite.
_BRIEFS: dict[str, Brief] = {}
# Per-brief MCP transcript captured during run_agent. Lives alongside
# the Brief so the rail can be re-fetched after the request completes.
_BRIEF_TRANSCRIPTS: dict[str, list[MCPToolCall]] = {}


# ── Schemas ────────────────────────────────────────────────────────


class AskRequest(BaseModel):
    question: str
    user_context: dict | None = None


class AskResponse(BaseModel):
    brief: Brief
    html_url: str
    pdf_url: str
    markdown: str


# ── Routes ─────────────────────────────────────────────────────────


@app.get("/api/health")
async def health() -> dict:
    return {
        "ok": True,
        "version": app.version,
        "mode": config.MODE,
        "llm_configured": config.has_llm(),
        "brightdata_configured": config.has_brightdata_creds(),
    }


@app.get("/api/modules")
async def list_modules() -> dict:
    return {"modules": MODULE_CATALOG}


def _persist(brief: Brief, transcript: list[MCPToolCall]) -> None:
    _BRIEFS[brief.id] = brief
    _BRIEF_TRANSCRIPTS[brief.id] = list(transcript)
    write_html(brief)
    # Mirror the brief JSON to disk so a process restart between
    # request and PDF/JSON fetch still serves it.
    json_path = config.BRIEFS_DIR / f"{brief.id}.json"
    json_path.write_text(brief.model_dump_json(), encoding="utf-8")


@app.post("/api/ask", response_model=AskResponse)
async def ask(req: AskRequest) -> AskResponse:
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="question is required")

    question = Question(text=req.question.strip(), user_context=req.user_context)
    async with capture_transcript() as transcript:
        brief = await run_agent(question)
    _persist(brief, transcript)

    return AskResponse(
        brief=brief,
        html_url=f"/api/briefs/{brief.id}.html",
        pdf_url=f"/api/briefs/{brief.id}.pdf",
        markdown=render_markdown(brief),
    )


# ── Streaming variant ─────────────────────────────────────────────
#
# Same agent, same output — but every MCP tool call, module-start, and
# module-done event is pushed to the browser as Server-Sent Events while
# the request is in flight. The frontend renders these into the "Live
# infrastructure trace" rail so a non-technical viewer can see the
# Bright Data work happening, not just the final brief.


def _sse_pack(event: str, payload) -> bytes:
    body = json.dumps(payload, default=str)
    return f"event: {event}\ndata: {body}\n\n".encode("utf-8")


@app.post("/api/ask/stream")
async def ask_stream(req: AskRequest) -> StreamingResponse:
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="question is required")

    question = Question(text=req.question.strip(), user_context=req.user_context)

    async def event_gen() -> AsyncIterator[bytes]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=2048)
        sentinel = object()

        async def driver() -> None:
            try:
                async with capture_transcript(event_queue=queue) as transcript:
                    brief = await run_agent(question)
                _persist(brief, transcript)
                response = AskResponse(
                    brief=brief,
                    html_url=f"/api/briefs/{brief.id}.html",
                    pdf_url=f"/api/briefs/{brief.id}.pdf",
                    markdown=render_markdown(brief),
                )
                await queue.put(("brief", response.model_dump(mode="json")))
            except Exception as e:  # pragma: no cover — defensive
                logging.exception("ask_stream agent run failed")
                await queue.put(("error", {"message": str(e)}))
            finally:
                await queue.put(("done", sentinel))

        task = asyncio.create_task(driver())
        # Open the stream with a hello so the browser can transition out
        # of any "connecting" state immediately.
        yield _sse_pack("hello", {"question": question.text})
        try:
            while True:
                event, payload = await queue.get()
                if payload is sentinel:
                    break
                yield _sse_pack(event, payload)
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        # Disable proxy buffering so each event flushes immediately.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Suffixed routes MUST be declared before the bare-id JSON route below —
# FastAPI's `{brief_id}` capture is greedy and would otherwise swallow `.html`.


@app.get("/api/briefs/{brief_id}.html", response_class=HTMLResponse)
async def get_brief_html(brief_id: str) -> HTMLResponse:
    brief = _BRIEFS.get(brief_id)
    if brief is None:
        # Fall back to disk in case the process was restarted.
        disk = Path(config.BRIEFS_DIR / f"{brief_id}.html")
        if disk.exists():
            return HTMLResponse(disk.read_text(encoding="utf-8"))
        raise HTTPException(status_code=404, detail="brief not found")
    return HTMLResponse(render_html(brief))


@app.get("/api/briefs/{brief_id}.md")
async def get_brief_md(brief_id: str):
    brief = _load_brief(brief_id)
    return {"markdown": render_markdown(brief)}


@app.get("/api/briefs/{brief_id}.pdf")
async def get_brief_pdf(brief_id: str) -> Response:
    brief = _BRIEFS.get(brief_id)
    if brief is None:
        disk = Path(config.BRIEFS_DIR / f"{brief_id}.pdf")
        if disk.exists():
            return Response(
                content=disk.read_bytes(),
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f'inline; filename="atlas-brief-{brief_id}.pdf"',
                },
            )
        # Try re-hydrating the brief from JSON and re-rendering.
        brief = _load_brief_or_none(brief_id)
        if brief is None:
            raise HTTPException(status_code=404, detail="brief not found")
    try:
        pdf_bytes = render_pdf(brief)
    except RuntimeError as exc:
        # WeasyPrint not installed — return a useful 503 so the UI can
        # show a clear "PDF export unavailable" message instead of 500.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="atlas-brief-{brief.subject}-{brief_id}.pdf"',
        },
    )


@app.get("/api/briefs/{brief_id}/transcript")
async def get_brief_transcript(brief_id: str) -> dict:
    """Return the captured MCP transcript for a brief.

    Powers the 'Live infrastructure trace' rail when the brief is loaded
    fresh (e.g. via a bookmarked URL) without the streaming connection.
    """
    transcript = _BRIEF_TRANSCRIPTS.get(brief_id)
    if transcript is None:
        # If the brief itself exists on disk but we lost the in-memory
        # transcript (e.g. after restart), return an empty list rather
        # than 404 so the rail just renders empty.
        brief = _load_brief_or_none(brief_id)
        if brief is None:
            raise HTTPException(status_code=404, detail="brief not found")
        transcript = []
    return {
        "brief_id": brief_id,
        "calls": [c.to_event() for c in transcript],
        "count": len(transcript),
    }


@app.get("/api/briefs/{brief_id}")
async def get_brief(brief_id: str) -> JSONResponse:
    brief = _load_brief(brief_id)
    return JSONResponse(brief.model_dump(mode="json"))


def _load_brief_or_none(brief_id: str) -> Brief | None:
    brief = _BRIEFS.get(brief_id)
    if brief is not None:
        return brief
    disk = config.BRIEFS_DIR / f"{brief_id}.json"
    if not disk.exists():
        return None
    brief = Brief.model_validate_json(disk.read_text(encoding="utf-8"))
    _BRIEFS[brief_id] = brief
    return brief


def _load_brief(brief_id: str) -> Brief:
    brief = _load_brief_or_none(brief_id)
    if brief is None:
        raise HTTPException(status_code=404, detail="brief not found")
    return brief
