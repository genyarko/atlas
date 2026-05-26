"""Visual-module-specific tests — Day 5 acceptance criterion.

Acceptance: "Visual flags your controlled lookalikes as high-suspicion
with specific anomaly callouts."

Coverage:
  * Pure data layer — verdict rubric, canonical/social filtering, controlled-target catalog.
  * Mock path — Linear/AcmeCorp controlled targets surface as expected.
  * Live path — SERP + Scraping Browser + Claude vision stubbed via monkeypatch.
  * Renderer integration — visual suspects table shows up in HTML + markdown briefs.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.brief import render_html, render_markdown
from app.brightdata import scraping_browser, serp
from app.models import Brief, BriefSection, Question, ResearchPlan
from app.modules import MODULES
from app.modules.visual import VisualModule
from app.modules.visual_data import (
    ACMECORP_COLOR_LOOKALIKE,
    ACMECORP_TARGET,
    ACMECORP_TYPO_LOOKALIKE,
    CONTROLLED_TARGETS,
    SOCIAL_PLATFORM_HOSTS,
    SuspectCandidate,
    VisionAnomaly,
    VisionDiff,
    diff_from_declared,
    filter_candidates,
    get_controlled_target,
    is_canonical_or_social,
    verdict_for,
    verdict_to_severity,
)


# ── Pure data layer ─────────────────────────────────────────────────


def test_controlled_target_catalog_has_acmecorp_with_two_lookalikes():
    """Day-5 deliverable: 2 controlled lookalike pages set up for guaranteed demo."""
    assert "AcmeCorp" in CONTROLLED_TARGETS
    target = CONTROLLED_TARGETS["AcmeCorp"]
    assert len(target.lookalikes) == 2
    slugs = {la.slug for la in target.lookalikes}
    assert slugs == {"acmecorp-typo-domain", "acmecorp-color-swap"}


def test_controlled_lookalike_html_files_exist_on_disk():
    """The two lookalike HTML files must be tracked alongside the catalog."""
    for la in ACMECORP_TARGET.lookalikes:
        assert la.html_path.exists(), f"missing controlled-lookalike HTML: {la.html_path}"
        text = la.html_path.read_text(encoding="utf-8")
        # Each lookalike file must be marked as a demo asset (so a future
        # contributor can't quietly delete one without surprising us).
        assert "Atlas demo asset" in text


def test_controlled_legit_reference_exists():
    assert ACMECORP_TARGET.legit_html_path.exists()


def test_typo_lookalike_declares_form_anomaly():
    """Form-post anomaly is the rubric escalator — make sure the typo
    lookalike actually carries one so its verdict can clear 'critical'."""
    kinds = {a.kind for a in ACMECORP_TYPO_LOOKALIKE.declared_anomalies}
    assert "form" in kinds


def test_color_lookalike_carries_subtler_anomalies():
    """Color-swap is the subtler page — at least 3 anomalies, no form-post anomaly."""
    kinds = [a.kind for a in ACMECORP_COLOR_LOOKALIKE.declared_anomalies]
    assert len(kinds) >= 3
    assert "form" not in kinds  # subtler — no phishing-shaped form post


@pytest.mark.parametrize("url,brand_url,expected", [
    # On canonical domain → not a suspect.
    ("https://acmecorp-demo.test/login", "https://acmecorp-demo.test", True),
    ("https://docs.acmecorp-demo.test", "https://acmecorp-demo.test", True),
    # Known social platform → not a suspect.
    ("https://www.linkedin.com/company/acme", "https://acmecorp-demo.test", True),
    ("https://github.com/acmecorp", "https://acmecorp-demo.test", True),
    # Different host, not social → IS a suspect.
    ("https://acmecorp-secure-login.test", "https://acmecorp-demo.test", False),
    ("https://app-acmecorp.test/signin", "https://acmecorp-demo.test", False),
    # Unparseable → drop as suspect (treat as canonical).
    ("not a url", "https://acmecorp-demo.test", True),
])
def test_is_canonical_or_social(url, brand_url, expected):
    assert is_canonical_or_social(url, brand_url) is expected


def test_filter_candidates_keeps_controlled_even_if_subdomain_of_brand():
    """Controlled candidates must never be filtered out — they're the demo guarantee."""
    candidates = [
        SuspectCandidate(url="https://acmecorp-demo.test/login", source="controlled"),
        SuspectCandidate(url="https://acmecorp-secure-login.test", source="serp"),
        SuspectCandidate(url="https://acmecorp-demo.test/help", source="serp"),  # canonical, drop
        SuspectCandidate(url="https://www.linkedin.com/in/acme", source="serp"),  # social, drop
    ]
    kept = filter_candidates(candidates, brand_url="https://acmecorp-demo.test")
    urls = {c.url for c in kept}
    assert "https://acmecorp-demo.test/login" in urls       # controlled kept
    assert "https://acmecorp-secure-login.test" in urls    # off-canonical kept
    assert "https://acmecorp-demo.test/help" not in urls    # canonical dropped
    assert "https://www.linkedin.com/in/acme" not in urls  # social dropped


