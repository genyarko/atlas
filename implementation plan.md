# Atlas — Implementation Plan

**Project:** Atlas — Autonomous Enterprise Intelligence Platform
**Tagline:** *The intelligence platform that sees through what your tools can't.*
**Hackathon:** Bright Data AI x Web Data Weekend
**Timeline:** 11 days
**Tracks:** Spans all three (GTM, Finance, Security)

---

## 1. Executive Summary

Atlas is an autonomous AI agent platform that takes any enterprise intelligence question and produces a structured *Ground Truth Brief* by invoking specialized intelligence modules. Each module uses Bright Data infrastructure to access web data that conventional scrapers cannot — through bot detection, CAPTCHAs, JavaScript renders, geo-blocks, and visual deception.

**Success criteria for the hackathon:**
- Working end-to-end demo of 3 distinct queries spanning all 3 tracks
- All 6 modules functional (or 5 polished if 6th is cut)
- MCP Server as the agent's tool layer (deliberate technical positioning)
- Submission-grade video, slides, GitHub repo, and live demo URL by Day 11

**Non-goals (explicit cuts):**
- Multi-user auth, billing, team features
- Production-grade error handling beyond demo paths
- Mobile responsiveness for the chat UI (desktop demo only)
- Anything that doesn't directly enable the 3 demo queries

---

## 2. System Architecture

### 2.1 High-level flow

```
User Query
   │
   ▼
[Next.js Chat UI] ──────────────────────────┐
   │                                         │
   ▼                                         │
[FastAPI Backend]                            │
   │                                         │
   ▼                                         │
[LangGraph Agent]                            │
   │                                         │
   ├─► Planner (Claude) ─► research plan     │
   │                                         │
   ├─► Executor                              │
   │     │                                   │
   │     ▼                                   │
   │   [Bright Data MCP Server]              │
   │     │                                   │
   │     ├─► TruePrice  (Scraping Browser)   │
   │     ├─► Signal     (Web Scraper API)    │
   │     ├─► Filing     (Web Unlocker)       │
   │     ├─► AltData    (Web Scraper API)    │
   │     ├─► Visual     (Scraping Browser)   │
   │     └─► Exposure   (SERP + Unlocker)    │
   │                                         │
   └─► Synthesizer (Claude) ─► Ground Truth Brief
                                  │           │
                                  ▼           ▼
                          [HTML/PDF Brief]  [Stream to UI]
```

### 2.2 Tech stack (committed)

| Layer | Choice | Why |
|-------|--------|-----|
| Frontend | Next.js 14 + Tailwind + shadcn/ui | Fast scaffolding, clean defaults, easy Vercel deploy |
| Backend | FastAPI (Python 3.11) | Best Bright Data SDK support; async-native |
| Agent orchestration | LangGraph | Conditional paths > CrewAI's linear flows; production-grade |
| LLM | Anthropic Claude Sonnet 4 (`claude-sonnet-4`) | Best vision + reasoning combo for the price; you're at an Anthropic-adjacent event |
| Tool layer | Bright Data MCP Server | Judges are explicitly rewarding deep MCP integration |
| Vision | Claude Sonnet 4 (multimodal) | One LLM provider simplifies deployment |
| Storage | SQLite | Hackathon scale; zero ops |
| Brief export | WeasyPrint (HTML→PDF) | Reliable; the Scraping-Browser-for-PDF idea is cute but adds dependency |
| Hosting (BE) | Railway or Render | Free tier, zero config |
| Hosting (FE) | Vercel | Zero config |
| Repo | GitHub (public) | Required for submission |

Swap freely based on team comfort. The LangGraph choice is the most defensible; everything else is fungible.

### 2.3 Project structure

