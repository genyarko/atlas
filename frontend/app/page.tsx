"use client";

import { useCallback, useRef, useState } from "react";
import { BriefView } from "@/components/brief/BriefView";
import { TranscriptRail, type RailPhase } from "@/components/brief/TranscriptRail";
import type {
  AskResponse,
  McpCall,
  ModuleProgress,
} from "@/components/brief/types";

const SAMPLE_QUERIES = [
  {
    track: "GTM",
    text: "Run a full competitive brief on Linear. I'm evaluating them vs Jira for our 250-person engineering team.",
  },
  {
    track: "Finance",
    text: "Pre-earnings signal scan on Datadog. Anything material in the last 30 days?",
  },
  {
    track: "Security",
    text: "Scan for brand exposure on AcmeCorp. Flag impersonation and credential leaks.",
  },
];

const API = process.env.NEXT_PUBLIC_ATLAS_API || "http://localhost:8000";

export default function Page() {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Live trace state — updated as SSE events stream in.
  const [calls, setCalls] = useState<McpCall[]>([]);
  const [modules, setModules] = useState<ModuleProgress[]>([]);
  const [phase, setPhase] = useState<RailPhase>("idle");
  const abortRef = useRef<AbortController | null>(null);

  const submit = useCallback(
    async (q?: string) => {
      const text = (q ?? question).trim();
      if (!text || loading) return;

      // Reset state.
      setLoading(true);
      setError(null);
      setResult(null);
      setCalls([]);
      setModules([]);
      setPhase("streaming");

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const res = await fetch(`${API}/api/ask/stream`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: text }),
          signal: controller.signal,
        });
        if (!res.ok || !res.body)
          throw new Error(`API ${res.status}: ${await res.text()}`);

        await consumeSSE(res.body, (event, data) => {
          if (event === "mcp") {
            const call = data as McpCall;
            setCalls((prev) => [...prev, call]);
          } else if (event === "module_start") {
            const m = data as { module: string; title: string; track: string; rationale: string };
            setModules((prev) => upsertModule(prev, { ...m, status: "running" }));
          } else if (event === "module_done") {
            const m = data as {
              module: string;
              status: string;
              findings: number;
              confidence: number;
              duration_ms: number;
              error: string | null;
            };
            setModules((prev) =>
              prev.map((p) =>
                p.module === m.module
                  ? {
                      ...p,
                      status:
                        m.status === "success"
                          ? "success"
                          : m.status === "partial"
                          ? "partial"
                          : "failed",
                      findings: m.findings,
                      confidence: m.confidence,
                      duration_ms: m.duration_ms,
                      error: m.error,
                    }
                  : p,
              ),
            );
          } else if (event === "brief") {
            const response = data as AskResponse;
            setResult(response);
            setQuestion(text);
          } else if (event === "error") {
            const e = data as { message: string };
            setError(e.message);
          }
        });
        setPhase("complete");
      } catch (e: unknown) {
        if ((e as DOMException)?.name === "AbortError") return;
        setError(e instanceof Error ? e.message : String(e));
        setPhase("complete");
      } finally {
        setLoading(false);
        abortRef.current = null;
      }
    },
    [loading, question],
  );

  const resetAll = () => {
    abortRef.current?.abort();
    setResult(null);
    setQuestion("");
    setCalls([]);
    setModules([]);
    setPhase("idle");
    setError(null);
  };

  return (
    <main className="max-w-[1280px] mx-auto px-6 py-10">
      {/* Top brand strip */}
      <header className="flex items-baseline justify-between border-b-2 border-accent pb-3.5 mb-9">
        <div className="flex items-baseline gap-3.5">
          <span className="font-mono text-[13px] font-bold tracking-[0.22em] text-accent uppercase">
            Atlas
          </span>
          <span className="w-3.5 h-px bg-accent" />
          <span className="font-mono text-[11px] tracking-[0.18em] text-ink-dim uppercase">
            Ground Truth Brief
          </span>
        </div>
        <span className="font-mono text-[10px] tracking-[0.15em] uppercase text-ink-dim">
          v0.1 · day 8 polish
        </span>
      </header>

      {!result && phase === "idle" ? (
        <>
          {/* Hero */}
          <section className="mb-10 max-w-5xl">
            <h1 className="font-serif text-[42px] leading-[1.1] font-semibold tracking-tight mb-3">
              The intelligence platform that sees through what your tools can&apos;t.
            </h1>
            <p className="text-ink-2 text-[15px] max-w-[680px] leading-relaxed">
              Atlas takes any enterprise intelligence question and produces a{" "}
              <em className="text-accent not-italic font-semibold">Ground Truth Brief</em> by
              invoking specialized modules over Bright Data&apos;s MCP-exposed web infrastructure.
              Live demo runs three queries across GTM, Finance, and Security tracks.
            </p>
          </section>

          {/* Query input */}
          <section className="mb-8 max-w-5xl">
            <label className="block font-mono text-[10px] tracking-[0.18em] uppercase text-ink-dim mb-2">
              Question
            </label>
            <textarea
              className="w-full bg-panel border border-rule rounded-md p-3.5 text-ink text-[15px]
                         focus:outline-none focus:border-accent transition resize-y min-h-[120px]"
              placeholder="Ask any enterprise intelligence question…"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
              }}
            />
            <div className="flex items-center justify-between mt-3">
              <div className="text-ink-dim text-xs font-mono">
                ⌘/Ctrl + Enter to submit
              </div>
              <button
                onClick={() => submit()}
                disabled={loading || !question.trim()}
                className="bg-accent text-black px-6 py-2.5 rounded-md font-semibold text-sm
                           disabled:opacity-40 disabled:cursor-not-allowed hover:bg-accent-2 transition"
              >
                {loading ? "Synthesizing brief…" : "Run brief"}
              </button>
            </div>
          </section>

          {/* Demo queries */}
          <section className="mb-10 max-w-5xl">
            <div className="font-mono text-[10px] tracking-[0.18em] uppercase text-ink-dim mb-3">
              Locked demo queries · 3 tracks
            </div>
            <div className="grid gap-2.5">
              {SAMPLE_QUERIES.map((q) => (
                <button
                  key={q.text}
                  onClick={() => submit(q.text)}
                  disabled={loading}
                  className="text-left bg-panel border border-rule rounded-md px-4 py-3
                             hover:border-accent hover:bg-panel-2 transition disabled:opacity-50
                             flex items-center gap-4"
                >
                  <span className="font-mono text-[9.5px] tracking-[0.16em] uppercase text-accent border border-accent rounded px-2 py-0.5 flex-shrink-0">
                    {q.track}
                  </span>
                  <span className="text-sm text-ink leading-relaxed">{q.text}</span>
                </button>
              ))}
            </div>
          </section>
        </>
      ) : null}

      {error ? (
        <div className="bg-crit/10 border border-crit/40 text-crit rounded-md p-3.5 mb-6 font-mono text-sm">
          {error}
        </div>
      ) : null}

      {/* While streaming OR after a brief is rendered, switch to a 2-column
          layout: brief / loading panel on the left, transcript rail on the right. */}
      {phase !== "idle" ? (
        <div className="grid lg:grid-cols-[minmax(0,1fr)_380px] gap-6">
          <div className="min-w-0">
            {loading && !result ? (
              <div className="bg-panel border border-rule rounded-md p-6 flex items-center gap-4 min-h-[200px]">
                <span className="inline-block w-3 h-3 rounded-full bg-accent animate-pulse" />
                <div>
                  <div className="font-mono text-[11px] tracking-[0.16em] uppercase text-accent mb-1">
                    Synthesizing
                  </div>
                  <div className="text-ink-dim text-[13px]">
                    Planner → MCP modules → Synthesizer. Trace updating live →
                  </div>
                </div>
              </div>
            ) : null}

            {result ? (
              <>
                <div className="flex justify-end mb-3 gap-3">
                  <button
                    onClick={resetAll}
                    className="font-mono text-[11px] text-ink-dim border border-rule rounded px-3 py-1 hover:text-accent hover:border-accent transition"
                  >
                    ← New query
                  </button>
                </div>
                <BriefView
                  brief={result.brief}
                  htmlUrl={result.html_url}
                  pdfUrl={result.pdf_url}
                  apiBase={API}
                />
              </>
            ) : null}
          </div>

          {/* Rail */}
          <div className="lg:sticky lg:top-6 lg:self-start lg:max-h-[calc(100vh-3rem)] overflow-hidden">
            <TranscriptRail
              phase={phase}
              calls={calls}
              modules={modules}
            />
          </div>
        </div>
      ) : null}
    </main>
  );
}

// ── SSE consumer ──────────────────────────────────────────────────────
//
// FastAPI's StreamingResponse with text/event-stream emits standard SSE
// frames. We parse manually rather than using EventSource because
// EventSource is GET-only, and we want POST so the question body stays
// out of URL logs.

async function consumeSSE(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: string, data: unknown) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep = buffer.indexOf("\n\n");
    while (sep !== -1) {
      const block = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      parseBlock(block, onEvent);
      sep = buffer.indexOf("\n\n");
    }
  }
}

function parseBlock(
  block: string,
  onEvent: (event: string, data: unknown) => void,
): void {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return;
  try {
    onEvent(event, JSON.parse(dataLines.join("\n")));
  } catch {
    // Non-JSON payload — ignore silently.
  }
}

function upsertModule(
  prev: ModuleProgress[],
  next: ModuleProgress,
): ModuleProgress[] {
  const existing = prev.findIndex((p) => p.module === next.module);
  if (existing === -1) return [...prev, next];
  const out = prev.slice();
  out[existing] = { ...out[existing], ...next };
  return out;
}
