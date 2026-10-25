/** Decoders for the trace read API (./trace-types.ts, ARCHITECTURE.md §5.1). */

import { LosslessInt } from "../json";
import { parseNs } from "../nanos";
import {
  array,
  boolean,
  count,
  decimalString,
  DecodeError,
  isoUtc,
  literal,
  nullable,
  object,
  positiveInt,
  string,
  type Decoder,
} from "../validate";
import {
  CALL_KEY_SOURCES,
  OPERATION_NAMES,
  SPAN_KINDS,
  SPAN_ROLES,
  SPAN_STATUSES,
  TRACE_STATUSES,
  type AttributeValue,
  type SpanRecord,
  type TraceDetail,
  type TraceList,
  type TraceSummary,
  type TraceModel,
  type TraceModels,
  type TraceUsageSummary,
  type UsageRecord,
} from "./trace-types";
import { COST_UNKNOWN_REASONS, TRACE_PROVENANCES, USAGE_PROVENANCES, USAGE_SOURCES, type Window } from "./types";

// Backend bounds: list `limit` ≤ 200; detail ≤ 2000 spans and ≤ 2000 usage rows.
export const MAX_LIST_ITEMS = 200;
export const MAX_DETAIL_SPANS = 2000;
export const MAX_DETAIL_USAGE = 2000;
// The allowlist is small and arrays keep ≤ 16 items (TELEMETRY_CONTRACT.md §3); generous bounds.
const MAX_ATTRIBUTES = 256;
const MAX_ATTR_DEPTH = 3;
const MAX_ATTR_STRING = 4096;

const TRACE_ID_RE = /^[0-9a-f]{32}$/;
const SPAN_ID_RE = /^[0-9a-f]{16}$/;
const CURSOR_RE = /^[A-Za-z0-9_-]{1,128}$/;
const DIGITS_RE = /^\d{1,20}$/;

function pattern(re: RegExp, expected: string): Decoder<string> {
  return (v, path) => {
    if (typeof v !== "string" || !re.test(v)) throw new DecodeError(path, expected);
    return v;
  };
}

export const traceId = pattern(TRACE_ID_RE, "a 32-character lowercase hex trace id");
export const spanId = pattern(SPAN_ID_RE, "a 16-character lowercase hex span id");

/** int64 nanoseconds as a decimal string (the backend never sends them as JSON numbers). */
const nanos: Decoder<string> = (v, path) => {
  if (typeof v !== "string" || parseNs(v) === null) throw new DecodeError(path, "an int64 nanosecond decimal string");
  return v;
};

/** A non-negative BIGINT count: a safe JSON integer, or an exact larger integer from lib/json. */
export const bigCount: Decoder<string> = (v, path) => {
  if (typeof v === "number" && Number.isSafeInteger(v) && v >= 0) return String(v);
  if (v instanceof LosslessInt && DIGITS_RE.test(v.digits)) return v.digits;
  throw new DecodeError(path, "a non-negative integer");
};

const label = string(256);

function attributeValue(depth: number): Decoder<AttributeValue> {
  return (v, path) => {
    if (v === null || typeof v === "boolean") return v;
    if (typeof v === "number") {
      if (!Number.isFinite(v)) throw new DecodeError(path, "a finite number");
      return v;
    }
    if (v instanceof LosslessInt) return v;
    if (typeof v === "string") {
      if (v.length > MAX_ATTR_STRING) throw new DecodeError(path, `a string of at most ${MAX_ATTR_STRING} characters`);
      return v;
    }
    if (depth >= MAX_ATTR_DEPTH) throw new DecodeError(path, "a shallower attribute value");
    if (Array.isArray(v)) return array(attributeValue(depth + 1), MAX_ATTRIBUTES)(v, path);
    if (typeof v === "object") return attributeMap(depth + 1)(v, path);
    throw new DecodeError(path, "an attribute value");
  };
}

function attributeMap(depth: number): Decoder<Record<string, AttributeValue>> {
  return (v, path) => {
    if (typeof v !== "object" || v === null || Array.isArray(v) || v instanceof LosslessInt) throw new DecodeError(path, "an object");
    const entries = Object.entries(v as Record<string, unknown>);
    if (entries.length > MAX_ATTRIBUTES) throw new DecodeError(path, `at most ${MAX_ATTRIBUTES} attributes`);
    const out: Record<string, AttributeValue> = {};
    for (const [key, value] of entries) {
      if (key.length > 256) throw new DecodeError(path, "attribute keys of at most 256 characters");
      // defineProperty: a key such as "__proto__" stays an ordinary own property.
      Object.defineProperty(out, key, { value: attributeValue(depth)(value, `${path}.${key}`), enumerable: true, writable: true, configurable: true });
    }
    return out;
  };
}

export const MAX_MODELS_PER_TRACE = 10;

const traceModels: Decoder<TraceModels> = object<TraceModels>({
  items: array(
    object<TraceModel>({ provider: label, requested_model: nullable(label), response_model: nullable(label), calls: positiveInt }),
    MAX_MODELS_PER_TRACE,
  ),
  distinct: count,
  truncated: boolean,
});

