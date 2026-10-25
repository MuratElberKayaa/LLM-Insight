/**
 * Response types of `GET /api/v1/traces` and `GET /api/v1/traces/{trace_id}`, field for
 * field as served by `backend/app/api/traces.py` (ARCHITECTURE.md §5.1). Nothing here is
 * computed by the frontend.
 *
 * Representation choices (lossless by construction):
 * - nanosecond times and durations stay decimal strings (`*_unix_nano`, `duration_ns`);
 * - token counts are decimal digit strings, because the column is BIGINT and a JSON number
 *   above 2^53 would otherwise be rounded (`lib/json.ts`);
 * - money stays a decimal string; unknown stays `null`, never 0.
 */

import type { LosslessInt } from "../json";
import type { FailureReview, ReviewSummary } from "./review-types";
import type { CostUnknownReason, TraceProvenance, UsageProvenance, UsageSource, Window } from "./types";

export const TRACE_STATUSES = ["ok", "error"] as const;
export const SPAN_STATUSES = ["UNSET", "OK", "ERROR"] as const;
export const SPAN_KINDS = ["UNSPECIFIED", "INTERNAL", "SERVER", "CLIENT", "PRODUCER", "CONSUMER"] as const;
export const SPAN_ROLES = ["root", "child", "orphan"] as const;
export const OPERATION_NAMES = ["chat", "text_completion", "generate_content", "embeddings"] as const;
export const CALL_KEY_SOURCES = ["provider_response_id", "sdk_attempt", "span_fallback"] as const;

export type TraceStatus = (typeof TRACE_STATUSES)[number];
export type SpanStatus = (typeof SPAN_STATUSES)[number];
export type SpanKind = (typeof SPAN_KINDS)[number];
export type SpanRole = (typeof SPAN_ROLES)[number];
export type OperationName = (typeof OPERATION_NAMES)[number];
export type CallKeySource = (typeof CALL_KEY_SOURCES)[number];

/** An allowlisted attribute value as stored in JSONB (scalars and short arrays; bounded). Integers above 2^53 stay exact as `LosslessInt`. */
export type AttributeValue = string | number | boolean | null | LosslessInt | AttributeValue[] | { [key: string]: AttributeValue };

export interface TraceUsageSummary {
  inference_calls: number;
  input_tokens: string | null;
  output_tokens: string | null;
  calls_with_unknown_tokens: number;
  known_cost_usd: string | null;
  unknown_cost_calls: number;
  /**
   * Phase 3C (R40): every distinct provider/model combination of the trace's inference calls,
   * largest first, at most 10; `distinct` counts all of them. A trace can use several
   * providers and models, so no single value represents it. Null models were not reported.
   */
  models: TraceModels;
}

export interface TraceModel {
  provider: string;
  requested_model: string | null;
  response_model: string | null;
  calls: number;
}

export interface TraceModels {
  items: TraceModel[];
  distinct: number;
  truncated: boolean;
}

export interface TraceSummary {
  trace_id: string;
  service_name: string;
  started_at: string;
  ended_at: string;
  start_time_unix_nano: string;
  end_time_unix_nano: string;
  duration_ns: string;
  status: TraceStatus;
  prompt_version: string | null;
  provenance: TraceProvenance;
  provenance_verified: false;
  span_count: number;
  usage: TraceUsageSummary;
}

/** A list item (Phase 4B): human review counts tell reviewed and unreviewed traces apart. */
export interface TraceListItem extends TraceSummary {
  reviews: ReviewSummary;
}

export interface TraceList {
  items: TraceListItem[];
  next_cursor: string | null;
  window: Window;
}

export interface SpanRecord {
  span_id: string;
  parent_span_id: string | null;
  role: SpanRole;
  children: string[];
  name: string;
  kind: SpanKind;
  started_at: string;
  ended_at: string;
  start_time_unix_nano: string;
  end_time_unix_nano: string;
  duration_ns: string;
  status: SpanStatus;
  error_type: string | null;
  scope_name: string | null;
  scope_version: string | null;
  resource_attributes: Record<string, AttributeValue>;
  attributes: Record<string, AttributeValue>;
  attributes_schema_version: string;
  provenance: UsageProvenance;
  provenance_verified: false;
  server_dropped_attributes_count: number;
}

export interface UsageRecord {
  span_id: string;
  canonical_call_key: string;
  call_key_source: CallKeySource;
  call_id: string | null;
  attempt_id: number | null;
  provider_response_id: string | null;
  operation_name: OperationName;
  provider: string;
  requested_model: string | null;
  response_model: string | null;
  input_tokens: string | null;
  output_tokens: string | null;
  cached_input_tokens: string | null;
  cache_write_input_tokens: string | null;
  reasoning_output_tokens: string | null;
  usage_source: UsageSource;
  cost_usd: string | null;
  cost_status: "computed" | "unknown";
  cost_unknown_reason: CostUnknownReason | null;
  price_version: string | null;
  provenance: UsageProvenance;
  provenance_verified: false;
  started_at: string;
}

export interface TraceDetail {
  trace: TraceSummary;
  spans: SpanRecord[];
  spans_truncated: boolean;
  usage: UsageRecord[];
  usage_truncated: boolean;
  /** Human failure reviews of this trace (Phase 4B), oldest first, at most 200. */
  reviews: FailureReview[];
  reviews_truncated: boolean;
}
