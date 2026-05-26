"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type {
  AltDataData,
  SignalData,
  TruePriceData,
} from "./types";

// ── Color tokens (synced with tailwind.config.ts) ─────────────────
const COLOR = {
  ink: "#e7ecf3",
  inkDim: "#8a94a4",
  rule: "#232938",
  ruleSoft: "#1b2030",
  panel: "#11141b",
  accent: "#d6b25c",
  high: "#ff9c4a",
  note: "#4dd1ff",
  crit: "#ff4d61",
  info: "#7d8896",
};

const tooltipBox = {
  background: COLOR.panel,
  border: `1px solid ${COLOR.rule}`,
  borderRadius: 6,
  color: COLOR.ink,
  fontFamily: "ui-monospace, SFMono-Regular, monospace",
  fontSize: 12,
  padding: "6px 10px",
};

// ── TruePrice: Δ vs baseline horizontal bars ──────────────────────

export function TruePriceDeltaChart({ data }: { data: TruePriceData }) {
  const rows = data.regions.map((r) => ({
    region: `${r.region}`,
    fullRegion: r.region_name,
    delta: r.delta_pct,
    usd: r.true_usd,
    polarity: r.delta_pct > 0 ? "up" : r.delta_pct < 0 ? "down" : "zero",
  }));
  const height = Math.max(180, rows.length * 38);

  return (
    <div className="bg-panel-2 border border-rule rounded-md p-4">
      <div className="font-mono text-[10px] tracking-[0.13em] uppercase text-ink-dim mb-3">
        Δ vs US baseline · per region
      </div>
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 60, left: 4, bottom: 4 }}>
          <CartesianGrid stroke={COLOR.ruleSoft} strokeDasharray="2 2" horizontal={false} />
          <XAxis
            type="number"
            stroke={COLOR.inkDim}
            tick={{ fontSize: 10, fontFamily: "ui-monospace", fill: COLOR.inkDim }}
            tickFormatter={(v) => `${v > 0 ? "+" : ""}${v}%`}
            domain={["dataMin - 2", "dataMax + 2"]}
          />
          <YAxis
            type="category"
            dataKey="region"
            stroke={COLOR.inkDim}
            tick={{ fontSize: 11, fontFamily: "ui-monospace", fill: COLOR.ink }}
            width={56}
          />
          <Tooltip
            cursor={{ fill: COLOR.ruleSoft }}
            contentStyle={tooltipBox}
            formatter={(_v, _n, row) => {
              const r = (row as { payload: (typeof rows)[number] }).payload;
              return [
                `${r.delta > 0 ? "+" : ""}${r.delta.toFixed(1)}% · $${r.usd.toFixed(2)}`,
                r.fullRegion,
              ];
            }}
          />
          <Bar dataKey="delta" radius={[0, 3, 3, 0]}>
            {rows.map((r, i) => (
              <Cell
                key={i}
                fill={
                  r.polarity === "up"
                    ? COLOR.high
                    : r.polarity === "down"
                      ? COLOR.note
                      : COLOR.inkDim
                }
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// ── Signal: roles by family ───────────────────────────────────────

export function SignalFamilyChart({ data }: { data: SignalData }) {
  const rows = Object.entries(data.by_family)
    .map(([family, count]) => ({ family, count }))
    .sort((a, b) => b.count - a.count);
  const height = Math.max(180, rows.length * 26);

  return (
    <div className="bg-panel-2 border border-rule rounded-md p-4">
      <div className="font-mono text-[10px] tracking-[0.13em] uppercase text-ink-dim mb-3">
        Roles by family · {data.total_postings} total · velocity {data.velocity_ratio.toFixed(2)}× baseline
      </div>
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 30, left: 4, bottom: 4 }}>
          <CartesianGrid stroke={COLOR.ruleSoft} strokeDasharray="2 2" horizontal={false} />
          <XAxis
            type="number"
            stroke={COLOR.inkDim}
            tick={{ fontSize: 10, fontFamily: "ui-monospace", fill: COLOR.inkDim }}
            allowDecimals={false}
          />
          <YAxis
            type="category"
            dataKey="family"
            stroke={COLOR.inkDim}
            tick={{ fontSize: 11, fontFamily: "ui-monospace", fill: COLOR.ink }}
            width={110}
          />
          <Tooltip
            cursor={{ fill: COLOR.ruleSoft }}
            contentStyle={tooltipBox}
            formatter={(v: number) => [`${v} postings`, "count"]}
          />
          <Bar dataKey="count" fill={COLOR.note} radius={[0, 3, 3, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// ── Signal: region recent vs prior ────────────────────────────────

export function SignalRegionChart({ data }: { data: SignalData }) {
  const rows = Object.keys(data.by_region)
    .map((k) => ({
      region: k,
      recent: data.recent_by_region[k] ?? 0,
      prior: data.older_by_region[k] ?? 0,
    }))
    .sort((a, b) => b.recent + b.prior - (a.recent + a.prior));
  const height = Math.max(220, rows.length * 44);

  return (
    <div className="bg-panel-2 border border-rule rounded-md p-4">
      <div className="font-mono text-[10px] tracking-[0.13em] uppercase text-ink-dim mb-3">
        Postings by region · current vs prior window
      </div>
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 30, left: 4, bottom: 4 }}>
          <CartesianGrid stroke={COLOR.ruleSoft} strokeDasharray="2 2" horizontal={false} />
          <XAxis
            type="number"
            stroke={COLOR.inkDim}
            tick={{ fontSize: 10, fontFamily: "ui-monospace", fill: COLOR.inkDim }}
            allowDecimals={false}
          />
          <YAxis
            type="category"
            dataKey="region"
            stroke={COLOR.inkDim}
            tick={{ fontSize: 11, fontFamily: "ui-monospace", fill: COLOR.ink }}
            width={80}
          />
          <Tooltip cursor={{ fill: COLOR.ruleSoft }} contentStyle={tooltipBox} />
          <Bar dataKey="recent" name="recent 30d" fill={COLOR.note} radius={[0, 3, 3, 0]} />
          <Bar dataKey="prior" name="prior 30-60d" fill={COLOR.accent} radius={[0, 3, 3, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// ── AltData: composite score meter ────────────────────────────────

export function AltDataMeter({ data }: { data: AltDataData }) {
  const pct = Math.max(0, Math.min(1, data.composite_score));
  const markerLeft = `${pct * 100}%`;
  const labelColor =
    data.composite_label === "momentum"
      ? COLOR.note
      : data.composite_label === "distress"
        ? COLOR.high
        : COLOR.inkDim;

  return (
    <div className="bg-panel-2 border border-rule rounded-md p-4">
      <div className="font-mono text-[10px] tracking-[0.13em] uppercase text-ink-dim mb-2">
        Composite gauge · 0.45 distress · 0.55 momentum
      </div>
      <div className="relative h-9 my-2">
        {/* track */}
        <div className="absolute inset-x-0 top-1/2 h-3 -translate-y-1/2 rounded bg-panel border border-rule" />
        {/* distress zone */}
        <div
          className="absolute top-1/2 h-3 -translate-y-1/2 rounded-l opacity-40"
          style={{ left: 0, width: "45%", background: COLOR.high }}
        />
        {/* momentum zone */}
        <div
          className="absolute top-1/2 h-3 -translate-y-1/2 rounded-r opacity-40"
          style={{ left: "55%", width: "45%", background: COLOR.note }}
        />
        {/* marker */}
        <div
          className="absolute top-0 -translate-x-1/2 flex flex-col items-center"
          style={{ left: markerLeft }}
        >
          <div
            className="w-0 h-0"
            style={{
              borderLeft: "5px solid transparent",
              borderRight: "5px solid transparent",
              borderTop: `8px solid ${COLOR.accent}`,
            }}
          />
          <div className="w-0.5 h-8" style={{ background: COLOR.accent }} />
        </div>
      </div>
      <div className="flex justify-between font-mono text-[10px] text-ink-dim mt-1">
        <span>0.00</span>
        <span>0.45</span>
        <span style={{ color: labelColor, fontWeight: 700 }}>
          {data.composite_score.toFixed(2)} · {data.composite_label.toUpperCase()}
        </span>
        <span>0.55</span>
        <span>1.00</span>
      </div>
    </div>
  );
}
