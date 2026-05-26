"""Data layer for the Exposure module — SERP dorks, host classifier,
controlled-target catalog, and the LeakRecord shape.

The split mirrors Visual / Filing: this file is the pure-data layer
(no network), ``exposure.py`` is the live + mock orchestration. Keeping
the dork templates, severity rubric, paste-host classifier, and
controlled fixtures in one place means rubric tweaks land here, not in
the orchestration code.

Severity rubric (from implementation plan §4.6):
  * ``critical``  — live-shaped credentials (password / token / key / webhook)
  * ``high``      — PII or persistent identifiers (email + secret pair)
  * ``notable``   — org-internal info or structural detail (infra hostnames,
                    deploy keys without secret bodies)
  * ``info``      — incidental brand mentions on third-party surfaces
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal
from urllib.parse import urlparse


# ── Types ──────────────────────────────────────────────────────────


LeakKind = Literal[
    "credential",   # username + password pair
    "api_key",      # PAT / API key / deploy key
    "webhook",      # Slack / Discord / PagerDuty webhook URL with secret
    "pii",          # personal identifiers (email + secret, address, phone)
    "infra",        # internal hostnames, infra topology
    "mention",      # incidental brand mention — useful as a low-signal pivot
]


Severity = Literal["info", "notable", "high", "critical"]


# Channels the Exposure module fans out over. Each channel uses a
# different Bright Data tool combo (SERP for discovery, Web Unlocker
# for the actual fetch on hostile / bot-blocked sources).
LeakChannel = Literal["paste", "code", "breach", "doxx"]


@dataclass(frozen=True)
class LeakRecord:
    """One extracted leak observation, ready to render as a Finding."""

    channel: LeakChannel
    kind: LeakKind
    severity: Severity
    location_url: str            # where the leak was found (paste URL, code URL)
    location_title: str          # human label for the location
    excerpt: str                 # short redacted excerpt (≤ 200 chars)
    rationale: str               # one-sentence "why this matters"
    via: str                     # Bright Data tool that retrieved it

    def to_dict(self) -> dict[str, str]:
        return {
            "channel": self.channel,
            "kind": self.kind,
            "severity": self.severity,
            "location_url": self.location_url,
            "location_title": self.location_title,
            "excerpt": self.excerpt,
            "rationale": self.rationale,
            "via": self.via,
        }


@dataclass(frozen=True)
class ControlledLeak:
    """A pre-built controlled leak in ``demo/exposure/``.

    The text file ships with the repo; this dataclass declares the
    ground-truth leak records that file contains so the mock path can
    emit findings without an LLM. The live extraction will surface the
    same kinds (with possibly different excerpts) when run against the
    same content via Web Unlocker."""

    slug: str
    claimed_url: str             # the "public" URL the demo cites
    text_path: Path              # local file in demo/exposure/
    records: tuple[LeakRecord, ...]
    note: str = ""


@dataclass(frozen=True)
class ControlledExposureTarget:
    """A brand we control end-to-end for guaranteed Exposure demo coverage."""

    brand: str
    domain: str                  # canonical brand domain we'd dork for
    leaks: tuple[ControlledLeak, ...]

    # SERP dork templates filled with ``{domain}``. Used by the live
    # path so judges can see the actual query trace in the brief.
    serp_dorks: tuple[str, ...] = (
        'site:pastebin.com "{domain}"',
        'site:github.com "{domain}" password',
        '"{domain}" "credentials" leak',
    )


# ── Controlled target catalog (the Day-7 deliverable) ──────────────


_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXPOSURE_DIR = _REPO_ROOT / "demo" / "exposure"


# Headline leak: the paste-bin entry. The "claimed_url" is the URL the
# demo cites in the brief — in production this would be the real
# pastebin URL Bright Data Web Unlocker fetched; for offline/mock runs
# the module synthesizes findings from ``records`` directly.
ACMECORP_PASTEBIN_LEAK = ControlledLeak(
    slug="acmecorp-pastebin-ci-bootstrap",
    claimed_url="https://pastebin.com/raw/acmecorp-demo-9182734",
    text_path=_EXPOSURE_DIR / "controlled-pastebin.txt",
    note=(
        "Anonymous CI scratchpad with a live-shaped staging-DB password, two "
        "PAT-shaped tokens, and a Slack webhook. Demo-friendly: 4 stacked "
        "credential records on one page."
    ),
    records=(
        LeakRecord(
            channel="paste",
            kind="credential",
            severity="critical",
            location_url="https://pastebin.com/raw/acmecorp-demo-9182734",
            location_title="Anonymous paste #9182734",
            excerpt=(
                "ACMECORP_DB_USER=ci-bootstrap / "
                "ACMECORP_DB_PASSWORD=Hunter2-staging-bootstrap-2026 "
                "(host db-staging.acmecorp-demo.test)"
            ),
            rationale=(
                "Live-shaped staging DB credential pair, posted anonymously — "
                "rotate immediately and audit access since paste timestamp."
            ),
            via="web_unlocker",
        ),
        LeakRecord(
            channel="paste",
            kind="api_key",
            severity="critical",
            location_url="https://pastebin.com/raw/acmecorp-demo-9182734",
            location_title="Anonymous paste #9182734",
            excerpt=(
                "ACMECORP_CI_TOKEN=acmecorp_pat_live_4f9c8e1a2b3d5f6e7a8b9c0d1e2f3a4b "
                "(PAT shape: 32-hex body, `_live_` channel)"
            ),
            rationale=(
                "Personal access token with `_live_` channel marker — would "
                "carry production scope. Revoke at the issuer and audit recent calls."
            ),
            via="web_unlocker",
        ),
        LeakRecord(
            channel="paste",
            kind="api_key",
            severity="high",
            location_url="https://pastebin.com/raw/acmecorp-demo-9182734",
            location_title="Anonymous paste #9182734",
            excerpt=(
                "ACMECORP_DEPLOY_KEY=acmecorp_deploy_5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d "
                "(deploy-key shape)"
            ),
            rationale=(
                "Deploy key alongside the CI token — same paste, same author. "
                "Treat the two as compromised in tandem."
            ),
            via="web_unlocker",
        ),
        LeakRecord(
            channel="paste",
            kind="webhook",
            severity="high",
            location_url="https://pastebin.com/raw/acmecorp-demo-9182734",
            location_title="Anonymous paste #9182734",
            excerpt=(
                "ACMECORP_SLACK_WEBHOOK=https://hooks.slack.com/services/"
                "T00000000/B00000000/XXXXXXXXXXXXXXXX"
            ),
            rationale=(
                "Slack incoming-webhook URL exposed — anyone with the URL "
                "can post into the deploy channel. Rotate the webhook in Slack."
            ),
            via="web_unlocker",
        ),
    ),
)


# Secondary leak: public-code-search hit. Lower severity (a test
# fixture, not a live secret), but proves the SERP-driven discovery
# channel is independent of the paste-site channel.
ACMECORP_GITHUB_LEAK = ControlledLeak(
    slug="acmecorp-github-seed-env",
    claimed_url=(
        "https://github.com/acmecorp-demo/internal-tools/blob/main/"
        "tests/fixtures/seed.env"
    ),
    text_path=_EXPOSURE_DIR / "controlled-github-snippet.txt",
    note=(
        "GitHub code-search snippet. Synthetic test fixture with a "
        "QA email + PAT pair — high severity because of the persistent "
        "identifier pairing, but not the same operational risk as the paste."
    ),
    records=(
        LeakRecord(
            channel="code",
            kind="pii",
            severity="high",
            location_url=(
                "https://github.com/acmecorp-demo/internal-tools/blob/main/"
                "tests/fixtures/seed.env"
            ),
            location_title="github.com/acmecorp-demo/internal-tools — tests/fixtures/seed.env",
            excerpt=(
                "ACME_TEST_USER=qa-bootstrap@acmecorp-demo.test / "
                "ACME_TEST_PASSWORD=qa-bootstrap-Spring2026!"
            ),
            rationale=(
                "QA-account email paired with a password literal in a public "
                "fixture file — typical seed pattern, still flag for rotation."
            ),
            via="serp_api",
        ),
        LeakRecord(
            channel="code",
            kind="api_key",
            severity="high",
            location_url=(
                "https://github.com/acmecorp-demo/internal-tools/blob/main/"
                "tests/fixtures/seed.env"
            ),
            location_title="github.com/acmecorp-demo/internal-tools — tests/fixtures/seed.env",
            excerpt=(
                "ACME_TEST_API_KEY=acmecorp_pat_test_8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e "
                "(`_test_` channel marker)"
            ),
            rationale=(
                "Test-channel PAT in a committed fixture — confirm scope is "
                "test-only, then rotate; many `_test_` tokens still authorize "
                "user-scope reads."
            ),
            via="serp_api",
        ),
    ),
)


ACMECORP_EXPOSURE_TARGET = ControlledExposureTarget(
    brand="AcmeCorp",
    domain="acmecorp-demo.test",
    leaks=(ACMECORP_PASTEBIN_LEAK, ACMECORP_GITHUB_LEAK),
)


CONTROLLED_TARGETS: dict[str, ControlledExposureTarget] = {
    ACMECORP_EXPOSURE_TARGET.brand: ACMECORP_EXPOSURE_TARGET,
}


def get_controlled_target(brand: str) -> ControlledExposureTarget | None:
    return CONTROLLED_TARGETS.get(brand)


# ── SERP dork templates (the discovery layer) ──────────────────────


# Default dorks for an unknown subject. Mirrors the implementation plan
# §4.6 examples: paste sites, code search, breach aggregators. The
# `{domain}` token is filled by ``build_dorks``.
DEFAULT_DORKS: tuple[str, ...] = (
    'site:pastebin.com "{domain}"',
    'site:ghostbin.co "{domain}"',
    'site:throwbin.io "{domain}"',
    'site:github.com "{domain}" password',
    'site:gitlab.com "{domain}" password',
    'site:github.com "{domain}" "api_key"',
    '"{domain}" "credentials" leak',
    '"{domain}" "password" filetype:env',
)


def build_dorks(
    *, domain: str, custom: Iterable[str] | None = None,
) -> list[str]:
    """Build the SERP dork list for a domain.

    Custom templates (if provided) replace the defaults. Each template
    can use ``{domain}`` as a placeholder; templates without the token
    are passed through unchanged."""
    templates = tuple(custom) if custom else DEFAULT_DORKS
    return [t.format(domain=domain) if "{domain}" in t else t for t in templates]


# ── Host classifier (channel inference) ────────────────────────────


PASTE_HOSTS: frozenset[str] = frozenset({
    "pastebin.com", "paste.ee", "ghostbin.co", "throwbin.io",
    "controlc.com", "rentry.co", "hastebin.com", "dpaste.com",
    "pastes.io", "p.ip.fi",
})

CODE_HOSTS: frozenset[str] = frozenset({
    "github.com", "gist.github.com", "gitlab.com", "bitbucket.org",
    "codeberg.org", "sourcegraph.com",
})

BREACH_HOSTS: frozenset[str] = frozenset({
    "haveibeenpwned.com", "dehashed.com", "breachdirectory.org",
    "leakcheck.io", "scylla.so",
})


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except (ValueError, AttributeError):
        return ""


def classify_channel(url: str) -> LeakChannel | None:
    """Map a discovered URL to its leak channel, or None if uninteresting.

    Returns None for hosts we don't recognise — the caller can choose
    whether to still fetch them (low signal) or drop them outright."""
    host = _host(url)
    if not host:
        return None
    # Direct match first, then apex domain (handles `raw.githubusercontent.com`).
    apex = ".".join(host.split(".")[-2:]) if "." in host else host
    if host in PASTE_HOSTS or apex in PASTE_HOSTS:
        return "paste"
    if host in CODE_HOSTS or apex in CODE_HOSTS or host == "raw.githubusercontent.com":
        return "code"
    if host in BREACH_HOSTS or apex in BREACH_HOSTS:
        return "breach"
    return None


@dataclass(frozen=True)
class SerpCandidate:
    """One discovered URL we'd consider running through Web Unlocker."""

    url: str
    title: str
    snippet: str                 # the SERP description excerpt
    channel: LeakChannel
    discovery_query: str         # the dork that surfaced this row