def test_filter_candidates_dedupes_by_url():
    candidates = [
        SuspectCandidate(url="https://x.test", source="serp"),
        SuspectCandidate(url="https://x.test", source="serp", title="dup"),
    ]
    assert len(filter_candidates(candidates, brand_url="https://brand.test")) == 1


def test_social_platform_hosts_includes_common_brand_surfaces():
    for host in ("linkedin.com", "github.com", "g2.com", "glassdoor.com"):
        assert host in SOCIAL_PLATFORM_HOSTS


# ── Verdict rubric ─────────────────────────────────────────────────


@pytest.mark.parametrize("anom_count,sim,has_form,expected", [
    (5, 0.91, True,  "critical"),   # typo lookalike profile
    (4, 0.88, False, "high"),       # 4 anomalies, no form → high but not critical
    (3, 0.84, False, "high"),       # color lookalike-ish profile
    (3, 0.78, False, "notable"),    # 3 anomalies but similarity too low for high
    (2, 0.85, True,  "high"),       # form anomaly is the escalator
    (2, 0.85, False, "notable"),
    (1, 0.90, False, "notable"),
    (1, 0.50, False, "low"),
    (0, 0.95, False, "low"),
])
def test_verdict_rubric(anom_count, sim, has_form, expected):
    assert verdict_for(
        anomaly_count=anom_count, similarity=sim, has_form_anomaly=has_form,
    ) == expected


def test_verdict_to_severity_maps_low_to_info():
    assert verdict_to_severity("low") == "info"
    assert verdict_to_severity("notable") == "notable"
    assert verdict_to_severity("high") == "high"
    assert verdict_to_severity("critical") == "critical"


# ── Vision response parser (trust boundary with Claude) ────────────


def test_parse_vision_response_happy_path():
    from app.modules.visual import _parse_vision_response

    parsed = _parse_vision_response(json.dumps({
        "similarity": 0.88,
        "anomalies": [
            {"kind": "logo", "description": "Wordmark misspelled"},
            {"kind": "form", "description": "Form posts off-canonical"},
        ],
        "reasoning": "phishing-shaped",
    }))
    assert parsed is not None
    assert parsed["similarity"] == 0.88
    assert len(parsed["anomalies"]) == 2
    assert parsed["anomalies"][0].kind == "logo"
    assert parsed["reasoning"] == "phishing-shaped"


def test_parse_vision_response_strips_markdown_fences():
    """Claude sometimes wraps JSON in ```json``` fences despite the prompt."""
    from app.modules.visual import _parse_vision_response

    fenced = "```json\n" + json.dumps({
        "similarity": 0.5,
        "anomalies": [],
        "reasoning": "ok",
    }) + "\n```"
    parsed = _parse_vision_response(fenced)
    assert parsed is not None
    assert parsed["similarity"] == 0.5


