/** `GET /api/v1/alerts` (Phase 10F, ADR-59): threshold rules evaluated at read time; no delivery. */
import { array, count, decimalString, DecodeError, isoUtc, literal, nullable, object, positiveInt, string, boolean, type Decoder } from "../validate";

export const ALERT_METRICS = ["inference_error_rate", "inference_latency_p95_ms", "time_to_first_chunk_p95_ms", "known_cost_usd"] as const;
export const ALERT_STATES = ["firing", "ok", "insufficient_data"] as const;

export interface AlertState {
  id: string;
  metric: (typeof ALERT_METRICS)[number];
  comparison: ">=";
  threshold: string;
  min_n: number;
  n: number;
  value: string | null;
  state: (typeof ALERT_STATES)[number];
  lower_bound: boolean;
}

export interface AlertList {
  project: number;
  window: { from: string; to: string };
  contract_version: "llminsight.alert_rules.v1";
  delivery: "none";
  rules: AlertState[];
  firing: number;
}

const rule: Decoder<AlertState> = (v, path) => {
  const r = object<AlertState>({
    id: string(64),
    metric: literal(...ALERT_METRICS),
    comparison: literal(">="),
    threshold: decimalString,
    min_n: positiveInt,
    n: count,
    value: nullable(decimalString),
    state: literal(...ALERT_STATES),
    lower_bound: boolean,
  })(v, path);
  if (r.state !== "insufficient_data" && (r.value === null || r.n < r.min_n)) throw new DecodeError(`${path}.state`, "a decided state only with a value and enough observations");
  if (r.state === "firing" && r.lower_bound) throw new DecodeError(`${path}.lower_bound`, "a firing rule is not a lower bound");
  if (r.value !== null && r.state !== "insufficient_data" && (Number(r.value) >= Number(r.threshold)) !== (r.state === "firing")) throw new DecodeError(`${path}.state`, "firing exactly when value >= threshold");
  return r;
};

export const alertListDecoder: Decoder<AlertList> = (v, path) => {
  const d = object<AlertList>({
    project: positiveInt,
    window: object({ from: isoUtc, to: isoUtc }),
    contract_version: literal("llminsight.alert_rules.v1"),
    delivery: literal("none"),
    rules: array(rule, 20),
    firing: count,
  })(v, path);
  if (d.firing !== d.rules.filter((r) => r.state === "firing").length) throw new DecodeError(`${path}.firing`, "the number of firing rules");
  if (new Set(d.rules.map((r) => r.id)).size !== d.rules.length) throw new DecodeError(`${path}.rules`, "distinct rule ids");
  return d;
};
