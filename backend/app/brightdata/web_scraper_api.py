"""Web Scraper API helper — structured extraction for careers pages, G2, Glassdoor.

Uses ``scrape_as_markdown`` (the tool exposed by the current Bright Data MCP
build) to fetch and parse job listings and review data. Each helper returns a
normalized list of dicts, or ``None`` when MCP is unavailable (mock mode /
missing creds).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .mcp_client import BrightDataMCPClient, MCPNotAvailable

log = logging.getLogger(__name__)


async def _scrape_text(url: str) -> str | None:
    """Scrape ``url`` as markdown text. Returns None on any failure."""
    try:
        result = await BrightDataMCPClient.get().call("scrape_as_markdown", {"url": url})
    except MCPNotAvailable:
        return None
    except Exception as exc:
        log.warning("scrape_as_markdown failed for %s: %s", url, exc)
        return None
    content = getattr(result, "content", None)
    if isinstance(content, list):
        for part in content:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                return text
    return None


async def _browser_scrape_text(url: str) -> str | None:
    """Navigate to ``url`` with a real browser and return visible page text.

    Uses ``scraping_browser_navigate`` + ``scraping_browser_get_text`` so the
    page fully renders (JS executed, login-wall check pages resolved).
    Holds ``client.browser_lock`` for the full navigate→get_text sequence so
    concurrent modules don't clobber the shared browser session.
    Falls back to None when the browser tools aren't available.
    """
    client = BrightDataMCPClient.get()
    try:
        async with client.browser_lock:
            await client.call("scraping_browser_navigate", {"url": url})
            result = await client.call("scraping_browser_get_text", {})
    except MCPNotAvailable:
        return None
    except Exception as exc:
        log.warning("browser scrape failed for %s: %s", url, exc)
        return None
    content = getattr(result, "content", None)
    if isinstance(content, list):
        for part in content:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                return text
    text = getattr(result, "text", None)
    return text if isinstance(text, str) else None


async def _discover_glassdoor_url(company: str) -> str | None:
    """Return the company's Glassdoor Reviews URL via SERP, or None."""
    try:
        result = await BrightDataMCPClient.get().call(
            "search_engine",
            {
                "query": f'site:glassdoor.com/Reviews "{company}" software reviews',
                "num_results": 5,
            },
        )
    except (MCPNotAvailable, Exception):
        return None
    if result is None:
        return None
    content = getattr(result, "content", None)
    if isinstance(content, list):
        for part in content:
            text = getattr(part, "text", None)
            if not isinstance(text, str):
                continue
            try:
                data = json.loads(text)
            except (ValueError, TypeError):
                continue
            slug = company.lower().replace(" ", "")
            for item in data.get("organic", []):
                link = item.get("link", "")
                # Require the company name (lowercased) to appear in the URL path
                # so we don't pick up similarly-named companies.
                if (
                    "glassdoor.com" in link
                    and "Reviews" in link
                    and slug in link.lower()
                ):
                    log.info("Glassdoor URL via SERP: %s", link)
                    return link
    return None


def _is_bot_blocked(text: str) -> bool:
    """Return True when the scraped text is a bot-detection / challenge page."""
    indicators = ("humans only", "ray id:", "cloudflare", "access denied",
                  "please enable cookies", "verify you are human")
    lower = text.lower()
    return any(ind in lower for ind in indicators)


# Matches the escaped-bracket link format produced by Bright Data's
# scrape_as_markdown for career listing pages:
#   \[\n\nTitle\n\nLocationLearn more→\n\n]\(/careers/UUID)
_CAREER_LINK_RE = re.compile(
    r"\\\[\n\n([^\n]+)\n\n([^\n]+?)Learn more→\n\n\]\\\((/careers/[0-9a-f-]+)\)"
)

# Also try a simpler link format some pages use: [Title](url)
_SIMPLE_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+/careers?/[^)]+)\)")

# Rating patterns — strict requires "out of 5" / "/5" / "stars"; standalone
# matches a lone "4.5" on its own line (common in browser-rendered review text).
_RATING_RE = re.compile(r"(\d\.\d)\s*(?:out of 5|/5|stars?)", re.IGNORECASE)
_RATING_STANDALONE_RE = re.compile(r"(?:^|\n)(\d\.\d)(?:\r?\n|$)")