```
atlas/
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI entry
│   │   ├── agent/
│   │   │   ├── graph.py             # LangGraph state machine
│   │   │   ├── planner.py           # Planner node
│   │   │   ├── executor.py          # Module dispatcher
│   │   │   └── synthesizer.py       # Brief generator
│   │   ├── modules/
│   │   │   ├── base.py              # Module ABC
│   │   │   ├── trueprice.py
│   │   │   ├── signal.py
│   │   │   ├── filing.py
│   │   │   ├── altdata.py
│   │   │   ├── visual.py
│   │   │   └── exposure.py
│   │   ├── brightdata/
│   │   │   ├── mcp_client.py        # MCP server wrapper
│   │   │   ├── scraping_browser.py
│   │   │   ├── web_scraper_api.py
│   │   │   ├── web_unlocker.py
│   │   │   └── serp.py
│   │   ├── brief/
│   │   │   ├── renderer.py          # HTML/PDF output
│   │   │   └── templates/
│   │   └── models.py                # Pydantic schemas
│   ├── tests/
│   └── pyproject.toml
├── frontend/
│   ├── app/
│   │   ├── page.tsx                 # Chat interface
│   │   ├── brief/[id]/page.tsx      # Brief viewer
│   │   └── api/                     # API routes (proxy to BE)
│   ├── components/
│   └── package.json
├── demo/
│   ├── queries.json                 # The 3 demo queries
│   ├── cached_responses/            # Fallback for live demo
│   └── targets.md                   # Pre-validated demo targets
├── docs/
│   ├── ARCHITECTURE.md
│   ├── README.md
│   └── DEMO_SCRIPT.md
└── .env.example
```

---

## 3. Data Models

```python
# Core flow types

class Question(BaseModel):
    id: str
    text: str
    user_context: dict | None  # company, vertical hints

class ResearchPlan(BaseModel):
    question_id: str
    intent: Literal["competitive", "financial", "security", "mixed"]
    modules_to_invoke: list[ModuleInvocation]
    reasoning: str  # planner's explanation

class ModuleInvocation(BaseModel):
    module: Literal["trueprice", "signal", "filing", "altdata", "visual", "exposure"]
    params: dict
    priority: int  # 1-5; affects retry budget

class ModuleResult(BaseModel):
    module: str
    status: Literal["success", "partial", "failed"]
    findings: list[Finding]
    raw_data: dict       # for the audit trail
    sources: list[Source]
    confidence: float    # 0-1
    duration_ms: int

class Finding(BaseModel):
    statement: str       # the insight
    evidence: list[str]  # source URLs/snippets
    severity: Literal["info", "notable", "high", "critical"]

class Source(BaseModel):
    url: str
    title: str
    accessed_at: datetime
    via: str  # which Bright Data tool

class Brief(BaseModel):
    question: Question
    plan: ResearchPlan
    module_results: list[ModuleResult]
    executive_summary: str
    sections: list[BriefSection]
    confidence_score: float
    generated_at: datetime
```

---

## 4. Module Specifications

Every module implements the same interface:

```python
class IntelligenceModule(ABC):
    name: str
    bright_data_tools: list[str]

    async def can_handle(self, plan: ResearchPlan) -> bool: ...
    async def execute(self, params: dict) -> ModuleResult: ...
    async def health_check(self) -> bool: ...
```

### 4.1 TruePrice (GTM)

**Purpose:** Reveal true purchase cost by completing checkout flows, geo-distributed.

**Bright Data tools:** Scraping Browser + residential proxies (geo-routed).

**Inputs:** `{target_url, plan_tier?, regions: list[str]}`

**Implementation:**
1. For each region in `regions`, instantiate a Scraping Browser session routed through a residential proxy in that country.
2. Navigate to target product/pricing page.
3. Use predefined interaction scripts per target (selectors, click sequences).
4. Reach checkout/cart summary. Extract: list price, taxes, fees, mandatory add-ons, final total, currency.
5. Normalize all prices to USD using a daily rate.
6. Output: comparative table + insights ("True cost in Germany is 23% higher than listed").

**Demo target pool (pre-validated):**
- Linear (SaaS, predictable checkout flow)
- Notion (multiple plans, region-aware pricing)
- A consumer e-commerce target with regional tax variation

**Failure modes:**
- Checkout flow changes mid-build → keep selectors in a separate config file, write resilient locator strategies
- Region detection fakeouts → verify with IP echo before checkout
- Rate limiting → cap to 5 regions for demo

**Cut criteria:** If checkout interaction is unreliable by Day 5, reduce to pricing-page-only comparison and rename "PriceWatch" instead.

### 4.2 Signal (GTM + Finance)

**Purpose:** Detect strategic intent from hiring patterns, exec movements, tech stack signals.

