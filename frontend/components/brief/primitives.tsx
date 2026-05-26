import type { Finding, Severity, Source } from "./types";

const SEV_DOT: Record<Severity, string> = {
  critical: "bg-crit",
  high: "bg-high",
  notable: "bg-note",
  info: "bg-[#7d8896]",
};

const SEV_BORDER: Record<Severity, string> = {
  critical: "border-crit",
  high: "border-high",
  notable: "border-note",
  info: "border-[#4a5161]",
};

const SEV_BADGE: Record<Severity, string> = {
  critical: "bg-crit text-[#1a0508]",
  high: "bg-high text-[#1a0d04]",
  notable: "bg-note text-[#03161e]",
  info: "bg-[#7d8896] text-[#0a0f15]",
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span
      className={`inline-flex items-center font-mono text-[9.5px] font-bold tracking-[0.14em] uppercase px-2 py-0.5 rounded ${SEV_BADGE[severity]}`}
    >
      {severity}
    </span>
  );
}

export function SeverityDot({ severity }: { severity: Severity }) {
  return (
    <span
      className={`inline-block w-2 h-2 rounded-full mr-2 align-middle ${SEV_DOT[severity]}`}
    />
  );
}

export function FindingRow({ finding }: { finding: Finding }) {
  return (
    <li
      className={`flex gap-3 items-start py-2.5 px-3.5 bg-panel border-l-[3px] ${SEV_BORDER[finding.severity]} rounded-r-md mb-1.5`}
    >
      <SeverityBadge severity={finding.severity} />
      <span className="text-[14px] leading-snug text-ink">{finding.statement}</span>
    </li>
  );
}

export function FindingsList({ findings }: { findings: Finding[] }) {
  return (
    <ul className="list-none p-0 m-0">
      {findings.map((f, i) => (
        <FindingRow key={i} finding={f} />
      ))}
    </ul>
  );
}

export function SectionHeading({
  num,
  title,
  trailing,
}: {
  num: string;
  title: string;
  trailing?: React.ReactNode;
}) {
  return (
    <h2 className="font-mono text-[11px] tracking-[0.22em] uppercase text-accent border-b border-rule pb-1.5 mt-9 mb-3.5 flex items-center justify-between">
      <span>{title}</span>
      <span className="text-ink-dim text-[10px]">
        § {num}
        {trailing ? <span className="ml-2">· {trailing}</span> : null}
      </span>
    </h2>
  );
}

export function SubHeading({ children }: { children: React.ReactNode }) {
  return (
    <h4 className="font-mono text-[10px] tracking-[0.16em] uppercase text-ink-dim mt-4 mb-2">
      {children}
    </h4>
  );
}

export function Sources({ sources }: { sources: Source[] }) {
  if (!sources.length) return null;
  return (
    <div className="text-[12.5px] text-ink-dim pt-3 mt-3 border-t border-[#1b2030]">
      <span className="font-mono text-[10px] tracking-[0.13em] uppercase text-ink-dim mr-1.5">
        Sources ({sources.length})
      </span>
      {sources.map((s, i) => (
        <span key={i}>
          <a
            href={s.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-note hover:underline"
          >
            {s.title}
          </a>
          <span className="inline-block font-mono text-[9.5px] text-ink-dim px-1.5 py-px ml-1 border border-rule rounded">
            {s.via}
          </span>
          {i < sources.length - 1 ? <span className="text-ink-dim mx-1">·</span> : null}
        </span>
      ))}
    </div>
  );
}

export function Caption({ children }: { children: React.ReactNode }) {
  return (
    <p className="font-mono text-[10px] tracking-[0.1em] uppercase text-ink-dim mt-1 mb-3">
      {children}
    </p>
  );
}
