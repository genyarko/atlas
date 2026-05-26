"""Data layer for the Visual module — controlled targets, candidate
filtering, vision-diff result shapes, and verdict scoring.

The split mirrors TruePrice: this file is the pure-data layer (no
network), ``visual.py`` is the live + mock orchestration. Keeping the
canonical-domain filter list, the controlled lookalike catalog, and the
verdict-rubric thresholds in one place means a Day-8 polish pass can
tweak rubric numbers without touching the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal
from urllib.parse import urlparse


# ── Types ──────────────────────────────────────────────────────────


# Anomaly kinds we look for in the vision diff. Keeping this a closed
# set means the synthesizer can write per-kind copy rather than echoing
# whatever the model returns.
AnomalyKind = Literal[
    "logo",      # wordmark / glyph mismatch (aspect, spelling, glyph weight)
    "color",     # brand palette drift
    "copy",      # CTA / heading copy mistranscribed
    "form",      # credential form posts to non-canonical destination
    "footer",    # footer links resolve to non-canonical domains
    "layout",    # gross layout / spacing divergence
    "stale",     # stale copyright / version / "beta" callouts
]


Verdict = Literal["low", "notable", "high", "critical"]


@dataclass(frozen=True)
class SuspectCandidate:
    """A URL we'd consider running through the vision diff."""

    url: str
    title: str = ""
    source: Literal["controlled", "serp"] = "serp"
    discovery_query: str = ""


@dataclass(frozen=True)
class VisionAnomaly:
    kind: AnomalyKind
    description: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "description": self.description}


@dataclass(frozen=True)
class ControlledLookalike:
    """A pre-built lookalike page in ``demo/lookalikes/``.

    The HTML file ships with the repo; this dataclass declares the
    ground-truth anomalies the page contains so the mock path can emit
    findings without an LLM. The Claude vision path will surface the
    same anomalies (more or fewer, depending on prompt tuning) when the
    live pipeline screenshots the page through Bright Data.
    """

    slug: str
    url: str            # the "public" URL we'd claim in a live demo
    html_path: Path     # local file in demo/lookalikes/
    declared_anomalies: tuple[VisionAnomaly, ...]
    similarity: float   # similarity score we report for this lookalike (0-1)
    note: str = ""


@dataclass(frozen=True)
class ControlledTarget:
    """A brand we control end-to-end for guaranteed demo coverage."""

    brand: str
    legit_url: str
    legit_html_path: Path
    lookalikes: tuple[ControlledLookalike, ...]

    # SERP terms we'd plausibly run against the brand. Used by the live
    # path to triangulate even on controlled targets — and surfaced in
    # the brief's "discovery" footer so judges can see the query trace.
    serp_terms: tuple[str, ...] = (
        "login", "signin", "sign in", "support",
    )


# ── Controlled target catalog (the Day-5 deliverable) ──────────────


# Repo root → demo/lookalikes/. Resolved at import time so the dataclass
# constants can refer to absolute paths without each call resolving them.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_LOOKALIKES_DIR = _REPO_ROOT / "demo" / "lookalikes"


ACMECORP_TYPO_LOOKALIKE = ControlledLookalike(
    slug="acmecorp-typo-domain",
    url="https://acmecorp-secure-login.test",
    html_path=_LOOKALIKES_DIR / "lookalike-typo-domain.html",
    similarity=0.91,
    declared_anomalies=(
        VisionAnomaly("logo", "Wordmark misspelled — 'AccmeCorp' (double-c) in masthead"),
        VisionAnomaly("copy", "Primary CTA reads 'Login' — canonical brand uses 'Sign in to AcmeCorp'"),
        VisionAnomaly(
            "form",
            "Credential form posts to collect.acmecorp-secure-login.test — not the canonical auth host",
        ),
        VisionAnomaly("footer", "Footer links resolve to non-canonical acmecorp-secure-login.test subdomains"),
        VisionAnomaly("copy", "Heading 'Login to your account' replaces 'Sign in to AcmeCorp'"),
    ),
    note="Typosquat domain with phishing-shaped form post. Demo-friendly: 5 stacked anomalies.",
)


