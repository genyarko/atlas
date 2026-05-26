"""TruePrice data layer — region specs, FX, target configs, and price math.

Import-side-effect free and deterministic. The live path in
``trueprice.py`` feeds the per-region checkout extraction here for
normalization to USD; the mock path skips the checkout step but uses
the same region+FX tables so the resulting comparison table is
structurally identical to a live brief.

Design notes
------------
* Regions are keyed by ISO-3166 alpha-2 country codes so the
  Bright Data residential-proxy ``country`` argument is the same
  string we use for our internal accounting.
* FX rates are *USD per unit of the local currency* and pinned to a
  single snapshot date. In a production system this would be replaced
  by a daily rate pull; for the hackathon a frozen snapshot keeps the
  brief deterministic across demo runs.
* The "consumption tax" column is the *baseline* VAT/GST that applies
  to the demo plan in each jurisdiction. When the live checkout flow
  surfaces a different effective rate (e.g. reduced rate, B2B reverse
  charge), the live number wins and the baseline is treated as a
  validation backstop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Region specifications ──────────────────────────────────────────


@dataclass(frozen=True)
class RegionSpec:
    """A pricing region — the country we proxy through and how tax stacks."""

    code: str                  # ISO-3166 alpha-2 ("US", "GB", "DE")
    name: str                  # Human label
    proxy_country: str         # Bright Data residential proxy code (lowercased)
    currency: str              # ISO-4217 ("USD", "GBP", "EUR")
    consumption_tax_pct: float  # baseline VAT/GST applied at checkout
    consumption_tax_label: str  # "Sales Tax", "VAT", "GST"
    notes: str = ""             # any non-tax adders we expect to surface

    @property
    def is_baseline(self) -> bool:
        return self.code == "US"


# US is the baseline (no national consumption tax on SaaS in most
# states; per-state sales tax is collected at checkout but is treated
# as 0 for the baseline so the +delta in other regions is honest).
US = RegionSpec("US", "United States", "us", "USD", 0.00, "Sales Tax",
                notes="baseline; state sales tax varies and is excluded.")
GB = RegionSpec("GB", "United Kingdom", "gb", "GBP", 0.20, "VAT",
                notes="+20% VAT applies to SaaS subscriptions for UK billing addresses.")
DE = RegionSpec("DE", "Germany", "de", "EUR", 0.19, "VAT",
                notes="+19% VAT + EUR↔USD FX drag.")

# Demo "core 3" — used by the acceptance test.
DEMO_REGIONS: tuple[RegionSpec, ...] = (US, GB, DE)

# Wider catalog, kept for future expansion. The module only invokes
# what the planner asks for via the ``regions`` param.
REGION_CATALOG: dict[str, RegionSpec] = {
    r.code: r for r in (
        US, GB, DE,
        RegionSpec("IN", "India", "in", "INR", 0.18, "GST",
                   notes="+18% GST on B2B SaaS imports."),
        RegionSpec("BR", "Brazil", "br", "BRL", 0.265, "ICMS+ISS",
                   notes="compounded local taxes (ICMS+ISS) plus IOF transfer charge."),
        RegionSpec("AU", "Australia", "au", "AUD", 0.10, "GST",
                   notes="+10% GST on digital services."),
        RegionSpec("CA", "Canada", "ca", "CAD", 0.13, "HST",
                   notes="HST/GST depending on province; +13% used as a blended baseline."),
        RegionSpec("JP", "Japan", "jp", "JPY", 0.10, "JCT",
                   notes="+10% Japan Consumption Tax on B2B digital services."),
    )
}


def get_region(code: str) -> RegionSpec:
    """Look up a region by ISO code (case-insensitive). Raises KeyError if missing."""
    return REGION_CATALOG[code.upper()]


def resolve_regions(codes: list[str] | None) -> list[RegionSpec]:
    """Map a list of region codes to RegionSpec, with the US baseline pinned first.

    Always returns at least the demo trio so the brief never goes empty.
    Unknown codes are silently dropped — better a clipped table than a crash.
    """
    if not codes:
        return list(DEMO_REGIONS)
    seen: set[str] = set()
    out: list[RegionSpec] = [US]
    seen.add("US")
    # Then preserve caller order for the rest.
    for code in codes:
        code_up = code.upper()
        if code_up in seen:
            continue
        spec = REGION_CATALOG.get(code_up)
        if spec is None:
            continue
        out.append(spec)
        seen.add(code_up)
    return out or list(DEMO_REGIONS)


# ── FX snapshot (USD per unit of local currency) ────────────────────

FX_SNAPSHOT_DATE: str = "2026-05-15"  # last refreshed; pinned for determinism

USD_PER_UNIT: dict[str, float] = {
    "USD": 1.00,
    "GBP": 1.26,
    "EUR": 1.08,
    "INR": 0.012,
    "BRL": 0.20,
    "AUD": 0.66,
    "CAD": 0.73,
    "JPY": 0.0064,
}


def to_usd(amount: float, currency: str) -> float:
    """Convert a local-currency amount to USD using the pinned snapshot."""
    rate = USD_PER_UNIT.get(currency.upper())
    if rate is None:
        raise KeyError(f"No FX rate for {currency!r}")
    return round(amount * rate, 4)


# ── Pricing quote types ────────────────────────────────────────────


# How a quote was produced. ``cart_extract`` means a Scraping Browser
# session reached the checkout page and the parser pulled real numbers
# out of it. ``baseline_tax`` means we synthesized the true cost from
# the region's baseline VAT/GST table — either because no session ran
# (mock mode) or because the session returned no usable fields.
QuoteSource = str  # Literal["cart_extract", "baseline_tax"] but kept loose for dict round-trips


@dataclass
class PriceQuote:
    """A single region's quote, in both local currency and normalized USD.

    ``sticker_local`` is the list price the public pricing page advertises
    (in the region's local currency). ``true_local`` is what the checkout
    summary actually requires — tax + mandatory fees included.

    All USD numbers go through the FX snapshot so the comparison table
    is apples-to-apples across regions.
    """

    region: RegionSpec
    plan_id: str
    plan_label: str
    sticker_local: float
    true_local: float
    sticker_usd: float
    true_usd: float
    breakdown: list[dict[str, Any]] = field(default_factory=list)
    via: str = "scraping_browser"            # bright data tool that fetched it
    source: QuoteSource = "baseline_tax"     # how the true_local number was produced
    source_url: str = ""
    # % by which the true cost in USD exceeds the US sticker baseline.
    # Filled in by ``annotate_deltas`` once the US baseline is known;
    # zero until then.
    delta_pct: float = 0.0

    def to_table_row(self) -> dict[str, Any]:
        return {
            "region": self.region.code,
            "region_name": self.region.name,
            "currency": self.region.currency,
            "plan": self.plan_label,
            "sticker_local": round(self.sticker_local, 2),
            "true_local": round(self.true_local, 2),
            "sticker_usd": round(self.sticker_usd, 2),
            "true_usd": round(self.true_usd, 2),
            "delta_pct": round(self.delta_pct, 1),
            "tax_label": self.region.consumption_tax_label,
            "tax_pct": round(self.region.consumption_tax_pct * 100, 1),
            "notes": self.region.notes,
            "breakdown": self.breakdown,
            "source": self.source,
            "source_url": self.source_url,
        }


# ── Pure price math ────────────────────────────────────────────────


def apply_local_taxes(sticker_local: float, region: RegionSpec) -> tuple[float, list[dict[str, Any]]]:
    """Apply the region's baseline consumption tax to the sticker.

    Returns ``(true_local, breakdown)``. The breakdown is a list of line
    items the brief can render verbatim — same shape the live checkout
    extractor produces, so callers can swap the two interchangeably.
    """
    tax_amount = round(sticker_local * region.consumption_tax_pct, 2)
    breakdown: list[dict[str, Any]] = [
        {"label": "List price", "amount": round(sticker_local, 2),
         "currency": region.currency, "kind": "sticker"},
    ]
    if tax_amount > 0:
        breakdown.append({
            "label": region.consumption_tax_label,
            "amount": tax_amount,
            "currency": region.currency,
            "kind": "tax",
            "rate_pct": round(region.consumption_tax_pct * 100, 1),
        })
    breakdown.append({
        "label": "Cart total",
        "amount": round(sticker_local + tax_amount, 2),
        "currency": region.currency,
        "kind": "total",
    })
    return round(sticker_local + tax_amount, 2), breakdown


def make_quote(
    *,
    region: RegionSpec,
    plan_id: str,
    plan_label: str,
    sticker_local: float,
    true_local: float | None = None,
    breakdown: list[dict[str, Any]] | None = None,
    source_url: str = "",
    via: str = "scraping_browser",
    source: QuoteSource | None = None,
) -> PriceQuote:
    """Build a PriceQuote, applying baseline taxes if ``true_local`` is missing.

    ``source`` is inferred when omitted: if the caller passed
    ``true_local`` + ``breakdown`` we assume a cart-extract path; if we
    had to synthesize them via ``apply_local_taxes`` we tag the result
    as ``baseline_tax``.
    """
    synthesized = true_local is None or breakdown is None
    if synthesized:
        true_local, breakdown = apply_local_taxes(sticker_local, region)
    sticker_usd = to_usd(sticker_local, region.currency)
    true_usd = to_usd(true_local, region.currency)
    if source is None:
        source = "baseline_tax" if synthesized else "cart_extract"
    return PriceQuote(
        region=region,
        plan_id=plan_id,
        plan_label=plan_label,
        sticker_local=sticker_local,
        true_local=true_local,
        sticker_usd=sticker_usd,
        true_usd=true_usd,
        breakdown=breakdown,
        source_url=source_url,
        via=via,
        source=source,
    )


def annotate_deltas(quotes: list[PriceQuote]) -> list[PriceQuote]:
    """Fill ``delta_pct`` on each quote relative to the US baseline (in USD).

    The baseline is whichever quote is flagged ``region.is_baseline``; if
    none is, the first quote in the list is used. Returns the same list
    for fluent chaining."""
    baseline = next((q for q in quotes if q.region.is_baseline), quotes[0] if quotes else None)
    if baseline is None:
        return quotes
    base_usd = baseline.true_usd or 1.0
    for q in quotes:
        if q is baseline:
            q.delta_pct = 0.0
            continue
        q.delta_pct = round(((q.true_usd / base_usd) - 1.0) * 100, 1)
    return quotes


def comparison_table(quotes: list[PriceQuote]) -> list[dict[str, Any]]:
    """Project the quotes into a renderable list of dicts (one row per region)."""
    return [q.to_table_row() for q in quotes]
