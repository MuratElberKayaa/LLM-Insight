/** `GET /api/v1/error-patterns` (Phase 10D, ADR-57): exact error fingerprints over ERROR spans. */
import { array, count, DecodeError, isoUtc, literal, nullable, object, positiveInt, string, type Decoder, boolean } from "../validate";
import { traceId } from "./ids";

export const ERROR_SOURCES = ["inference", "tool", "agent", "other"] as const;

export interface ErrorPattern {
  source: (typeof ERROR_SOURCES)[number];
  provider: string | null;
  model: string | null;
  tool_name: string | null;
  error_type: string | null;
  error_class: string;
  spans: number;
  traces: number;
  first_seen: string;
  last_seen: string;
  latest_trace_id: string;
}

export interface ErrorPatterns {
  project: number;
  window: { from: string; to: string };
  method: "exact_fingerprint";
  fingerprint: string[];
  error_spans: number;
  patterns: ErrorPattern[];
  truncated: boolean;
  limit: number;
}

const label = string(256);
const pattern: Decoder<ErrorPattern> = (v, path) => {
  const p = object<ErrorPattern>({
    source: literal(...ERROR_SOURCES),
    provider: nullable(label),
    model: nullable(label),
    tool_name: nullable(label),
    error_type: nullable(label),
    error_class: string(64),
    spans: positiveInt,
    traces: positiveInt,
    first_seen: isoUtc,
    last_seen: isoUtc,
    latest_trace_id: traceId,
  })(v, path);
  if (p.traces > p.spans) throw new DecodeError(`${path}.traces`, "at most the spans");
  if (p.model !== null && p.source !== "inference") throw new DecodeError(`${path}.model`, "a model only on inference patterns");
  if (p.tool_name !== null && p.source !== "tool") throw new DecodeError(`${path}.tool_name`, "a tool only on tool patterns");
  if ((p.error_type === null) !== (p.error_class === "not_recorded")) throw new DecodeError(`${path}.error_class`, "not_recorded exactly without an error type");
  if (Date.parse(p.first_seen) > Date.parse(p.last_seen)) throw new DecodeError(`${path}.first_seen`, "first seen at or before last seen");
  return p;
};

export const errorPatternsDecoder: Decoder<ErrorPatterns> = (v, path) => {
  const d = object<ErrorPatterns>({
    project: positiveInt,
    window: object({ from: isoUtc, to: isoUtc }),
    method: literal("exact_fingerprint"),
    fingerprint: array(string(32), 8),
    error_spans: count,
    patterns: array(pattern, 100),
    truncated: boolean,
    limit: positiveInt,
  })(v, path);
  const listed = d.patterns.reduce((n, p) => n + p.spans, 0);
  if (d.truncated ? listed > d.error_spans : listed !== d.error_spans) throw new DecodeError(`${path}.patterns`, "pattern spans adding up to error_spans");
  for (let i = 1; i < d.patterns.length; i++) if (d.patterns[i]!.spans > d.patterns[i - 1]!.spans) throw new DecodeError(`${path}.patterns`, "most spans first");
  return d;
};
