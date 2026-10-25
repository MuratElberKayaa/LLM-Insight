/**
 * Response types of the backend read API, field for field as served by
 * `backend/app/services/metrics.py` (ARCHITECTURE.md §5.2). Nothing here is computed by the
 * frontend: counts are counts, unknown values are `null`, money is a decimal string.
 */

export const USAGE_PROVENANCES = ["real", "synthetic", "unknown"] as const;
export const TRACE_PROVENANCES = ["real", "synthetic", "mixed", "unknown"] as const;
export const COST_UNKNOWN_REASONS = ["price_missing", "usage_missing", "usage_inconsistent"] as const;
export const USAGE_SOURCES = ["provider_reported", "estimated", "unknown"] as const;

export type UsageProvenance = (typeof USAGE_PROVENANCES)[number];
export type TraceProvenance = (typeof TRACE_PROVENANCES)[number];
export type CostUnknownReason = (typeof COST_UNKNOWN_REASONS)[number];
export type UsageSource = (typeof USAGE_SOURCES)[number];

/** `{total, by_provenance}`: `total` is computed by PostgreSQL, never summed from buckets. */
export interface Split<T, P extends string> {
  total: T;
  by_provenance: Record<P, T>;
}

export interface Latency {
  n: number;
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
  low_sample: { p95: boolean; p99: boolean };
}

export interface TraceErrors {
  n: number;
  error_traces: number;
  rate: number | null;
}

export interface InferenceErrors {
  n: number;
  failed: number;
  succeeded: number;
  rate: number | null;
}

export interface LogicalCalls {
  value: number | null;
  attempts_without_call_id: number;
  n: number;
}

export interface TokenSubset {
  sum: number | null;
  calls_reporting: number;
}

export interface Tokens {
  n: number;
  input_tokens: number | null;
  calls_with_unknown_input_tokens: number;
  output_tokens: number | null;
  calls_with_unknown_output_tokens: number;
  subsets: {
    cached_input_tokens: TokenSubset;
    cache_write_input_tokens: TokenSubset;
    reasoning_output_tokens: TokenSubset;
  };
}

export interface Cost {
  n: number;
  currency: "USD";
  known_cost_usd: string | null;
  computed_cost_calls: number;
  unknown_cost_calls: number;
  unknown_cost_by_reason: Record<CostUnknownReason, number>;
  cost_complete: boolean | null;
  known_cost_per_call_usd: string | null;
}

export interface TrendItem {
  start: string;
  traces: number;
  error_traces: number;
  inference_attempts: number;
  failed_attempts: number;
  known_cost_usd: string | null;
  unknown_cost_calls: number;
}

export interface Filters {
  model: string | null;
  status: "ok" | "error" | null;
  prompt_version: string | null;
  provenance: TraceProvenance | null;
}

export interface Window {
  from: string;
  to: string;
}

export interface Overview {
  project: number;
  window: Window;
  filters: Filters;
  provenance_label: string;
  provenance_verified: false;
  traces: {
    count: Split<number, TraceProvenance>;
    error_rate: Split<TraceErrors, TraceProvenance>;
    latency: Split<Latency, TraceProvenance> & { traces_without_root: number };
  };
  inference: {
    attempts: Split<number, UsageProvenance>;
    logical_calls: Split<LogicalCalls, UsageProvenance>;
    errors: Split<InferenceErrors, UsageProvenance> & {
      by_error_type: { error_type: string | null; count: number }[];
      by_error_type_truncated: boolean;
    };
    latency: Split<Latency, UsageProvenance>;
    time_to_first_chunk: { available: boolean; reason: string; detail: string };
    tokens: Split<Tokens, UsageProvenance>;
    cost: Split<Cost, UsageProvenance>;
    price_versions: {
      items: { provider: string; model: string; price_version: string | null; calls: number }[];
      truncated: boolean;
    };
    usage_source: Split<Record<UsageSource, number>, UsageProvenance>;
  };
  trend: { bucket: "hour" | "day"; items: TrendItem[] };
}

export interface ModelGroup {
  provider: string;
  requested_model: string | null;
  response_model: string | null;
  prompt_version: string | null;
  attempts: number;
  errors: { failed: number; succeeded: number; rate: number | null };
  latency: Latency;
  /** M08/M09 per group (Phase 3C, additive): unknown sums are null and counted. */
  tokens: Tokens;
  /** M14 per group: how the token counts were obtained. */
  usage_source: Record<UsageSource, number>;
  cost: {
    currency: "USD";
    known_cost_usd: string | null;
    computed_cost_calls: number;
    unknown_cost_calls: number;
    unknown_cost_by_reason: Record<CostUnknownReason, number>;
    cost_complete: boolean;
  };
  by_provenance: Record<UsageProvenance, number>;
}

export interface ModelsCompare {
  project: number;
  window: Window;
  filters: Filters;
  provenance_label: string;
  provenance_verified: false;
  groups: ModelGroup[];
  groups_truncated: boolean;
  limit: number;
}

/** `GET /api/v1/projects` (Phase 3C): id order, bounded, no key material. */
export interface ProjectItem {
  id: number;
  /** As stored (1–100 characters, admin-chosen): rendered as text only. */
  name: string;
  created_at: string;
}

export interface ProjectList {
  items: ProjectItem[];
  truncated: boolean;
  limit: number;
}

/** `GET /health/ready` (not under /api/v1, no auth). */
export type BackendHealth = "ready" | "not_ready" | "unreachable";
