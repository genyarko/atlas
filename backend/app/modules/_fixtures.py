"""Helpers for picking realistic-looking mock data based on the query subject."""

from __future__ import annotations

import re

# Keyword → canonical subject. Order matters: more specific names first.
_SUBJECT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\blinear\b", re.I), "Linear"),
    (re.compile(r"\bdatadog\b", re.I), "Datadog"),
    (re.compile(r"\bnotion\b", re.I), "Notion"),
    (re.compile(r"\bjira\b", re.I), "Jira"),
    (re.compile(r"\bacme(?:corp)?\b", re.I), "AcmeCorp"),
]

# Imperative verbs and question/connective words that look proper-noun-ish
# when sentence-cased ("Run a brief on Stripe", "Compare X to Y"). Excluding
# them from the last-token heuristic stops "Run"/"Compare"/"What" from being
# picked as the subject when the real brand sits earlier in the query.
_SUBJECT_STOPWORDS: frozenset[str] = frozenset({
    "Run", "Investigate", "Compare", "Scan", "Check", "Find", "Audit",
    "Analyze", "Analyse", "Show", "Generate", "Produce", "Build", "Give",
    "Tell", "Pull", "Look", "Get", "Brief", "Report", "Atlas",
    "What", "Who", "How", "Why", "When", "Which", "Where",
    "The", "This", "That", "These", "Those", "Our", "Their",
})


def infer_subject(query: str, *, fallback: str = "the target") -> str:
    """Extract a canonical company/brand name from the query.

    1. Fast-path: regex match against known fixture companies.
    2. Fallback: take the last proper-noun-looking token, filtering out
       imperative verbs / question words that masquerade as proper nouns
       at sentence start ("Run a brief on Stripe" → "Stripe", not "Run").
    """
    for pattern, name in _SUBJECT_PATTERNS:
        if pattern.search(query):
            return name
    tokens = re.findall(r"\b[A-Z][A-Za-z0-9]{2,}\b", query)
    candidates = [t for t in tokens if t not in _SUBJECT_STOPWORDS]
    if candidates:
        return candidates[-1]
    return tokens[-1] if tokens else fallback


def subject_domain(subject: str) -> str:
    return {
        "Linear": "linear.app",
        "Datadog": "datadoghq.com",
        "Notion": "notion.so",
        "Jira": "atlassian.com",
        "AcmeCorp": "acmecorp-demo.test",
    }.get(subject, f"{subject.lower().replace(' ', '')}.com")
