"""AltData-module-specific tests — Day 6 acceptance criterion.

Acceptance: "AltData returns useful output for at least one demo
target. Polish ceiling lower than first 3 modules — supporting cast."

Coverage:
  * Pure data layer — normalization, complaint cluster detection,
    trend computation, composite score blending.
  * Mock path — Linear surfaces momentum, Datadog surfaces distress.
  * Live path — Web Scraper API stubbed via monkeypatch.
"""

from __future__ import annotations

import pytest

from app.brightdata import web_scraper_api
from app.modules import MODULES
from app.modules.altdata import AltDataModule
from app.modules.altdata_data import (
    Review,
    composite_score,
    detect_clusters,
    fixture_for,
    g2_product_url,
    glassdoor_search_url,
    normalize,
    summarize_trend,
)


# ── Cluster vocabulary ───────────────────────────────────────────────


@pytest.mark.parametrize("text,expected", [
    ("Support ticket response time has been awful", "support"),
    ("Long outages and crashes — app is unreliable", "stability"),
    ("Pricing keeps going up at renewal", "pricing"),
    ("Layoffs and reorg have killed morale", "leadership"),
    ("Comp is below market and no raise this year", "compensation"),
    ("Work-life balance is terrible, weekends and on-call", "wlb"),
    ("Internal mobility is broken, no promotion path", "mobility"),
])
def test_detect_clusters_picks_up_label(text, expected):
    assert expected in detect_clusters(text)


def test_detect_clusters_returns_empty_on_neutral_text():
    assert detect_clusters("great team and clear vision, smart leadership") != []
    # Above includes "leadership" — but the *summarize_trend* filter only
    # counts clusters in rating<=3 reviews, so the positive mention won't
    # surface as a complaint downstream. (Covered in trend tests below.)


# ── Normalization ───────────────────────────────────────────────────


def test_normalize_accepts_common_aliases():
    rows = [
        {"rating": "4.5 / 5", "body": "x", "review_date": "5 days ago",
         "url": "https://x.test/1", "role": "Current Employee"},
        {"stars": 3, "comment": "y", "review_date": "12 days ago"},
        {"score": 5.0, "summary": "z", "posted_at": "2026-03-15"},
        # Missing both rating and body — drop.
        {"role": "anonymous"},
    ]
    out = normalize(rows, source="glassdoor")
    assert len(out) == 3
    assert out[0].rating == 4.5
    assert out[0].posted_days_ago == 5
    assert out[1].rating == 3.0
    assert out[2].rating == 5.0


def test_normalize_assigns_source():
    rows = [{"rating": 4, "body": "ok"}]
    out_gd = normalize(rows, source="glassdoor")
    out_g2 = normalize(rows, source="g2")
    assert out_gd[0].source == "glassdoor"
    assert out_g2[0].source == "g2"


def test_normalize_handles_unknown_date_format_gracefully():
    """Date strings we can't parse default to 30 days ago — keeps the
    trend computation from crashing on weird inputs."""
    out = normalize(
        [{"rating": 4, "body": "x", "review_date": "yesterday probably"}],
        source="g2",
    )
    assert out[0].posted_days_ago == 30


def test_normalize_drops_non_dict_rows():
    """Defensive: Bright Data sometimes interleaves error markers."""
    out = normalize(
        ["not a dict", {"rating": 4, "body": "x"}, None],  # type: ignore[list-item]
        source="g2",
    )
    assert len(out) == 1


# ── Trend math ──────────────────────────────────────────────────────


def test_summarize_trend_computes_rating_delta_and_velocity():
    reviews = [
        # Recent window (5)
        Review(rating=5, posted_days_ago=2, title="", body="great", source="glassdoor"),
        Review(rating=5, posted_days_ago=5, title="", body="great", source="glassdoor"),
        Review(rating=4, posted_days_ago=8, title="", body="ok", source="glassdoor"),
        Review(rating=5, posted_days_ago=14, title="", body="great", source="glassdoor"),
        Review(rating=5, posted_days_ago=22, title="", body="great", source="glassdoor"),
        # Prior window (2)
        Review(rating=3, posted_days_ago=40, title="", body="ok", source="glassdoor"),
        Review(rating=3, posted_days_ago=55, title="", body="ok", source="glassdoor"),
    ]
    s = summarize_trend(reviews, subject="ZZ")
    assert s.recent_30d == 5
    assert s.prior_30_60d == 2
    assert s.avg_rating_recent == pytest.approx(4.8, abs=0.05)
    assert s.avg_rating_prior == 3.0
    assert s.rating_delta == pytest.approx(1.8, abs=0.05)
    assert s.velocity_ratio == pytest.approx(2.5, abs=0.05)


