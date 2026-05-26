"""Centralized environment / runtime config.

Read once at import time. Never read os.environ from anywhere else.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_ROOT.parent

# Load .env files in priority order. Later loads do NOT overwrite earlier ones.
load_dotenv(_BACKEND_ROOT / ".env", override=False)
load_dotenv(_REPO_ROOT / ".env", override=False)


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


# ── LLM ────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str | None = os.environ.get("ANTHROPIC_API_KEY") or None
LLM_MODEL: str = os.environ.get("ATLAS_LLM_MODEL", "claude-sonnet-4-6")

# ── Bright Data MCP ────────────────────────────────────────────────
BRIGHTDATA_API_TOKEN: str | None = os.environ.get("BRIGHTDATA_API_TOKEN") or None
BRIGHTDATA_WEB_UNLOCKER_ZONE: str | None = os.environ.get("BRIGHTDATA_WEB_UNLOCKER_ZONE") or None
BRIGHTDATA_SCRAPING_BROWSER_ZONE: str | None = os.environ.get("BRIGHTDATA_SCRAPING_BROWSER_ZONE") or None
BRIGHTDATA_SERP_ZONE: str | None = os.environ.get("BRIGHTDATA_SERP_ZONE") or None
BRIGHTDATA_WEB_SCRAPER_ZONE: str | None = os.environ.get("BRIGHTDATA_WEB_SCRAPER_ZONE") or None

MCP_COMMAND: str = os.environ.get("ATLAS_MCP_COMMAND", "npx")
MCP_ARGS: list[str] = _split_csv(os.environ.get("ATLAS_MCP_ARGS")) or ["-y", "@brightdata/mcp"]
# Tool groups to expose from the Bright Data MCP server.
# "browser" unlocks scraping_browser_navigate, scraping_browser_get_text, etc.
# "social"  unlocks web_data_linkedin_job_listings and the other dataset
#           tools that Signal and AltData rely on.
# Default to both so the live path matches the README's "5 of 6 Bright Data
# products" pitch without requiring a manual env toggle before the demo.
MCP_GROUPS: str = os.environ.get("BRIGHTDATA_MCP_GROUPS", "browser,social")

# ── Mode ───────────────────────────────────────────────────────────
MODE: str = os.environ.get("ATLAS_MODE", "mock").lower()


def is_mock_mode() -> bool:
    return MODE != "live"


def has_llm() -> bool:
    return bool(ANTHROPIC_API_KEY)


def has_brightdata_creds() -> bool:
    return bool(BRIGHTDATA_API_TOKEN)


# ── Backend server ────────────────────────────────────────────────
HOST: str = os.environ.get("ATLAS_BACKEND_HOST", "127.0.0.1")
PORT: int = int(os.environ.get("ATLAS_BACKEND_PORT", "8000"))
CORS_ORIGINS: list[str] = _split_csv(os.environ.get("ATLAS_CORS_ORIGINS")) or [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
]

# ── Paths ──────────────────────────────────────────────────────────
REPO_ROOT: Path = _REPO_ROOT
BACKEND_ROOT: Path = _BACKEND_ROOT
RUNTIME_DIR: Path = _BACKEND_ROOT / "runtime"
BRIEFS_DIR: Path = RUNTIME_DIR / "briefs"
BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
