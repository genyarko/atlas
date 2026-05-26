"""Render a Brief as HTML, Markdown, and PDF.

Day-8 polish: institutional-grade visual identity. The HTML template
(``templates/brief.html``) handles both screen and print via dual
stylesheets; the PDF route runs WeasyPrint over the same HTML so the
exported PDF and the in-browser view stay in sync.

We precompute per-module chart specs (bar widths, max values,
polarities) here in Python and inject them as a parallel
``chart_specs`` mapping the template reads alongside the brief's
section data. Keeping the math out of Jinja makes both layers
testable and the template a layout file rather than a calculator.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import config
from ..models import Brief, Severity
from ..modules import MODULE_CATALOG
from . import chart_specs

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_env = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _severity_class(sev: Severity) -> str:
    return f"sev-{sev}"


def _severity_label(sev: Severity) -> str:
    return sev.upper()


_env.filters["sev_class"] = _severity_class
_env.filters["sev_label"] = _severity_label


# ── Chart spec assembly ────────────────────────────────────────────


def _build_chart_specs(brief: Brief) -> dict[str, dict]:
    """Walk the brief's sections and precompute chart geometry.

    Returns a dict keyed by section.module → spec dict. The template
    consults this dict when rendering each section's visuals."""
    specs: dict[str, dict] = {}
    for section in brief.sections:
        data = section.data or {}
        if section.module == "trueprice" and data.get("regions"):
            specs["trueprice"] = {
                "delta_bars": chart_specs.trueprice_delta_bars(data["regions"]),
            }
        elif section.module == "signal" and data.get("by_family"):
            specs["signal"] = {
                "family_bars": chart_specs.signal_family_bars(data.get("by_family", {})),
                "region_bars": chart_specs.signal_region_bars(
                    data.get("by_region", {}),
                    data.get("recent_by_region", {}),
                    data.get("older_by_region", {}),
                ),
            }
        elif section.module == "altdata" and "composite_score" in data:
            specs["altdata"] = {
                "meter": chart_specs.altdata_score_meter(data.get("composite_score", 0.5)),
            }
    return specs


# ── Public API ────────────────────────────────────────────────────


def render_html(brief: Brief) -> str:
    template = _env.get_template("brief.html")
    return template.render(
        brief=brief,
        catalog=MODULE_CATALOG,
        specs=_build_chart_specs(brief),
    )


def render_markdown(brief: Brief) -> str:
    lines: list[str] = []
    lines.append("# Atlas — Ground Truth Brief")
    lines.append("")
    lines.append(f"**Subject:** {brief.subject}  ")
    lines.append(f"**Generated:** {brief.generated_at:%Y-%m-%d %H:%M UTC}  ")
    lines.append(f"**Confidence:** {brief.confidence_score:.2f}  ")
    lines.append(f"**Mode:** {brief.mode}  ")
    lines.append(f"**Question:** {brief.question.text}")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(brief.executive_summary or "_no summary_")
    lines.append("")
    if brief.key_findings:
        lines.append("## Key Findings")
        lines.append("")
        for f in brief.key_findings:
            lines.append(f"- **[{f.severity.upper()}]** {f.statement}")
        lines.append("")
    for section in brief.sections:
        lines.append(f"## {section.title}")
        lines.append(f"_confidence {section.confidence:.2f}_")
        lines.append("")
        lines.append(section.summary)
        lines.append("")
        if section.module == "trueprice" and section.data.get("regions"):
            lines.extend(_render_price_table(section.data))
            lines.append("")
        if section.module == "visual" and section.data.get("suspects"):
            lines.extend(_render_visual_table(section.data))
            lines.append("")
        if section.findings:
            for f in section.findings:
                lines.append(f"- **[{f.severity.upper()}]** {f.statement}")
            lines.append("")
        if section.sources:
            lines.append("**Sources**")
            for s in section.sources:
                lines.append(f"- [{s.title}]({s.url}) — via `{s.via}`")
            lines.append("")
    return "\n".join(lines)