def filter_candidates(
    rows: Iterable[dict[str, object]],
    *,
    discovery_query: str = "",
) -> list[SerpCandidate]:
    """Coerce raw SERP rows into typed candidates, keeping only known channels."""
    out: list[SerpCandidate] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = row.get("link") or row.get("url") or ""
        if not isinstance(url, str) or not url or url in seen:
            continue
        channel = classify_channel(url)
        if channel is None:
            continue
        title = row.get("title") if isinstance(row.get("title"), str) else ""
        snippet = (
            row.get("snippet")
            or row.get("description")
            or row.get("excerpt")
            or ""
        )
        if not isinstance(snippet, str):
            snippet = ""
        seen.add(url)
        out.append(SerpCandidate(
            url=url,
            title=title or "",
            snippet=snippet,
            channel=channel,
            discovery_query=(
                row.get("_query") if isinstance(row.get("_query"), str)
                else discovery_query
            ),
        ))
    return out


# ── Credential / token regex helpers ───────────────────────────────


# Patterns the LLM is supposed to surface, but we also keep these so the
# live path can short-circuit obvious hits without an LLM call when the
# model is unavailable (cheap belt-and-braces classifier). These are
# *shape* checks only — not validators of actual issuer formats.
#
# No leading ``\b``: env-style identifiers like ``ACMECORP_API_KEY`` have
# no word boundary between the prefix and ``api`` (both sides are word
# characters), so requiring one would miss the most common shape. The
# trailing ``\b`` is enough — the key suffix is followed by ``=`` / ``:``
# which is always a boundary against a word character.
_PASSWORD_LINE_RE = re.compile(
    r"(?:password|passwd|pwd)\b\s*[:=]\s*\S{6,}", re.IGNORECASE
)
_TOKEN_LINE_RE = re.compile(
    r"(?:token|api[_-]?key|secret|deploy[_-]?key|access[_-]?key)\b\s*[:=]\s*\S{16,}",
    re.IGNORECASE,
)
_SLACK_WEBHOOK_RE = re.compile(
    r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+",
)
_PAT_SHAPE_RE = re.compile(
    r"\b[a-z][a-z0-9_]{4,30}_pat_[a-z]+_[0-9a-f]{16,}\b",
    re.IGNORECASE,
)