const usageSummary: Decoder<TraceUsageSummary> = (v, path) => {
  const u = object<TraceUsageSummary>({
    inference_calls: count,
    input_tokens: nullable(bigCount),
    output_tokens: nullable(bigCount),
    calls_with_unknown_tokens: count,
    known_cost_usd: nullable(decimalString),
    unknown_cost_calls: count,
    models: traceModels,
  })(v, path);
  const m = u.models;
  const listed = m.items.reduce((sum, item) => sum + item.calls, 0);
  // The combinations partition the trace's calls: listed ones can only account for fewer.
  if (m.distinct < m.items.length || m.truncated !== m.distinct > m.items.length) throw new DecodeError(`${path}.models`, "a consistent combination count");
  if (listed > u.inference_calls || (!m.truncated && listed !== u.inference_calls)) throw new DecodeError(`${path}.models`, "calls matching inference_calls");
  return u;
};

const window: Decoder<Window> = object<Window>({ from: isoUtc, to: isoUtc });

export const traceSummaryDecoder: Decoder<TraceSummary> = (v, path) => {
  const t = object<TraceSummary>({
    trace_id: traceId,
    service_name: label,
    started_at: isoUtc,
    ended_at: isoUtc,
    start_time_unix_nano: nanos,
    end_time_unix_nano: nanos,
    duration_ns: nanos,
    status: literal(...TRACE_STATUSES),
    prompt_version: nullable(string(64)),
    provenance: literal(...TRACE_PROVENANCES),
    provenance_verified: literal(false),
    span_count: positiveInt,
    usage: usageSummary,
  })(v, path);
  if (BigInt(t.end_time_unix_nano) < BigInt(t.start_time_unix_nano)) throw new DecodeError(`${path}.end_time_unix_nano`, "an end at or after the start");
  return t;
};

export const traceListDecoder: Decoder<TraceList> = object<TraceList>({
  items: array(traceSummaryDecoder, MAX_LIST_ITEMS),
  next_cursor: nullable(pattern(CURSOR_RE, "an opaque cursor")),
  window,
});

const spanDecoder: Decoder<SpanRecord> = (v, path) => {
  const s = object<SpanRecord>({
    span_id: spanId,
    parent_span_id: nullable(spanId),
    role: literal(...SPAN_ROLES),
    children: array(spanId, MAX_DETAIL_SPANS),
    name: label,
    kind: literal(...SPAN_KINDS),
    started_at: isoUtc,
    ended_at: isoUtc,
    start_time_unix_nano: nanos,
    end_time_unix_nano: nanos,
    duration_ns: nanos,
    status: literal(...SPAN_STATUSES),
    error_type: nullable(label),
    scope_name: nullable(label),
    scope_version: nullable(string(64)),
    resource_attributes: attributeMap(0),
    attributes: attributeMap(0),
    attributes_schema_version: string(64),
    provenance: literal(...USAGE_PROVENANCES),
    provenance_verified: literal(false),
    server_dropped_attributes_count: count,
  })(v, path);
  if ((s.role === "root") !== (s.parent_span_id === null)) throw new DecodeError(`${path}.role`, "root exactly when there is no parent");
  if (BigInt(s.end_time_unix_nano) < BigInt(s.start_time_unix_nano)) throw new DecodeError(`${path}.end_time_unix_nano`, "an end at or after the start");
  return s;
};

const usageDecoder: Decoder<UsageRecord> = (v, path) => {
  const u = object<UsageRecord>({
    span_id: spanId,
    canonical_call_key: string(300),
    call_key_source: literal(...CALL_KEY_SOURCES),
    call_id: nullable(string(64)),
    attempt_id: nullable(positiveInt),
    provider_response_id: nullable(label),
    operation_name: literal(...OPERATION_NAMES),
    provider: string(64),
    requested_model: nullable(label),
    response_model: nullable(label),
    input_tokens: nullable(bigCount),
    output_tokens: nullable(bigCount),
    cached_input_tokens: nullable(bigCount),
    cache_write_input_tokens: nullable(bigCount),
    reasoning_output_tokens: nullable(bigCount),
    usage_source: literal(...USAGE_SOURCES),
    cost_usd: nullable(decimalString),
    cost_status: literal("computed", "unknown"),
    cost_unknown_reason: nullable(literal(...COST_UNKNOWN_REASONS)),
    price_version: nullable(string(64)),
    provenance: literal(...USAGE_PROVENANCES),
    provenance_verified: literal(false),
    started_at: isoUtc,
  })(v, path);
  // The ledger's own table checks (ARCHITECTURE.md §4.6): unknown is never a stored 0.
  if ((u.cost_status === "computed") !== (u.cost_usd !== null)) throw new DecodeError(`${path}.cost_usd`, "a cost exactly when cost_status is computed");
  if ((u.cost_status === "unknown") !== (u.cost_unknown_reason !== null)) throw new DecodeError(`${path}.cost_unknown_reason`, "a reason exactly when cost_status is unknown");
  return u;
};

export const traceDetailDecoder: Decoder<TraceDetail> = object<TraceDetail>({
  trace: traceSummaryDecoder,
  spans: array(spanDecoder, MAX_DETAIL_SPANS),
  spans_truncated: boolean,
  usage: array(usageDecoder, MAX_DETAIL_USAGE),
  usage_truncated: boolean,
});
