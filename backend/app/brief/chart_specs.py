"""Chart specs — precomputed bar/series geometry for the HTML brief.

The Jinja template renders inline SVG with fixed coordinates, so we
compute the bar widths, max values, and label positions here in Python.
Keeping the math out of the template:

- avoids Jinja's awkward namespace-for-counter gymnastics
- makes it unit-testable
- keeps the template a layout file, not a calculator

Each function returns a plain dict the template can iterate over. Bar
widths are in SVG user units, anchored to the column-pad ``axis_x`` so
all charts use the same visual baseline.
"""

from __future__ import annotations

from typing import Any


# Shared SVG dimensions. The template's viewBox is always 600 wide; the
# row height varies per chart based on the number of rows.
_AXIS_X = 180            # x-coordinate of the chart's left axis
_BAR_TRACK = 380         # usable horizontal track for bar width


def trueprice_delta_bars(regions: list[dict[str, Any]]) -> dict[str, Any]:
    """Build bars for the "Δ vs baseline %" chart."""
    if not regions:
        return {"bars": [], "row_height": 28, "axis_x": _AXIS_X}
    max_abs = max((abs(r.get("delta_pct", 0.0)) for r in regions), default=1.0)
    if max_abs < 1.0:
        max_abs = 1.0
    bars: list[dict[str, Any]] = []
    for r in regions:
        delta = float(r.get("delta_pct", 0.0))
        usd = float(r.get("true_usd", 0.0))
        bars.append({
            "label": r.get("region", ""),
            "delta": delta,
            "width": (abs(delta) / max_abs) * _BAR_TRACK,
            "is_baseline": delta == 0,
            "polarity": "up" if delta > 0 else "down" if delta < 0 else "zero",
            "value_text": "baseline" if delta == 0 else f"{delta:+.1f}%",
            "usd_text": f"${usd:,.2f}",
        })
    return {"bars": bars, "row_height": 28, "axis_x": _AXIS_X,
            "track": _BAR_TRACK, "max_abs": max_abs}


def signal_family_bars(by_family: dict[str, int]) -> dict[str, Any]:
    """Build bars for the "roles by family" chart, sorted desc."""
    if not by_family:
        return {"bars": [], "row_height": 24, "axis_x": 170}
    items = sorted(by_family.items(), key=lambda kv: kv[1], reverse=True)
    fmax = max((v for _, v in items), default=1) or 1
    track = 400
    bars = [{
        "label": k,
        "value": v,
        "width": (v / fmax) * track,
    } for k, v in items]
    return {"bars": bars, "row_height": 24, "axis_x": 170, "track": track, "fmax": fmax}


def signal_region_bars(
    by_region: dict[str, int],
    recent_by_region: dict[str, int],
    older_by_region: dict[str, int],
) -> dict[str, Any]:
    """Build paired recent/prior bars for region heatmap."""
    if not by_region:
        return {"rows": [], "row_height": 36, "axis_x": 100}
    # Order by total volume desc.
    regions = sorted(by_region.keys(), key=lambda k: -by_region.get(k, 0))
    rmax = 1
    for k in regions:
        rmax = max(rmax, recent_by_region.get(k, 0), older_by_region.get(k, 0))
    track = 430
    rows: list[dict[str, Any]] = []
    for k in regions:
        rv = recent_by_region.get(k, 0)
        ov = older_by_region.get(k, 0)
        rows.append({
            "label": k,
            "recent": rv,
            "older": ov,
            "recent_width": (rv / rmax) * track,
            "older_width": (ov / rmax) * track,
        })
    return {"rows": rows, "row_height": 36, "axis_x": 100, "track": track, "rmax": rmax}


def altdata_score_meter(score: float) -> dict[str, Any]:
    """Build a horizontal meter (0..1) for the composite score."""
    score = max(0.0, min(1.0, float(score)))
    # Anchor zones: 0..0.45 distress, 0.45..0.55 neutral, 0.55..1 momentum.
    return {
        "score": score,
        "pct": int(round(score * 100)),
        "marker_x": 20 + score * 540,   # SVG coord
        "distress_end": 20 + 0.45 * 540,
        "momentum_start": 20 + 0.55 * 540,
    }


__all__ = [
    "trueprice_delta_bars",
    "signal_family_bars",
    "signal_region_bars",
    "altdata_score_meter",
]
