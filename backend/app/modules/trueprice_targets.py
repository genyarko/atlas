"""Per-target interaction scripts for TruePrice.

Each ``TargetConfig`` carries everything the Scraping Browser needs to
reach the checkout/cart summary page for a given product:

* the pricing-page URL we land on
* the plan we click ("Standard", "Plus", etc.) and its native sticker
* an ordered ``interaction_script`` of ``goto / click / fill /
  wait_selector / extract`` actions

The script is the Day-4 deliverable: "interaction script for 1 demo
target reaching cart total". Selectors are kept in this file (rather
than inline in the module) so swapping a target when a site redesigns
mid-build is a one-file edit — the failure mode called out in the plan
under TruePrice §4.1.

Selector strategy
-----------------
* Prefer ``data-*`` test attributes when the target exposes them.
* Fall back to stable text-content matches (``text=Standard``) over
  brittle nth-child paths.
* Each ``extract`` step lists every field we want; the executor returns
  whichever ones it could find and the price-parser tolerates missing
  optional fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .trueprice_data import RegionSpec, USD_PER_UNIT


# ── Plan + target descriptors ──────────────────────────────────────


@dataclass(frozen=True)
class TargetPlan:
    """A pricing tier we'll route the checkout flow toward.

    ``sticker_by_currency`` is the publicly listed price per seat per
    month in each region's native currency. Many SaaS products show
    the same number in USD then convert at checkout; others (Notion,
    Linear in EU) localise the sticker itself. We capture both up
    front so the comparison-table delta isn't muddied by sticker drift
    we already knew about.
    """

    plan_id: str
    label: str
    seats: int
    # Sticker price by currency code. Targets that genuinely localize
    # (e.g. Notion) list multiple entries; targets that quote USD
    # globally (e.g. Linear) list only ``"USD"`` and the FX fallback
    # below converts on demand.
    sticker_by_currency: dict[str, float]

    def sticker_for(self, currency: str) -> float:
        """Return the plan's sticker in ``currency`` units.

        Two paths:
          1. Explicit entry in ``sticker_by_currency`` — the target
             genuinely localizes its public price; use that number.
          2. Fallback — convert the USD sticker via the pinned FX
             snapshot. Used when the target quotes a single global
             USD price and surfaces local-currency totals only at
             checkout (Linear's pattern).
        """
        if currency in self.sticker_by_currency:
            return self.sticker_by_currency[currency]
        base = self.sticker_by_currency.get("USD")
        if base is None:
            return 0.0
        rate = USD_PER_UNIT.get(currency.upper())
        if not rate:
            return base
        return round(base / rate, 2)


@dataclass(frozen=True)
class TargetConfig:
    name: str                              # canonical subject ("Linear")
    pricing_url: str                       # public pricing page
    cart_url: str                          # post-trial / cart summary URL
    default_plan: str                      # plan_id picked when caller doesn't specify
    plans: dict[str, TargetPlan]
    interaction_script: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""


# ── Linear (the demo headline) ─────────────────────────────────────

LINEAR_STANDARD = TargetPlan(
    plan_id="standard",
    label="Standard (per-user, monthly)",
    seats=1,
    # Linear quotes USD globally; EU users see the dollar number then
    # VAT is added at checkout. We list only the USD sticker so the
    # GBP/EUR sticker_local values are derived deterministically via
    # the FX snapshot in trueprice_data.
    sticker_by_currency={"USD": 8.0},
)

LINEAR_PLUS = TargetPlan(
    plan_id="plus",
    label="Plus (per-user, monthly)",
    seats=1,
    sticker_by_currency={"USD": 14.0},
)


LINEAR_TARGET = TargetConfig(
    name="Linear",
    pricing_url="https://linear.app/pricing",
    cart_url="https://linear.app/join/checkout",
    default_plan="standard",
    plans={"standard": LINEAR_STANDARD, "plus": LINEAR_PLUS},
    interaction_script=[
        {"action": "goto", "url": "https://linear.app/pricing"},
        {"action": "wait_selector", "selector": "[data-plan='standard']", "timeout_ms": 8000},
        # Some regions surface a cookie/region banner — dismiss if present.
        {"action": "click_if_present", "selector": "button[aria-label*='Accept']"},
        # Pick the Standard plan's CTA.
        {"action": "click", "selector": "[data-plan='standard'] [data-cta='start']"},
        {"action": "wait_selector", "selector": "[data-checkout='summary']", "timeout_ms": 12000},
        # Pull every line item the checkout shows.
        {"action": "extract", "selectors": {
            "list_price": "[data-checkout='list-price']",
            "tax_label": "[data-checkout='tax-label']",
            "tax_amount": "[data-checkout='tax-amount']",
            "fees": "[data-checkout='fees']",
            "total": "[data-checkout='total']",
            "currency": "[data-checkout='currency']",
            "billing_country": "[data-checkout='billing-country']",
        }},
        {"action": "screenshot", "name": "cart_summary"},
    ],
    notes=(
        "Linear quotes USD globally; checkout VAT for UK/DE billing addresses "
        "is the dominant true-cost driver."
    ),
)


# ── Notion (multi-plan, region-aware) ───────────────────────────────

NOTION_PLUS = TargetPlan(
    plan_id="plus",
    label="Plus (per-user, monthly)",
    seats=1,
    sticker_by_currency={"USD": 12.0, "GBP": 10.0, "EUR": 10.5},
)


NOTION_TARGET = TargetConfig(
    name="Notion",
    pricing_url="https://www.notion.so/pricing",
    cart_url="https://www.notion.so/checkout",
    default_plan="plus",
    plans={"plus": NOTION_PLUS},
    interaction_script=[
        {"action": "goto", "url": "https://www.notion.so/pricing"},
        {"action": "wait_selector", "selector": "button[data-plan-id='plus']", "timeout_ms": 8000},
        {"action": "click_if_present", "selector": "button[data-test='dismiss-banner']"},
        {"action": "click", "selector": "button[data-plan-id='plus'][data-cta='upgrade']"},
        {"action": "wait_selector", "selector": "[data-checkout-step='review']", "timeout_ms": 12000},
        {"action": "extract", "selectors": {
            "list_price": "[data-line='subtotal'] [data-amount]",
            "tax_label": "[data-line='tax'] [data-label]",
            "tax_amount": "[data-line='tax'] [data-amount]",
            "total": "[data-line='total'] [data-amount]",
            "currency": "[data-checkout-currency]",
            "billing_country": "[data-checkout-country]",
        }},
        {"action": "screenshot", "name": "cart_summary"},
    ],
    notes=(
        "Notion localises sticker for GBP/EUR; the true-cost delta isolates "
        "the VAT layer that those localised stickers still don't include."
    ),
)


# ── AcmeCorp (controlled demo target) ─────────────────────────────────

ACMECORP_PLAN = TargetPlan(
    plan_id="team",
    label="Team (per-user, monthly)",
    seats=1,
    sticker_by_currency={"USD": 12.0, "GBP": 12.0, "EUR": 12.0},
)

ACMECORP_TARGET = TargetConfig(
    name="AcmeCorp",
    pricing_url="https://acmecorp-demo.test/pricing",
    cart_url="https://acmecorp-demo.test/checkout",
    default_plan="team",
    plans={"team": ACMECORP_PLAN},
    interaction_script=[
        {"action": "goto", "url": "https://acmecorp-demo.test/pricing"},
        {"action": "wait_selector", "selector": "#plan-team", "timeout_ms": 6000},
        {"action": "click", "selector": "#plan-team .cta-checkout"},
        {"action": "wait_selector", "selector": "#cart-summary", "timeout_ms": 8000},
        {"action": "extract", "selectors": {
            "list_price": "#cart-summary [data-row='subtotal'] .amount",
            "tax_label":  "#cart-summary [data-row='tax'] .label",
            "tax_amount": "#cart-summary [data-row='tax'] .amount",
            "total":      "#cart-summary [data-row='total'] .amount",
            "currency":   "#cart-summary [data-currency]",
            "billing_country": "#cart-summary [data-country]",
        }},
    ],
    notes="Controlled demo target — used when no public target is requested.",
)


# ── Registry + lookup ──────────────────────────────────────────────

TARGETS: dict[str, TargetConfig] = {
    LINEAR_TARGET.name: LINEAR_TARGET,
    NOTION_TARGET.name: NOTION_TARGET,
    ACMECORP_TARGET.name: ACMECORP_TARGET,
}


def is_pre_validated(subject: str) -> bool:
    """Whether we have a hand-validated interaction script for this subject."""
    return subject in TARGETS


def _generic_target(subject: str) -> TargetConfig:
    """Synthesize a placeholder TargetConfig for an unrecognized subject.

    The interaction script is intentionally empty: callers (TruePrice's
    live path) detect that and skip the Scraping Browser invocation
    entirely rather than spending a session running zero actions. The
    pricing URL is a best-guess for the subject's domain so the brief
    still has *some* source to cite when it explains that this target
    isn't pre-validated."""
    domain = f"{subject.lower().replace(' ', '')}.com"
    return TargetConfig(
        name=subject,
        pricing_url=f"https://{domain}/pricing",
        cart_url="",
        default_plan="standard",
        plans={
            "standard": TargetPlan(
                plan_id="standard",
                label="Standard (per-user, monthly)",
                seats=1,
                sticker_by_currency={"USD": 12.0},
            ),
        },
        interaction_script=[],  # empty → live path skips this target
        notes=(
            f"{subject} is not in the pre-validated target pool. "
            "Live cart extraction is skipped; figures are baseline-tax estimates."
        ),
    )


def get_target(subject: str) -> TargetConfig:
    """Look up a target config by subject name.

    Pre-validated subjects (Linear, Notion, AcmeCorp) return their real
    config. Anything else gets a generic placeholder with an empty
    interaction script — the live path will detect that and stay in
    baseline-tax mode rather than misattributing cart numbers to a
    target we never tested."""
    pre = TARGETS.get(subject)
    if pre is not None:
        return pre
    return _generic_target(subject)


# ── Live-response parsing ──────────────────────────────────────────


def parse_checkout_extract(
    extracted: dict[str, Any],
    *,
    target: TargetConfig,
    region: RegionSpec,
) -> dict[str, Any] | None:
    """Coerce the Scraping Browser's ``extract`` payload into our schema.

    Returns ``{sticker_local, true_local, breakdown}`` or ``None`` when
    nothing usable was scraped — caller falls back to the baseline tax
    calculation."""
    if not isinstance(extracted, dict):
        return None

    def _money(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            # Strip currency symbols, commas, NBSPs.
            cleaned = value.replace("\xa0", "").replace(",", "")
            cleaned = "".join(ch for ch in cleaned if ch.isdigit() or ch in ".-")
            try:
                return float(cleaned) if cleaned else None
            except ValueError:
                return None
        return None

    list_price = _money(extracted.get("list_price"))
    tax_amount = _money(extracted.get("tax_amount")) or 0.0
    total = _money(extracted.get("total"))
    fees = _money(extracted.get("fees")) or 0.0
    tax_label = extracted.get("tax_label") or region.consumption_tax_label

    if list_price is None and total is None:
        return None
    if list_price is None and total is not None:
        list_price = max(round(total - tax_amount - fees, 2), 0.0)
    if total is None:
        total = round((list_price or 0.0) + tax_amount + fees, 2)

    breakdown: list[dict[str, Any]] = []
    if list_price is not None:
        breakdown.append({"label": "List price", "amount": round(list_price, 2),
                          "currency": region.currency, "kind": "sticker"})
    if tax_amount:
        breakdown.append({
            "label": str(tax_label),
            "amount": round(tax_amount, 2),
            "currency": region.currency,
            "kind": "tax",
            "rate_pct": round(
                (tax_amount / list_price * 100) if list_price else region.consumption_tax_pct * 100,
                1,
            ),
        })
    if fees:
        breakdown.append({"label": "Fees", "amount": round(fees, 2),
                          "currency": region.currency, "kind": "fee"})
    breakdown.append({"label": "Cart total", "amount": round(total, 2),
                      "currency": region.currency, "kind": "total"})

    return {
        "sticker_local": round(list_price or 0.0, 2),
        "true_local": round(total, 2),
        "breakdown": breakdown,
    }