**Bright Data tools:** Web Scraper API (LinkedIn, job boards) + SERP API for triangulation.

**Inputs:** `{company: str, lookback_days: int, signal_types: list[str]}`

**Implementation:**
1. Pull all active job postings (LinkedIn + company careers page via Web Scraper API).
2. Cluster by role family, geography, seniority. Compare to 90-day baseline.
3. Pull leadership changes via LinkedIn employee API (people who recently changed title).
4. SERP queries for `"<company>" leadership` and `"<company>" launches` in the lookback window.
5. LLM pass: synthesize raw signals into intent inferences ("8 EU enterprise AE roles + GDPR engineer + Frankfurt SDR → European GTM motion launching").

**Demo target pool:**
- Linear (active hiring; clean signal)
- A public mid-cap with recent strategic moves
- A B2B SaaS in expansion mode

**Failure modes:**
- LinkedIn rate limits → Web Scraper API handles this; have backup company (Greenhouse/Lever feeds) for one demo target
- Sparse signal → pre-pick targets with confirmed signal density

### 4.3 Filing (Finance)

**Purpose:** Surface material changes in regulatory filings and patents.

**Bright Data tools:** Web Unlocker (SEC, patent offices, regional regulators) + MCP for orchestration.

**Inputs:** `{company: str, filing_types: list[str], lookback_days: int}`

**Implementation:**
1. Fetch recent filings via Web Unlocker (handles geo restrictions on some regulator sites).
2. Diff against prior filings — focus on risk factors section (SEC 10-K Item 1A), material agreements, executive comp changes.
3. LLM pass: rate materiality of each change (1-5), generate plain-English summary.
4. Cross-reference patent filings (USPTO) for strategic direction signals.

**Demo target pool:**
- Recent 8-K filings from a public SaaS company
- A regulatory diff that's actually material (pre-scout one for the demo)

**Failure modes:**
- SEC EDGAR has its own API and doesn't strictly need Web Unlocker → still route through Web Unlocker for the demo so the "blocked vs unblocked" comparison works; route patent searches via Web Unlocker to justify it
- Long documents blow context → chunk + filter to risk-factor and material-events sections only

### 4.4 AltData (Finance)

**Purpose:** Composite distress/momentum score from alternative web signals.

**Bright Data tools:** Web Scraper API (Glassdoor, G2, Trustpilot, marketplace sites).

**Inputs:** `{company: str, metrics: list[str]}`

**Implementation:**
1. Glassdoor: pull last 100 reviews, trend rating over time, sentiment shift.
2. G2/Trustpilot: review velocity, complaint clustering (downtime, latency, support).
3. Marketplace signal (the "forklift indicator"): if applicable, check industrial resale sites for asset liquidation patterns.
4. Composite scoring: weighted blend of trend signals → momentum (positive) or distress (negative) score.

**Demo target pool:**
- A SaaS with visible review-velocity changes
- A company with public Glassdoor sentiment shift

**Failure modes:**
- Sparse reviews → drop marketplace signal, keep Glassdoor + G2 only
- Sentiment classification noise → use Claude with explicit rubric, not raw star ratings

### 4.5 Visual (Security)

**Purpose:** Detect brand impersonation via visual diff of suspect domains.

**Bright Data tools:** SERP API (discovery) + Scraping Browser (screenshots) + Claude vision (analysis).

**Inputs:** `{brand: str, brand_url: str, search_terms: list[str]}`

**Implementation:**
1. SERP queries for `"<brand>" login`, `"<brand>" support`, `"<brand>" signin` — collect candidate URLs.
2. Filter to non-canonical domains (anything not on `brand_url` or known social platforms).
3. For each suspect, Scraping Browser captures full-page screenshot.
4. Claude vision: side-by-side comparison with legitimate brand. Output structural similarity score + specific anomalies (off-brand colors, typo logos, misspelled CTAs).
5. Rank by suspicion score; output top N with screenshots embedded.

**Demo target pool:**
- A made-up brand you control (set up 2 lookalike test pages yourself for guaranteed live demo)
- A real brand impersonation case from the wild, pre-validated

**Failure modes:**
- Live brand impersonations may go down before demo → always have a controlled lookalike as backup
- Vision false positives → tune prompt to require ≥3 visual anomalies before flagging "high"