def test_parse_vision_response_clamps_out_of_range_similarity():
    from app.modules.visual import _parse_vision_response

    over = _parse_vision_response(json.dumps({"similarity": 1.5, "anomalies": []}))
    under = _parse_vision_response(json.dumps({"similarity": -0.2, "anomalies": []}))
    assert over is not None and over["similarity"] == 1.0
    assert under is not None and under["similarity"] == 0.0


def test_parse_vision_response_drops_invalid_anomalies():
    """Invalid kinds and empty descriptions get filtered, not crash the parse."""
    from app.modules.visual import _parse_vision_response

    parsed = _parse_vision_response(json.dumps({
        "similarity": 0.7,
        "anomalies": [
            {"kind": "logo", "description": "valid"},
            {"kind": "INVALID_KIND", "description": "drop me"},
            {"kind": "copy", "description": "  "},  # empty after strip → drop
            {"kind": "color"},                       # missing description → drop
            "not even a dict",                       # → drop
        ],
        "reasoning": "mixed",
    }))
    assert parsed is not None
    kinds = [a.kind for a in parsed["anomalies"]]
    assert kinds == ["logo"]


@pytest.mark.parametrize("payload", [
    "not json at all",
    "{ invalid",
    "[1, 2, 3]",                                # list at top level → drop
    json.dumps({"anomalies": []}),              # missing similarity → drop
    json.dumps({"similarity": "high"}),         # non-numeric similarity → drop
])
def test_parse_vision_response_returns_none_on_malformed(payload):
    from app.modules.visual import _parse_vision_response

    assert _parse_vision_response(payload) is None


def test_diff_from_declared_uses_lookalike_anomalies_and_url():
    diff = diff_from_declared(
        ACMECORP_TYPO_LOOKALIKE, legit_url="https://acmecorp-demo.test/login",
    )
    assert diff.suspect_url == ACMECORP_TYPO_LOOKALIKE.url
    assert diff.legit_url == "https://acmecorp-demo.test/login"
    assert len(diff.anomalies) == len(ACMECORP_TYPO_LOOKALIKE.declared_anomalies)
    # Form-anomaly + 5 anomalies + 0.91 similarity → critical.
    assert diff.verdict == "critical"


# ── Mock acceptance: AcmeCorp flags both controlled lookalikes high+ ───


async def test_visual_acmecorp_flags_both_controlled_lookalikes_at_high_or_better():
    """Day-5 acceptance: 'flags your controlled lookalikes as high-suspicion
    with specific anomaly callouts'."""
    result = await MODULES["visual"].run(
        {"query": "Scan AcmeCorp for impersonation", "subject": "AcmeCorp"}
    )
    assert result.module == "visual"
    suspects = result.raw_data["suspects"]
    assert len(suspects) == 2
    # Both controlled lookalikes appear in the result.
    suspect_urls = {s["suspect_url"] for s in suspects}
    assert suspect_urls == {
        ACMECORP_TYPO_LOOKALIKE.url, ACMECORP_COLOR_LOOKALIKE.url,
    }
    # Both clear the "notable" bar; the typo squad is critical.
    verdicts = {s["suspect_url"]: s["verdict"] for s in suspects}
    assert verdicts[ACMECORP_TYPO_LOOKALIKE.url] == "critical"
    assert verdicts[ACMECORP_COLOR_LOOKALIKE.url] in ("high", "notable")
    # Each surface has specific anomaly callouts (not generic).
    for s in suspects:
        assert s["anomaly_count"] >= 3
        for a in s["anomalies"]:
            assert a["description"]
            assert a["kind"] in {"logo", "color", "copy", "form", "footer", "layout", "stale"}
    # Headline finding cites the suspect URL.
    headline = result.findings[0]
    assert headline.severity in ("critical", "high")
    assert ACMECORP_TYPO_LOOKALIKE.url in headline.evidence


