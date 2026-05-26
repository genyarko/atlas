# Controlled lookalikes — Visual module demo assets

These three HTML files are the controlled-target asset pool for the **Visual** module's brand-impersonation demo (implementation plan §4.5).

| File | Role | "Public" URL the demo claims |
|------|------|------------------------------|
| `legit-acmecorp.html` | Legitimate AcmeCorp reference (ground truth) | `https://acmecorp-demo.test/login` |
| `lookalike-typo-domain.html` | Typosquat: misspelled wordmark, off-brand CTA copy, off-canonical form post + footer | `https://acmecorp-secure-login.test` |
| `lookalike-color-swap.html` | Visually subtler: brand-color drift (`#21508C` vs `#1A2B45`), stale "Beta" pill, stale `© 2025` | `https://app-acmecorp.test/signin` |

The mock path for `VisualModule` reads the declared anomalies for each lookalike from `backend/app/modules/visual_data.py` (kept beside the code, not embedded in the HTML, so the data layer can be diffed independently of the markup).

## Why two lookalikes, not one

§4.5 of the implementation plan calls for ≥3 visual anomalies before flagging a target "high" suspicion. The two files exercise distinct failure shapes:

- **Typosquat** — obvious, demo-friendly. Five anomalies; vision-diff flags `critical`.
- **Color-swap** — subtler, still detectable. Three anomalies; vision-diff flags `high` / `notable`.

Together they prove the module isn't keyword-matching domain names: it scores each suspect on observed visual evidence.

## Hosting

For a live in-browser demo, serve this directory from any static host that supports the `acmecorp-demo.test` / `acmecorp-secure-login.test` / `app-acmecorp.test` virtual hostnames (or substitute real registered demo domains you control). The Scraping Browser call in `VisualModule.execute()` only needs a screenshot — any public URL of the file works.

For the offline/mock path (default), no hosting is required: the Visual module's `mock()` synthesizes findings directly from the declared-anomaly metadata in `visual_data.py`.

## Ethical note

These pages exist purely to give the demo a deterministic, controllable target. **Do not publish or imply a real organization's impersonation findings in submission materials** (per implementation plan §4.5 ethics note).