**Ethics/legal note:** Use only your own controlled lookalikes for live demos. Don't publish real-org impersonation findings in submission materials.

### 4.6 Exposure (Security)

**Purpose:** Surface org-specific exposure signals across the open web.

**Bright Data tools:** Web Unlocker + SERP API.

**Inputs:** `{domain: str, executives: list[str], keywords: list[str]}`

**Implementation:**
1. Targeted SERP queries: `"<domain>" site:pastebin.com`, `"<domain>" site:github.com password`, `"<exec_name>" home address`, leaked-database aggregator sites.
2. Web Unlocker fetches candidate pages (paste sites, archives often block scrapers).
3. LLM extracts: credential patterns, API key formats, personal data exposure.
4. Severity classification: critical (live credentials), high (PII), medium (org info), low (incidental mentions).

**Demo target pool:**
- A controlled paste-bin entry you create (with fake creds for `acmecorp-demo.test`)
- Optionally: a known-leaked credential from a public breach archive (without exposing the org)

**Failure modes:**
- Live results may include real exposed data → filter heavily, redact in UI, don't publish in submission
- Paste site availability varies → diversify sources

**Ethics/legal note:** Same as Visual. Controlled targets for the live demo. Disclose this in the README ("demo uses controlled test data; production system uses real signals").

---

## 5. Agent Orchestration

### 5.1 LangGraph state machine

```
START
  │
  ▼
[plan_node] ── invokes Planner LLM with question + module catalog
  │
  ▼
[route_node] ── for each module in plan, queue for execution
  │
  ▼
[execute_node] ── runs modules in parallel via MCP tool calls
  │
  ▼
[evaluate_node] ── checks if any module needs retry or follow-up
  │     │
  │     ▼ (if follow-up needed)
  │   [refine_plan_node] ──► back to execute_node (max 2 iterations)
  │
  ▼
[synthesize_node] ── Synthesizer LLM produces executive summary + sections
  │
  ▼
[render_node] ── HTML + PDF output
  │
  ▼
END
```

### 5.2 Planner prompt (sketch)

```
You are the Atlas Planner. Decompose the user's question into a research plan
using the available intelligence modules.

Modules available:
- TruePrice: True cost analysis via checkout completion (cross-region pricing)
- Signal: Hiring patterns, exec movements, strategic intent inference
- Filing: SEC/regulatory/patent filing analysis with materiality scoring
- AltData: Review sentiment, alt-data composite distress/momentum scoring
- Visual: Brand impersonation detection via visual diff
- Exposure: Credential/PII/data exposure scanning across open web

For the question below, output JSON:
{
  "intent": "competitive" | "financial" | "security" | "mixed",
  "modules_to_invoke": [
    { "module": "...", "params": {...}, "priority": 1-5, "rationale": "..." }
  ],
  "reasoning": "1-2 sentences explaining the plan"
}

Rules:
- Invoke 2-4 modules typically. Don't invoke all 6 unless the question genuinely
  spans security + financial + competitive.
- Prefer high-signal modules for the intent over low-signal ones.
- Module params must be concrete: company names, URLs, regions, time windows.

Question: {question}
User context: {user_context}
```

### 5.3 Synthesizer prompt (sketch)

```
You are the Atlas Synthesizer. Produce an executive intelligence brief from the
module results below.

Structure:
1. Executive Summary (3-4 sentences; the "so what")
2. Key Findings (top 5; each with severity tag and evidence)
3. Module Sections (one per invoked module; structured insights with sources)
4. Confidence & Caveats (data freshness, gaps, recommended follow-ups)

Tone: institutional research analyst. No marketing language. No hedging where
evidence is strong. Cite every claim with a source URL.

If module results contradict each other, surface the contradiction and propose
resolution. If a module failed, note it and reduce confidence accordingly.

Question: {question}
Module results: {module_results}
```

### 5.4 MCP Server integration

The agent does NOT call Bright Data APIs directly. It calls MCP tools, which the Bright Data MCP server exposes. This is the architectural commitment.

Each module wraps its specific Bright Data tool calls in MCP-compatible tool definitions registered to the agent. The agent's executor node dispatches via MCP.