def has_credential_shape(text: str) -> bool:
    """Cheap pre-flight check: does this text look like it contains creds?

    Used by the live path as a sanity gate before paying for an LLM
    extraction call. False negatives are acceptable (LLM still runs); a
    True is the green light to actually treat the candidate seriously."""
    if not text:
        return False
    if _PASSWORD_LINE_RE.search(text):
        return True
    if _TOKEN_LINE_RE.search(text):
        return True
    if _SLACK_WEBHOOK_RE.search(text):
        return True
    if _PAT_SHAPE_RE.search(text):
        return True
    return False


# ── Severity escalation ────────────────────────────────────────────


_SEVERITY_RANK: dict[Severity, int] = {
    "info": 1, "notable": 2, "high": 3, "critical": 4,
}


def coerce_severity(value: object, default: Severity = "notable") -> Severity:
    """Coerce LLM-returned severity strings to the Atlas scale."""
    if not isinstance(value, str):
        return default
    v = value.strip().lower()
    if v in _SEVERITY_RANK:
        return v  # type: ignore[return-value]
    # Common aliases the LLM tends to emit.
    if v in {"low", "informational"}:
        return "info"
    if v in {"medium", "moderate", "warning"}:
        return "notable"
    if v in {"severe"}:
        return "high"
    return default