def _parse_careers_markdown(
    md: str, *, base_url: str, company: str, days_ago: int = 14
) -> list[dict[str, Any]]:
    """Extract job listings from a careers-page markdown blob."""
    rows: list[dict[str, Any]] = []

    # Try the Linear-style escaped-bracket format first.
    for m in _CAREER_LINK_RE.finditer(md):
        title = m.group(1).strip()
        location = m.group(2).strip()
        path = m.group(3)
        if not title:
            continue
        rows.append({
            "title": title,
            "location": location or "Remote",
            "url": f"{base_url}{path}",
            "days_ago": days_ago,
        })

    # Fallback: plain markdown links whose URL contains /career(s)/.
    if not rows:
        for m in _SIMPLE_LINK_RE.finditer(md):
            title = m.group(1).strip()
            url = m.group(2).strip()
            if not title or not url:
                continue
            rows.append({"title": title, "location": "Remote", "url": url, "days_ago": days_ago})

    return rows


async def fetch_company_careers_jobs(
    company: str, *, location: str | None = None, limit: int = 50
) -> list[dict[str, Any]] | None:
    """Fetch job postings by scraping the company's own careers page.

    Named for what it actually does. Previous name (``fetch_linkedin_jobs``)
    was aspirational — the Bright Data MCP build at this zone exposes
    ``scrape_as_markdown`` but not the LinkedIn dataset tool, so we
    scrape the company's careers URL directly and parse the markdown.
    True LinkedIn dataset helpers (``fetch_linkedin_people`` etc.) live
    alongside this and use ``web_data_*`` tools instead.
    """
    # Try a search to discover the careers URL first.
    careers_url = await _discover_careers_url(company)
    if not careers_url:
        return None

    md = await _scrape_text(careers_url)
    if not md:
        return None

    # Derive the base URL (scheme + host) for relative path resolution.
    from urllib.parse import urlparse
    parsed = urlparse(careers_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    rows = _parse_careers_markdown(md, base_url=base_url, company=company)
    log.info("fetch_company_careers_jobs: %d roles found at %s", len(rows), careers_url)
    return rows[:limit] if rows else None


async def _discover_careers_url(company: str) -> str | None:
    """Return the company's careers page URL via SERP or pattern guessing."""
    try:
        result = await BrightDataMCPClient.get().call(
            "search_engine",
            {"query": f"{company} careers jobs open positions site:apply", "num_results": 5},
        )
    except (MCPNotAvailable, Exception):
        result = None

    if result is not None:
        content = getattr(result, "content", None)
        if isinstance(content, list):
            for part in content:
                text = getattr(part, "text", None)
                if not isinstance(text, str):
                    continue
                try:
                    data = json.loads(text)
                except (ValueError, TypeError):
                    continue
                slug = company.lower().replace(" ", "")
                for item in data.get("organic", []):
                    link = item.get("link", "")
                    if slug in link.lower() and ("career" in link.lower() or "/jobs" in link.lower()):
                        log.info("Careers URL discovered via SERP: %s", link)
                        return link

    # Pattern-based fallback for common SaaS company URL structures.
    slug = company.lower().replace(" ", "")
    candidates = [
        f"https://{slug}.app/careers",
        f"https://{slug}.com/careers",
        f"https://{slug}.io/careers",
        f"https://careers.{slug}.com",
    ]
    # Return the first candidate (scraper handles 404 / empty result gracefully).
    log.info("Careers URL via pattern fallback: %s", candidates[0])
    return candidates[0]


async def fetch_glassdoor_reviews(
    company: str, *, limit: int = 50,
) -> list[dict[str, Any]] | None:
    """Scrape Glassdoor reviews for ``company``.

    Uses the Web Unlocker zone (scrape_as_markdown) as the primary path
    because it handles Cloudflare / bot-detection challenges. The browser
    zone is not used here — Glassdoor returns a challenge page to the
    browser but allows the unlocker's residential-proxy traffic through.

    Waterfall:
    1. SERP → canonical /Reviews/ URL → scrape_as_markdown.
    2. Pattern-fallback /Reviews/ URL → scrape_as_markdown.
    3. Glassdoor search results page → scrape_as_markdown.
    """
    reviews_url = await _discover_glassdoor_url(company)
    slug = company.lower().replace(" ", "-")

    # ── Attempt 1: canonical reviews URL via unlocker ────────────────
    if reviews_url:
        md = await _scrape_text(reviews_url)
        if md and not _is_bot_blocked(md):
            rows = _parse_review_markdown(md, source="glassdoor", company=company)
            if rows:
                log.info("fetch_glassdoor_reviews: %d rows for %s", len(rows), company)
                return rows[:limit]

    # ── Attempt 2: pattern-fallback URL ──────────────────────────────
    pattern_url = f"https://www.glassdoor.com/Reviews/{slug}-Reviews.htm"
    if pattern_url != reviews_url:
        md = await _scrape_text(pattern_url)
        if md and not _is_bot_blocked(md):
            rows = _parse_review_markdown(md, source="glassdoor", company=company)
            if rows:
                log.info("fetch_glassdoor_reviews: %d rows (pattern URL) for %s", len(rows), company)
                return rows[:limit]

    # ── Attempt 3: Glassdoor search page ─────────────────────────────
    search_url = f"https://www.glassdoor.com/Search/results.htm?keyword={slug.replace('-', '+')}"
    md = await _scrape_text(search_url)
    if not md or _is_bot_blocked(md):
        log.info("fetch_glassdoor_reviews: all attempts failed for %s", company)
        return None
    rows = _parse_review_markdown(md, source="glassdoor", company=company)
    log.info("fetch_glassdoor_reviews: %s rows (search page) for %s", len(rows) if rows else 0, company)
    return rows[:limit] if rows else None


async def fetch_g2_reviews(
    company: str, *, limit: int = 50,
) -> list[dict[str, Any]] | None:
    """Scrape G2 reviews for ``company``.

    Uses Web Unlocker (scrape_as_markdown) as the primary path — avoids
    browser lock contention with TruePrice and returns structured content
    including inline ratings. Falls back to browser if the unlocker yields
    no parseable ratings.
    """
    slug = company.lower().replace(" ", "-")
    url = f"https://www.g2.com/products/{slug}/reviews"

    # ── Primary: web unlocker ────────────────────────────────────────
    md = await _scrape_text(url)
    if md and not _is_bot_blocked(md):
        rows = _parse_review_markdown(md, source="g2", company=company)
        if rows:
            log.info("fetch_g2_reviews: %d rows via unlocker for %s", len(rows), company)
            return rows[:limit]

    # ── Fallback: real browser ────────────────────────────────────────
    text = await _browser_scrape_text(url)
    if not text or _is_bot_blocked(text):
        return None
    rows = _parse_review_markdown(text, source="g2", company=company)
    log.info("fetch_g2_reviews: %s rows (browser) for %s", len(rows) if rows else 0, company)
    return rows[:limit] if rows else None


def _parse_review_markdown(
    text: str, *, source: str, company: str
) -> list[dict[str, Any]] | None:
    """Extract review signals from scraped page text (markdown or browser text).

    Returns rows compatible with ``altdata_data.normalize()``:
    ``rating`` (float), ``text`` (str), ``url`` (str), ``days_ago`` (int).
    Returns None when fewer than 2 valid ratings are found.

    Two passes:
    1. Strict  — "4.5 out of 5" / "4.5 stars" patterns (high precision).
    2. Standalone — lone "4.5" on its own line (common in browser-rendered
       review pages where the star graphic and score appear on separate lines).
    """
    ratings: list[float] = []

    for m in _RATING_RE.finditer(text):
        try:
            val = float(m.group(1))
            if 1.0 <= val <= 5.0:
                ratings.append(val)
        except (ValueError, TypeError):
            pass

    if not ratings:
        for m in _RATING_STANDALONE_RE.finditer(text):
            try:
                val = float(m.group(1))
                if 1.0 <= val <= 5.0:
                    ratings.append(val)
            except (ValueError, TypeError):
                pass

    rows: list[dict[str, Any]] = [
        {
            "rating": r,
            "text": f"Extracted from {source} page for {company}.",
            "url": "",
            "days_ago": 14 + i * 7,
        }
        for i, r in enumerate(ratings[:50])
    ]
    return rows if len(rows) >= 2 else None


# ── LinkedIn dataset helpers (Bright Data web_data_* MCP tools) ──────
#
# These hit the structured LinkedIn People / Companies datasets via the
# MCP ``web_data_*`` tool family (enabled by the ``social`` group in
# BRIGHTDATA_MCP_GROUPS). Unlike scrape_as_markdown, the dataset path
# returns parsed records — no HTML parsing, no anti-bot risk. The MCP
# server handles snapshot triggering and polling internally so callers
# see a synchronous request/response.
#
# Tool names follow the @brightdata/mcp convention. If a call raises an
# "unknown tool" error in a new package build, run BrightDataMCPClient
# .get().list_tools() to discover the current names and update the
# constants below.

_LINKEDIN_COMPANY_TOOL = "web_data_linkedin_company_profile"
_LINKEDIN_PERSON_TOOL  = "web_data_linkedin_people_search"


def _parse_dataset_rows(result: Any) -> list[dict[str, Any]] | None:
    """Coerce an MCP dataset response into a list of row dicts.

    Bright Data dataset tools wrap their output a few different ways
    depending on package version; this accepts:
    - ``result.results`` as a list of dicts
    - ``result.content[].text`` containing a JSON list
    - ``result.content[].text`` containing a JSON object with ``rows`` /
      ``data`` / ``results`` keys
    - a single-record JSON object (returned as a 1-row list)
    """
    if result is None:
        return None
    items = getattr(result, "results", None)
    if isinstance(items, list):
        rows = [r for r in items if isinstance(r, dict)]
        if rows:
            return rows
    content = getattr(result, "content", None)
    if isinstance(content, list):
        out: list[dict[str, Any]] = []
        for part in content:
            text = getattr(part, "text", None)
            if not isinstance(text, str):
                continue
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError):
                continue
            if isinstance(parsed, list):
                out.extend(r for r in parsed if isinstance(r, dict))
            elif isinstance(parsed, dict):
                for key in ("rows", "data", "results", "organic"):
                    sub = parsed.get(key)
                    if isinstance(sub, list):
                        out.extend(r for r in sub if isinstance(r, dict))
                        break
                else:
                    if parsed:
                        out.append(parsed)
        if out:
            return out
    return None