Why this matters for judging: explicit MCP-first architecture is what Bright Data is rewarding. Make this *visible* — terminal logs in the demo should show MCP tool invocations.

---

## 6. Output Design

The brief is what judges see. This is where most hackathon projects look amateurish.

### 6.1 Visual reference

Study these layouts (don't copy logos/IP, do copy structure):
- PitchBook company tear sheets
- McKinsey strategy briefs
- Palantir investor decks
- Bloomberg Intelligence summary pages

### 6.2 Brief structure

```
┌──────────────────────────────────────────┐
│  ATLAS                                    │
│  Ground Truth Brief                       │
│  ──────────────────                       │
│  Subject: Linear                          │
│  Generated: 2026-05-19 14:32 PT           │
│  Confidence: 0.84  •  Modules: 3          │
├──────────────────────────────────────────┤
│                                            │
│  EXECUTIVE SUMMARY                         │
│  Linear is in active European GTM         │
│  expansion. True pricing in EU is 19%     │
│  above US sticker when localized fees     │
│  applied. Hiring velocity 3.2x baseline.  │
│                                            │
│  KEY FINDINGS                              │
│  ⬤ CRITICAL  European launch imminent      │
│              (8 EU AE roles + GDPR eng)    │
│  ⬤ HIGH      DE pricing +23% vs sticker    │
│  ⬤ NOTABLE   Glassdoor sentiment +0.4 QoQ  │
│                                            │
│  ─────────────────────────────────────    │
│  MODULE: TRUEPRICE                         │
│  [comparative pricing table across 5      │
│   regions, with annotations]              │
│                                            │
│  ─────────────────────────────────────    │
│  MODULE: SIGNAL                            │
│  [job posting heatmap, role family chart] │
│                                            │
│  ─────────────────────────────────────    │
│  MODULE: ALTDATA                           │
│  [sentiment trend chart, review velocity] │
│                                            │
│  SOURCES (24)                              │
│  CONFIDENCE & CAVEATS                      │
└──────────────────────────────────────────┘
```

### 6.3 Output formats

- **In-app HTML** — what's shown in the brief viewer page
- **PDF** — downloadable via WeasyPrint
- **Markdown** — for GitHub/Slack/Notion paste
- **JSON** — for API consumers (mention this in README to show platform thinking)

---

## 7. 11-Day Execution Plan

Each day has a deliverable and an acceptance criterion. If you miss the acceptance criterion, do not proceed to the next day's work — fix the gap first.

### Day 1 — Foundation

**Deliverables:**
- GitHub repo initialized with structure above
- FastAPI hello-world deployed to Railway
- Next.js chat UI shell deployed to Vercel
- Bright Data account, MCP server credentials, API keys for all products
- `.env.example` documented

**Acceptance:** End of day, you can type a question in the UI and see it echo back from the backend.

### Day 2 — Agent skeleton + MCP wiring

**Deliverables:**
- LangGraph state machine wired with Planner + Synthesizer nodes (Executor returns mocks)
- MCP client wrapper instantiated; one successful test call to SERP API via MCP
- Module ABC class + 6 stub modules returning hardcoded fixtures
- Brief data model + minimal HTML renderer

**Acceptance:** Question → planner → executor (mocks) → synthesizer → HTML brief, end-to-end. Brief is ugly but flows.

### Day 3 — Module 1: Signal (start with the easiest win) - done

**Deliverables:**
- Signal module: Web Scraper API for LinkedIn jobs/company data
- LLM synthesis of raw job data into strategic inferences
- Working against 1 demo target end-to-end

**Acceptance:** Real Signal output for "Linear" includes ≥3 concrete inferences with source URLs.

### Day 4 — Module 2: TruePrice

**Deliverables:**
- Scraping Browser integration with 1 region (US baseline)
- Interaction script for 1 demo target reaching cart total
- Add 2 more regions

**Acceptance:** TruePrice produces a 3-region comparison table for demo target.

### Day 5 — Module 3: Visual + buffer day - done

**Deliverables:**
- Visual module: SERP discovery + Scraping Browser screenshots + Claude vision diff
- 2 controlled lookalike pages set up for guaranteed demo
- Half-day buffer for catching up on Modules 1-2

**Acceptance:** Visual flags your controlled lookalikes as high-suspicion with specific anomaly callouts.

### Day 6 — Modules 4-5: Filing + AltData

**Deliverables:**
- Filing module: Web Unlocker fetches SEC filings; LLM diff against prior version
- AltData module: Web Scraper API for Glassdoor + G2; sentiment trend computation

**Acceptance:** Both modules return useful output for at least one demo target each. Polish ceiling lower than first 3 modules — these are supporting cast.

### Day 7 — Module 6: Exposure + Decision Point - done

**Deliverables:**
- Exposure module: SERP for org-specific terms + Web Unlocker for paste-site fetches
- Controlled paste-bin entry created for demo
- **DECISION:** If Exposure is fighting back, cut it and run a 5-module project. Five great > six mediocre.

**Acceptance:** Exposure flags your controlled credential leak. Or you've cut it cleanly and updated the demo plan.

### Day 8 — Brief output polish (THE most important day)

**Deliverables:**
- Visual redesign of brief: typography, color system, chart components (Recharts), severity badges
- PDF export working via WeasyPrint
- Brief viewer page polished in Next.js
- All 3 demo queries produce briefs you'd show a CEO

**Acceptance:** Print one brief, hand it to someone who's never seen the project. They say "this looks like a real product."

### Day 9 — Demo prep

**Deliverables:**
- 3 demo queries locked and run 50 times each
- "Cached fallback" mode wired: live agent reasoning visible, deterministic execution for demo queries
- Demo script written (DEMO_SCRIPT.md) with exact narration timing
- Video recorded (2 takes minimum)

**Acceptance:** You can run all 3 demo queries back-to-back in under 4 minutes with zero failures across 10 consecutive dry runs.

### Day 10 — Submission materials

**Deliverables:**
- README polished (problem, demo, architecture, Bright Data integration callouts, run instructions)
- Cover image designed
- Slide deck (Google Slides; 8-10 slides max)
- Video edited with subtitles and intro/outro
- Live demo URL stable

**Acceptance:** Have one person who's not on the team review everything and rate "would I award a prize" on each judging criterion. Address any criterion below 4/5.

### Day 11 — Final dry runs and submission

**Deliverables:**
- 5 fresh end-to-end dry runs from cold start
- Submission form completed on lablab.ai
- Backup: every artifact (video, slides, brief samples) mirrored to a public Drive folder linked in README

**Acceptance:** Submitted before deadline with buffer. Then sleep.

---

## 8. Demo Specification

### Demo query 1 — GTM

**Query:** *"Run a full competitive brief on Linear. I'm evaluating them vs Jira for our 250-person engineering team."*

**Expected modules:** TruePrice + Signal + AltData

**Expected output highlights:**
- Region-by-region true pricing table for a 250-seat plan
- Strategic intent inference from hiring (EU expansion → likely roadmap implications)
- Sentiment trend showing user sentiment trajectory

**Live-demo wow moment:** Show terminal-side MCP server tool calls executing in parallel; brief renders in <60s.

### Demo query 2 — Finance

**Query:** *"Pre-earnings signal scan on [public-co with upcoming earnings]. Anything material in the last 30 days?"*

**Expected modules:** Filing + Signal + AltData

**Expected output highlights:**
- Filing diff showing actual material change (pre-scouted)
- Hiring velocity vs baseline
- Glassdoor sentiment shift quarter-over-quarter

**Live-demo wow moment:** "Filing module surfaced a material risk-factor addition the news hasn't covered yet." Show source URL with timestamp.

### Demo query 3 — Security

**Query:** *"Scan for brand exposure on [demo brand we control]. Flag impersonation and credential leaks."*

**Expected modules:** Visual + Exposure

**Expected output highlights:**
- Side-by-side screenshots of legitimate vs lookalike with vision-anomaly annotations
- Found credential entry on paste site with severity rating

**Live-demo wow moment:** Vision diff with specific anomaly callouts highlighted on screenshot.

### The killer transition

Between demo queries, include the **403 vs Web-Unlocked side-by-side**:
- Run `curl https://[blocked-site]` → 403 Forbidden
- Same URL via Web Unlocker → full content
- Total time: 10 seconds. Devastating proof of Bright Data's value.

### Fallback strategy

For live demo (judging session):
- The "live" agent runs in `demo_mode=true`
- In demo mode, the 3 known queries route through deterministic pipelines with cached intermediate results — sub-30-second response, zero failure risk
- Non-demo queries (any judge asking ad-hoc) route through full live execution
- This is honest — disclosed in the README — and standard practice for live demos

---

## 9. Submission Checklist

Per the hackathon brief:

- [ ] Project Title: **Atlas**
- [ ] Short Description (50 words)
- [ ] Long Description (300-500 words; emphasize Bright Data integration depth)
- [ ] Technology Tags: `agentic-ai`, `mcp`, `bright-data`, `claude`, `langgraph`, `web-intelligence`
- [ ] Category Tags: all 3 tracks
- [ ] Cover Image (1920x1080; clean visual showing brief + Bright Data logo callout)
- [ ] Video Presentation (3 min; demo + architecture overview)
- [ ] Slide Presentation (Google Slides; problem → solution → tech → demo → ask)
- [ ] Public GitHub Repo with comprehensive README
- [ ] Demo Application Platform: Vercel (frontend) + Railway (backend)
- [ ] Application URL: live, working, with at least the 3 demo queries pre-warmed

**Bright Data integration callout** (must be explicit in README and video):
> Atlas uses **5 of Bright Data's 6 core products**: MCP Server (agent orchestration), Web Unlocker (filings + exposure), SERP API (visual discovery + exposure), Scraping Browser (TruePrice + Visual), and Web Scraper API (Signal + AltData). Residential proxies are routed geographically for TruePrice's regional pricing analysis.

---

## 10. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Bright Data credit burn before Day 11 | Medium | High | Monitor daily; request top-up via Discord on Day 5; cache aggressively |
| Demo target site redesigns mid-build | Medium | High | Use 2 backup targets per module; selector configs in version control |
| Live agent unreliable on demo day | Medium | Critical | `demo_mode` deterministic pipelines for known queries |
| LinkedIn/Glassdoor rate limits | High | Medium | Web Scraper API designed for this; have static fixtures as ultimate fallback |
| Vision module false positives | Medium | Medium | Tune prompt rubric; require ≥3 visual anomalies for "high" rating |
| Scope creep adding 7th module | High | Medium | Document is the commitment; reread on Day 7 |
| One team member loses days to environment setup | Medium | Medium | Day 1 includes complete `.env.example` and Docker compose; standardize |
| Brief output looks like ChatGPT | High | High | Day 8 is sacred; do not skip output design for "one more module" |
| Submission deadline missed | Low | Critical | Day 11 is buffer; aim to submit Day 10 evening |
| Legal/ethical exposure (Visual/Exposure modules) | Low | High | Controlled targets only; clear disclosure in README; never publish real-org findings |

---

## 11. Judging Criteria — Explicit Alignment

| Criterion | How Atlas wins it |
|-----------|-------------------|
| **Application of Technology** | MCP-first architecture; 5 of 6 Bright Data products integrated as agent tools, not bolted on; multi-agent orchestration via LangGraph |
| **Presentation** | Sub-4-minute demo covering 3 tracks; institutional-quality brief output; 403-vs-unblocked side-by-side; clean GitHub README |
| **Business Value** | 3 distinct enterprise buyers shown in single demo (sales/GTM, finance/IR, security/IT); each brief replaces 4-8 hours of analyst work |
| **Originality** | TruePrice's checkout-completion mechanic is genuinely novel; the "Ground Truth Brief" framing positions Bright Data as enabling a new category, not just better scraping |

---

## 12. Open Decisions

Decide these by end of Day 1:

1. **Team structure** — if you're solo, cut Exposure and one other module to 4 total. If 2+ people, full plan applies.
2. **Demo targets** — pick the 3 specific companies/brands for demos. Lock them in `demo/targets.md`.
3. **Live URL** — what root domain? Affects how seriously judges treat the project.
4. **Public or staged data?** — for Visual and Exposure, default to controlled targets. Reverse only if you have a real org's written permission.
5. **Recording setup for the video** — book the time on Day 9, not Day 10. Lighting matters more than you'd think.

---

*Last updated: planning draft.*