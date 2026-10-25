/** Decoders for the backend responses in ./types.ts. */

import {
  array,
  boolean,
  count,
  decimalString,
  isoUtc,
  keyed,
  literal,
  nonNegativeNumber,
  nullable,
  object,
  positiveInt,
  rate,
  string,
  type Decoder,
} from "../validate";
import {
  COST_UNKNOWN_REASONS,
  TRACE_PROVENANCES,
  USAGE_PROVENANCES,
  USAGE_SOURCES,
  type Cost,
  type Filters,
  type InferenceErrors,
  type Latency,
  type LogicalCalls,
  type ModelGroup,
  type ModelsCompare,
  type Overview,
  type Split,
  type TokenSubset,
  type Tokens,
  type TraceErrors,
  type TrendItem,
  type Window,
} from "./types";

// Backend bounds (ARCHITECTURE.md §5.2): 50 error types, 200 price versions, 200 groups.
// A 31-day window has at most 32 daily or 49 hourly buckets; 800 leaves headroom.
const MAX_ERROR_TYPES = 50;
const MAX_PRICE_VERSIONS = 200;
const MAX_GROUPS = 200;
const MAX_TREND_BUCKETS = 800;

const label = string(256);
const ms = nullable(nonNegativeNumber);

function split<T, P extends string>(keys: readonly P[], decoder: Decoder<T>): Decoder<Split<T, P>> {
  return object<Split<T, P>>({ total: decoder, by_provenance: keyed(keys, decoder) });
}

const window: Decoder<Window> = object<Window>({ from: isoUtc, to: isoUtc });

const filters: Decoder<Filters> = object<Filters>({
  model: nullable(label),
  status: nullable(literal("ok", "error")),
  prompt_version: nullable(string(64)),
  provenance: nullable(literal(...TRACE_PROVENANCES)),
});

const latency: Decoder<Latency> = object<Latency>({
  n: count,
  p50_ms: ms,
  p95_ms: ms,
  p99_ms: ms,
  low_sample: object({ p95: boolean, p99: boolean }),
});

const traceErrors: Decoder<TraceErrors> = object<TraceErrors>({ n: count, error_traces: count, rate: nullable(rate) });

const inferenceErrors: Decoder<InferenceErrors> = object<InferenceErrors>({
  n: count,
  failed: count,
  succeeded: count,
  rate: nullable(rate),
});

const logicalCalls: Decoder<LogicalCalls> = object<LogicalCalls>({
  value: nullable(count),
  attempts_without_call_id: count,
  n: count,
});

const subset: Decoder<TokenSubset> = object<TokenSubset>({ sum: nullable(count), calls_reporting: count });

const tokens: Decoder<Tokens> = object<Tokens>({
  n: count,
  input_tokens: nullable(count),
  calls_with_unknown_input_tokens: count,
  output_tokens: nullable(count),
  calls_with_unknown_output_tokens: count,
  subsets: object({
    cached_input_tokens: subset,
    cache_write_input_tokens: subset,
    reasoning_output_tokens: subset,
  }),
});

const unknownByReason = keyed(COST_UNKNOWN_REASONS, count);

const cost: Decoder<Cost> = object<Cost>({
  n: count,
  currency: literal("USD"),
  known_cost_usd: nullable(decimalString),
  computed_cost_calls: count,
  unknown_cost_calls: count,
  unknown_cost_by_reason: unknownByReason,
  cost_complete: nullable(boolean),
  known_cost_per_call_usd: nullable(decimalString),
});

const trendItem: Decoder<TrendItem> = object<TrendItem>({
  start: isoUtc,
  traces: count,
  error_traces: count,
  inference_attempts: count,
  failed_attempts: count,
  known_cost_usd: nullable(decimalString),
  unknown_cost_calls: count,
});

export const overviewDecoder: Decoder<Overview> = object<Overview>({
  project: positiveInt,
  window,
  filters,
  provenance_label: label,
  provenance_verified: literal(false),
  traces: object<Overview["traces"]>({
    count: split(TRACE_PROVENANCES, count),
    error_rate: split(TRACE_PROVENANCES, traceErrors),
    latency: (v, path) => ({
      ...split(TRACE_PROVENANCES, latency)(v, path),
      ...object({ traces_without_root: count })(v, path),
    }),
  }),
  inference: object<Overview["inference"]>({
    attempts: split(USAGE_PROVENANCES, count),
    logical_calls: split(USAGE_PROVENANCES, logicalCalls),
    errors: (v, path) => ({
      ...split(USAGE_PROVENANCES, inferenceErrors)(v, path),
      ...object({
        by_error_type: array(object({ error_type: nullable(label), count }), MAX_ERROR_TYPES),
        by_error_type_truncated: boolean,
      })(v, path),
    }),
    latency: split(USAGE_PROVENANCES, latency),
    time_to_first_chunk: object({ available: boolean, reason: string(64), detail: string(256) }),
    tokens: split(USAGE_PROVENANCES, tokens),
    cost: split(USAGE_PROVENANCES, cost),
    price_versions: object({
      items: array(
        object({ provider: label, model: label, price_version: nullable(label), calls: count }),
        MAX_PRICE_VERSIONS,
      ),
      truncated: boolean,
    }),
    usage_source: split(USAGE_PROVENANCES, keyed(USAGE_SOURCES, count)),
  }),
  trend: object({ bucket: literal("hour", "day"), items: array(trendItem, MAX_TREND_BUCKETS) }),
});

const modelGroup: Decoder<ModelGroup> = object<ModelGroup>({
  provider: label,
  requested_model: nullable(label),
  response_model: nullable(label),
  prompt_version: nullable(string(64)),
  attempts: count,
  errors: object({ failed: count, succeeded: count, rate: nullable(rate) }),
  latency,
  cost: object({
    currency: literal("USD"),
    known_cost_usd: nullable(decimalString),
    computed_cost_calls: count,
    unknown_cost_calls: count,
    unknown_cost_by_reason: unknownByReason,
    cost_complete: boolean,
  }),
  by_provenance: keyed(USAGE_PROVENANCES, count),
});

export const modelsCompareDecoder: Decoder<ModelsCompare> = object<ModelsCompare>({
  project: positiveInt,
  window,
  filters,
  provenance_label: label,
  provenance_verified: literal(false),
  groups: array(modelGroup, MAX_GROUPS),
  groups_truncated: boolean,
  limit: positiveInt,
});
