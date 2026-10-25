/**
 * Display model of one trace (pure, server-side): the span hierarchy in a deterministic
 * depth-first order, exact relative timing for the waterfall, the usage row of each
 * inference span, and the physical attempts grouped per logical call.
 *
 * Rules:
 * - Parent-child comes from `parent_span_id` among the returned spans; siblings and top-level
 *   spans are ordered by (start ns, span id), the backend's own order, so equal start
 *   times always render the same way.
 * - A trace need not have one clean root. Top level holds roots, orphans (parent never
 *   stored, T27), spans whose parent was cut by the 2000-span response limit, and spans
 *   that no root reaches (a parent cycle sent by a client); each is labeled as such.
 * - Times are BigInt nanoseconds; nothing is inferred when data is missing.
 * - Failure is reported as recorded (span status, `error.type`); no cause is derived.
 */

import { formatUsd } from "./decimal";
import { LosslessInt } from "./json";
import { axisPercent, formatDurationNs, formatExactMs, formatIsoNs, parseNs } from "./nanos";
import type { AttributeValue, SpanKind, SpanRecord, SpanRole, SpanStatus, TraceDetail, UsageRecord } from "./api/trace-types";
import type { CostUnknownReason, UsageProvenance, UsageSource } from "./api/types";

export type Placement = "root" | "child" | "orphan" | "parent_not_returned" | "unreachable";

export const PLACEMENT_LABEL: Record<Placement, string> = {
  root: "Root span",
  child: "Child span",
  orphan: "Orphan: parent span was never stored",
  parent_not_returned: "Parent not in this response (span limit reached)",
  unreachable: "Not reachable from a root (parent chain loops)",
};

export const COST_REASON_LABEL: Record<CostUnknownReason, string> = {
  price_missing: "no verified price for this model",
  usage_missing: "usage not reported",
  usage_inconsistent: "reported usage is inconsistent",
};

export type CostView = { kind: "computed"; text: string; exact: string } | { kind: "unknown"; reason: CostUnknownReason };

export interface InferenceView {
  provider: string;
  requestedModel: string | null;
  responseModel: string | null;
  operation: string;
  callId: string | null;
  attemptId: number | null;
  responseId: string | null;
  callKeySource: string;
  inputTokens: string | null;
  outputTokens: string | null;
  cachedInputTokens: string | null;
  cacheWriteInputTokens: string | null;
  reasoningOutputTokens: string | null;
  usageSource: UsageSource;
  cost: CostView;
  priceVersion: string | null;
  provenance: UsageProvenance;
}

export interface SpanView {
  spanId: string;
  parentSpanId: string | null;
  name: string;
  kind: SpanKind;
  status: SpanStatus;
  errorType: string | null;
  role: SpanRole;
  placement: Placement;
  depth: number;
  childIds: string[];
  startLabel: string;
  endLabel: string;
  startNs: string;
  endNs: string;
  offsetLabel: string;
  durationNs: string;
  durationLabel: string;
  durationExact: string;
  left: number;
  width: number;
  provenance: UsageProvenance;
  scopeName: string | null;
  scopeVersion: string | null;
  schemaVersion: string;
  droppedAttributes: number;
  attributes: [string, string][];
  resourceAttributes: [string, string][];
  inference: InferenceView | null;
}

export type CallOutcome = "no_failure" | "recovered" | "all_failed" | "last_failed" | "status_unavailable";

export const CALL_OUTCOME_LABEL: Record<CallOutcome, string> = {
  no_failure: "No failed attempt",
  recovered: "Recovered after retry",
  all_failed: "All attempts failed",
  last_failed: "Last attempt failed",
  status_unavailable: "Attempt status not in response",
};

export interface AttemptView {
  spanId: string;
  attemptId: number | null;
  status: SpanStatus | null;
  errorType: string | null;
  durationLabel: string | null;
  model: string | null;
}

export interface CallView {
  /** null: attempts without `llminsight.call_id` cannot be grouped; each is listed alone. */
  callId: string | null;
  attempts: AttemptView[];
  failed: number;
  outcome: CallOutcome;
}

export interface TraceView {
  rows: SpanView[];
  axis: { durationLabel: string; ticks: { pct: number; label: string }[] };
  counts: {
    spans: number;
    errorSpans: number;
    inferenceSpans: number;
    failedInference: number;
    failedApplication: number;
    usageWithoutSpan: number;
    topLevel: Record<Placement, number>;
  };
  calls: CallView[];
}

// ---------------------------------------------------------------- formatting helpers

