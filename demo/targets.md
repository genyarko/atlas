# Demo targets — pre-validated

Lock these by end of Day 1. Re-validate Day 8 right before brief polish.

## GTM — Linear
- **Pricing page:** https://linear.app/pricing
- **Regions to compare:** US, GB, DE, IN, BR
- **Signal source:** linear.app/careers (Greenhouse-backed; resilient feed)
- **AltData source:** g2.com/products/linear, glassdoor.com/Overview/Working-at-Linear

## Finance — Datadog
- **Filing source:** SEC EDGAR (CIK 0001561550), most recent 10-Q + 8-K
- **Signal source:** datadoghq.com/careers
- **AltData source:** glassdoor.com/Overview/Working-at-Datadog, g2.com/products/datadog

## Security — AcmeCorp (controlled)
- **Legitimate brand:** controlled test page hosted under our domain (see `lookalikes/`)
- **Lookalikes:** two we stand up ourselves (typo-squat domain + brand-colour-swap)
- **Exposure controlled fixtures (`exposure/`):**
  - `controlled-pastebin.txt` — synthetic CI scratchpad with PAT-shaped tokens, claimed source `https://pastebin.com/raw/acmecorp-demo-9182734`
  - `controlled-github-snippet.txt` — synthetic `.env` snippet, claimed source `github.com/acmecorp-demo/internal-tools/blob/main/tests/fixtures/seed.env`
- **Exposure SERP dorks (live path):** `site:pastebin.com "acmecorp-demo.test"`, `site:github.com "acmecorp-demo.test" password`, `"acmecorp-demo.test" credentials leak`

> ⚠️ Live demos for Visual/Exposure must use controlled targets only. Every credential, token, and webhook in `exposure/` is deliberately fabricated. Real-org findings stay out of the submission video.