def _render_price_table(data: dict) -> list[str]:
    rows = data.get("regions") or []
    if not rows:
        return []
    out: list[str] = [
        "| Region | Plan | Sticker (local) | True cart (local) | True (USD) | Δ vs US | Notes |",
        "|--------|------|----------------:|------------------:|-----------:|--------:|-------|",
    ]
    for row in rows:
        delta = row.get("delta_pct", 0.0)
        delta_cell = "baseline" if delta == 0 else f"{delta:+.1f}%"
        out.append(
            f"| {row.get('region', '')} — {row.get('region_name', '')} "
            f"| {row.get('plan', '')} "
            f"| {row.get('sticker_local', 0):.2f} {row.get('currency', '')} "
            f"| {row.get('true_local', 0):.2f} {row.get('currency', '')} "
            f"| ${row.get('true_usd', 0):.2f} "
            f"| {delta_cell} "
            f"| {row.get('notes', '')} |"
        )
    fx = data.get("fx_snapshot_date", "")
    mode = data.get("mode", "")
    failed = data.get("failed_regions") or []
    caption = f"_FX snapshot {fx} · mode {mode}"
    if failed:
        caption += f" · failed: {', '.join(failed)}"
    caption += "_"
    out.append("")
    out.append(caption)
    return out


def _render_visual_table(data: dict) -> list[str]:
    rows = data.get("suspects") or []
    if not rows:
        return []
    out: list[str] = [
        "| Suspect | Verdict | Similarity | Anomalies | Top observation |",
        "|---------|---------|-----------:|----------:|-----------------|",
    ]
    for row in rows:
        anomalies = row.get("anomalies") or []
        top = anomalies[0]["description"] if anomalies else ""
        out.append(
            f"| {row.get('suspect_url', '')} "
            f"| {str(row.get('verdict', '')).upper()} "
            f"| {row.get('similarity', 0):.2f} "
            f"| {row.get('anomaly_count', len(anomalies))} "
            f"| {top} |"
        )
    mode = data.get("mode", "")
    dropped = data.get("dropped") or []
    caption = f"_Verdict rubric: ≥3 anomalies + sim≥0.80 ⇒ HIGH · mode {mode}"
    if dropped:
        caption += f" · dropped: {len(dropped)}"
    caption += "_"
    out.append("")
    out.append(caption)
    return out


def write_html(brief: Brief) -> Path:
    """Write the brief HTML to ``runtime/briefs/<id>.html`` and return the path."""
    path = config.BRIEFS_DIR / f"{brief.id}.html"
    path.write_text(render_html(brief), encoding="utf-8")
    return path


def _render_pdf_html(brief: Brief) -> str:
    """Render the print-tuned HTML used by the PDF pipeline.

    The PDF template (``brief_pdf.html``) uses inline hex colors and
    table-based layouts so the pure-Python ``xhtml2pdf`` engine can
    handle it without losing the institutional look. The on-screen
    template (``brief.html``) keeps the richer CSS3 design.
    """
    template = _env.get_template("brief_pdf.html")
    return template.render(
        brief=brief,
        catalog=MODULE_CATALOG,
        specs=_build_chart_specs(brief),
    )


def render_pdf(brief: Brief) -> bytes:
    """Render the brief as a PDF.

    Uses ``xhtml2pdf`` (pure-Python, built on reportlab) so the PDF
    path works on Windows / macOS / Linux without any native runtime
    dependencies. The implementation plan called for WeasyPrint, but
    GTK on Windows is a footgun for local dev; reportlab-backed
    xhtml2pdf gets us 90% of the visual fidelity for 0% of the install
    pain. Inline SVG bar charts route through ``svglib`` so the visuals
    still print correctly.

    Raises ``RuntimeError`` if the PDF pipeline isn't available — the
    caller is expected to translate that into a 503 so the demo doesn't
    crash on a missing optional dep.
    """
    try:
        from xhtml2pdf import pisa  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "PDF pipeline not installed. Install with: pip install xhtml2pdf"
        ) from exc

    import io
    html = _render_pdf_html(brief)
    buf = io.BytesIO()
    result = pisa.CreatePDF(
        src=html,
        dest=buf,
        encoding="utf-8",
    )
    if result.err:
        raise RuntimeError(
            f"xhtml2pdf reported {result.err} errors while building the PDF; "
            f"check the brief template."
        )
    return buf.getvalue()


def write_pdf(brief: Brief) -> Path:
    """Write the brief PDF to ``runtime/briefs/<id>.pdf`` and return the path."""
    path = config.BRIEFS_DIR / f"{brief.id}.pdf"
    path.write_bytes(render_pdf(brief))
    return path
