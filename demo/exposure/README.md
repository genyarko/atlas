# Controlled exposure assets — Exposure module demo

These files are the controlled-target asset pool for the **Exposure** module's credential-leak demo (implementation plan §4.6). They sit alongside `demo/lookalikes/` for the Visual module so all Security-track controlled material lives in one place.

| File | Role | Claimed source the demo cites |
|------|------|------------------------------|
| `controlled-pastebin.txt` | Headline leak: a CI-scratchpad paste containing fake `acmecorp-demo.test` credentials | `https://pastebin.com/raw/acmecorp-demo-9182734` |
| `controlled-github-snippet.txt` | Secondary leak: a public-code-search snippet showing the same brand exposed in a synthetic GitHub repo | `https://github.com/acmecorp-demo/internal-tools/blob/main/tests/fixtures/seed.env` |

The Exposure module's mock path reads the declared leak metadata for each fixture from `backend/app/modules/exposure_data.py` (kept beside the code, not parsed out of the text files at runtime — that keeps the data layer deterministic and diff-able).

## Why two leak surfaces, not one

§4.6 of the implementation plan calls for both SERP-discovered paste-site leaks (Web Unlocker) and code-search exposure (SERP API + Web Unlocker). The two files exercise the two channels:

- **Paste-bin** — anonymous paste host. Critical: a live-shaped CI password + PAT-shaped tokens + a fake Slack webhook. The Web Unlocker call lands the page; the LLM extracts credential patterns.
- **GitHub code-search snippet** — public-code surface. High: a test-fixture `.env` with PAT-shaped tokens. The SERP API surfaces it via a `site:github.com` dork.

Together they prove Exposure isn't single-source: it's a fan-out across paste sites + public code + (in production) breach archives, with per-channel severity.

## The credentials are synthetic

Every credential, token, webhook, and database name in these files is **deliberately fabricated** for the demo. Specifically:

- `acmecorp-demo.test` is an [RFC 6761](https://datatracker.ietf.org/doc/html/rfc6761#section-6.2)-reserved domain and resolves nowhere.
- Token prefixes (`acmecorp_pat_live_…`, `acmecorp_deploy_…`) mimic the shape of real PATs (32-hex-char body) so the regex extractors in `exposure_data.py` have something realistic to flag — the values themselves are not live and grant access to nothing.
- The Slack webhook URL uses the documented placeholder team/channel IDs from Slack's own docs (`T00000000` / `B00000000`).

The fabrication is the point. From the implementation plan §4.6 ethics note:

> Controlled targets for the live demo. Disclose this in the README ("demo uses controlled test data; production system uses real signals").

## Hosting

For a live demo where Web Unlocker actually fetches the paste, host `controlled-pastebin.txt` at a public URL of your choice — the module accepts a configured `controlled_url` and will surface whatever Web Unlocker returns from that URL. For the default mock/offline path, no hosting is needed: `exposure_data.py` ships the declared leak metadata so the brief renders identically without a network call.

## Ethical note

These pages exist purely so the demo has a deterministic, controllable target. **Do not run the live Exposure module against a real organization's exposure surface in submission materials.** Real findings stay out of the video, slides, and README per implementation plan §4.6.