def coerce_kind(value: object, default: LeakKind = "mention") -> LeakKind:
    if not isinstance(value, str):
        return default
    v = value.strip().lower()
    aliases: dict[str, LeakKind] = {
        "credential": "credential", "credentials": "credential",
        "password": "credential", "passwd": "credential",
        "api_key": "api_key", "apikey": "api_key", "key": "api_key",
        "token": "api_key", "pat": "api_key", "deploy_key": "api_key",
        "webhook": "webhook", "slack_webhook": "webhook",
        "pii": "pii", "email": "pii", "phone": "pii",
        "infra": "infra", "hostname": "infra", "topology": "infra",
        "mention": "mention", "reference": "mention",
    }
    return aliases.get(v, default)


def coerce_channel(value: object, default: LeakChannel = "paste") -> LeakChannel:
    if not isinstance(value, str):
        return default
    v = value.strip().lower()
    if v in {"paste", "code", "breach", "doxx"}:
        return v  # type: ignore[return-value]
    aliases: dict[str, LeakChannel] = {
        "pastebin": "paste",
        "github": "code", "gitlab": "code", "source_code": "code",
        "archive": "breach", "dump": "breach",
        "personal": "doxx",
    }
    return aliases.get(v, default)


def severity_rank(s: Severity) -> int:
    return _SEVERITY_RANK.get(s, 0)


