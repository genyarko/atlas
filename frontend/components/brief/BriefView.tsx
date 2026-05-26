"use client";

import { ModuleBody } from "./modules";
import {
  FindingsList,
  SectionHeading,
  Sources,
  SubHeading,
} from "./primitives";
import { MODULE_META, type Brief, type ModuleName } from "./types";

export function BriefView({
  brief,
  htmlUrl,
  pdfUrl,
  apiBase,
}: {
  brief: Brief;
  htmlUrl: string;
  pdfUrl: string;
  apiBase: string;
}) {
  const html = apiBase + htmlUrl;
  const pdf = apiBase + pdfUrl;

  return (
    <article className="bg-panel border border-rule rounded-lg p-8 print:bg-white print:border-0 print:p-0">
      {/* Masthead */}
      <header className="flex items-center justify-between pb-3.5 border-b-2 border-accent">
        <div className="flex items-baseline gap-3.5">
          <span className="font-mono text-[13px] font-bold tracking-[0.22em] text-accent uppercase">
            Atlas
          </span>
          <span className="w-3.5 h-px bg-accent" />
          <span className="font-mono text-[11px] tracking-[0.18em] text-ink-dim uppercase">
            Ground Truth Brief
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="font-mono text-[10px] tracking-[0.1em] uppercase text-ink-dim">
            id · {brief.id}
          </span>
          <a
            href={html}
            target="_blank"
            rel="noopener noreferrer"
            className="font-mono text-[11px] text-note hover:underline"
          >
            HTML ↗
          </a>
          <a
            href={pdf}
            target="_blank"
            rel="noopener noreferrer"
            className="font-mono text-[11px] text-accent border border-accent rounded px-2 py-0.5 hover:bg-accent hover:text-black transition"
          >
            PDF ↓
          </a>
        </div>
      </header>

      {/* Cover */}
      <section className="mt-7 mb-7">
        <h1 className="font-serif text-[46px] leading-[1.05] font-semibold tracking-tight text-ink mb-2.5">
          {brief.subject}
        </h1>
        <p className="font-serif italic text-[17px] leading-relaxed text-ink-2 m-0 mb-4 max-w-[720px] border-l-2 border-accent pl-3.5 py-1">
          &ldquo;{brief.question.text}&rdquo;
        </p>

        <div className="grid grid-cols-5 border border-rule rounded-md bg-panel-2 overflow-hidden">
          <MetaCell label="Generated" value={new Date(brief.generated_at).toLocaleString()} />
          <MetaCell label="Subject" value={brief.subject} />
          <MetaCell label="Intent" value={brief.plan.intent.toUpperCase()} />
          <MetaCell
            label="Confidence"
            value={`${brief.confidence_score.toFixed(2)} · ${brief.sections.length} mod`}
            highlight={brief.confidence_score >= 0.75}
          />
          <MetaCell
            label="Mode"
            value={brief.mode.toUpperCase()}
            highlight={brief.mode === "live"}
            highlightClass="text-note"
          />
        </div>
      </section>

      {/* Executive summary */}
      <SectionHeading num="01" title="Executive Summary" />
      <p className="font-serif text-[18px] leading-relaxed text-ink m-0 p-5 bg-panel-2 border-l-[3px] border-accent rounded-r-md">
        {brief.executive_summary}
      </p>

      {/* Key findings */}
      {brief.key_findings.length > 0 ? (
        <>
          <SectionHeading
            num="02"
            title="Key Findings"
            trailing={`top ${brief.key_findings.length}`}
          />
          <FindingsList findings={brief.key_findings} />
        </>
      ) : null}

      {/* Modules */}
      <SectionHeading
        num="03"
        title="Module Detail"
        trailing={`${brief.sections.length} module${brief.sections.length === 1 ? "" : "s"}`}
      />
      <div className="space-y-4">
        {brief.sections.map((section) => {
          const meta = MODULE_META[section.module as ModuleName] ?? { title: section.title, track: "", purpose: "", tools: [] };
          return (
            <div
              key={section.module}
              className="bg-panel-2 border border-rule rounded-lg p-5"
            >
              <div className="flex items-baseline justify-between mb-1">
                <div className="flex items-baseline gap-2.5">
                  <span className="font-serif text-[22px] font-semibold text-ink">
                    {section.title}
                  </span>
                  <span className="font-mono text-[9.5px] tracking-[0.16em] uppercase text-accent border border-accent rounded px-1.5 py-px">
                    {meta.track}
                  </span>
                </div>
                <div className="font-mono text-[11px] text-ink-dim">
                  confidence{" "}
                  <strong className="text-ink font-semibold">
                    {section.confidence.toFixed(2)}
                  </strong>{" "}
                  · via {meta.tools.join(" + ")}
                </div>
              </div>
              <div className="font-sans italic text-[12.5px] text-ink-dim mb-3.5">
                {meta.purpose}
              </div>
              <p className="text-[15px] leading-relaxed text-ink m-0 mb-3.5">
                {section.summary}
              </p>

              <ModuleBody section={section} />

              {section.findings.length ? (
                <>
                  <SubHeading>Findings ({section.findings.length})</SubHeading>
                  <FindingsList findings={section.findings} />
                </>
              ) : null}

              <Sources sources={section.sources} />
            </div>
          );
        })}
      </div>

      {/* Plan trace */}
      <SectionHeading num="04" title="Plan Trace" />
      <div className="font-serif italic text-[13.5px] text-ink-2 p-3.5 px-4.5 bg-panel-2 border-l-2 border-rule rounded-r-md">
        <span className="block font-mono not-italic text-[10px] tracking-[0.14em] uppercase text-ink-dim mb-1">
          Planner reasoning
        </span>
        {brief.plan.reasoning}
      </div>

      {/* Colophon */}
      <footer className="mt-12 pt-4 border-t border-rule flex justify-between font-mono text-[10.5px] text-ink-dim tracking-[0.08em]">
        <span>
          Atlas · Ground Truth Brief · <span className="text-accent">powered by Bright Data MCP</span>
        </span>
        <span>{new Date(brief.generated_at).toISOString().slice(0, 16).replace("T", " ")} UTC</span>
      </footer>
    </article>
  );
}

function MetaCell({
  label,
  value,
  highlight = false,
  highlightClass = "text-accent",
}: {
  label: string;
  value: string;
  highlight?: boolean;
  highlightClass?: string;
}) {
  return (
    <div className="px-4 py-3 border-r border-[#1b2030] last:border-r-0">
      <div className="font-mono text-[9.5px] tracking-[0.15em] uppercase text-ink-dim mb-1">
        {label}
      </div>
      <div className={`font-mono text-[13px] ${highlight ? highlightClass : "text-ink"}`}>
        {value}
      </div>
    </div>
  );
}