ACMECORP_COLOR_LOOKALIKE = ControlledLookalike(
    slug="acmecorp-color-swap",
    url="https://app-acmecorp.test/signin",
    html_path=_LOOKALIKES_DIR / "lookalike-color-swap.html",
    similarity=0.84,
    declared_anomalies=(
        VisionAnomaly("color", "Brand primary #21508C drifted from canonical #1A2B45 (ΔE ≈ 18)"),
        VisionAnomaly("color", "Brand accent #54B0FF replaces canonical #FFB347 — cool vs warm hue swap"),
        VisionAnomaly("stale", "Stale 'BETA' pill on the Customers nav item — removed from the canonical site last quarter"),
        VisionAnomaly("stale", "Footer copyright reads '© 2025' on a page rendered in 2026"),
    ),
    note="Subtler visual lookalike — keeps copy and layout, drifts palette and stale assets only.",
)


ACMECORP_TARGET = ControlledTarget(
    brand="AcmeCorp",
    legit_url="https://acmecorp-demo.test/login",
    legit_html_path=_LOOKALIKES_DIR / "legit-acmecorp.html",
    lookalikes=(ACMECORP_TYPO_LOOKALIKE, ACMECORP_COLOR_LOOKALIKE),
)


CONTROLLED_TARGETS: dict[str, ControlledTarget] = {
    ACMECORP_TARGET.brand: ACMECORP_TARGET,
}


def get_controlled_target(brand: str) -> ControlledTarget | None:
    return CONTROLLED_TARGETS.get(brand)


# ── Canonical / social allowlist (filter discovered candidates) ────


# Hosts that are *expected* to surface in brand-related SERP queries
# without being phishing-shaped. Anything matching these gets dropped
# before the vision-diff stage — they're not impersonation candidates.
SOCIAL_PLATFORM_HOSTS: frozenset[str] = frozenset({
    "linkedin.com", "www.linkedin.com",
    "x.com", "twitter.com", "www.x.com", "www.twitter.com",
    "facebook.com", "www.facebook.com",
    "youtube.com", "www.youtube.com",
    "github.com", "www.github.com",
    "wikipedia.org", "en.wikipedia.org",
    "crunchbase.com", "www.crunchbase.com",
    "apps.apple.com", "play.google.com",
    "g2.com", "www.g2.com",
    "trustpilot.com", "www.trustpilot.com",
    "glassdoor.com", "www.glassdoor.com",
    "producthunt.com", "www.producthunt.com",
    "ycombinator.com", "news.ycombinator.com",
    "reddit.com", "www.reddit.com",
})


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except (ValueError, AttributeError):
        return ""


def _is_same_or_subdomain(host: str, of: str) -> bool:
    if not host or not of:
        return False
    host = host.lstrip(".")
    of = of.lstrip(".")
    return host == of or host.endswith("." + of)


def is_canonical_or_social(candidate_url: str, brand_url: str) -> bool:
    """Should this candidate be dropped from the suspect pool?

    A candidate is *not* a suspect when it lives on the brand's own
    domain (or a subdomain of it) or on a known social platform. Any
    other host is fair game for the vision diff."""
    host = _host(candidate_url)
    if not host:
        return True  # unparseable → drop
    brand_host = _host(brand_url)
    if brand_host and _is_same_or_subdomain(host, brand_host):
        return True
    apex = ".".join(host.split(".")[-2:]) if "." in host else host
    return host in SOCIAL_PLATFORM_HOSTS or apex in SOCIAL_PLATFORM_HOSTS


def filter_candidates(
    candidates: Iterable[SuspectCandidate],
    *,
    brand_url: str,
) -> list[SuspectCandidate]:
    """Drop canonical and social hosts; de-duplicate by URL."""
    seen: set[str] = set()
    out: list[SuspectCandidate] = []
    for c in candidates:
        if not c.url or c.url in seen:
            continue
        if c.source != "controlled" and is_canonical_or_social(c.url, brand_url):
            continue
        seen.add(c.url)
        out.append(c)
    return out


