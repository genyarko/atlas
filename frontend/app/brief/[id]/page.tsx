"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { BriefView } from "@/components/brief/BriefView";
import { TranscriptRail } from "@/components/brief/TranscriptRail";
import type {
  Brief,
  McpCall,
  TranscriptResponse,
} from "@/components/brief/types";

const API = process.env.NEXT_PUBLIC_ATLAS_API || "http://localhost:8000";

export default function BriefPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;
  const [brief, setBrief] = useState<Brief | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [calls, setCalls] = useState<McpCall[]>([]);

  useEffect(() => {
    if (!id) return;
    const run = async () => {
      try {
        const [briefRes, transcriptRes] = await Promise.all([
          fetch(`${API}/api/briefs/${id}`),
          fetch(`${API}/api/briefs/${id}/transcript`),
        ]);
        if (!briefRes.ok)
          throw new Error(`API ${briefRes.status}: ${await briefRes.text()}`);
        setBrief((await briefRes.json()) as Brief);
        if (transcriptRes.ok) {
          const data = (await transcriptRes.json()) as TranscriptResponse;
          setCalls(data.calls);
        }
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : String(e));
      }
    };
    run();
  }, [id]);

  if (error) {
    return (
      <main className="max-w-3xl mx-auto px-6 py-10">
        <div className="bg-crit/10 border border-crit/40 text-crit rounded-md p-3.5 font-mono text-sm">
          {error}
        </div>
      </main>
    );
  }

  if (!brief) {
    return (
      <main className="max-w-3xl mx-auto px-6 py-10">
        <div className="bg-panel border border-rule rounded-md p-6 flex items-center gap-4">
          <span className="inline-block w-3 h-3 rounded-full bg-accent animate-pulse" />
          <div className="font-mono text-[11px] tracking-[0.16em] uppercase text-accent">
            Loading brief…
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="max-w-[1280px] mx-auto px-6 py-10">
      <div className="grid lg:grid-cols-[minmax(0,1fr)_380px] gap-6">
        <div className="min-w-0">
          <BriefView
            brief={brief}
            htmlUrl={`/api/briefs/${brief.id}.html`}
            pdfUrl={`/api/briefs/${brief.id}.pdf`}
            apiBase={API}
          />
        </div>
        {calls.length > 0 ? (
          <div className="lg:sticky lg:top-6 lg:self-start lg:max-h-[calc(100vh-3rem)] overflow-hidden">
            <TranscriptRail phase="complete" calls={calls} modules={[]} />
          </div>
        ) : null}
      </div>
    </main>
  );
}