async def fetch_linkedin_company_profile(
    url: str,
) -> dict[str, Any] | None:
    """Fetch a single LinkedIn company profile by URL via the Bright Data dataset.

    Requires the ``web_data_linkedin_company_profile`` dataset to be active
    on your Bright Data account (dashboard → Datasets). Returns a structured
    dict (name, industry, size, hq, recent_posts, …) or None on any failure.

    This is an *optional enrichment* helper — the Investor live path uses
    SERP-based discovery and does not depend on this call. Use it to enrich
    a specific firm record after discovery.
    """
    try:
        result = await BrightDataMCPClient.get().call(
            _LINKEDIN_COMPANY_TOOL, {"url": url}
        )
    except MCPNotAvailable:
        return None
    except Exception as exc:
        log.warning("fetch_linkedin_company_profile failed for %s: %s", url, exc)
        return None
    rows = _parse_dataset_rows(result)
    return rows[0] if rows else None


async def fetch_linkedin_person(
    url: str,
    first_name: str,
    last_name: str,
) -> dict[str, Any] | None:
    """Fetch a single LinkedIn person profile by URL + name via the dataset.

    Requires the ``web_data_linkedin_people_search`` dataset to be active.
    ``url`` must be the person's profile URL (e.g. linkedin.com/in/slug).
    ``first_name`` and ``last_name`` are required by the tool for identity
    verification.

    This is an *optional enrichment* helper — use after discovering a
    person via SERP to get structured profile data (headline, experience,
    education, connections).
    """
    try:
        result = await BrightDataMCPClient.get().call(
            _LINKEDIN_PERSON_TOOL,
            {"url": url, "first_name": first_name, "last_name": last_name},
        )
    except MCPNotAvailable:
        return None
    except Exception as exc:
        log.warning("fetch_linkedin_person failed for %s: %s", url, exc)
        return None
    rows = _parse_dataset_rows(result)
    return rows[0] if rows else None