# ── Live-path leak structures ──────────────────────────────────────


@dataclass(frozen=True)
class ExposureScan:
    """Result of one Exposure run — flattens nicely into ``raw_data``."""

    subject: str
    domain: str
    dorks: tuple[str, ...]
    candidates: tuple[SerpCandidate, ...]
    records: tuple[LeakRecord, ...]
    dropped: tuple[str, ...] = ()

    @property
    def max_severity(self) -> Severity:
        if not self.records:
            return "info"
        return max(self.records, key=lambda r: severity_rank(r.severity)).severity

    @property
    def critical_count(self) -> int:
        return sum(1 for r in self.records if r.severity == "critical")

    @property
    def channels_hit(self) -> tuple[str, ...]:
        return tuple(sorted({r.channel for r in self.records}))

    def to_raw(self) -> dict[str, object]:
        return {
            "subject": self.subject,
            "domain": self.domain,
            "dorks": list(self.dorks),
            "candidates": [
                {
                    "url": c.url,
                    "title": c.title,
                    "snippet": c.snippet[:300],
                    "channel": c.channel,
                    "discovery_query": c.discovery_query,
                }
                for c in self.candidates
            ],
            "records": [r.to_dict() for r in self.records],
            "record_count": len(self.records),
            "critical_count": self.critical_count,
            "max_severity": self.max_severity,
            "channels": list(self.channels_hit),
            "dropped": list(self.dropped),
        }


__all__ = [
    "LeakKind",
    "LeakChannel",
    "Severity",
    "LeakRecord",
    "ControlledLeak",
    "ControlledExposureTarget",
    "ACMECORP_EXPOSURE_TARGET",
    "ACMECORP_PASTEBIN_LEAK",
    "ACMECORP_GITHUB_LEAK",
    "CONTROLLED_TARGETS",
    "get_controlled_target",
    "DEFAULT_DORKS",
    "build_dorks",
    "PASTE_HOSTS",
    "CODE_HOSTS",
    "BREACH_HOSTS",
    "classify_channel",
    "SerpCandidate",
    "filter_candidates",
    "has_credential_shape",
    "coerce_severity",
    "coerce_kind",
    "coerce_channel",
    "severity_rank",
    "ExposureScan",
]
