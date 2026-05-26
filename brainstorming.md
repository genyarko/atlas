Quick scorecard
SignalGaze AI is essentially my "Signal" concept fused with competitor pricing and review sentiment. The MCP Server emphasis is correct — Bright Data is heavily pushing it and judges will reward deep MCP integration. But three subsystems (hiring + pricing + sentiment) in a weekend is a scope trap. You'll ship three half-built features instead of one polished one. The three-vector framing is good for a pitch; bad for a build.
OmniSight is the most dangerous recommendation in the set. "Ask any business question, get a board-ready brief" sounds impressive in writing but it's exactly what 30+ other teams will build — it's ChatGPT-with-tools wearing an enterprise wrapper. When the deliverable is "anything," the demo lands as "nothing in particular." It also fails the demo-moment test: a generic research report doesn't feel magical the way a specific, narrow capability does. Multi-agent architecture is technically impressive but judges grade on outcome, not org chart. This is my "Stratos" concept and I undersold the risk on it in my last message.
Shadow Cart is the most original idea across all four responses (mine included). I missed this entirely. Actually completing checkout flows to extract true pricing — with taxes, fees, mandatory add-ons — is a genuinely novel angle that nobody else at the hackathon will think of, and it uses Scraping Browser in a way that's hard to fake. The demo of a bot autonomously filling a cart is genuinely visual. Caveats: real ToS/legal exposure if you do this against named brands live on stage (use fictional test sites or sandboxed e-commerce demos), and the flow is brittle — one site redesign mid-demo and you're dead. But the originality ceiling is the highest of any idea on the table.
PhishEye is also stronger than anything I proposed for Security & Compliance. Vision AI + SERP discovery + Scraping Browser screenshots is a clean three-tool integration story, and the side-by-side "legitimate vs lookalike" visual is instantly comprehensible to non-technical judges. Security track is also less crowded than GTM, which matters for relative scoring.
Supply Chain Pulse is solid but unspectacular — closest to a "real product" but lacks a single jaw-dropping demo moment. The forklift detail is memorable.
Policy Radar is the weakest — it's "monitor regulatory websites" which has been done a hundred times.
What everyone (including me) missed or underweighted

The "Blocked vs Unblocked" demo trick in response 3 is the single best tactical insight in any of these. Show a curl returning 403, then the same URL through Web Unlocker returning full content. That's a 10-second proof of why Bright Data matters. Build that into the demo regardless of which idea you pick.
Geographic arbitrage — using residential proxies across 30+ countries is uniquely Bright Data. Only my Prism idea touched this and none of the others did. It's underexploited.
MCP Server specifically — Bright Data is heavily promoting MCP this hackathon. Building your agent around MCP (not just "we use it") will score with judges. The other responses gestured at this; you should make it central.
Vision AI — completely absent from my response. PhishEye's use of it is smart. Vision-based scraping is a frontier capability and judges will reward it.

Updated recommendation
I'm changing my top pick. Honest version:
If you want to maximize originality and demo wow: Build Shadow Cart. It's the most defensible idea against the inevitable wave of "competitor monitoring dashboards." The risk is execution brittleness — mitigate by pre-recording the live demo and having a Plan B replay.
If you want maximum business value and broad appeal: Build a narrowed version of Diligence — pick one buyer (PE associate doing pre-screen) and one deliverable (10-page investment memo on a target company). Don't try to be a generic research agent.
If you want the easiest win because Security is less crowded: Build PhishEye. Vision AI is hot, the demo is dead simple to understand, and you'll likely face fewer competitors in that track.

