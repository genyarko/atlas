"use client";

// Live infrastructure trace — renders the MCP call stream as a polished
// activity feed alongside the brief. Three states drive it:
//
//   • streaming  → SSE is open, rows animate in as each call completes
//   • complete   → the brief is done, the rail shows the final transcript
//   • idle       → no run yet (page just loaded) — rail is hidden
//
// The component is purely presentational; the page owns the SSE wiring
// and feeds new calls in via the ``calls`` prop.

import { useMemo } from "react";
import {
  MODULE_META,
  type McpCall,
  type ModuleName,
  type ModuleProgress,
} from "./types";

export type RailPhase = "idle" | "streaming" | "complete";

interface TranscriptRailProps {
  phase: RailPhase;
  calls: McpCall[];
  modules: ModuleProgress[];
  // Number of new calls in the last tick — drives the "fresh" highlight
  // animation. The page-level state increments this and the rail consumes it.
  freshCount?: number;
}

export function TranscriptRail({
  phase,
  calls,
  modules,
}: TranscriptRailProps) {
  const stats = useMemo(() => deriveStats(calls), [calls]);

  if (phase === "idle") return null;

  return (
    <aside
      className="bg-panel border border-rule rounded-lg p-5 flex flex-col gap-4"
      aria-live="polite"
    >
      {/* Header */}
      <header className="flex items-baseline justify-between border-b border-rule pb-3">
        <div className="flex items-baseline gap-3">
          <span className="font-mono text-[11px] font-bold tracking-[0.22em] text-accent uppercase">
            Live infrastructure trace
          </span>
          {phase === "streaming" ? (
            <span className="inline-flex items-center gap-1.5 font-mono text-[10px] tracking-[0.16em] uppercase text-accent">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
              streaming
            </span>
          ) : (
            <span className="font-mono text-[10px] tracking-[0.16em] uppercase text-ink-dim">
              {calls.length} call{calls.length === 1 ? "" : "s"}
            </span>
          )}
        </div>
        <span className="font-mono text-[10px] tracking-[0.14em] uppercase text-ink-dim">
          Bright Data MCP
        </span>
      </header>

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-3 text-center">
        <Stat label="Calls" value={String(stats.total)} />
        <Stat
          label="Latency"
          value={`${(stats.totalMs / 1000).toFixed(1)}s`}
        />
        <Stat
          label="Products"
          value={String(stats.products.size)}
        />
      </div>

      {/* Module progress */}
      {modules.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {modules.map((m) => (
            <ModuleChip key={m.module} progress={m} />
          ))}
        </div>
      ) : null}

      {/* Call list */}
      <ol className="flex flex-col gap-1.5 max-h-[440px] overflow-y-auto pr-1">
        {calls.length === 0 ? (
          <li className="font-mono text-[11px] text-ink-dim py-4 text-center">
            Waiting for first MCP call…
          </li>
        ) : (
          calls.map((call) => <CallRow key={call.id} call={call} />)
        )}
      </ol>

      {/* Footnote — only when simulated entries are present */}
      {stats.simulated > 0 ? (
        <footer className="border-t border-rule pt-2.5 font-mono text-[10px] tracking-[0.05em] text-ink-dim leading-relaxed">
          <span className="inline-block w-2.5 h-2.5 border border-dashed border-ink-dim mr-1.5 align-middle" />
          {stats.simulated} of {stats.total} call{stats.total === 1 ? "" : "s"}{" "}
          shown as <em className="not-italic text-ink-2">simulated</em> — Atlas
          is running in mock mode. Trace mirrors the live MCP path.
        </footer>
      ) : null}
    </aside>
  );
}

// ── Stats helpers ────────────────────────────────────────────────────

function deriveStats(calls: McpCall[]): {
  total: number;
  totalMs: number;
  simulated: number;
  products: Set<string>;
} {
  let totalMs = 0;
  let simulated = 0;
  const products = new Set<string>();
  for (const c of calls) {
    totalMs += c.duration_ms || 0;
    if (c.simulated) simulated += 1;
    products.add(toolToProduct(c.tool));
  }
  return { total: calls.length, totalMs, simulated, products };
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-panel-2 border border-rule rounded px-2 py-2">
      <div className="font-mono text-[9px] tracking-[0.16em] uppercase text-ink-dim mb-0.5">
        {label}
      </div>
      <div className="font-mono text-[15px] font-semibold text-ink tabular-nums">
        {value}
      </div>
    </div>
  );
}

// ── Module chips ─────────────────────────────────────────────────────

function ModuleChip({ progress }: { progress: ModuleProgress }) {
  const tone: Record<typeof progress.status, string> = {
    running: "border-accent text-accent",
    success: "border-emerald-500/60 text-emerald-400",
    partial: "border-amber-500/60 text-amber-400",
    failed: "border-rose-500/60 text-rose-400",
  };
  const glyph: Record<typeof progress.status, string> = {
    running: "⟳",
    success: "✓",
    partial: "◐",
    failed: "✗",
  };
  return (
    <span
      className={`inline-flex items-center gap-1.5 font-mono text-[10px] tracking-[0.12em] uppercase border rounded px-2 py-0.5 ${tone[progress.status]}`}
    >
      <span className={progress.status === "running" ? "animate-spin inline-block" : ""}>
        {glyph[progress.status]}
      </span>
      {progress.title}
    </span>
  );
}

