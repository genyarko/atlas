"""Scraping Browser helpers using the Bright Data MCP browser automation tools.

With GROUPS=browser in the MCP server environment, the following tools are
available and used here:

    scraping_browser_navigate   — goto a URL with optional geo-routed country
    scraping_browser_get_text   — extract visible text from the current page
    scraping_browser_screenshot — capture a PNG of the current page

The checkout_session function runs TruePrice's geo-priced checkout flow:
1. Navigate to the pricing URL through a residential proxy in the target country.
2. Extract the sticker price from the live page (catches price changes in real time).
3. Apply the country's known statutory VAT/GST rate to compute the true landed cost.
4. Optionally navigate to the cart URL and extract cart-level totals.

The browser session inside the Bright Data MCP server is stateful: switching
countries spawns a fresh browser session. To keep concurrent modules (e.g.
the executor running trueprice and visual in parallel) from clobbering each
other's navigate→extract sequences, every browser entrypoint here holds
``client.browser_lock`` across its navigate+extract pair. TruePrice also
runs its regions sequentially on top of that so per-country state can't
interleave within a single module.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from .mcp_client import BrightDataMCPClient, MCPNotAvailable

log = logging.getLogger(__name__)

# ── VAT / GST rates by ISO-2 country code ──────────────────────────
_VAT: dict[str, float] = {
    "gb": 0.20,   # UK 20% VAT
    "de": 0.19,   # Germany 19% MwSt
    "fr": 0.20,   # France 20% TVA
    "nl": 0.21,   # Netherlands 21% BTW
    "be": 0.21,   # Belgium 21% BTW
    "se": 0.25,   # Sweden 25% Moms
    "no": 0.25,   # Norway 25%
    "dk": 0.25,   # Denmark 25%
    "at": 0.20,   # Austria 20%
    "ch": 0.081,  # Switzerland 8.1%
    "au": 0.10,   # Australia 10% GST
    "nz": 0.15,   # New Zealand 15% GST
    "sg": 0.09,   # Singapore 9% GST
    "in": 0.18,   # India 18% GST
    "ca": 0.05,   # Canada 5% federal GST
    "jp": 0.10,   # Japan 10% Consumption Tax
    "us": 0.0,    # US — varies by state; excluded for SaaS at federal level
}

_TAX_LABELS: dict[str, str] = {
    "gb": "VAT (20%)",
    "de": "MwSt (19%)",
    "fr": "TVA (20%)",
    "nl": "BTW (21%)",
    "be": "BTW (21%)",
    "se": "Moms (25%)",
    "no": "MVA (25%)",
    "dk": "Moms (25%)",
    "at": "MwSt (20%)",
    "ch": "MWST (8.1%)",
    "au": "GST (10%)",
    "nz": "GST (15%)",
    "sg": "GST (9%)",
    "in": "GST (18%)",
    "ca": "GST (5%)",
    "jp": "Consumption Tax (10%)",
    "us": "",
}

# Matches "$8", "$8.00", "8/user", "8 per seat" etc.
_PRICE_RE = re.compile(
    r"\$\s*(\d+(?:\.\d+)?)"          # "$8" or "$8.00"
    r"|(\d+(?:\.\d+)?)\s*(?:USD|/mo|/month|per user|/user|per seat)",
    re.IGNORECASE,
)


def _extract_text(result: Any) -> str | None:
    content = getattr(result, "content", None)
    if isinstance(content, list):
        for part in content:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                return text
    text = getattr(result, "text", None)
    return text if isinstance(text, str) else None


def _parse_sticker(text: str) -> float | None:
    """Extract the first plausible per-seat-per-month price from page text."""
    for m in _PRICE_RE.finditer(text):
        raw = m.group(1) or m.group(2)
        try:
            price = float(raw)
        except (ValueError, TypeError):
            continue
        if 1.0 <= price <= 500.0:   # sanity range: $1–$500/seat/mo
            return price
    return None


async def screenshot(url: str, *, region: str | None = None) -> bytes | None:
    """Navigate to ``url`` and return a PNG screenshot. None on failure."""
    client = BrightDataMCPClient.get()
    nav_args: dict[str, Any] = {"url": url}
    if region:
        nav_args["country"] = region.lower()
    try:
        async with client.browser_lock:
            await client.call("scraping_browser_navigate", nav_args)
            result = await client.call("scraping_browser_screenshot", {})
    except MCPNotAvailable:
        return None
    except Exception as exc:
        log.warning("screenshot failed for %s: %s", url, exc)
        return None
    content = getattr(result, "content", None)
    if isinstance(content, list):
        for part in content:
            data = getattr(part, "data", None) or getattr(part, "bytes", None)
            if data:
                return data if isinstance(data, bytes) else None
    return None


async def navigate_and_extract(url: str, script: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Navigate to ``url`` and return visible page text (simplified extract)."""
    client = BrightDataMCPClient.get()
    try:
        async with client.browser_lock:
            await client.call("scraping_browser_navigate", {"url": url})
            result = await client.call("scraping_browser_get_text", {})
    except MCPNotAvailable:
        return None
    text = _extract_text(result)
    return {"page_text": text} if text else None