async def test_visual_acmecorp_finding_calls_out_typosquat_specifically():
    result = await MODULES["visual"].run({"query": "AcmeCorp", "subject": "AcmeCorp"})
    text = " ".join(f.statement for f in result.findings)
    # The headline finding should cite the typosquat URL by name.
    assert "acmecorp-secure-login.test" in text
    # And include some anomaly observation (the "—" separator marks the observation).
    assert "—" in text or "anomal" in text.lower()


async def test_visual_acmecorp_high_or_critical_findings_aggregate():
    """When ≥2 lookalikes clear 'high', emit the aggregate domain-takedown finding."""
    result = await MODULES["visual"].run({"query": "AcmeCorp", "subject": "AcmeCorp"})
    # AcmeCorp's typo (critical) + color (high or notable). When color clears
    # 'high', the aggregate finding should fire.
    high_count = result.raw_data["high_count"]
    aggregate = [f for f in result.findings if "Multiple high-suspicion" in f.statement]
    if high_count >= 2:
        assert aggregate, "aggregate domain-takedown finding should fire when ≥2 highs"


async def test_visual_unknown_brand_falls_back_to_synthetic_diffs():
    """No controlled target → still surfaces two plausible typosquat profiles."""
    result = await MODULES["visual"].run({"query": "Scan ZZ Unknown", "subject": "ZZ Unknown"})
    assert len(result.raw_data["suspects"]) == 2
    # Synthetic URLs are derived from the subject slug.
    urls = {s["suspect_url"] for s in result.raw_data["suspects"]}
    assert any("zzunknown" in u.lower() for u in urls)


# ── Live path stubbed via monkeypatch ───────────────────────────────


def _vision_response(*, similarity: float, anomalies: list[dict], reasoning: str = "") -> SimpleNamespace:
    """Shape a fake Claude response with .content carrying our JSON."""
    payload = json.dumps({
        "similarity": similarity,
        "anomalies": anomalies,
        "reasoning": reasoning,
    })
    return SimpleNamespace(content=payload)


class _FakeLLM:
    """Minimal async LLM stand-in that returns scripted responses per call."""

    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self._responses = list(responses)
        self.calls: list[list] = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if not self._responses:
            raise RuntimeError("FakeLLM exhausted")
        return self._responses.pop(0)


async def test_visual_live_uses_serp_scraping_browser_and_claude(monkeypatch):
    """End-to-end live path: SERP discovery → screenshots → vision diff."""

    serp_calls: list[str] = []
    screenshot_calls: list[str] = []

    async def fake_serp(query, *, num=10):
        serp_calls.append(query)
        # Return a mix: one off-canonical suspect, one social (to be filtered),
        # one canonical (to be filtered).
        return [
            {"title": "Linear sign in", "link": "https://linear.app/login"},          # canonical, drop
            {"title": "Linear · LinkedIn", "link": "https://www.linkedin.com/company/linear"},  # social, drop
            {"title": "Login to Linear", "link": "https://linear-secure-login.test"},  # SUSPECT
        ]

    async def fake_screenshot(url, *, region=None):
        screenshot_calls.append(url)
        return b"\x89PNG\r\n\x1a\nfakebytes"

    fake_llm = _FakeLLM([
        _vision_response(
            similarity=0.88,
            anomalies=[
                {"kind": "logo", "description": "Logo aspect drifted 7%"},
                {"kind": "copy", "description": "Heading reads 'Login' not 'Sign in to Linear'"},
                {"kind": "form", "description": "Form posts to collect.linear-secure-login.test"},
                {"kind": "footer", "description": "Footer points to non-canonical .test domains"},
            ],
            reasoning="Phishing-shaped impersonation with off-brand CTA.",
        ),
    ])

    monkeypatch.setattr(serp, "search", fake_serp)
    monkeypatch.setattr(scraping_browser, "screenshot", fake_screenshot)
    # VisualModule imports get_llm lazily inside _vision_diff — patch the
    # source module, not app.modules.visual, since the name isn't bound
    # there at module-import time.
    import app.agent.llm as llm_mod
    monkeypatch.setattr(llm_mod, "get_llm", lambda: fake_llm)

    module = VisualModule()
    result = await module.execute({"query": "Scan Linear for impersonation", "subject": "Linear"})

    # SERP was called for each default term.
    assert len(serp_calls) >= 1
    # Legit + 1 suspect screenshotted (canonical and social filtered out before screenshot).
    assert "https://linear.app" in screenshot_calls
    assert "https://linear-secure-login.test" in screenshot_calls
    # And the suspect made it through to a vision call.
    assert len(fake_llm.calls) == 1
    # Live mode flagged; verdict reflects form-anomaly escalator.
    assert result.raw_data["mode"] == "live"
    assert result.raw_data["suspects"][0]["verdict"] in ("high", "critical")
    assert result.raw_data["suspects"][0]["suspect_url"] == "https://linear-secure-login.test"