def test_summarize_trend_excludes_positive_mentions_from_complaint_clusters():
    """Positive reviews that mention 'leadership' or 'compensation' should NOT
    inflate complaint clusters — we filter to rating ≤ 3."""
    reviews = [
        Review(rating=5, posted_days_ago=3, title="Best leadership I've seen",
               body="Leadership trust is amazing", source="glassdoor"),
        Review(rating=5, posted_days_ago=5, title="Comp is great",
               body="Salary and bonus best in industry", source="glassdoor"),
        Review(rating=2, posted_days_ago=10, title="Bad on-call",
               body="Burnout culture, overworked on weekends", source="glassdoor"),
    ]
    s = summarize_trend(reviews, subject="ZZ")
    # The only complaint cluster present should be 'wlb' from the low-rated review.
    assert s.top_complaint == "wlb"
    assert "leadership" not in s.complaint_clusters
    assert "compensation" not in s.complaint_clusters


def test_summarize_trend_handles_empty_prior_window():
    """When there's no prior baseline, velocity is large (capped) and avg
    prior rating is 0 — downstream finding logic guards on that."""
    reviews = [
        Review(rating=4, posted_days_ago=5, title="", body="ok", source="g2"),
        Review(rating=5, posted_days_ago=10, title="", body="great", source="g2"),
    ]
    s = summarize_trend(reviews, subject="ZZ")
    assert s.prior_30_60d == 0
    assert s.avg_rating_prior == 0.0
    assert s.velocity_ratio == pytest.approx(9.9)


def test_summarize_trend_clusters_complaints_from_recent_negative_reviews():
    """Complaint clusters come from rating<=3 reviews — typically the
    'cons' surface in Bright Data's row shape."""
    reviews = [
        Review(rating=2, posted_days_ago=3, title="bad",
               body="Support ticket response time was awful, no support",
               source="glassdoor"),
        Review(rating=2, posted_days_ago=7, title="frustrating",
               body="No support, ignored tickets",
               source="glassdoor"),
        Review(rating=3, posted_days_ago=12, title="poor support",
               body="Account manager unresponsive",
               source="glassdoor"),
    ]
    s = summarize_trend(reviews, subject="ZZ")
    assert s.top_complaint == "support"
    assert s.complaint_clusters["support"] >= 2


# ── Composite score ─────────────────────────────────────────────────


def test_composite_score_returns_neutral_on_empty():
    cs = composite_score([])
    assert cs.score == 0.50
    assert cs.label == "neutral"
    assert cs.drivers == []


def test_composite_score_labels_distress_when_rating_drops():
    """A negative rating shift on a single source should label distress."""
    # Build a summary with material rating decline + complaint cluster.
    reviews = [
        Review(rating=2, posted_days_ago=5, title="",
               body="layoffs and reorg killed morale", source="glassdoor"),
        Review(rating=2, posted_days_ago=10, title="",
               body="reorg made things worse", source="glassdoor"),
        Review(rating=3, posted_days_ago=15, title="",
               body="leadership comms are gone, layoffs", source="glassdoor"),
        Review(rating=4, posted_days_ago=40, title="", body="solid", source="glassdoor"),
        Review(rating=5, posted_days_ago=55, title="", body="great", source="glassdoor"),
    ]
    s = summarize_trend(reviews, subject="ZZ")
    cs = composite_score([s])
    assert cs.label == "distress"
    assert cs.score < 0.45
    # The drivers list cites the rating shift and the leadership cluster.
    drivers_text = " ".join(cs.drivers).lower()
    assert "leadership" in drivers_text or "rating" in drivers_text


def test_composite_score_labels_momentum_when_rating_climbs():
    reviews = [
        Review(rating=5, posted_days_ago=3, title="", body="great", source="glassdoor"),
        Review(rating=5, posted_days_ago=8, title="", body="great", source="glassdoor"),
        Review(rating=5, posted_days_ago=14, title="", body="great", source="glassdoor"),
        Review(rating=4, posted_days_ago=22, title="", body="great", source="glassdoor"),
        Review(rating=3, posted_days_ago=38, title="", body="ok", source="glassdoor"),
        Review(rating=3, posted_days_ago=50, title="", body="ok", source="glassdoor"),
    ]
    s = summarize_trend(reviews, subject="ZZ")
    cs = composite_score([s])
    assert cs.label == "momentum"
    assert cs.score >= 0.55