async def checkout_session(
    *,
    target_url: str,
    region_country: str,
    script: list[dict[str, Any]],
    locale: str | None = None,
    timeout_ms: int = 45_000,
) -> dict[str, Any] | None:
    """Run a geo-routed pricing-page session to extract true landed cost.

    Flow:
    1. Navigate to the pricing URL through a residential proxy in ``region_country``.
    2. Extract the sticker price from the live page.
    3. Compute landed cost = sticker × (1 + local VAT/GST rate).
    4. Return a dict that ``parse_checkout_extract`` can ingest directly.

    Returns None when the MCP browser tools are unavailable (e.g. GROUPS=browser
    not set), or when the pricing page returns no parseable price.
    """
    client = BrightDataMCPClient.get()
    country = region_country.lower()
    timeout = timeout_ms / 1000

    # ── Steps 1 + 2: Navigate then extract (held under browser_lock) ─
    # browser_lock serialises all navigate→get_text sequences across
    # concurrent modules so they don't clobber the shared browser session.
    try:
        async with client.browser_lock:
            try:
                await asyncio.wait_for(
                    client.call("scraping_browser_navigate", {
                        "url": target_url,
                        "country": country,
                    }),
                    timeout=min(timeout, 25.0),
                )
            except MCPNotAvailable:
                return None
            except asyncio.TimeoutError:
                log.warning("checkout_session: navigate timed out for %s", country)
                return None
            except Exception as exc:
                log.warning("checkout_session: navigate failed for %s: %s", country, exc)
                return None

            try:
                text_result = await asyncio.wait_for(
                    client.call("scraping_browser_get_text", {}),
                    timeout=15.0,
                )
            except (asyncio.TimeoutError, Exception) as exc:
                log.warning("checkout_session: get_text failed for %s: %s", country, exc)
                return None
    except MCPNotAvailable:
        return None

    text = _extract_text(text_result)
    if not text:
        log.info("checkout_session: empty page text for %s at %s", country, target_url)
        return None

    # ── Step 3: Parse sticker price ──────────────────────────────────
    sticker = _parse_sticker(text)
    if sticker is None:
        log.info("checkout_session: no price found for %s at %s", country, target_url)
        return None

    log.info("checkout_session: sticker=%.2f for %s from %s", sticker, country, target_url)

    # ── Step 4: Apply statutory VAT/GST ─────────────────────────────
    vat_rate = _VAT.get(country, 0.0)
    tax_label = _TAX_LABELS.get(country, "Tax") if vat_rate else ""
    tax_amount = round(sticker * vat_rate, 2)
    total = round(sticker + tax_amount, 2)

    # Return in the shape that parse_checkout_extract expects.
    return {
        "list_price": str(sticker),
        "tax_label": tax_label,
        "tax_amount": str(tax_amount),
        "fees": "0",
        "total": str(total),
        "currency": "USD",   # Linear quotes USD globally
        "billing_country": country.upper(),
    }
