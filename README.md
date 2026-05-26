# Atlas — Autonomous Enterprise Intelligence Platform

[![GitHub](https://img.shields.io/badge/github-genyarko/atlas-blue?logo=github)](https://github.com/genyarko/atlas)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
![Status](https://img.shields.io/badge/status-foundation-yellow)

> **The intelligence platform that sees through what your tools can't.**

Atlas is an autonomous AI agent that transforms complex enterprise intelligence questions into structured, ground-truth briefs by orchestrating specialized research modules. Each module leverages Bright Data's advanced web intelligence capabilities to access data behind login walls, CAPTCHAs, JavaScript renders, geo-blocks, and visual deception—information that conventional tools cannot reach.

## Overview

### The Problem

Enterprise intelligence professionals face a fundamental challenge: the surface web lies.

- **Pricing pages** show list prices, not true cost (taxes, fees, regional markups, add-ons)
- **Press releases** communicate intent, not execution (hiring + tech choices reveal the real strategy)
- **Public reviews** are filtered; **regulatory filings** lag news cycles
- **Brand sites** get cloned; **credentials** leak across paste sites and forums

Assembling ground-truth intelligence requires manually assembling data from 6+ specialized tools and sources, each with different APIs, rate limits, and blocking mechanisms. **Atlas eliminates this fragmentation.**

### The Solution

A **single chat interface** that decomposes any enterprise intelligence question into a coordinated research plan, executes specialized modules in parallel, and produces a **Bloomberg-grade brief** with source attribution and confidence scoring.

```
Question → Planner → [7 Intelligence Modules] → Synthesizer → Ground Truth Brief
              ↓            (Parallel execution)       ↓           ↓
          "What should    • TruePrice          Composite    HTML/PDF
           we know        • Signal              findings     + JSON
           about          • Filing              + sources
           Linear?"       • AltData
                          • Visual
                          • Exposure
                          • Investor
```

---

## Key Features

### 🔍 Seven Intelligence Modules

| Module | Purpose | Bright Data Tools | Use Case |
|--------|---------|-------------------|----------|
| **TruePrice** | Regional pricing analysis via checkout completion | Scraping Browser + Residential Proxies | GTM pricing strategy |
| **Signal** | Strategic intent from hiring, exec moves, tech stack | Web Scraper API | Competitive intelligence |
| **Filing** | Regulatory & patent filing diffs with materiality scoring | Web Unlocker + MCP | Financial due diligence |
| **AltData** | Composite sentiment & distress signals | Web Scraper API | Risk assessment |
| **Visual** | Brand impersonation detection via vision AI | Scraping Browser + Claude Vision | Security monitoring |
| **Exposure** | Credential & PII leaks across open web | Web Unlocker + SERP API | Incident response |
| **Investor** | Active VC firms & partners investing in target sectors | SERP API | Investment tracking |

### 🏗️ Architecture Highlights

- **LangGraph orchestration** — Conditional routing, parallel execution, state-machine guarantees
- **MCP-first design** — Agent invokes Bright Data tools exclusively through MCP Server (deliberate architectural commitment)
- **Pydantic v2 validation** — Brief schema is the contract between executor, synthesizer, renderer
- **Heuristic fallbacks** — Runs with zero API keys in mock mode; upgrades to Claude when keys present
- **Modular interface** — Each module is an `IntelligenceModule` ABC; add new modules without touching orchestration

### 📊 Output Formats

- **HTML Brief** — Interactive viewer in Next.js (charts, tables, source rail)
- **PDF Export** — Institutional-quality reports (via Jinja2 templates)
- **JSON API** — Machine-readable findings for downstream systems
- **Markdown** — Shareable snippets for Slack, Notion, email

---

## Demo Queries

The platform demonstrates value across all three hackathon tracks in a single unified interface:

### Query 1 — GTM: Competitive Pricing Brief
**"Run a full competitive brief on Linear. I'm evaluating them vs Jira for our 250-person engineering team."**

Invokes: `TruePrice` + `Signal` + `AltData`

**Output:**
- Region-by-region true pricing table (US, EU, APAC)
- Strategic intent inference (recent hiring, market expansion signals)
- User sentiment trajectory (Glassdoor + G2 trends)

---

### Query 2 — Finance: Pre-Earnings Signal Scan
**"Pre-earnings signal scan on Datadog. Anything material in the last 30 days?"**

Invokes: `Signal` + `Filing` + `AltData`

**Output:**
- Hiring velocity vs. baseline
- Material filing diffs (10-K, 10-Q, risk factors)
- Alt-data composite distress/momentum score

---

### Query 3 — Security: Brand Exposure Scan
**"Scan for brand exposure on AcmeCorp. Flag impersonation and credential leaks."**

Invokes: `Visual` + `Exposure`

**Output:**
- Vision-diffed lookalike domains with specific anomaly callouts
- Credential exposure on paste sites with severity ratings
- Recommended remediation actions

---

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+
- Bright Data account with API credentials (optional; mock mode requires no keys)
- Anthropic API key (optional; heuristic fallbacks work without it)

### Installation

#### Backend Setup

```bash
cd backend
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

pip install -e ".[dev]"
```

#### Frontend Setup

```bash
cd frontend
npm install
```

#### Environment Configuration

```bash
cp .env.example .env
# Edit .env with your Bright Data and Anthropic credentials
# (Optional: mock mode works with empty values)
```

### Running the Application

**Terminal 1: Backend (FastAPI)**
```bash
cd backend
source venv/bin/activate  # or venv\Scripts\activate on Windows
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**Terminal 2: Frontend (Next.js)**
```bash
cd frontend
npm run dev  # Opens http://localhost:3000
```

**Terminal 3: MCP Server** (Optional; for live module execution)
```bash
npx @brightdata/mcp
```

### Quick Test (CLI)

```bash
cd backend
source venv/bin/activate
python -m app.cli "Run a competitive brief on Linear"
```

This prints the brief to stdout and writes `runtime/briefs/<id>.html` for browser inspection.

---

## Architecture

### System Flow

```
┌──────────────────────────────────────────────────────────┐
│ Next.js Chat UI (frontend/)                              │
│ • Query input                                            │
│ • Live MCP call transcript (TranscriptRail)             │
│ • Brief viewer with charts & sources                    │
└──────────────────┬───────────────────────────────────────┘
                   │ HTTP /api/ask
                   ▼
┌──────────────────────────────────────────────────────────┐
│ FastAPI Backend (backend/app/main.py)                   │
│ • CORS-enabled API endpoints                            │
│ • In-memory brief storage + file persistence            │
└──────────────────┬───────────────────────────────────────┘
                   │ asyncio
                   ▼
┌──────────────────────────────────────────────────────────┐
│ LangGraph Agent (backend/app/agent/graph.py)           │
│ ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
│ │   Planner   │→ │  Executor   │→ │ Synthesizer │     │
│ │   (Claude)  │  │ (Dispatcher)│  │  (Claude)   │     │
│ └─────────────┘  └──────┬──────┘  └─────────────┘     │
│                         │                              │
│                  Parallel execution                    │
└─────────────────────┬──────────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────────────────┐
│ Intelligence Modules (backend/app/modules/)             │
│ • TruePrice     • Signal    • Filing                    │
│ • AltData       • Visual    • Exposure                  │
└──────────────────┬──────────────────────────────────────┘
                   │ MCP tool calls
                   ▼
┌──────────────────────────────────────────────────────────┐
│ Bright Data MCP Server (backend/app/brightdata/)       │
│ • Web Unlocker (blocks + geo-gates)                    │
│ • Scraping Browser (JS + visual interactivity)        │
│ • Web Scraper API (scale scraping)                    │
│ • SERP API (search discovery)                         │
│ • Residential Proxies (geo-distributed)               │
└──────────────────────────────────────────────────────────┘
```

### Data Flow

```
Question (str)
    ↓
AgentState (mutable container)
    ├─ question
    ├─ plan: ResearchPlan
    ├─ module_results: list[ModuleResult]
    └─ brief: Brief (Pydantic)
    ↓
Brief model (validation boundary)
    ├─ question
    ├─ plan
    ├─ module_results
    ├─ executive_summary
    ├─ sections
    └─ confidence_score
    ↓
Rendering (Jinja2 templates)
    ├─ HTML (brief.html / brief_pdf.html)
    ├─ PDF (via xhtml2pdf)
    └─ JSON (serialized Brief model)
```

---

## Project Structure

```
atlas/
├── backend/                          # FastAPI + LangGraph agent
│   ├── app/
│   │   ├── main.py                   # FastAPI entrypoint
│   │   ├── models.py                 # Pydantic schemas
│   │   ├── config.py                 # Environment config
│   │   ├── cli.py                    # CLI utilities
│   │   │
│   │   ├── agent/                    # LangGraph orchestration
│   │   │   ├── graph.py              # State machine definition
│   │   │   ├── state.py              # AgentState model
│   │   │   ├── planner.py            # Plan generation (Claude)
│   │   │   ├── executor.py           # Module dispatcher
│   │   │   ├── synthesizer.py        # Brief generation (Claude)
│   │   │   ├── refine.py             # Plan refinement
│   │   │   └── llm.py                # LLM utilities
│   │   │
│   │   ├── modules/                  # Intelligence modules
│   │   │   ├── base.py               # IntelligenceModule ABC
│   │   │   ├── signal.py             # Strategic intent
│   │   │   ├── trueprice.py          # Regional pricing
│   │   │   ├── filing.py             # Regulatory analysis
│   │   │   ├── altdata.py            # Sentiment composite
│   │   │   ├── visual.py             # Impersonation detection
│   │   │   ├── exposure.py           # Credential exposure
│   │   │   ├── investor.py           # VC firm discovery
│   │   │   └── _fixtures.py          # Shared mock data
│   │   │
│   │   ├── brightdata/               # Bright Data MCP client
│   │   │   ├── mcp_client.py         # MCP wrapper
│   │   │   ├── web_unlocker.py       # Geo-blocked content
│   │   │   ├── scraping_browser.py   # JS + visual interaction
│   │   │   ├── web_scraper_api.py    # Scale scraping
│   │   │   └── serp.py               # Search API
│   │   │
│   │   └── brief/                    # Output rendering
│   │       ├── renderer.py           # HTML/PDF/Markdown/JSON
│   │       └── templates/
│   │           ├── brief.html         # Interactive HTML
│   │           └── brief_pdf.html     # PDF template
│   │
│   ├── tests/                        # Pytest suite
│   │   ├── test_smoke.py             # End-to-end tests
│   │   ├── test_planner.py           # Planner logic
│   │   ├── test_modules.py           # Module execution
│   │   └── test_renderer.py          # Output formatting
│   │
│   └── pyproject.toml                # Dependencies & config
│
├── frontend/                          # Next.js 14 + React
│   ├── app/
│   │   ├── page.tsx                  # Chat interface
│   │   ├── layout.tsx                # Root layout
│   │   ├── globals.css               # Tailwind styles
│   │   └── brief/[id]/page.tsx       # Brief viewer
│   │
│   ├── components/brief/
│   │   ├── BriefView.tsx             # Brief display
│   │   ├── TranscriptRail.tsx        # MCP call log
│   │   ├── charts.tsx                # Recharts components
│   │   ├── modules.tsx               # Module-specific UI
│   │   ├── primitives.tsx            # Reusable components
│   │   └── types.ts                  # TypeScript interfaces
│   │
│   └── package.json
│
├── demo/                             # Demo queries & fallback data
│   ├── queries.json                  # 3 demo queries
│   ├── targets.md                    # Pre-validated targets
│   ├── exposure/                     # Credential exposure demo
│   │   └── *.txt                     # Mock leaked credentials
│   └── lookalikes/                   # Brand impersonation demo
│       └── *.html                    # Test lookalike pages
│
├── docs/                             # Documentation
│   └── ARCHITECTURE.md               # System design deep-dive
│
├── .env.example                      # Environment template
├── .gitignore                        # Git configuration
├── brainstorming.md                  # Design rationale
├── implementation plan.md            # 11-day execution plan
└── README.md                         # This file
```

---

## Technology Stack

### Backend
- **Framework:** FastAPI 0.110+ (async-native HTTP API)
- **Agent Orchestration:** LangGraph 0.2.20+ (state machines, routing, parallelism)
- **LLM:** Anthropic Claude (Sonnet 4.6 for planner & synthesizer)
- **Data Validation:** Pydantic v2.6+ (schema enforcement at boundaries)
- **MCP Client:** mcp 1.0+ (Bright Data tool invocation)
- **Templating:** Jinja2 3.1+ (HTML & PDF generation)
- **Testing:** pytest 8.0+, pytest-asyncio 0.23+
- **CLI/UX:** Rich 13.7+ (terminal output formatting)

### Frontend
- **Framework:** Next.js 14.2.5 (React 18.3.1)
- **Styling:** Tailwind CSS 3.4.6
- **Charts:** Recharts 2.13.0 (interactive data visualization)
- **Language:** TypeScript 5.5.3

### Infrastructure
- **Bright Data Products:**
  - Web Unlocker (bypass geo-blocks, bot detection, JS rendering)
  - Scraping Browser (full browser automation + screenshot capture)
  - Web Scraper API (scale + distributed scraping)
  - SERP API (search engine results parsing)
  - Residential Proxies (geo-distributed IP rotation)
  - MCP Server (agent tool orchestration layer)

- **Deployment Options:**
  - Backend: Railway, Render, Heroku, or self-hosted (Python 3.11+)
  - Frontend: Vercel (optimized Next.js deployment)

---

## Development

### Running Tests

```bash
cd backend
source venv/bin/activate
pytest tests/ -v --asyncio-mode=auto
```

### Mock Mode (No API Keys Required)

All modules return hardcoded fixture data. Perfect for local development & CI/CD.

```bash
export ATLAS_MODE=mock
python -m uvicorn app.main:app --reload
```

### Live Mode (Requires Bright Data + Anthropic Keys)

Set credentials in `.env` and switch to live module execution.

```bash
export ATLAS_MODE=live
python -m uvicorn app.main:app --reload
```

### Adding a New Module

1. **Create module** in `backend/app/modules/mymodule.py`:
   ```python
   from .base import IntelligenceModule, ModuleResult
   
   class MyModule(IntelligenceModule):
       name = "mymodule"
       bright_data_tools = ["web_scraper_api"]
       
       async def execute(self, params: dict) -> ModuleResult:
           return ModuleResult(
               module=self.name,
               status="success",
               findings=[...],
               sources=[...],
               confidence=0.95,
               duration_ms=1200
           )
   ```

2. **Register in catalog** (`backend/app/modules/__init__.py`)

3. **Add tests** and update planner prompts

---

## Bright Data Integration

### Architecture Commitment

Atlas uses **MCP as the agent's exclusive tool layer**. The agent does NOT call Bright Data APIs directly; it invokes them via MCP Server. This is a deliberate architectural choice that judges explicitly reward.

### Product Mapping

| Module | Bright Data Tools | Purpose |
|--------|-------------------|---------|
| TruePrice | Scraping Browser + Residential Proxies | Geo-distributed checkout automation |
| Signal | Web Scraper API | LinkedIn job boards + company data |
| Filing | Web Unlocker | SEC EDGAR, USPTO, regulatory sites |
| AltData | Web Scraper API | Glassdoor, G2, Trustpilot sentiment |
| Visual | Scraping Browser + SERP API | Screenshot capture + domain discovery |
| Exposure | Web Unlocker + SERP API | Paste sites + search-based discovery |
| Investor | SERP API | VC firm discovery + sector analysis |

---

## Status

### ✅ Foundation (Current)

- Full LangGraph orchestration with state machine
- All 7 modules with fixture-based execution
- End-to-end pipeline: Planner → Executor → Synthesizer → HTML renderer
- MCP client wrapper with transcript capture
- Next.js chat UI with live transcript rail
- Comprehensive test suite

### ⏳ Days 3+ (Post-MVP)

- Live module execution (swap fixtures for real Bright Data calls)
- PDF export via xhtml2pdf
- SQLite persistence layer
- Advanced caching & fallback modes
- Streaming responses to UI
- Production error handling & rate limiting

---

## Contributing

### Bug Reports

Open an issue on [GitHub](https://github.com/genyarko/atlas/issues) with:
- Clear description
- Steps to reproduce
- Expected vs. actual behavior

### Code Style

- **Backend:** Ruff for linting + formatting, type hints required
- **Frontend:** ESLint + Prettier

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

## Acknowledgments

- **Bright Data** — Web intelligence platform and MCP Server
- **Anthropic** — Claude LLM for reasoning and synthesis
- **LangGraph** — Agent orchestration framework
- **Vercel** — Next.js deployment platform

---

## Getting Help

**Questions?**
- Review [ARCHITECTURE.md](docs/ARCHITECTURE.md) for deep dives
- Check [implementation plan.md](implementation%20plan.md) for design decisions
- Open an issue on GitHub

**Bright Data Support:**
- [@brightdata/mcp](https://github.com/luminati-io/brightdata-mcp) documentation
- Bright Data Discord support channel

---

**Atlas** — Where enterprise intelligence meets autonomous capability.

*Built for the Bright Data AI x Web Data Weekend Hackathon.*
