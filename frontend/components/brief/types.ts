// Shared types for the brief viewer + components. Mirrors the
// backend's Pydantic models exactly — anything that round-trips
// through /api/ask or /api/briefs/:id lives here.

export type Severity = "info" | "notable" | "high" | "critical";
export type Verdict = "low" | "notable" | "high" | "critical";
export type ModuleName =
  | "trueprice"
  | "signal"
  | "filing"
  | "altdata"
  | "visual"
  | "exposure"
  | "investor";

export interface Finding {
  statement: string;
  evidence: string[];
  severity: Severity;
}

export interface Source {
  url: string;
  title: string;
  via: string;
  accessed_at: string;
}

export interface TruePriceRow {
  region: string;
  region_name: string;
  plan: string;
  sticker_local: number;
  true_local: number;
  true_usd: number;
  currency: string;
  delta_pct: number;
  notes: string;
}

export interface TruePriceData {
  subject: string;
  regions: TruePriceRow[];
  fx_snapshot_date: string;
  mode: string;
  failed_regions?: string[];
}

export interface SignalExample {
  title: string;
  location: string;
  url: string;
  family: string;
  seniority: string;
  region: string;
}

export interface SignalData {
  subject: string;
  lookback_days: number;
  total_postings: number;
  recent_30d: number;
  older_60d: number;
  velocity_ratio: number;
  by_family: Record<string, number>;
  by_region: Record<string, number>;
  recent_by_region: Record<string, number>;
  older_by_region: Record<string, number>;
  by_seniority: Record<string, number>;
  top_examples: SignalExample[];
  news_items?: { title: string; url: string; snippet: string }[];
  mode: string;
}

export interface AltDataSourceTrend {
  total: number;
  recent_30d: number;
  prior_30_60d: number;
  avg_rating_recent: number;
  avg_rating_prior: number;
  rating_delta: number;
  velocity_ratio: number;
  complaint_clusters: Record<string, number>;
  top_complaint: string;
  representative_urls: string[];
}

export interface AltDataData {
  subject: string;
  mode: string;
  composite_score: number;
  composite_label: "momentum" | "neutral" | "distress";
  drivers: string[];
  sources: Record<string, AltDataSourceTrend>;
}

export interface FilingChange {
  kind: "added" | "removed" | "modified";
  headline: string;
  excerpt: string;
  materiality: number;
  rationale: string;
}

export interface FilingInfo {
  accession_no: string;
  filing_type: string;
  filed_at: string;
  fiscal_period: string;
  url: string;
  index_url: string;
}

export interface FilingDiff {
  current: FilingInfo | null;
  prior: FilingInfo | null;
  changes: FilingChange[];
  max_materiality: number;
  summary: string;
}

export interface FilingData {
  subject: string;
  mode: string;
  filing_diff: FilingDiff;
  max_materiality: number;
  change_count: number;
}

export interface VisualAnomaly {
  kind: string;
  description: string;
}

export interface VisualSuspect {
  suspect_url: string;
  suspect_title: string;
  legit_url: string;
  similarity: number;
  verdict: Verdict;
  anomalies: VisualAnomaly[];
  anomaly_count: number;
  reasoning: string;
}

export interface VisualData {
  subject: string;
  brand_url: string;
  controlled: boolean;
  mode: string;
  suspects: VisualSuspect[];
  suspect_count: number;
  high_count: number;
  dropped: string[];
}

export interface ExposureRecord {
  channel: string;
  kind: string;
  severity: Severity;
  location_url: string;
  location_title: string;
  excerpt: string;
  rationale: string;
  via: string;
}

export interface ExposureScan {
  subject: string;
  domain: string;
  dorks: string[];
  candidates: { url: string; title: string; snippet: string; channel: string; discovery_query: string }[];
  records: ExposureRecord[];
  record_count: number;
  critical_count: number;
  max_severity: Severity;
  channels: string[];
  dropped: string[];
}

export interface ExposureData {
  subject: string;
  domain: string;
  mode: string;
  exposure_scan: ExposureScan;
  max_severity: Severity;
  critical_count: number;
  channels: string[];
}

export interface BriefSection {
  module: ModuleName;
  title: string;
  summary: string;
  findings: Finding[];
  sources: Source[];
  confidence: number;
  // Module-specific payload; type-narrow by checking `module`.
  data: Record<string, unknown>;
}

export interface Brief {
  id: string;
  subject: string;
  question: { id: string; text: string };
  plan: { question_id: string; intent: string; reasoning: string };
  executive_summary: string;
  key_findings: Finding[];
  sections: BriefSection[];
  confidence_score: number;
  generated_at: string;
  mode: "mock" | "live";
}

export interface AskResponse {
  brief: Brief;
  html_url: string;
  pdf_url: string;
  markdown: string;
}

// ── MCP transcript ─────────────────────────────────────────────────
//
// Mirrors backend MCPToolCall. Powers the live infrastructure rail.

export interface McpCall {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  ok: boolean;
  duration_ms: number;
  result_preview?: string;
  error?: string | null;
  module?: ModuleName | string | null;
  simulated: boolean;
  started_at: string;
}

export type ModuleProgressStatus = "running" | "success" | "partial" | "failed";

export interface ModuleProgress {
  module: ModuleName | string;
  title: string;
  track?: string;
  rationale?: string;
  status: ModuleProgressStatus;
  findings?: number;
  confidence?: number;
  duration_ms?: number;
  error?: string | null;
}

export interface TranscriptResponse {
  brief_id: string;
  calls: McpCall[];
  count: number;
}

// Module catalog (mirrors backend MODULE_CATALOG)
export const MODULE_META: Record<ModuleName, { title: string; track: string; purpose: string; tools: string[] }> = {
  trueprice: {
    title: "TruePrice",
    track: "GTM",
    purpose: "True purchase cost via checkout completion across geographies.",
    tools: ["scraping_browser", "residential_proxies"],
  },
  signal: {
    title: "Signal",
    track: "GTM + Finance",
    purpose: "Strategic intent inferred from hiring, exec moves, tech stack.",
    tools: ["web_scraper_api", "serp_api"],
  },
  filing: {
    title: "Filing",
    track: "Finance",
    purpose: "Materiality-scored diffs of SEC, regulatory, and patent filings.",
    tools: ["web_unlocker"],
  },
  altdata: {
    title: "AltData",
    track: "Finance",
    purpose: "Composite distress/momentum from reviews and alt-data signals.",
    tools: ["web_scraper_api"],
  },
  visual: {
    title: "Visual",
    track: "Security",
    purpose: "Brand-impersonation detection via vision-diff of suspect domains.",
    tools: ["serp_api", "scraping_browser"],
  },
  exposure: {
    title: "Exposure",
    track: "Security",
    purpose: "Credentials, PII, and doxx surface across the open web.",
    tools: ["serp_api", "web_unlocker"],
  },
  investor: {
    title: "Investor",
    track: "GTM + Finance",
    purpose: "Active VC firms and partners investing in a target sector — fundable contacts and portfolio signals.",
    tools: ["web_scraper_api", "serp_api"],
  },
};
