"""AltData data layer — review normalization, sentiment trend, fixtures.

Import-side-effect free and deterministic. The live path in
``altdata.py`` feeds Glassdoor/G2 rows from Bright Data's Web Scraper
API into this layer; the mock path feeds the same shape from
hardcoded fixtures. Both pipelines run through the same
``normalize`` → ``summarize_trend`` → ``Finding`` flow so the brief
output is identical regardless of mode.

Polish ceiling is intentionally lower than the first 3 modules (per
implementation plan day-6 brief): one trend computation, one
complaint-cluster pass, no LLM dependency for the basic findings.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Literal


ReviewSource = Literal["glassdoor", "g2"]


# ── Review ─────────────────────────────────────────────────────────


@dataclass
class Review:
    """One Glassdoor / G2 review, normalized to a common shape."""

    rating: float                # 1.0-5.0; G2 rates 0-5, Glassdoor 1-5.
    posted_days_ago: int         # 0+ days since publication
    title: str
    body: str                    # cons + summary fields concatenated
    source: ReviewSource
    url: str = ""
    role: str = ""               # Glassdoor "current/former employee — title"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rating": round(self.rating, 2),
            "posted_days_ago": self.posted_days_ago,
            "title": self.title,
            "body_excerpt": self.body[:240],
            "source": self.source,
            "url": self.url,
            "role": self.role,
        }


# ── Complaint cluster vocabulary ───────────────────────────────────


# Light keyword vocab — the deterministic fallback for cluster
# detection. The LLM-synthesizer can pick this up later for a polish
# pass, but for day-6 supporting-cast scope this is enough to surface
# whether the gripes cluster on a specific theme (support, pricing,
# stability, etc.) versus diffuse complaints.
_COMPLAINT_VOCAB: dict[str, tuple[str, ...]] = {
    "support":      ("support", "ticket", "response time", "no response",
                     "csm", "customer success", "account manager"),
    "stability":    ("downtime", "outage", "crash", "latency", "slow",
                     "lag", "timeout", "unreliable", "buggy", "bugs"),
    "pricing":      ("expensive", "pricing", "cost", "overpriced", "value for",
                     "price hike", "renewal", "negotiation"),
    "ui-ux":        ("clunky", "ux", "ui", "interface", "navigation",
                     "confusing", "design"),
    "leadership":   ("leadership", "management", "exec", "ceo", "layoff",
                     "reorg", "restructure", "rif"),
    "compensation": ("comp", "salary", "raise", "bonus", "rsu", "equity",
                     "pay"),
    "wlb":          ("work life", "work-life", "overworked", "burnout",
                     "hours", "weekends", "on-call", "always on"),
    "mobility":     ("internal mobility", "promotion", "career growth",
                     "stuck", "no growth", "dead end"),
}


_CLUSTER_PATTERNS: dict[str, re.Pattern[str]] = {
    label: re.compile(
        r"\b(?:" + "|".join(re.escape(k) for k in keywords) + r")\b",
        re.IGNORECASE,
    )
    for label, keywords in _COMPLAINT_VOCAB.items()
}


def detect_clusters(text: str) -> list[str]:
    """Return cluster labels present in ``text`` (word-boundary regex match).

    Word boundaries matter: short tokens like "ui", "rif", "comp", "lag"
    would otherwise substring-match common words ("build", "configure",
    "Linux") and false-flag positive reviews.
    """
    return [label for label, pattern in _CLUSTER_PATTERNS.items() if pattern.search(text)]


# ── Normalization (Bright Data rows → Review) ──────────────────────


def normalize(
    rows: Iterable[dict[str, Any]],
    *,
    source: ReviewSource,
) -> list[Review]:
    """Convert Web Scraper API rows into normalized ``Review`` objects.

    Both Glassdoor and G2 ship slightly different key names; we accept
    the common aliases so a small change on the Bright Data side
    doesn't break us. Rows missing both rating AND body get dropped.
    """
    out: list[Review] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rating = _coerce_rating(row)
        body = (
            row.get("body")
            or row.get("review")
            or row.get("cons")
            or row.get("comment")
            or row.get("summary")
            or ""
        )
        if not isinstance(body, str):
            body = ""
        body = body.strip()
        if rating is None and not body:
            continue
        title = (
            row.get("title")
            or row.get("headline")
            or row.get("review_title")
            or ""
        )
        if not isinstance(title, str):
            title = ""
        days_ago = _coerce_days_ago(row)
        url = (
            row.get("url")
            or row.get("link")
            or row.get("review_url")
            or ""
        )
        if not isinstance(url, str):
            url = ""
        role = row.get("role") or row.get("position") or row.get("employee_status") or ""
        if not isinstance(role, str):
            role = ""
        out.append(Review(
            rating=float(rating) if rating is not None else 0.0,
            posted_days_ago=days_ago,
            title=title.strip(),
            body=body,
            source=source,
            url=url.strip(),
            role=role.strip(),
        ))
    return out


_RATING_KEYS = ("rating", "overall_rating", "stars", "score")
_DAYS_RE = re.compile(r"(\d+)\s*(day|week|month|year)s?\s*ago", re.IGNORECASE)
_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _coerce_rating(row: dict[str, Any]) -> float | None:
    for key in _RATING_KEYS:
        val = row.get(key)
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            # Sometimes ratings arrive as "4.5 / 5" or "4.5".
            m = re.match(r"\s*([0-9]+(?:\.[0-9]+)?)", val)
            if m:
                try:
                    return float(m.group(1))
                except ValueError:
                    continue
    return None


def _coerce_days_ago(row: dict[str, Any], *, today_days_baseline: int = 0) -> int:
    """Pull a "days ago" integer out of whatever date field the row has."""
    val = row.get("days_ago")
    if isinstance(val, int):
        return val
    raw_date = (
        row.get("review_date")
        or row.get("posted_at")
        or row.get("date")
        or row.get("published_at")
        or ""
    )
    if not isinstance(raw_date, str) or not raw_date:
        return 30
    m = _DAYS_RE.search(raw_date)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        return n if unit == "day" else n * 7 if unit == "week" else n * 30 if unit == "month" else n * 365
    iso = _ISO_DATE_RE.search(raw_date)
    if iso:
        # Without anchoring to today (which would make tests non-deterministic),
        # treat anything older than a year as 365 days, anything newer as a
        # token 30. Real days-ago is computed by the Bright Data side anyway.
        try:
            year = int(iso.group(1))
            # Demo runs in 2026; anything older than ~2 years = stale.
            if year <= 2023:
                return 365
            if year == 2024:
                return 180
            if year == 2025:
                return 90
        except ValueError:
            pass
    return 30


# ── Summary / trend math ───────────────────────────────────────────


@dataclass
class ReviewSummary:
    subject: str
    source: ReviewSource
    total: int
    recent_30d: int
    prior_30_60d: int
    avg_rating_recent: float
    avg_rating_prior: float
    rating_delta: float            # recent - prior (signed; negative = decline)
    velocity_ratio: float          # recent_30d / (prior_30_60d / 1) — capped at 9.9
    complaint_clusters: dict[str, int]  # label → count, sorted desc
    top_complaint: str             # highest-count label, or "" if all empty
    representative_urls: list[str] # 2-3 review URLs to cite

    def to_prompt_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize_trend(reviews: list[Review], *, subject: str) -> ReviewSummary:
    """Compute the headline trend over recent vs prior 30-day windows.

    The 30-day windows are arbitrary but mirror the Signal module's
    ``recent_30d`` vs ``older_60d`` (last-30 vs prior-30-60), which
    keeps the brief language consistent across sections.
    """
    source: ReviewSource = reviews[0].source if reviews else "glassdoor"
    recent = [r for r in reviews if r.posted_days_ago <= 30]
    prior = [r for r in reviews if 30 < r.posted_days_ago <= 60]

    avg_recent = _avg_rating(recent)
    avg_prior = _avg_rating(prior)
    rating_delta = round(avg_recent - avg_prior, 2)

    # Velocity ratio — same shape as Signal's job-posting velocity.
    if prior:
        ratio = round(len(recent) / max(len(prior), 1), 2)
    else:
        ratio = 0.0 if not recent else 9.9
    ratio = min(ratio, 9.9)

    # Cluster the *recent* window's complaints. Two filters:
    # (a) limit to recent reviews to capture the actionable trend
    # (b) limit to reviews rated <=3 — otherwise positive mentions of
    #     "leadership" or "compensation" get miscounted as complaints.
    cluster_counter: Counter[str] = Counter()
    for r in recent:
        if r.rating and r.rating > 3.0:
            continue
        for label in detect_clusters(f"{r.title}\n{r.body}"):
            cluster_counter[label] += 1
    clusters = dict(cluster_counter.most_common())
    top = next(iter(clusters), "")

    urls = [r.url for r in recent if r.url][:3]

    return ReviewSummary(
        subject=subject,
        source=source,
        total=len(reviews),
        recent_30d=len(recent),
        prior_30_60d=len(prior),
        avg_rating_recent=avg_recent,
        avg_rating_prior=avg_prior,
        rating_delta=rating_delta,
        velocity_ratio=ratio,
        complaint_clusters=clusters,
        top_complaint=top,
        representative_urls=urls,
    )


def _avg_rating(reviews: list[Review]) -> float:
    rated = [r.rating for r in reviews if r.rating > 0]
    if not rated:
        return 0.0
    return round(sum(rated) / len(rated), 2)


# ── Composite score (momentum vs distress) ─────────────────────────


# Per-cluster distress weight applied to the composite when a source's
# top complaint matches. Stability + leadership are the most acute
# operational signals; people-process clusters (wlb, support, mobility,
# compensation) sit in the middle; product/pricing complaints are real
# but softer indicators of distress.
_CLUSTER_DISTRESS_WEIGHTS: dict[str, float] = {
    "stability":    -0.05,
    "leadership":   -0.05,
    "wlb":          -0.03,
    "support":      -0.03,
    "mobility":     -0.03,
    "compensation": -0.03,
    "pricing":      -0.02,
    "ui-ux":        -0.02,
}


@dataclass
class CompositeScore:
    """Blended 0-1 momentum / distress score across sources.

    Above 0.55 = momentum, below 0.45 = distress, between = neutral.
    """

    score: float
    label: Literal["momentum", "neutral", "distress"]
    drivers: list[str]              # short bullets the renderer can cite


def composite_score(summaries: list[ReviewSummary]) -> CompositeScore:
    """Blend per-source trend signals into one 0-1 score.

    The formula is intentionally simple — judges shouldn't have to
    read a weighting matrix to understand the score. We anchor at
    0.50 (neutral) and shift by +/-0.05 per driver, capped on either
    end.
    """
    if not summaries:
        return CompositeScore(score=0.50, label="neutral", drivers=[])

    score = 0.50
    drivers: list[str] = []

    for s in summaries:
        if s.rating_delta >= 0.20:
            score += 0.08
            drivers.append(
                f"{s.source} rating +{s.rating_delta:.2f} QoQ "
                f"({s.avg_rating_prior:.2f} → {s.avg_rating_recent:.2f})"
            )
        elif s.rating_delta <= -0.20:
            score -= 0.08
            drivers.append(
                f"{s.source} rating {s.rating_delta:+.2f} QoQ "
                f"({s.avg_rating_prior:.2f} → {s.avg_rating_recent:.2f})"
            )

        if s.velocity_ratio >= 1.5 and s.recent_30d >= 5:
            score += 0.04
            drivers.append(
                f"{s.source} review velocity {s.velocity_ratio:.1f}× the prior window"
            )
        elif 0 < s.velocity_ratio <= 0.5:
            score -= 0.04
            drivers.append(
                f"{s.source} review velocity {s.velocity_ratio:.1f}× — quieting down"
            )

        weight = _CLUSTER_DISTRESS_WEIGHTS.get(s.top_complaint, 0.0)
        if weight:
            score += weight
            drivers.append(
                f"{s.source} complaints cluster on '{s.top_complaint}' "
                f"({s.complaint_clusters.get(s.top_complaint, 0)} of {s.recent_30d} recent)"
            )

    score = max(0.0, min(1.0, score))
    label: Literal["momentum", "neutral", "distress"] = (
        "momentum" if score >= 0.55 else "distress" if score <= 0.45 else "neutral"
    )
    return CompositeScore(score=round(score, 2), label=label, drivers=drivers)


# ── Fixtures (mock-mode reviews) ───────────────────────────────────


_LINEAR_GLASSDOOR: list[dict[str, Any]] = [
    {"rating": 5, "title": "Best place I've worked",
     "body": "Leadership trust, clean tooling, real autonomy. Compensation finally caught up.",
     "review_date": "5 days ago",
     "role": "Current Employee — Software Engineer",
     "url": "https://www.glassdoor.com/Reviews/Employee-Review-Linear-RVW9001.htm"},
    {"rating": 5, "title": "Great trajectory",
     "body": "Healthy growth, clear product direction, no toxic exec drama. Hiring bar stayed high.",
     "review_date": "8 days ago",
     "role": "Current Employee — Product Manager",
     "url": "https://www.glassdoor.com/Reviews/Employee-Review-Linear-RVW9002.htm"},
    {"rating": 4, "title": "Solid",
     "body": "Mostly excellent. Comp could be better at senior levels, but everything else is dialed in.",
     "review_date": "12 days ago",
     "role": "Current Employee — Designer",
     "url": "https://www.glassdoor.com/Reviews/Employee-Review-Linear-RVW9003.htm"},
    {"rating": 5, "title": "Best engineering culture I've seen",
     "body": "Real ownership, no bullshit process, leadership listens. Pay raises landed this quarter.",
     "review_date": "16 days ago",
     "role": "Current Employee — Senior Engineer",
     "url": "https://www.glassdoor.com/Reviews/Employee-Review-Linear-RVW9004.htm"},
    {"rating": 5, "title": "Calm + ambitious — rare combo",
     "body": "No politics. Strong design culture. Glad I joined.",
     "review_date": "22 days ago",
     "role": "Current Employee — Designer",
     "url": "https://www.glassdoor.com/Reviews/Employee-Review-Linear-RVW9005.htm"},
    # Prior 30-60d window
    {"rating": 4, "title": "Great place but pace can spike",
     "body": "Mostly excellent. A few weeks of long hours around launches but no burnout culture.",
     "review_date": "35 days ago",
     "role": "Current Employee — Engineer",
     "url": "https://www.glassdoor.com/Reviews/Employee-Review-Linear-RVW9006.htm"},
    {"rating": 4, "title": "Fast-paced",
     "body": "Great product, smart people. Comp was OK but not category-leading.",
     "review_date": "44 days ago",
     "role": "Former Employee — PM",
     "url": "https://www.glassdoor.com/Reviews/Employee-Review-Linear-RVW9007.htm"},
    {"rating": 4, "title": "Solid SaaS gig",
     "body": "Smart leadership, but career growth was slower than I'd hoped at the senior IC band.",
     "review_date": "52 days ago",
     "role": "Former Employee — Engineer",
     "url": "https://www.glassdoor.com/Reviews/Employee-Review-Linear-RVW9008.htm"},
    {"rating": 5, "title": "Best decision I made",
     "body": "Hardest I've worked, most aligned team I've been on.",
     "review_date": "58 days ago",
     "role": "Current Employee — Designer",
     "url": "https://www.glassdoor.com/Reviews/Employee-Review-Linear-RVW9009.htm"},
]


_LINEAR_G2: list[dict[str, Any]] = [
    {"rating": 5, "title": "Best issue tracker we've used",
     "body": "Fast, clean, opinionated. Cycle planning UX is unmatched.",
     "review_date": "4 days ago",
     "url": "https://www.g2.com/products/linear/reviews/linear-review-9100"},
    {"rating": 5, "title": "Replaced Jira for our 80-person eng org",
     "body": "Mobile experience is the only weak spot — desktop is genuinely the best in the space.",
     "review_date": "9 days ago",
     "url": "https://www.g2.com/products/linear/reviews/linear-review-9101"},
    {"rating": 4, "title": "Excellent on the web; clunky on phone",
     "body": "Web UX is beautiful, mobile UI feels rushed. Otherwise nothing to complain about.",
     "review_date": "13 days ago",
     "url": "https://www.g2.com/products/linear/reviews/linear-review-9102"},
    {"rating": 5, "title": "Love it",
     "body": "Replaced our entire Jira workflow. The mobile UI could be smoother, that's the only nit.",
     "review_date": "18 days ago",
     "url": "https://www.g2.com/products/linear/reviews/linear-review-9103"},
    {"rating": 5, "title": "Fastest tool we use",
     "body": "Sync latency is sub-second. Filters are powerful. Mobile UX still feels like an afterthought.",
     "review_date": "21 days ago",
     "url": "https://www.g2.com/products/linear/reviews/linear-review-9104"},
    {"rating": 5, "title": "Worth the price",
     "body": "Adoption was immediate across the team. No complaints.",
     "review_date": "26 days ago",
     "url": "https://www.g2.com/products/linear/reviews/linear-review-9105"},
    # Prior window
    {"rating": 5, "title": "Migrated from Jira",
     "body": "Migration was painless. Web app is best in class.",
     "review_date": "42 days ago",
     "url": "https://www.g2.com/products/linear/reviews/linear-review-9106"},
    {"rating": 4, "title": "Great tool",
     "body": "Solid, fast, opinionated. Pricing felt steep at first but the productivity gain paid off.",
     "review_date": "55 days ago",
     "url": "https://www.g2.com/products/linear/reviews/linear-review-9107"},
]


_DATADOG_GLASSDOOR: list[dict[str, Any]] = [
    {"rating": 2, "title": "Headcount efficiency push is grinding",
     "body": "Workloads went up after the RIF, internal mobility is essentially frozen. Leadership comms have gone quiet on growth.",
     "review_date": "3 days ago",
     "role": "Current Employee — Senior SWE",
     "url": "https://www.glassdoor.com/Reviews/Employee-Review-Datadog-RVW8001.htm"},
    {"rating": 3, "title": "Internal mobility broken",
     "body": "Cannot move teams. Promotions slowed. Comp is fine but the growth path is gone.",
     "review_date": "6 days ago",
     "role": "Current Employee — Engineer",
     "url": "https://www.glassdoor.com/Reviews/Employee-Review-Datadog-RVW8002.htm"},
    {"rating": 2, "title": "On-call is rough",
     "body": "On-call rotations doubled and burnout is visible. Always-on culture.",
     "review_date": "11 days ago",
     "role": "Current Employee — SRE",
     "url": "https://www.glassdoor.com/Reviews/Employee-Review-Datadog-RVW8003.htm"},
    {"rating": 3, "title": "Compensation OK, growth slowed",
     "body": "Comp competitive. Promotion paths reorganized — many ICs are stuck.",
     "review_date": "15 days ago",
     "role": "Current Employee — Senior PM",
     "url": "https://www.glassdoor.com/Reviews/Employee-Review-Datadog-RVW8004.htm"},
    {"rating": 4, "title": "Strong product, choppy leadership",
     "body": "Loved the technical work. Recent reorgs created friction with management chain.",
     "review_date": "20 days ago",
     "role": "Former Employee — Engineer",
     "url": "https://www.glassdoor.com/Reviews/Employee-Review-Datadog-RVW8005.htm"},
    # Prior window — clearer ratings
    {"rating": 4, "title": "Solid place",
     "body": "Solid compensation, smart engineers. Some on-call grind during incidents.",
     "review_date": "36 days ago",
     "role": "Current Employee — SWE",
     "url": "https://www.glassdoor.com/Reviews/Employee-Review-Datadog-RVW8006.htm"},
    {"rating": 4, "title": "Good engineering org",
     "body": "Great product, good comp, fair leadership.",
     "review_date": "42 days ago",
     "role": "Former Employee — Engineer",
     "url": "https://www.glassdoor.com/Reviews/Employee-Review-Datadog-RVW8007.htm"},
    {"rating": 4, "title": "Mostly positive",
     "body": "Worked here 3 years. Strong technical culture. Last reorg felt rough but bounced back.",
     "review_date": "51 days ago",
     "role": "Former Employee — Senior Engineer",
     "url": "https://www.glassdoor.com/Reviews/Employee-Review-Datadog-RVW8008.htm"},
    {"rating": 5, "title": "Best decision",
     "body": "Strong team, smart leadership.",
     "review_date": "58 days ago",
     "role": "Current Employee — Engineer",
     "url": "https://www.glassdoor.com/Reviews/Employee-Review-Datadog-RVW8009.htm"},
]


_DATADOG_G2: list[dict[str, Any]] = [
    {"rating": 4, "title": "Great observability stack",
     "body": "Cloud SIEM rollout is good. Pricing is the only complaint — expensive at scale.",
     "review_date": "5 days ago",
     "url": "https://www.g2.com/products/datadog/reviews/datadog-review-8100"},
    {"rating": 4, "title": "Powerful but expensive",
     "body": "Pricing model on log ingestion is brutal. Otherwise rock-solid.",
     "review_date": "10 days ago",
     "url": "https://www.g2.com/products/datadog/reviews/datadog-review-8101"},
    {"rating": 4, "title": "Best in observability",
     "body": "We've negotiated 3 renewals and pricing has gone up materially each time. Product is great.",
     "review_date": "18 days ago",
     "url": "https://www.g2.com/products/datadog/reviews/datadog-review-8102"},
    {"rating": 4, "title": "Solid, getting pricey",
     "body": "Renewal negotiation gets harder every year. Cost is now a board-level line item for us.",
     "review_date": "24 days ago",
     "url": "https://www.g2.com/products/datadog/reviews/datadog-review-8103"},
    # Prior window — same overall rating, no obvious distress
    {"rating": 4, "title": "Works well",
     "body": "Fast, comprehensive observability. Pricing is reasonable for what you get.",
     "review_date": "40 days ago",
     "url": "https://www.g2.com/products/datadog/reviews/datadog-review-8104"},
    {"rating": 5, "title": "Industry standard",
     "body": "We standardized on Datadog 4 years ago and have no regrets.",
     "review_date": "55 days ago",
     "url": "https://www.g2.com/products/datadog/reviews/datadog-review-8105"},
]


_FIXTURE_TABLE: dict[str, dict[ReviewSource, list[dict[str, Any]]]] = {
    "linear":  {"glassdoor": _LINEAR_GLASSDOOR, "g2": _LINEAR_G2},
    "datadog": {"glassdoor": _DATADOG_GLASSDOOR, "g2": _DATADOG_G2},
}


def fixture_for(subject: str, source: ReviewSource) -> list[dict[str, Any]]:
    """Return realistic-shaped Web Scraper API rows for ``(subject, source)``."""
    table = _FIXTURE_TABLE.get(subject.strip().lower(), {})
    return table.get(source, [])


# ── URL helpers (Bright Data search URLs / source links) ───────────


def glassdoor_search_url(subject: str) -> str:
    slug = subject.replace(" ", "-")
    return f"https://www.glassdoor.com/Search/results.htm?keyword={slug}"


def g2_product_url(subject: str) -> str:
    slug = subject.lower().replace(" ", "-")
    return f"https://www.g2.com/products/{slug}/reviews"


__all__ = [
    "Review",
    "ReviewSource",
    "ReviewSummary",
    "CompositeScore",
    "normalize",
    "summarize_trend",
    "composite_score",
    "detect_clusters",
    "fixture_for",
    "glassdoor_search_url",
    "g2_product_url",
]
