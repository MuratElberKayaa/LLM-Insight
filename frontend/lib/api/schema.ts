/** Decoders for the backend responses in ./types.ts. */

import {
  DecodeError,
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
  REFERENCE_NOT_PRICED_REASONS,
  REFERENCE_TIERS,
  REFERENCE_UNAVAILABLE_REASONS,
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
  type TimeToFirstChunk,
  type ReferenceListPrice,
  type ProjectList,
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

const baseFilters = object<Omit<Filters, "provider">>({
  model: nullable(label),
  status: nullable(literal("ok", "error")),
  prompt_version: nullable(string(64)),
  provenance: nullable(literal(...TRACE_PROVENANCES)),
});

/** `provider` is additive (Phase 6C-C): absent → null, so a backend without it decodes, and
 * `echoMatches` then refuses a provider-filtered response that did not apply the filter. */
const filters: Decoder<Filters> = (v, path) => {
  const base = baseFilters(v, path);
  const raw = typeof v === "object" && v !== null && Object.hasOwn(v, "provider") ? (v as { provider: unknown }).provider : null;
  return { ...base, provider: nullable(string(64))(raw, `${path}.provider`) };
};

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

/** M20 (Phase 10B, ADR-55): a value exactly when recorded; the reason says why not. */
export const ttfcDecoder: Decoder<TimeToFirstChunk> = (v, path) => {
  const t = object<TimeToFirstChunk>({
    available: boolean,
    reason: nullable(literal("no_streaming_attempts", "not_recorded")),
    detail: nullable(string(256)),
    streaming_attempts: count,
    n: count,
    p50_ms: ms,
    p95_ms: ms,
    low_sample: object({ p95: boolean }),
  })(v, path);
  if (t.available !== t.n > 0 || t.available !== (t.reason === null) || (t.reason === null) !== (t.detail === null)) throw new DecodeError(path, "available exactly with a recorded value, otherwise a reason");
  if ((t.p50_ms === null) !== (t.n === 0) || (t.p95_ms === null) !== (t.n === 0)) throw new DecodeError(path, "percentiles exactly when recorded");
  if (t.reason === "no_streaming_attempts" && t.streaming_attempts !== 0) throw new DecodeError(`${path}.reason`, "no streaming attempts only when none was counted");
  if (t.reason === "not_recorded" && t.streaming_attempts === 0) throw new DecodeError(`${path}.reason`, "not recorded only when streaming attempts exist");
  if (t.low_sample.p95 !== t.n < 20) throw new DecodeError(`${path}.low_sample`, "low sample below 20 values");
  if (t.p50_ms !== null && t.p95_ms !== null && t.p50_ms > t.p95_ms) throw new DecodeError(`${path}.p50_ms`, "p50 at most p95");
  return t;
};

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
    time_to_first_chunk: split(USAGE_PROVENANCES, ttfcDecoder),
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

const httpsUrl: Decoder<string> = (v, path) => {
  const s = string(512)(v, path);
  if (!s.startsWith("https://")) throw new DecodeError(path, "an https URL");
  return s;
};

const referenceListPrice: Decoder<ReferenceListPrice> = object<ReferenceListPrice>({
  available: boolean,
  unavailable_reason: nullable(literal(...REFERENCE_UNAVAILABLE_REASONS)),
  label: literal("reference_not_billed"),
  currency: nullable(literal("USD")),
  tier: nullable(literal(...REFERENCE_TIERS)),
  catalogue_version: nullable(string(64)),
  verified_at: nullable(isoUtc),
  source_url: nullable(httpsUrl),
  reference_usd: nullable(decimalString),
  calls_priced: count,
  calls_not_priced: count,
  not_priced_by_reason: keyed(REFERENCE_NOT_PRICED_REASONS, count),
  stale: nullable(boolean),
});

const modelGroup: Decoder<ModelGroup> = object<ModelGroup>({
  provider: label,
  requested_model: nullable(label),
  response_model: nullable(label),
  prompt_version: nullable(string(64)),
  attempts: count,
  errors: object({ failed: count, succeeded: count, rate: nullable(rate) }),
  latency,
  time_to_first_chunk: ttfcDecoder,
  tokens,
  usage_source: keyed(USAGE_SOURCES, count),
  cost: object({
    currency: literal("USD"),
    known_cost_usd: nullable(decimalString),
    computed_cost_calls: count,
    unknown_cost_calls: count,
    unknown_cost_by_reason: unknownByReason,
    cost_complete: boolean,
  }),
  by_provenance: keyed(USAGE_PROVENANCES, count),
  reference_list_price: referenceListPrice,
});

/** Group-level consistency: what the backend counts per group must add up per group. */
const checkedModelGroup: Decoder<ModelGroup> = (v, path) => {
  const g = modelGroup(v, path);
  if (g.errors.failed + g.errors.succeeded !== g.attempts) throw new DecodeError(`${path}.errors`, "failed + succeeded = attempts");
  if (g.cost.computed_cost_calls + g.cost.unknown_cost_calls !== g.attempts) throw new DecodeError(`${path}.cost`, "computed + unknown cost calls = attempts");
  if (g.cost.cost_complete !== (g.cost.unknown_cost_calls === 0)) throw new DecodeError(`${path}.cost.cost_complete`, "complete exactly when no cost is unknown");
  if ((g.cost.known_cost_usd === null) !== (g.cost.computed_cost_calls === 0)) throw new DecodeError(`${path}.cost.known_cost_usd`, "a known cost exactly when a call was priced");
  if (g.tokens.n !== g.attempts || g.latency.n > g.attempts) throw new DecodeError(`${path}.tokens.n`, "sample sizes within the group");
  if (g.time_to_first_chunk.n > g.attempts || g.time_to_first_chunk.streaming_attempts > g.attempts) throw new DecodeError(`${path}.time_to_first_chunk`, "counts within the group");
  const r = g.reference_list_price;
  const rp = `${path}.reference_list_price`;
  if (r.available !== (r.reference_usd !== null) || r.available !== (r.unavailable_reason === null)) throw new DecodeError(rp, "an amount exactly when available");
  if (r.available !== (r.calls_priced > 0)) throw new DecodeError(`${rp}.calls_priced`, "priced calls exactly when available");
  if (r.calls_priced + r.calls_not_priced !== g.attempts) throw new DecodeError(rp, "priced + not priced calls = attempts");
  if (REFERENCE_NOT_PRICED_REASONS.reduce((s, k) => s + r.not_priced_by_reason[k], 0) !== r.calls_not_priced) throw new DecodeError(`${rp}.not_priced_by_reason`, "reasons add up to the calls not priced");
  const inCatalogue = r.unavailable_reason !== "not_in_reference_catalogue";
  for (const k of ["currency", "tier", "catalogue_version", "verified_at", "source_url", "stale"] as const) {
    if ((r[k] !== null) !== inCatalogue) throw new DecodeError(`${rp}.${k}`, "catalogue details exactly when the model is in the catalogue");
  }
  return g;
};

export const modelsCompareDecoder: Decoder<ModelsCompare> = object<ModelsCompare>({
  project: positiveInt,
  window,
  filters,
  provenance_label: label,
  provenance_verified: literal(false),
  groups: array(checkedModelGroup, MAX_GROUPS),
  groups_truncated: boolean,
  limit: positiveInt,
});

// Project names are 1–100 characters (database contract); in UTF-16 at most 200 code units.
const MAX_PROJECTS = 200;

export const projectListDecoder: Decoder<ProjectList> = object<ProjectList>({
  items: array(object({ id: positiveInt, name: string(200), created_at: isoUtc }), MAX_PROJECTS),
  truncated: boolean,
  limit: positiveInt,
});
