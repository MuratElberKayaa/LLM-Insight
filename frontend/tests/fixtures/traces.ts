/**
 * SYNTHETIC builders shaped exactly like `GET /api/v1/traces` and
 * `GET /api/v1/traces/{trace_id}` (backend/app/api/traces.py). Unit tests only; production
 * code never imports this file (tests/unit/security-static.test.ts).
 */

import type { SpanRecord, TraceDetail, TraceList, TraceSummary, UsageRecord } from "../../lib/api/trace-types";

/** An epoch-nanosecond base above 2^53 with sub-microsecond digits (T26). */
export const BASE_NS = 1792913387428346901n;

export const tid = (n: number) => n.toString(16).padStart(32, "a");
export const sid = (n: number) => n.toString(16).padStart(16, "0");

function iso(ns: bigint): string {
  const s = ns / 1_000_000_000n;
  const frac = (ns % 1_000_000_000n).toString().padStart(9, "0");
  return `${new Date(Number(s) * 1000).toISOString().slice(0, 19)}.${frac}Z`;
}

export function makeTrace(over: Partial<TraceSummary> = {}, durationNs = 31_853_300n): TraceSummary {
  return {
    trace_id: tid(1),
    service_name: "synthetic-service",
    started_at: iso(BASE_NS),
    ended_at: iso(BASE_NS + durationNs),
    start_time_unix_nano: BASE_NS.toString(),
    end_time_unix_nano: (BASE_NS + durationNs).toString(),
    duration_ns: durationNs.toString(),
    status: "ok",
    prompt_version: "v1",
    provenance: "synthetic",
    provenance_verified: false,
    span_count: 1,
    usage: {
      inference_calls: 1,
      input_tokens: "120",
      output_tokens: "48",
      calls_with_unknown_tokens: 0,
      known_cost_usd: "0.0001875000",
      unknown_cost_calls: 0,
      models: { items: [{ provider: "openai", requested_model: "gpt-5.4-mini", response_model: "gpt-5.4-mini-2026-03-17", calls: 1 }], distinct: 1, truncated: false },
    },
    ...over,
  };
}

export interface SpanSpec {
  id: number;
  parent?: number | null;
  role?: SpanRecord["role"];
  start: bigint; // offset from BASE_NS
  end: bigint;
  name?: string;
  status?: SpanRecord["status"];
  errorType?: string | null;
  kind?: SpanRecord["kind"];
  provenance?: SpanRecord["provenance"];
  attributes?: SpanRecord["attributes"];
}

export function makeSpan(s: SpanSpec): SpanRecord {
  const parent = s.parent ?? null;
  return {
    span_id: sid(s.id),
    parent_span_id: parent === null ? null : sid(parent),
    role: s.role ?? (parent === null ? "root" : "child"),
    children: [],
    name: s.name ?? `span-${s.id}`,
    kind: s.kind ?? (parent === null ? "SERVER" : "CLIENT"),
    started_at: iso(BASE_NS + s.start),
    ended_at: iso(BASE_NS + s.end),
    start_time_unix_nano: (BASE_NS + s.start).toString(),
    end_time_unix_nano: (BASE_NS + s.end).toString(),
    duration_ns: (s.end - s.start).toString(),
    status: s.status ?? "UNSET",
    error_type: s.errorType ?? null,
    scope_name: "llm_insight_sdk",
    scope_version: "0.2.0",
    resource_attributes: { "service.name": "synthetic-service" },
    attributes: s.attributes ?? {},
    attributes_schema_version: "llminsight.attrmap.v1",
    provenance: s.provenance ?? "unknown",
    provenance_verified: false,
    server_dropped_attributes_count: 0,
  };
}

export function makeUsage(spanNum: number, over: Partial<UsageRecord> = {}): UsageRecord {
  return {
    span_id: sid(spanNum),
    canonical_call_key: `sdk:call:${spanNum}`,
    call_key_source: "sdk_attempt",
    call_id: "11111111-2222-4333-a444-555555555555",
    attempt_id: 1,
    provider_response_id: null,
    operation_name: "chat",
    provider: "llminsight.mock",
    requested_model: "mock-chat-small",
    response_model: "mock-chat-small-2026-10-01",
    input_tokens: "120",
    output_tokens: "48",
    cached_input_tokens: "32",
    cache_write_input_tokens: "0",
    reasoning_output_tokens: "0",
    usage_source: "provider_reported",
    cost_usd: null,
    cost_status: "unknown",
    cost_unknown_reason: "price_missing",
    price_version: null,
    provenance: "synthetic",
    provenance_verified: false,
    started_at: "2026-10-25T07:29:47.428346Z",
    ...over,
  };
}

export function makeDetail(spans: SpanRecord[], usage: UsageRecord[] = [], trace: Partial<TraceSummary> = {}, durationNs = 100_000_000n): TraceDetail {
  return {
    trace: makeTrace({ span_count: spans.length, ...trace }, durationNs),
    spans,
    spans_truncated: false,
    usage,
    usage_truncated: false,
  };
}

/** Root → failed attempt 1 (429) → successful attempt 2 of one logical call. */
export function retryDetail(): TraceDetail {
  const spans = [
    makeSpan({ id: 1, start: 0n, end: 31_853_300n, name: "POST /v1/summarize/retry" }),
    makeSpan({ id: 2, parent: 1, start: 0n, end: 11_262_300n, name: "chat mock-chat-small", status: "ERROR", errorType: "429", provenance: "synthetic" }),
    makeSpan({ id: 3, parent: 1, start: 11_262_300n, end: 31_853_300n, name: "chat mock-chat-small", provenance: "synthetic" }),
  ];
  const usage = [
    makeUsage(2, { attempt_id: 1, input_tokens: null, output_tokens: null, cached_input_tokens: null, cache_write_input_tokens: null, reasoning_output_tokens: null, usage_source: "unknown", cost_unknown_reason: "usage_missing" }),
    makeUsage(3, { attempt_id: 2, provider_response_id: "mockresp_1" }),
  ];
  return makeDetail(spans, usage, { status: "error", usage: {
        inference_calls: 2,
        input_tokens: "120",
        output_tokens: "48",
        calls_with_unknown_tokens: 1,
        known_cost_usd: null,
        unknown_cost_calls: 2,
        models: { items: [{ provider: "openai", requested_model: "gpt-5.4-mini", response_model: null, calls: 2 }], distinct: 1, truncated: false },
      },
    }, 31_853_300n);
}

export function makeList(items: TraceSummary[], next_cursor: string | null = null): TraceList {
  return { items, next_cursor, window: { from: "2026-10-24T09:00:00.000000Z", to: "2026-10-25T09:00:00.000000Z" } };
}

const TOKEN_FIELDS = ["input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens", "reasoning_output_tokens"] as const;

/** The JSON the backend sends: token counts are JSON numbers there (strings only after decoding). */
export function toWire<T extends TraceDetail | TraceList>(value: T): unknown {
  const v = JSON.parse(JSON.stringify(value)) as Record<string, unknown>;
  const num = (o: Record<string, unknown>) => {
    for (const f of TOKEN_FIELDS) if (typeof o[f] === "string") o[f] = Number(o[f]);
  };
  const traces = "items" in v ? (v.items as Record<string, unknown>[]) : [v.trace as Record<string, unknown>];
  for (const t of traces) num(t.usage as Record<string, unknown>);
  if ("usage" in v && Array.isArray(v.usage)) for (const u of v.usage as Record<string, unknown>[]) num(u);
  return v;
}