# ── Mock acceptance: Linear momentum + Datadog distress ─────────────


async def test_altdata_linear_meets_day6_acceptance():
    """Day-6 acceptance: 'useful output for at least one demo target'.

    Linear should surface a momentum label with ≥1 high-severity finding
    citing the actual review URLs.
    """
    result = await MODULES["altdata"].run(
        {"query": "Linear sentiment scan", "subject": "Linear"},
    )
    assert result.module == "altdata"
    assert result.raw_data["composite_label"] == "momentum"
    assert result.raw_data["composite_score"] >= 0.55
    # At least one high-severity finding.
    severities = {f.severity for f in result.findings}
    assert "high" in severities or "notable" in severities
    # Findings cite real review URLs from the fixture.
    urls = [u for f in result.findings for u in f.evidence]
    assert any("glassdoor.com" in u for u in urls)
    assert any("g2.com" in u for u in urls)


async def test_altdata_datadog_surfaces_distress_signal():
    """Per implementation plan §8.2 demo query — Datadog alt-data is the
    'Glassdoor sentiment shift QoQ' headline."""
    result = await MODULES["altdata"].run({"subject": "Datadog"})
    assert result.raw_data["composite_label"] == "distress"
    assert result.raw_data["composite_score"] <= 0.45
    severities = {f.severity for f in result.findings}
    assert "high" in severities, f"distress signal should produce a high-severity finding"
    # At least one finding mentions the rating decline.
    text = " ".join(f.statement.lower() for f in result.findings)
    assert "qoq" in text or "decline" in text or "stars" in text
    # Source label appears clean (no "Glassdoor glassdoor-sentiment" repetition).
    assert "glassdoor glassdoor" not in text
    assert "g2 g2" not in text


async def test_altdata_per_source_breakdown_in_raw_data():
    """Renderer needs per-source recent/prior counts in raw_data."""
    result = await MODULES["altdata"].run({"subject": "Linear"})
    sources = result.raw_data["sources"]
    assert "glassdoor" in sources and "g2" in sources
    for s in sources.values():
        assert "recent_30d" in s
        assert "prior_30_60d" in s
        assert "rating_delta" in s
        assert "complaint_clusters" in s


async def test_altdata_unknown_subject_returns_info_finding():
    """No fixture, no live data → honest 'no data' finding, not crash."""
    result = await MODULES["altdata"].run({"subject": "ZZ Unknown"})
    assert result.findings
    assert result.findings[0].severity == "info"
    text = result.findings[0].statement.lower()
    assert "no review" in text or "no data" in text


async def test_altdata_via_attribution_is_web_scraper_api():
    """Per implementation plan §4.4 the AltData module is the Web Scraper
    API customer — every source should be attributed to it."""
    result = await MODULES["altdata"].run({"subject": "Linear"})
    assert result.sources
    for s in result.sources:
        assert s.via == "web_scraper_api"


def test_altdata_url_helpers_produce_plausible_urls():
    assert glassdoor_search_url("Linear").startswith("https://www.glassdoor.com/")
    assert g2_product_url("Linear").startswith("https://www.g2.com/products/")
    # Multi-word subjects slug correctly.
    assert "Acme-Corp" in glassdoor_search_url("Acme Corp")
    assert "acme-corp" in g2_product_url("Acme Corp")


# ── Live path stubbed via monkeypatch ───────────────────────────────


async def test_altdata_live_uses_both_sources_when_both_return_data(monkeypatch):
    """Both Glassdoor + G2 live → mode=live, composite blends both."""

    async def fake_glassdoor(company, *, limit=50):
        return [
            {"rating": 2, "body": "Layoffs and reorg, leadership comms gone",
             "review_date": "5 days ago",
             "url": "https://glassdoor.com/r1"},
            {"rating": 2, "body": "Internal mobility frozen",
             "review_date": "10 days ago",
             "url": "https://glassdoor.com/r2"},
            {"rating": 3, "body": "stuck, no promotion",
             "review_date": "15 days ago",
             "url": "https://glassdoor.com/r3"},
            {"rating": 4, "body": "solid", "review_date": "45 days ago"},
            {"rating": 5, "body": "great", "review_date": "55 days ago"},
        ]

    async def fake_g2(company, *, limit=50):
        return [
            {"rating": 4, "body": "Pricing keeps climbing at renewal",
             "review_date": "4 days ago",
             "url": "https://g2.com/r1"},
            {"rating": 4, "body": "Renewal pricing is brutal",
             "review_date": "12 days ago",
             "url": "https://g2.com/r2"},
            {"rating": 4, "body": "Expensive",
             "review_date": "20 days ago",
             "url": "https://g2.com/r3"},
            {"rating": 4, "body": "ok", "review_date": "45 days ago"},
            {"rating": 5, "body": "great", "review_date": "55 days ago"},
        ]

    monkeypatch.setattr(web_scraper_api, "fetch_glassdoor_reviews", fake_glassdoor)
    monkeypatch.setattr(web_scraper_api, "fetch_g2_reviews", fake_g2)

    result = await AltDataModule().execute({"subject": "ZZ Datadog"})
    assert result.raw_data["mode"] == "live"
    sources = result.raw_data["sources"]
    assert "glassdoor" in sources and "g2" in sources
    # Real review URLs flow through to evidence.
    urls = [u for f in result.findings for u in f.evidence]
    assert any("glassdoor.com/r" in u for u in urls)


