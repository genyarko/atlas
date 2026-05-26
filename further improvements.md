
  1. Expose the MCP transcript. BrightDataMCPClient.transcript is populated on every call (mcp_client.py:75,147) and never read by anyone. The
  implementation plan literally calls this out as a demo wow-moment ("terminal logs should show MCP tool invocations"). Add GET 
  /api/briefs/{id}/transcript (key it by brief id by capturing the transcript slice during run_agent), and render a collapsible "MCP calls" rail in
  BriefView showing tool name, args, duration, ok/err. This is the single highest-impact change you can still make. - DONE
  2. Pass evidence — not just finding statements — to the synthesizer. synthesizer.py:96-99 only ships [module/severity] statement lines to Claude. The
  juicy numbers (TruePrice's +23% DE, Signal's velocity_ratio, AltData's composite_score) live in raw_data and never reach the LLM. Serialise a compact
  summary of each section's raw_data into the prompt and your executive summary reads 2× more credible. - DONE
  3. Stream per-module progress to the UI. frontend/app/page.tsx:148-159 shows one static "Synthesizing" spinner for ~60s. The executor already logs
  every dispatch (executor.py:20). An SSE endpoint that emits module.start/module.done events lets you replace the spinner with a live checklist — looks
   far more "agentic" on camera. - DONE
  4. Wire the refine loop the plan promised. graph.py is straight plan→execute→synthesize; the plan §5.1 specifies an evaluate→refine_plan loop (max 2
  iterations). Even a minimal evaluate_node that re-plans when ≥2 modules return mock-mode or confidence<0.5 would be a real "agentic depth" callout for
   judges. The comment at the top of graph.py already acknowledges the gap.

  Reliability — likely to bite mid-demo

  5. Brief store is in-memory only. main.py:34 _BRIEFS: dict[str, Brief] = {}. The HTML route falls back to disk (main.py:98-100); the JSON, MD, and PDF
   routes don't. A backend restart between query and PDF click → 404. Fix: dump brief.model_dump_json() to runtime/briefs/<id>.json alongside the HTML
  and reload on miss.
  6. Browser-lock coverage is uneven. _browser_scrape_text in web_scraper_api.py:50 takes client.browser_lock, but TruePrice's
  scraping_browser.checkout_session path doesn't appear to (only sequential within trueprice). When the executor runs trueprice and visual in parallel
  (executor.py:23), both hit the stateful browser session and can race (one switches country mid-flight). Either route every browser call through
  client.browser_lock, or serialize the two browser-bound modules in the executor.
  7. MCP GROUPS default is "browser" only (config.py:43), but the README pitches "5 of 6 Bright Data products" — the social/datasets tools that
  Signal+AltData need aren't actually loaded by default. Either flip the default to include them, or document the toggle visibly in the demo script so
  you don't fumble it live.
  8. Planner LLM silently drops bad modules. planner.py:148-152 filters invalid names and emits whatever's left. If Claude hallucinates 3 of 4 module
  names, you get a 1-module brief with no warning. Log when this happens and treat empty/short results as a heuristic fallback signal. - All DONE TILL HERE.

  Polish

  9. infer_subject is a 5-company regex (_fixtures.py:8-14). LLM planner already returns subject in its JSON so the LLM path is fine — but the
  heuristic-router path will mis-extract anything unfamiliar. Either accept that limitation (it's a hackathon) or have the planner call an LLM purely
  for subject extraction even when the heuristic router decides modules.
  10. Missing tests for the agent/brief/brightdata layers. Module tests are healthy (~3k lines), but no test_planner.py / test_synthesizer.py /
  and reload on miss.
  6. Browser-lock coverage is uneven. _browser_scrape_text in web_scraper_api.py:50 takes client.browser_lock, but TruePrice's
  scraping_browser.checkout_session path doesn't appear to (only sequential within trueprice). When the executor runs trueprice and visual in parallel
  (executor.py:23), both hit the stateful browser session and can race (one switches country mid-flight). Either route every browser call through
  client.browser_lock, or serialize the two browser-bound modules in the executor.
  7. MCP GROUPS default is "browser" only (config.py:43), but the README pitches "5 of 6 Bright Data products" — the social/datasets tools that
  Signal+AltData need aren't actually loaded by default. Either flip the default to include them, or document the toggle visibly in the demo script so
  you don't fumble it live.
  8. Planner LLM silently drops bad modules. planner.py:148-152 filters invalid names and emits whatever's left. If Claude hallucinates 3 of 4 module
  names, you get a 1-module brief with no warning. Log when this happens and treat empty/short results as a heuristic fallback signal.

  Polish

  9. infer_subject is a 5-company regex (_fixtures.py:8-14). LLM planner already returns subject in its JSON so the LLM path is fine — but the
  heuristic-router path will mis-extract anything unfamiliar. Either accept that limitation (it's a hackathon) or have the planner call an LLM purely
  for subject extraction even when the heuristic router decides modules.
  10. Missing tests for the agent/brief/brightdata layers. Module tests are healthy (~3k lines), but no test_planner.py / test_synthesizer.py /
  test_renderer.py. The planner's JSON parser (fences, missing keys, invalid module names) is the most worthwhile target.
  11. CORS is open (main.py:26-31) and MODE is read at import time so live↔mock requires a restart — both fine for a hackathon, just noting for the
  README's "non-goals" list so judges don't ding you.

  LinkedIn module
  Investor module.