What we settled on...
Project: Atlas
The autonomous intelligence platform that sees through what your tools can't.
The core insight (and the pitch): The surface web lies. Pricing pages show list prices, not true cost. Press releases say what companies want you to hear, not what they're doing. Brand sites get cloned, reviews get filtered, regulatory updates lag the news. The most valuable enterprise intelligence is behind login walls, JS renders, CAPTCHAs, geo-blocks, and visual deception. Atlas is an autonomous agent that goes through all of it.
The product: One chat-style interface. Ask any enterprise intelligence question. Atlas decomposes it, decides which intelligence modules to invoke, runs them in parallel, and produces a single structured Ground Truth Brief — Bloomberg-grade output with citations and confidence scores.
The six modules (each maps to a track and a Bright Data tool)
TruePrice (GTM) — Scraping Browser + geo-routed residential proxies. Completes actual checkout flows across geographies to reveal real cost: taxes, fees, mandatory add-ons, regional markups. This is the Shadow Cart idea, weaponized.
Signal (GTM + Finance) — Web Scraper API. Detects intent signals from job postings ("8 new EU AE roles + 3 GDPR engineers → European launch imminent"), exec movements, hiring freezes, tech-stack changes.
Filing (Finance) — Web Unlocker + MCP. Parses SEC filings, patent applications, regulatory updates. Materiality scoring on changes vs. prior versions.
AltData (Finance) — Web Scraper API. Glassdoor sentiment trends, marketplace equipment listings (the forklift signal from Supply Chain Pulse), review velocity changes. Composite distress/momentum score.
Visual (Security) — Scraping Browser + Vision AI (Claude or GPT-4o). Captures screenshots of suspect lookalike domains discovered via SERP, vision-diffs against legitimate brand. This is PhishEye, integrated.
Exposure (Security) — Web Unlocker + SERP API. Monitors paste sites, public GitHub commits, forums for org-specific credentials, leaked docs, exec doxxing surface.
The orchestrator is Bright Data's MCP Server — agent's tool layer. The agent reasons about the question, picks modules, executes, synthesizes. This is the MCP-first architecture Bright Data is heavily pushing this hackathon, so building around it scores deliberate technical points.
The demo that wins (90 seconds, three queries, all three tracks, one product)

"Run a competitive brief on Linear" → TruePrice + Signal + AltData → GTM battlecard: true pricing across 5 regions, hiring-inferred roadmap, review sentiment trend. (Track 1)
"Assess pre-earnings signals for Datadog" → Signal + Filing + AltData → finance brief: hiring velocity, regulatory exposure, alt-data composite. (Track 2)
"Scan brand impersonation for [demo brand]" → Visual + Exposure → security brief: screenshot diffs, leaked credentials. (Track 3)

Same product, same UI, same agent — judges see one platform doing three radically different things. That's what proves the platform thesis instead of a toy. Bonus: include the 403 vs. Web-Unlocked side-by-side moment from one of the earlier responses. Ten seconds, devastating proof of why Bright Data matters.
11-day build plan
Days 1-2: Foundation. MCP Server setup, agent scaffolding (LangGraph or CrewAI), tool definitions for all 6 modules as stubs that return mock data. Goal: end-to-end pipeline that takes a question and returns a brief, even if every module is faked.
Days 3-5: Build the three strongest modules to working quality — TruePrice, Signal, Visual. These have the most demo impact. Each needs to work on real targets you've pre-validated.
Days 6-7: Build the remaining three — Filing, AltData, Exposure — to demo-grade. Lower polish ceiling acceptable; they're supporting cast.
Day 8: Brief output design. This is where most projects lose points and nobody talks about it. The PDF/HTML report is what judges actually look at. Study McKinsey, PitchBook, Bloomberg layouts. Copy structure ruthlessly. Charts, tables, source attribution, confidence scores.
Day 9: Demo prep. Lock the three demo queries. Run them 50 times. Build a "cached fallback" mode so live failure is invisible — the live agent should call deterministic pipelines for the demo queries while behaving autonomously for everything else. Record the video.
Day 10: Buffer. Submission materials (cover image, slides, README, GitHub clean-up, video edit).
Day 11: Dry runs, final polish, submit.
What kills you, and how to prevent it
Module sprawl. You'll be tempted to add a 7th module. Don't. Six is already aggressive. If anything isn't working by Day 6, cut it — five great modules beats six mediocre ones.
Unreliable orchestration on demo day. Hard-code the demo paths. Your "autonomous agent" demo should actually invoke deterministic pipelines for the three known queries. Reserve autonomous reasoning for the README and longer video, where edge-case failures don't matter.
Generic-looking brief output. ChatGPT-with-citations is the failure mode. Spend Day 8 on visual report design — a polished output PDF wins more judging points than another half-finished module.