async def test_visual_live_falls_back_to_mock_when_screenshot_unavailable(monkeypatch):
    """If Scraping Browser yields no screenshots and there's no controlled
    fallback, mock takes over so the brief still renders."""

    async def fake_serp(query, *, num=10):
        return [{"title": "Login", "link": "https://unknown-secure-login.test"}]

    async def fake_screenshot(url, *, region=None):
        return None  # MCP unavailable everywhere

    monkeypatch.setattr(serp, "search", fake_serp)
    monkeypatch.setattr(scraping_browser, "screenshot", fake_screenshot)

    module = VisualModule()
    result = await module.execute(
        {"query": "ZZ Unknown impersonation", "subject": "ZZ Unknown"}
    )
    # No live diff possible → mock fallback. Two synthetic suspects.
    assert result.raw_data["mode"] == "mock"
    assert len(result.raw_data["suspects"]) == 2


async def test_visual_live_for_controlled_target_falls_back_on_vision_failure(monkeypatch):
    """When vision call fails on a controlled target, declared anomalies
    are used so the brief still surfaces anomaly callouts for the demo."""

    async def fake_serp(query, *, num=10):
        return []  # no SERP padding

    async def fake_screenshot(url, *, region=None):
        return b"\x89PNG\r\n\x1a\nfake"

    class _FailingLLM:
        async def ainvoke(self, messages):
            raise RuntimeError("vision API down")

    monkeypatch.setattr(serp, "search", fake_serp)
    monkeypatch.setattr(scraping_browser, "screenshot", fake_screenshot)
    import app.agent.llm as llm_mod
    monkeypatch.setattr(llm_mod, "get_llm", lambda: _FailingLLM())

    module = VisualModule()
    result = await module.execute({"query": "AcmeCorp impersonation", "subject": "AcmeCorp"})

    # Vision call failed for every suspect → no live diffs → mock-mode result,
    # but the controlled-target declared anomalies still flow through.
    assert result.raw_data["mode"] == "mock"
    urls = {s["suspect_url"] for s in result.raw_data["suspects"]}
    assert urls == {ACMECORP_TYPO_LOOKALIKE.url, ACMECORP_COLOR_LOOKALIKE.url}