# ── Verdict scoring ────────────────────────────────────────────────


# Implementation plan §4.5: "tune prompt to require ≥3 visual anomalies
# before flagging 'high'." We encode that here so the LLM-side prompt
# and the rule-based fallback agree on what "high" means.
def verdict_for(
    *,
    anomaly_count: int,
    similarity: float,
    has_form_anomaly: bool = False,
) -> Verdict:
    """Map (#anomalies, similarity, credential-form anomaly?) → verdict.

    The credential-form anomaly is special-cased: a single off-brand
    form-post destination is a classic phishing signal even when the
    visual mimicry is otherwise weak, so it escalates verdict by one
    band."""
    if anomaly_count >= 4 and similarity >= 0.85 and has_form_anomaly:
        return "critical"
    if anomaly_count >= 4 and similarity >= 0.85:
        return "high"
    if anomaly_count >= 3 and similarity >= 0.80:
        return "high"
    if has_form_anomaly and anomaly_count >= 2:
        return "high"
    if anomaly_count >= 2:
        return "notable"
    if anomaly_count >= 1:
        return "notable" if similarity >= 0.75 else "low"
    return "low"


def verdict_to_severity(v: Verdict) -> Literal["info", "notable", "high", "critical"]:
    """Map vision verdict → Atlas Finding severity scale."""
    return {
        "low": "info",
        "notable": "notable",
        "high": "high",
        "critical": "critical",
    }[v]


@dataclass(frozen=True)
class VisionDiff:
    """Result of running the vision diff against one suspect."""

    suspect_url: str
    similarity: float                         # 0.0 - 1.0
    anomalies: tuple[VisionAnomaly, ...]
    verdict: Verdict
    reasoning: str = ""                       # short LLM rationale
    legit_url: str = ""
    suspect_title: str = ""

    def to_raw(self) -> dict[str, object]:
        return {
            "suspect_url": self.suspect_url,
            "suspect_title": self.suspect_title,
            "legit_url": self.legit_url,
            "similarity": round(self.similarity, 3),
            "verdict": self.verdict,
            "anomalies": [a.to_dict() for a in self.anomalies],
            "anomaly_count": len(self.anomalies),
            "reasoning": self.reasoning,
        }


def diff_from_declared(
    lookalike: ControlledLookalike,
    *,
    legit_url: str,
    reasoning: str = "",
) -> VisionDiff:
    """Build a VisionDiff directly from a controlled lookalike's
    declared anomalies.

    Used by the mock path so a judge sees the exact same shape they'd
    see from the live Claude vision call against this same page."""
    has_form = any(a.kind == "form" for a in lookalike.declared_anomalies)
    v = verdict_for(
        anomaly_count=len(lookalike.declared_anomalies),
        similarity=lookalike.similarity,
        has_form_anomaly=has_form,
    )
    return VisionDiff(
        suspect_url=lookalike.url,
        similarity=lookalike.similarity,
        anomalies=lookalike.declared_anomalies,
        verdict=v,
        reasoning=reasoning or (
            f"{len(lookalike.declared_anomalies)} declared visual anomalies "
            f"at similarity {lookalike.similarity:.2f}: " + lookalike.note
        ),
        legit_url=legit_url,
        suspect_title=lookalike.slug,
    )


__all__ = [
    "AnomalyKind",
    "Verdict",
    "SuspectCandidate",
    "VisionAnomaly",
    "VisionDiff",
    "ControlledLookalike",
    "ControlledTarget",
    "ACMECORP_TARGET",
    "ACMECORP_TYPO_LOOKALIKE",
    "ACMECORP_COLOR_LOOKALIKE",
    "CONTROLLED_TARGETS",
    "SOCIAL_PLATFORM_HOSTS",
    "get_controlled_target",
    "is_canonical_or_social",
    "filter_candidates",
    "verdict_for",
    "verdict_to_severity",
    "diff_from_declared",
]