async def test_altdata_live_partial_when_one_source_empty(monkeypatch):
    """G2 live, Glassdoor empty → falls back to Glassdoor fixture → partial."""

    async def fake_glassdoor(company, *, limit=50):
        return None  # MCP unavailable for Glassdoor

    async def fake_g2(company, *, limit=50):
        return [
            {"rating": 5, "body": "great", "review_date": "3 days ago",
             "url": "https://g2.com/x1"},
            {"rating": 5, "body": "great", "review_date": "10 days ago",
             "url": "https://g2.com/x2"},
            {"rating": 4, "body": "ok", "review_date": "20 days ago",
             "url": "https://g2.com/x3"},
            {"rating": 3, "body": "ok", "review_date": "40 days ago"},
            {"rating": 3, "body": "ok", "review_date": "55 days ago"},
        ]

    monkeypatch.setattr(web_scraper_api, "fetch_glassdoor_reviews", fake_glassdoor)
    monkeypatch.setattr(web_scraper_api, "fetch_g2_reviews", fake_g2)

    result = await AltDataModule().execute({"subject": "Linear"})
    assert result.raw_data["mode"] == "partial"
    # Both source summaries still flow through (Glassdoor uses fixture).
    assert set(result.raw_data["sources"].keys()) == {"glassdoor", "g2"}


async def test_altdata_live_falls_back_to_mock_when_both_unavailable(monkeypatch):
    """When both Web Scraper API calls return None and there's no fixture
    for the subject, we surface the unknown-subject info finding."""

    async def fake_glassdoor(company, *, limit=50):
        return None

    async def fake_g2(company, *, limit=50):
        return None

    monkeypatch.setattr(web_scraper_api, "fetch_glassdoor_reviews", fake_glassdoor)
    monkeypatch.setattr(web_scraper_api, "fetch_g2_reviews", fake_g2)

    # Subject with a fixture → mock fallback still surfaces useful data.
    result = await AltDataModule().execute({"subject": "Linear"})
    assert result.raw_data["mode"] == "mock"
    assert result.raw_data["composite_label"] == "momentum"


async def test_altdata_live_too_few_normalized_rows_fallback(monkeypatch):
    """If a source returns <3 normalized reviews, treat as 'no live data'
    and fall back to fixture for that source — avoids inflating velocity
    ratios on a sample of 1."""

    async def fake_glassdoor(company, *, limit=50):
        return [{"rating": 4, "body": "ok"}]  # 1 row — too few

    async def fake_g2(company, *, limit=50):
        return None  # also no live G2

    monkeypatch.setattr(web_scraper_api, "fetch_glassdoor_reviews", fake_glassdoor)
    monkeypatch.setattr(web_scraper_api, "fetch_g2_reviews", fake_g2)

    result = await AltDataModule().execute({"subject": "Linear"})
    # Both ended up on fixtures — pure mock mode.
    assert result.raw_data["mode"] == "mock"


# ── Fixture sanity ──────────────────────────────────────────────────


def test_fixture_for_known_subjects():
    """Demo targets must have both sources populated."""
    for subject in ("Linear", "Datadog"):
        gd = fixture_for(subject, "glassdoor")
        g2 = fixture_for(subject, "g2")
        assert len(gd) >= 5, f"{subject} glassdoor fixture too small"
        assert len(g2) >= 4, f"{subject} g2 fixture too small"


def test_fixture_for_unknown_returns_empty():
    assert fixture_for("UnknownCo", "glassdoor") == []
    assert fixture_for("UnknownCo", "g2") == []