/** "1,234,567" from a digit string, without converting to a float. */
export function formatDigits(digits: string): string {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** An attribute value as plain text (rendered escaped by React; never as HTML). */
export function formatAttributeValue(v: AttributeValue): string {
  if (v === null) return "null";
  if (v instanceof LosslessInt) return v.digits;
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return `[${v.map(formatAttributeValue).join(", ")}]`;
  return `{${Object.entries(v)
    .map(([k, x]) => `${k}: ${formatAttributeValue(x)}`)
    .join(", ")}}`;
}

function attributeList(attrs: Record<string, AttributeValue>): [string, string][] {
  return Object.keys(attrs)
    .sort()
    .map((k) => [k, formatAttributeValue(attrs[k] as AttributeValue)]);
}

function costView(u: UsageRecord): CostView {
  if (u.cost_status === "computed" && u.cost_usd !== null) {
    const f = formatUsd(u.cost_usd);
    return { kind: "computed", text: f?.text ?? u.cost_usd, exact: f?.exact ?? u.cost_usd };
  }
  return { kind: "unknown", reason: u.cost_unknown_reason ?? "price_missing" };
}

function inferenceView(u: UsageRecord): InferenceView {
  return {
    provider: u.provider,
    requestedModel: u.requested_model,
    responseModel: u.response_model,
    operation: u.operation_name,
    callId: u.call_id,
    attemptId: u.attempt_id,
    responseId: u.provider_response_id,
    callKeySource: u.call_key_source,
    inputTokens: u.input_tokens,
    outputTokens: u.output_tokens,
    cachedInputTokens: u.cached_input_tokens,
    cacheWriteInputTokens: u.cache_write_input_tokens,
    reasoningOutputTokens: u.reasoning_output_tokens,
    usageSource: u.usage_source,
    cost: costView(u),
    priceVersion: u.price_version,
    provenance: u.provenance,
  };
}

// ---------------------------------------------------------------- tree

function compareSpans(a: SpanRecord, b: SpanRecord): number {
  const sa = BigInt(a.start_time_unix_nano);
  const sb = BigInt(b.start_time_unix_nano);
  if (sa !== sb) return sa < sb ? -1 : 1;
  return a.span_id < b.span_id ? -1 : a.span_id > b.span_id ? 1 : 0;
}

export function buildTraceView(detail: TraceDetail): TraceView {
  const spans = [...detail.spans].sort(compareSpans);
  const byId = new Map<string, SpanRecord>();
  for (const s of spans) if (!byId.has(s.span_id)) byId.set(s.span_id, s);
  const usageBySpan = new Map<string, UsageRecord>();
  for (const u of detail.usage) if (!usageBySpan.has(u.span_id)) usageBySpan.set(u.span_id, u);

  const childrenOf = new Map<string, SpanRecord[]>();
  for (const s of byId.values()) {
    if (s.parent_span_id !== null && byId.has(s.parent_span_id)) {
      const list = childrenOf.get(s.parent_span_id) ?? [];
      list.push(s);
      childrenOf.set(s.parent_span_id, list);
    }
  }

  const axisStart = parseNs(detail.trace.start_time_unix_nano) ?? 0n;
  const axisLength = parseNs(detail.trace.duration_ns) ?? 0n;

  const rows: SpanView[] = [];
  const visited = new Set<string>();
  const topLevel: Record<Placement, number> = { root: 0, child: 0, orphan: 0, parent_not_returned: 0, unreachable: 0 };

  const view = (s: SpanRecord, depth: number, placement: Placement): SpanView => {
    const start = BigInt(s.start_time_unix_nano);
    const end = BigInt(s.end_time_unix_nano);
    const duration = BigInt(s.duration_ns);
    const { left, width } = axisPercent(start, end, axisStart, axisLength);
    const usage = usageBySpan.get(s.span_id);
    return {
      spanId: s.span_id,
      parentSpanId: s.parent_span_id,
      name: s.name,
      kind: s.kind,
      status: s.status,
      errorType: s.error_type,
      role: s.role,
      placement,
      depth,
      childIds: (childrenOf.get(s.span_id) ?? []).map((c) => c.span_id),
      startLabel: formatIsoNs(s.started_at),
      endLabel: formatIsoNs(s.ended_at),
      startNs: s.start_time_unix_nano,
      endNs: s.end_time_unix_nano,
      offsetLabel: `+${formatDurationNs(start - axisStart)}`,
      durationNs: s.duration_ns,
      durationLabel: formatDurationNs(duration),
      durationExact: formatExactMs(duration),
      left,
      width,
      provenance: s.provenance,
      scopeName: s.scope_name,
      scopeVersion: s.scope_version,
      schemaVersion: s.attributes_schema_version,
      droppedAttributes: s.server_dropped_attributes_count,
      attributes: attributeList(s.attributes),
      resourceAttributes: attributeList(s.resource_attributes),
      inference: usage ? inferenceView(usage) : null,
    };
  };

  // Iterative depth-first walk (no recursion limit for deep traces); `visited` stops cycles.
  const walk = (start: SpanRecord, placement: Placement) => {
    topLevel[placement] += 1;
    const stack: { span: SpanRecord; depth: number }[] = [{ span: start, depth: 0 }];
    while (stack.length > 0) {
      const { span, depth } = stack.pop()!;
      if (visited.has(span.span_id)) continue;
      visited.add(span.span_id);
      rows.push(view(span, depth, depth === 0 ? placement : "child"));
      const kids = childrenOf.get(span.span_id) ?? [];
      for (let i = kids.length - 1; i >= 0; i -= 1) {
        const kid = kids[i]!;
        if (!visited.has(kid.span_id)) stack.push({ span: kid, depth: depth + 1 });
      }
    }
  };

  for (const s of byId.values()) {
    if (s.parent_span_id === null) walk(s, "root");
    else if (s.role === "orphan") walk(s, "orphan");
    else if (!byId.has(s.parent_span_id)) walk(s, "parent_not_returned");
  }
  for (const s of byId.values()) if (!visited.has(s.span_id)) walk(s, "unreachable");

  // ------------------------------------------------------------ counts and logical calls
  let errorSpans = 0;
  let failedInference = 0;
  let failedApplication = 0;
  for (const r of rows) {
    if (r.status !== "ERROR") continue;
    errorSpans += 1;
    if (r.inference) failedInference += 1;
    else failedApplication += 1;
  }

  const attempt = (u: UsageRecord): AttemptView => {
    const s = byId.get(u.span_id);
    return {
      spanId: u.span_id,
      attemptId: u.attempt_id,
      status: s?.status ?? null,
      errorType: s?.error_type ?? null,
      durationLabel: s ? formatDurationNs(BigInt(s.duration_ns)) : null,
      model: u.response_model ?? u.requested_model,
    };
  };
  const outcome = (attempts: AttemptView[]): CallOutcome => {
    if (attempts.some((a) => a.status === null)) return "status_unavailable";
    const failed = attempts.map((a) => a.status === "ERROR");
    if (!failed.includes(true)) return "no_failure";
    if (failed.every(Boolean)) return "all_failed";
    return failed[failed.length - 1] ? "last_failed" : "recovered";
  };
  const callOrder = (a: UsageRecord, b: UsageRecord) => {
    const x = a.attempt_id ?? Number.MAX_SAFE_INTEGER;
    const y = b.attempt_id ?? Number.MAX_SAFE_INTEGER;
    if (x !== y) return x - y;
    return a.started_at < b.started_at ? -1 : a.started_at > b.started_at ? 1 : a.span_id < b.span_id ? -1 : 1;
  };

  const groups = new Map<string, UsageRecord[]>();
  const ungrouped: UsageRecord[] = [];
  for (const u of detail.usage) {
    if (u.call_id === null) ungrouped.push(u);
    else groups.set(u.call_id, [...(groups.get(u.call_id) ?? []), u]);
  }
  const calls: CallView[] = [];
  for (const [callId, list] of groups) {
    const attempts = [...list].sort(callOrder).map(attempt);
    calls.push({ callId, attempts, failed: attempts.filter((a) => a.status === "ERROR").length, outcome: outcome(attempts) });
  }
  for (const u of ungrouped) {
    const attempts = [attempt(u)];
    calls.push({ callId: null, attempts, failed: attempts[0]!.status === "ERROR" ? 1 : 0, outcome: outcome(attempts) });
  }
  // Calls in the order of their first attempt's start (then call id), like the waterfall.
  const firstStart = (c: CallView) => byId.get(c.attempts[0]!.spanId)?.start_time_unix_nano ?? null;
  calls.sort((a, b) => {
    const x = firstStart(a);
    const y = firstStart(b);
    if (x !== null && y !== null && x !== y) return BigInt(x) < BigInt(y) ? -1 : 1;
    if ((x === null) !== (y === null)) return x === null ? 1 : -1;
    return (a.callId ?? "~").localeCompare(b.callId ?? "~") || a.attempts[0]!.spanId.localeCompare(b.attempts[0]!.spanId);
  });

  const ticks = [0, 1, 2, 3, 4].map((k) => ({ pct: k * 25, label: k === 0 ? "0" : formatDurationNs((axisLength * BigInt(k)) / 4n) }));

  return {
    rows,
    axis: { durationLabel: formatDurationNs(axisLength), ticks },
    counts: {
      spans: rows.length,
      errorSpans,
      inferenceSpans: rows.filter((r) => r.inference !== null).length,
      failedInference,
      failedApplication,
      usageWithoutSpan: detail.usage.filter((u) => !byId.has(u.span_id)).length,
      topLevel,
    },
    calls,
  };
}