async def test_visual_live_partial_when_one_suspect_drops(monkeypatch):
    """A SERP suspect whose screenshot fails gets dropped; the controlled
    suspects still contribute → mode=partial."""

    async def fake_serp(query, *, num=10):
        # Always return the same off-canonical SERP suspect.
        return [{"title": "AcmeCorp login", "link": "https://acmecorp-phish.test"}]

    async def fake_screenshot(url, *, region=None):
        # Suspect screenshot fails; controlled lookalikes succeed; legit succeeds.
        if "phish" in url:
            return None
        return b"\x89PNG\r\n\x1a\nok"

    fake_llm = _FakeLLM([
        # Controlled typo lookalike → high-confidence
        _vision_response(
            similarity=0.91,
            anomalies=[
                {"kind": "logo", "description": "Wordmark misspelled"},
                {"kind": "copy", "description": "CTA reads 'Login'"},
                {"kind": "form", "description": "Form posts off-canonical"},
                {"kind": "footer", "description": "Footer points off-canonical"},
            ],
            reasoning="phishing-shaped",
        ),
        # Controlled color lookalike → notable / high
        _vision_response(
            similarity=0.83,
            anomalies=[
                {"kind": "color", "description": "Primary drifted"},
                {"kind": "color", "description": "Accent swapped"},
                {"kind": "stale", "description": "Stale Beta callout"},
            ],
            reasoning="palette drift",
        ),
    ])
    monkeypatch.setattr(serp, "search", fake_serp)
    monkeypatch.setattr(scraping_browser, "screenshot", fake_screenshot)
    import app.agent.llm as llm_mod
    monkeypatch.setattr(llm_mod, "get_llm", lambda: fake_llm)

    result = await VisualModule().execute(
        {"query": "Scan AcmeCorp", "subject": "AcmeCorp"}
    )
    assert result.raw_data["mode"] == "partial"
    assert result.status == "partial"
    # The phish SERP suspect dropped, controlled suspects survived.
    assert "https://acmecorp-phish.test" in result.raw_data["dropped"]
    urls = {s["suspect_url"] for s in result.raw_data["suspects"]}
    assert urls == {ACMECORP_TYPO_LOOKALIKE.url, ACMECORP_COLOR_LOOKALIKE.url}


# ── Renderer integration: visual table appears in HTML + markdown ───


def _brief_with_visual_section(result) -> Brief:
    q = Question(text="Scan AcmeCorp for impersonation")
    plan = ResearchPlan(question_id=q.id, intent="security", modules_to_invoke=[])
    section = BriefSection(
        module=result.module, title="Visual",
        summary=result.findings[0].statement if result.findings else "",
        findings=result.findings, sources=result.sources,
        confidence=result.confidence, data=result.raw_data,
    )
    return Brief(
        question=q, plan=plan, subject="AcmeCorp",
        executive_summary="AcmeCorp impersonation scan.", sections=[section],
    )


async def test_visual_html_brief_renders_suspects_table():
    result = await MODULES["visual"].run({"query": "AcmeCorp scan", "subject": "AcmeCorp"})
    html = render_html(_brief_with_visual_section(result))
    assert "visual-table" in html
    assert ACMECORP_TYPO_LOOKALIKE.url in html
    assert ACMECORP_COLOR_LOOKALIKE.url in html
    # Verdict pills are emitted in upper case via the template filter.
    assert "CRITICAL" in html or "HIGH" in html
    # Specific anomaly callouts surface as bullets.
    assert "Wordmark misspelled" in html or "AccmeCorp" in html


async def test_visual_markdown_brief_renders_suspects_table():
    result = await MODULES["visual"].run({"query": "AcmeCorp scan", "subject": "AcmeCorp"})
    md = render_markdown(_brief_with_visual_section(result))
    assert "| Suspect | Verdict |" in md
    assert ACMECORP_TYPO_LOOKALIKE.url in md
    assert "CRITICAL" in md or "HIGH" in md


# ── Regression: planner already routes Visual for the security demo ──


async def test_planner_security_query_uses_controlled_acmecorp_target():
    """The security demo query routes Visual on AcmeCorp; assert the
    module picks up the controlled target so the demo doesn't degrade
    to generic synthetic suspects."""
    result = await MODULES["visual"].run(
        {"query": "Scan for brand exposure on AcmeCorp.", "subject": "AcmeCorp"}
    )
    assert result.raw_data["controlled"] is True
    assert get_controlled_target("AcmeCorp") is not None
