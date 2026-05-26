"use client";

import {
  AltDataMeter,
  SignalFamilyChart,
  SignalRegionChart,
  TruePriceDeltaChart,
} from "./charts";
import { Caption, FindingsList, SubHeading } from "./primitives";
import type {
  AltDataData,
  BriefSection,
  ExposureData,
  FilingChange,
  FilingData,
  SignalData,
  TruePriceData,
  VisualData,
  VisualSuspect,
} from "./types";

// ── TruePrice ─────────────────────────────────────────────────────

export function TruePriceSection({ section }: { section: BriefSection }) {
  const data = section.data as unknown as TruePriceData;
  if (!data.regions?.length) return null;

  return (
    <>
      <SubHeading>Comparative true pricing</SubHeading>
      <table className="atlas-table w-full">
        <thead>
          <tr>
            <th>Region</th>
            <th>Plan</th>
            <th className="num">Sticker (local)</th>
            <th className="num">True cart (local)</th>
            <th className="num">True (USD)</th>
            <th className="num">Δ vs baseline</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {data.regions.map((r) => (
            <tr key={r.region} className={r.delta_pct === 0 ? "baseline" : ""}>
              <td>
                <strong>{r.region}</strong> — {r.region_name}
              </td>
              <td>{r.plan}</td>
              <td className="num">
                {r.sticker_local.toFixed(2)} {r.currency}
              </td>
              <td className="num">
                {r.true_local.toFixed(2)} {r.currency}
              </td>
              <td className="num">${r.true_usd.toFixed(2)}</td>
              <td
                className={`num ${
                  r.delta_pct > 0
                    ? "text-high font-bold"
                    : r.delta_pct < 0
                      ? "text-note font-bold"
                      : "text-ink-dim"
                }`}
              >
                {r.delta_pct === 0
                  ? "baseline"
                  : `${r.delta_pct > 0 ? "+" : ""}${r.delta_pct.toFixed(1)}%`}
              </td>
              <td>{r.notes}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <Caption>
        FX snapshot {data.fx_snapshot_date} · mode {data.mode}
        {data.failed_regions?.length ? ` · failed: ${data.failed_regions.join(", ")}` : ""}
      </Caption>
      <TruePriceDeltaChart data={data} />
    </>
  );
}

// ── Signal ────────────────────────────────────────────────────────

export function SignalSection({ section }: { section: BriefSection }) {
  const data = section.data as unknown as SignalData;
  if (!data.by_region) return null;

  return (
    <>
      <SubHeading>Hiring composition · last {data.lookback_days} days</SubHeading>
      <SignalFamilyChart data={data} />
      <SubHeading>Geographic shift · recent 30d vs prior 30-60d</SubHeading>
      <SignalRegionChart data={data} />
      {data.top_examples?.length ? (
        <>
          <SubHeading>Representative postings</SubHeading>
          <table className="atlas-table w-full">
            <thead>
              <tr>
                <th>Role</th>
                <th>Family</th>
                <th>Location</th>
                <th>Region</th>
              </tr>
            </thead>
            <tbody>
              {data.top_examples.slice(0, 5).map((ex, i) => (
                <tr key={i}>
                  <td>
                    <a
                      href={ex.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-note hover:underline"
                    >
                      {ex.title}
                    </a>
                  </td>
                  <td>
                    <span className="font-mono text-[10px] tracking-[0.08em] uppercase bg-[#7d8896] text-[#0a0f15] px-2 py-0.5 rounded">
                      {ex.family}
                    </span>
                  </td>
                  <td>{ex.location}</td>
                  <td>{ex.region}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
    </>
  );
}

// ── AltData ───────────────────────────────────────────────────────

export function AltDataSection({ section }: { section: BriefSection }) {
  const data = section.data as unknown as AltDataData;
  if (data.composite_score === undefined) return null;
  const label = data.composite_label;
  const labelColor =
    label === "momentum"
      ? "text-note"
      : label === "distress"
        ? "text-high"
        : "text-ink-dim";

  return (
    <>
      <div className="grid grid-cols-[120px_1fr] gap-4 items-center bg-panel-2 border border-rule rounded-md p-4 my-2">
        <div className="text-center py-2 border-r border-[#1b2030]">
          <div className="font-serif text-[38px] font-semibold leading-none tracking-tight text-ink">
            {data.composite_score.toFixed(2)}
          </div>
          <div className={`font-mono text-[10px] tracking-[0.14em] uppercase mt-1 ${labelColor}`}>
            {label}
          </div>
        </div>
        <div>
          <Caption>Composite alt-data drivers</Caption>
          <ul className="list-disc list-inside text-[13px] text-ink-2 m-0 pl-0 space-y-0.5">
            {data.drivers.slice(0, 5).map((d, i) => (
              <li key={i} className="text-ink">
                {d}
              </li>
            ))}
          </ul>
        </div>
      </div>

      <AltDataMeter data={data} />

      {data.sources ? (
        <>
          <SubHeading>Per-source trend</SubHeading>
          <table className="atlas-table w-full">
            <thead>
              <tr>
                <th>Source</th>
                <th className="num">Recent / Prior</th>
                <th className="num">Rating prior → recent</th>
                <th className="num">Δ rating</th>
                <th className="num">Velocity</th>
                <th>Top complaint</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(data.sources).map(([name, s]) => (
                <tr key={name}>
                  <td>
                    <strong>{name.charAt(0).toUpperCase() + name.slice(1)}</strong>
                  </td>
                  <td className="num">
                    {s.recent_30d} / {s.prior_30_60d}
                  </td>
                  <td className="num">
                    {s.avg_rating_prior.toFixed(2)} → {s.avg_rating_recent.toFixed(2)}
                  </td>
                  <td
                    className={`num ${
                      s.rating_delta > 0
                        ? "text-note font-bold"
                        : s.rating_delta < 0
                          ? "text-high font-bold"
                          : "text-ink-dim"
                    }`}
                  >
                    {s.rating_delta > 0 ? "+" : ""}
                    {s.rating_delta.toFixed(2)}
                  </td>
                  <td className="num">{s.velocity_ratio.toFixed(2)}×</td>
                  <td>
                    {s.top_complaint ? (
                      <>
                        <span className="font-mono text-[10px] tracking-[0.08em] uppercase bg-note text-[#03161e] px-2 py-0.5 rounded">
                          {s.top_complaint}
                        </span>{" "}
                        ({s.complaint_clusters[s.top_complaint] ?? 0})
                      </>
                    ) : (
                      <span className="text-ink-dim">none clustered</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
    </>
  );
}

// ── Filing ────────────────────────────────────────────────────────

const MAT_BORDER = ["", "border-[#4a5161]", "border-[#7d8896]", "border-note", "border-high", "border-crit"];
const MAT_LABEL = ["info", "info", "low", "notable", "high", "critical"];
const MAT_COLOR = [
  "text-ink-dim",
  "text-ink-dim",
  "text-ink-dim",
  "text-note",
  "text-high",
  "text-crit",
];

export function FilingSection({ section }: { section: BriefSection }) {
  const data = section.data as unknown as FilingData;
  const diff = data.filing_diff;
  if (!diff) return null;

  return (
    <>
      {diff.current ? (
        <>
          <SubHeading>Filing under review</SubHeading>
          <table className="atlas-table w-full">
            <thead>
              <tr>
                <th>Type</th>
                <th>Period</th>
                <th>Filed</th>
                <th>Accession</th>
                <th>Prior comp.</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>
                  <strong>{diff.current.filing_type}</strong>
                </td>
                <td>{diff.current.fiscal_period || "—"}</td>
                <td>{diff.current.filed_at}</td>
                <td>
                  <a
                    href={diff.current.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="font-mono text-[11.5px] text-note hover:underline"
                  >
                    {diff.current.accession_no}
                  </a>
                </td>
                <td>
                  {diff.prior
                    ? `${diff.prior.filing_type} · ${diff.prior.filed_at}`
                    : "—"}
                </td>
              </tr>
            </tbody>
          </table>
        </>
      ) : null}

      {diff.changes?.length ? (
        <>
          <SubHeading>Material changes · materiality 1–5</SubHeading>
          <div className="flex flex-col gap-2.5">
            {diff.changes.map((c: FilingChange, i) => (
              <div
                key={i}
                className={`p-3.5 bg-panel-2 border border-rule border-l-[3px] ${MAT_BORDER[c.materiality]} rounded-r-md`}
              >
                <div className="flex justify-between items-baseline mb-1">
                  <span className="font-mono text-[10px] tracking-[0.14em] uppercase text-ink-dim">
                    {c.kind} · materiality {c.materiality}/5
                  </span>
                  <span
                    className={`font-mono text-[10px] tracking-[0.14em] uppercase font-bold ${MAT_COLOR[c.materiality]}`}
                  >
                    {MAT_LABEL[c.materiality]}
                  </span>
                </div>
                <div className="font-serif text-[16px] font-semibold my-1">{c.headline}</div>
                {c.excerpt ? (
                  <div className="font-serif italic text-[13.5px] text-ink-2 pl-2.5 border-l-2 border-[#1b2030] my-1.5">
                    {c.excerpt}
                  </div>
                ) : null}
                {c.rationale ? (
                  <div className="text-[12.5px] text-ink-dim">{c.rationale}</div>
                ) : null}
              </div>
            ))}
          </div>
        </>
      ) : null}
    </>
  );
}

// ── Visual ────────────────────────────────────────────────────────

const VERDICT_COLOR: Record<string, string> = {
  critical: "text-crit",
  high: "text-high",
  notable: "text-note",
  low: "text-ink-dim",
};

export function VisualSection({ section }: { section: BriefSection }) {
  const data = section.data as unknown as VisualData;
  if (!data.suspects?.length) return null;

  return (
    <>
      <SubHeading>Suspect domains · ranked by verdict</SubHeading>
      <table className="atlas-table w-full">
        <thead>
          <tr>
            <th>Suspect</th>
            <th>Verdict</th>
            <th className="num">Similarity</th>
            <th className="num">Anomalies</th>
            <th>Top observation</th>
          </tr>
        </thead>
        <tbody>
          {data.suspects.map((row: VisualSuspect) => (
            <tr key={row.suspect_url}>
              <td>
                <a
                  href={row.suspect_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-mono text-[11.5px] text-note hover:underline break-words"
                >
                  {row.suspect_url}
                </a>
              </td>
              <td>
                <span
                  className={`font-mono text-[10px] tracking-[0.13em] uppercase font-bold ${VERDICT_COLOR[row.verdict] ?? "text-ink"}`}
                >
                  {row.verdict}
                </span>
              </td>
              <td className="num">{row.similarity.toFixed(2)}</td>
              <td className="num">{row.anomaly_count}</td>
              <td>{row.anomalies[0]?.description ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <SubHeading>Anomaly detail</SubHeading>
      {data.suspects.map((row: VisualSuspect, i) =>
        row.anomalies.length ? (
          <div
            key={i}
            className={`p-3.5 bg-panel-2 border border-rule border-l-[3px] ${
              row.verdict === "critical"
                ? "border-crit"
                : row.verdict === "high"
                  ? "border-high"
                  : row.verdict === "notable"
                    ? "border-note"
                    : "border-[#7d8896]"
            } rounded-r-md mb-2.5`}
          >
            <div className="font-mono text-[10px] tracking-[0.14em] uppercase text-ink-dim">
              {row.verdict} · sim {row.similarity.toFixed(2)} · {row.anomaly_count} anomalies
            </div>
            <div className="font-mono text-[12px] text-ink mt-1 break-words">{row.suspect_url}</div>
            <ul className="mt-1.5 pl-4 text-[13px] list-disc">
              {row.anomalies.map((a, j) => (
                <li key={j}>
                  <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-accent mr-1">
                    {a.kind}
                  </span>
                  {a.description}
                </li>
              ))}
            </ul>
          </div>
        ) : null,
      )}
      <Caption>
        Rubric: ≥3 anomalies + sim≥0.80 ⇒ HIGH · mode {data.mode}
        {data.dropped?.length ? ` · dropped ${data.dropped.length}` : ""}
      </Caption>
    </>
  );
}

// ── Exposure ──────────────────────────────────────────────────────

const SEV_BORDER_MAP: Record<string, string> = {
  critical: "border-crit",
  high: "border-high",
  notable: "border-note",
  info: "border-[#7d8896]",
};

const SEV_COLOR_MAP: Record<string, string> = {
  critical: "text-crit",
  high: "text-high",
  notable: "text-note",
  info: "text-ink-dim",
};

export function ExposureSection({ section }: { section: BriefSection }) {
  const data = section.data as unknown as ExposureData;
  const scan = data.exposure_scan;
  if (!scan) return null;

  return (
    <>
      <SubHeading>
        Exposure tally · {scan.record_count} record{scan.record_count === 1 ? "" : "s"} across {scan.channels.length} channel{scan.channels.length === 1 ? "" : "s"}
      </SubHeading>
      {scan.records.length ? (
        <div className="flex flex-col gap-2.5">
          {scan.records.map((rec, i) => (
            <div
              key={i}
              className={`p-3.5 bg-panel-2 border border-rule border-l-[3px] ${SEV_BORDER_MAP[rec.severity] ?? "border-rule"} rounded-r-md`}
            >
              <div className="flex justify-between items-baseline mb-1">
                <span className="font-mono text-[10px] tracking-[0.14em] uppercase text-ink-dim">
                  {rec.channel} · {rec.kind}
                </span>
                <span
                  className={`font-mono text-[10px] tracking-[0.14em] uppercase font-bold ${SEV_COLOR_MAP[rec.severity]}`}
                >
                  {rec.severity}
                </span>
              </div>
              <a
                href={rec.location_url}
                target="_blank"
                rel="noopener noreferrer"
                className="font-mono text-[12px] text-note hover:underline break-words"
              >
                {rec.location_title}
              </a>
              {rec.excerpt ? (
                <div className="font-serif italic text-[13.5px] text-ink-2 pl-2.5 border-l-2 border-[#1b2030] my-1.5">
                  {rec.excerpt}
                </div>
              ) : null}
              {rec.rationale ? (
                <div className="text-[12.5px] text-ink-dim">{rec.rationale}</div>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}

      {scan.dorks?.length ? (
        <>
          <SubHeading>SERP dorks executed</SubHeading>
          <table className="atlas-table w-full">
            <thead>
              <tr>
                <th>#</th>
                <th>Query</th>
              </tr>
            </thead>
            <tbody>
              {scan.dorks.map((q, i) => (
                <tr key={i}>
                  <td className="num">{i + 1}</td>
                  <td className="font-mono text-[12px] break-words">{q}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
    </>
  );
}

// ── Dispatcher ────────────────────────────────────────────────────

export function ModuleBody({ section }: { section: BriefSection }) {
  switch (section.module) {
    case "trueprice":
      return <TruePriceSection section={section} />;
    case "signal":
      return <SignalSection section={section} />;
    case "altdata":
      return <AltDataSection section={section} />;
    case "filing":
      return <FilingSection section={section} />;
    case "visual":
      return <VisualSection section={section} />;
    case "exposure":
      return <ExposureSection section={section} />;
    default:
      return null;
  }
}