// ── Row ──────────────────────────────────────────────────────────────

function CallRow({ call }: { call: McpCall }) {
  const tool = TOOL_META[call.tool] ?? FALLBACK_TOOL(call.tool);
  const flag = pickFlag(call);
  const argSummary = summarizeArgs(call);
  const moduleMeta = call.module
    ? MODULE_META[call.module as ModuleName] ?? { title: call.module, track: "", purpose: "", tools: [] }
    : null;
  const ok = call.ok && !call.error;

  return (
    <li
      className={`flex items-center gap-2.5 px-2.5 py-2 rounded border text-[12px] transition-all ${
        call.simulated
          ? "border-dashed border-rule/70 bg-panel-2/40"
          : "border-rule bg-panel-2"
      } ${!ok ? "border-rose-500/40" : ""}`}
      title={call.error ?? call.result_preview ?? ""}
    >
      {/* Status */}
      <span
        className={`font-mono text-[12px] w-3.5 text-center flex-shrink-0 ${
          ok ? "text-emerald-400" : "text-rose-400"
        }`}
      >
        {ok ? "✓" : "✗"}
      </span>

      {/* Tool icon */}
      <span className="text-[15px] w-5 text-center flex-shrink-0 leading-none">
        {tool.icon}
      </span>

      {/* Label */}
      <span className="font-mono text-[11px] tracking-[0.06em] text-ink-2 flex-shrink-0 min-w-[110px]">
        {tool.label}
      </span>

      {/* Args */}
      <span className="font-mono text-[11px] text-ink-dim truncate flex-1 min-w-0">
        {flag ? <span className="mr-1 not-italic">{flag}</span> : null}
        {argSummary}
      </span>

      {/* Module badge */}
      {moduleMeta ? (
        <span className="font-mono text-[9px] tracking-[0.12em] uppercase text-ink-dim border border-rule rounded px-1.5 py-0.5 flex-shrink-0">
          {moduleMeta.title}
        </span>
      ) : null}

      {/* Duration pill */}
      <span
        className={`font-mono text-[10px] tabular-nums px-1.5 py-0.5 rounded flex-shrink-0 ${
          call.duration_ms < 1000
            ? "text-emerald-400 bg-emerald-500/10"
            : call.duration_ms < 3000
            ? "text-amber-400 bg-amber-500/10"
            : "text-rose-400 bg-rose-500/10"
        }`}
      >
        {formatDuration(call.duration_ms)}
      </span>
    </li>
  );
}

// ── Tool metadata ────────────────────────────────────────────────────

interface ToolMeta {
  icon: string;
  label: string;
  product: string;
}

const TOOL_META: Record<string, ToolMeta> = {
  search_engine: { icon: "🌐", label: "SERP · search", product: "serp" },
  scrape_as_markdown: { icon: "🔓", label: "Unlocker · fetch", product: "unlocker" },
  scraping_browser_navigate: { icon: "🖥️", label: "Browser · navigate", product: "browser" },
  scraping_browser_get_text: { icon: "📄", label: "Browser · extract", product: "browser" },
  scraping_browser_screenshot: { icon: "📷", label: "Browser · screenshot", product: "browser" },
  web_data_linkedin_job_listings: { icon: "💼", label: "Scraper · jobs", product: "scraper_api" },
};

const FALLBACK_TOOL = (tool: string): ToolMeta => ({
  icon: "·",
  label: tool,
  product: "other",
});

function toolToProduct(tool: string): string {
  return (TOOL_META[tool] ?? FALLBACK_TOOL(tool)).product;
}

// ── Arg summarization ────────────────────────────────────────────────

function summarizeArgs(call: McpCall): string {
  const args = call.args || {};
  const url = typeof args.url === "string" ? args.url : null;
  if (url) return truncateUrl(url);
  const query = typeof args.query === "string" ? args.query : null;
  if (query) return `"${truncate(query, 60)}"`;
  const target = typeof args.target === "string" ? args.target : null;
  if (target) return truncateUrl(target);
  return JSON.stringify(args).slice(0, 60);
}

function truncateUrl(url: string, max = 56): string {
  try {
    const u = new URL(url);
    const path = u.pathname === "/" ? "" : u.pathname;
    const compact = `${u.hostname}${path}`;
    return compact.length > max ? compact.slice(0, max - 1) + "…" : compact;
  } catch {
    return truncate(url, max);
  }
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

function formatDuration(ms: number): string {
  if (!ms || ms < 1) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

// ── Country flag inference ───────────────────────────────────────────
//
// Maps the ``country`` arg (TruePrice's per-region routing) or hostname
// tld hints to a flag emoji. The flag emoji uses regional indicator
// codepoints — works in all modern browsers.

function pickFlag(call: McpCall): string | null {
  const args = call.args || {};
  const country = typeof args.country === "string" ? args.country.toLowerCase() : null;
  if (country && country.length === 2) return flagEmoji(country);
  return null;
}

function flagEmoji(iso2: string): string {
  const A = 0x1f1e6;
  const code = iso2.toLowerCase();
  if (code.length !== 2) return "";
  return String.fromCodePoint(
    A + (code.charCodeAt(0) - 97),
    A + (code.charCodeAt(1) - 97),
  );
}
