/** `GET /api/v1/sessions` (Phase 10C, ADR-56): pseudonymous session aggregates. */
import { array, boolean, count, decimalString, DecodeError, isoUtc, literal, nullable, object, positiveInt, type Decoder } from "../validate";
import { pattern } from "./ids";

export interface SessionRow {
  session: string;
  traces: number;
  error_traces: number;
  first_seen: string;
  last_seen: string;
  attempts: number;
  input_tokens: number | null;
  unknown_input_tokens: number;
  output_tokens: number | null;
  unknown_output_tokens: number;
  known_cost_usd: string | null;
  computed_cost_calls: number;
  unknown_cost_calls: number;
  cost_complete: boolean;
}

export interface SessionList {
  project: number;
  window: { from: string; to: string };
  pseudonymous: true;
  items: SessionRow[];
  truncated: boolean;
  limit: number;
}

const MAX_SESSIONS = 200;
const sessionHash = pattern(/^s1_[0-9a-f]{32}$/, "a pseudonymous session hash");

const row: Decoder<SessionRow> = (v, path) => {
  const r = object<SessionRow>({
    session: sessionHash,
    traces: positiveInt,
    error_traces: count,
    first_seen: isoUtc,
    last_seen: isoUtc,
    attempts: count,
    input_tokens: nullable(count),
    unknown_input_tokens: count,
    output_tokens: nullable(count),
    unknown_output_tokens: count,
    known_cost_usd: nullable(decimalString),
    computed_cost_calls: count,
    unknown_cost_calls: count,
    cost_complete: boolean,
  })(v, path);
  if (r.error_traces > r.traces) throw new DecodeError(`${path}.error_traces`, "at most the traces");
  if (r.computed_cost_calls + r.unknown_cost_calls !== r.attempts) throw new DecodeError(`${path}`, "computed + unknown cost calls = attempts");
  if ((r.known_cost_usd === null) !== (r.computed_cost_calls === 0)) throw new DecodeError(`${path}.known_cost_usd`, "a known cost exactly when a call was priced");
  if (r.cost_complete !== (r.attempts > 0 && r.unknown_cost_calls === 0)) throw new DecodeError(`${path}.cost_complete`, "complete exactly when every attempt is priced");
  if (Date.parse(r.first_seen) > Date.parse(r.last_seen)) throw new DecodeError(`${path}.first_seen`, "first seen at or before last seen");
  return r;
};

export const sessionListDecoder: Decoder<SessionList> = (v, path) => {
  const d = object<SessionList>({
    project: positiveInt,
    window: object({ from: isoUtc, to: isoUtc }),
    pseudonymous: literal(true),
    items: array(row, MAX_SESSIONS),
    truncated: boolean,
    limit: positiveInt,
  })(v, path);
  if (new Set(d.items.map((r) => r.session)).size !== d.items.length) throw new DecodeError(`${path}.items`, "distinct sessions");
  if (d.items.length > d.limit) throw new DecodeError(`${path}.items`, "at most the limit");
  return d;
};
