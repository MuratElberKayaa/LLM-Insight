/**
 * Decoders for the Phase 4D experiment responses (./experiment-types.ts). Besides the field
 * types, each decoder checks the report's own definitions (ARCHITECTURE.md §5.9): a report
 * whose counts disagree with each other is rejected as a whole (`malformed`), so the page never
 * shows a comparison built on inconsistent numbers. Nothing is recomputed for display.
 */

import { array, boolean, count, decimalString, DecodeError, isoUtc, literal, nonNegativeNumber, nullable, object, positiveInt, rate, string, type Decoder } from "../validate";
import {
  COST_UNKNOWN_REASONS,
  EXPERIMENT_LIMITS,
  EXPERIMENT_MODES,
  OPTIONS_CONTRACT,
  OUTCOMES,
  REPORT_CONTRACT,
  type CostComparison,
  type ExperimentList,
  type ExperimentOptions,
  type ExperimentReport,
  type ExperimentSummary,
  type QualityComparison,
  type Rate,
  type ResultRow,
  type TokenSum,
  type Variant,
} from "./experiment-types";

export const MAX_EXPERIMENTS = 200;
/** 200 cases × 2 prompt versions. */
export const MAX_RESULTS = 2 * EXPERIMENT_LIMITS.maxCases;
const CODE_RE = /^[a-z][a-z0-9_]{0,63}$/;
const SIGNED_DECIMAL_RE = /^-?\d{1,30}(\.\d{1,20})?$/;
const ID_LABEL = string(EXPERIMENT_LIMITS.promptVersion);
const MODEL = string(EXPERIMENT_LIMITS.model);
const DATASET_NAME = string(100);

/** A decimal string that may be negative (a cost difference). Never converted to a float. */
export const signedDecimalString: Decoder<string> = (v, path) => {
  if (typeof v !== "string" || !SIGNED_DECIMAL_RE.test(v)) throw new DecodeError(path, "a decimal string");
  return v;
};

const code: Decoder<string> = (v, path) => {
  if (typeof v !== "string" || !CODE_RE.test(v)) throw new DecodeError(path, "an outcome code");
  return v;
};

/** `{code: count}` with identifier keys and positive counts (at most 50 keys). */
function codeCounts<K extends string>(keyDecoder: Decoder<K>): Decoder<Partial<Record<K, number>>> {
  return (v, path) => {
    if (typeof v !== "object" || v === null || Array.isArray(v)) throw new DecodeError(path, "an object");
    const entries = Object.entries(v);
    if (entries.length > 50) throw new DecodeError(path, "at most 50 codes");
    const out: Partial<Record<K, number>> = {};
    for (const [key, value] of entries) {
      const k = keyDecoder(key, `${path}.${key}`);
      // defineProperty: a key such as "__proto__" stays an ordinary own property.
      Object.defineProperty(out, k, { value: positiveInt(value, `${path}.${key}`), enumerable: true, writable: true, configurable: true });
    }
    return out;
  };
}

function sumOf(counts: Partial<Record<string, number>>): number {
  return Object.values(counts).reduce<number>((a, b) => a + (b ?? 0), 0);
}

const summaryFields = {
  id: positiveInt,
  dataset_id: positiveInt,
  dataset_name: DATASET_NAME,
  prompt_version_a: ID_LABEL,
  prompt_version_b: ID_LABEL,
  model: MODEL,
  mode: literal(...EXPERIMENT_MODES),
  budget_usd: nullable(decimalString),
  started_at: isoUtc,
  finished_at: nullable(isoUtc),
  state: literal("finished", "running"),
};

function checkState<T extends { state: string; finished_at: string | null }>(e: T, path: string): T {
  if ((e.state === "finished") !== (e.finished_at !== null)) throw new DecodeError(`${path}.state`, "finished exactly when finished_at is set");
  return e;
}

export const experimentSummaryDecoder: Decoder<ExperimentSummary> = (v, path) => {
  const e = checkState(object<ExperimentSummary>({ ...summaryFields, results: count })(v, path), path);
  if (e.results > MAX_RESULTS) throw new DecodeError(`${path}.results`, `at most ${MAX_RESULTS} results`);
  return e;
};

export const experimentListDecoder: Decoder<ExperimentList> = (v, path) => {
  const l = object<ExperimentList>({ items: array(experimentSummaryDecoder, MAX_EXPERIMENTS), truncated: boolean, limit: positiveInt })(v, path);
  if (l.items.length > l.limit) throw new DecodeError(`${path}.items`, "at most limit items");
  return l;
};

export const experimentOptionsDecoder: Decoder<ExperimentOptions> = object<ExperimentOptions>({
  contract_version: literal(OPTIONS_CONTRACT),
  // Mock only: a response offering any other mode is refused, never rendered as a choice.
  modes: (v, path) => {
    const modes = array(literal("mock"), 1)(v, path);
    if (modes.length !== 1) throw new DecodeError(path, "exactly the mock mode");
    return modes;
  },
  real_mode: object<ExperimentOptions["real_mode"]>({ available: literal(false), code }),
  provider: object<ExperimentOptions["provider"]>({ kind: literal("scripted_mock"), simulates: string(64), provenance: literal("synthetic") }),
  prompt_versions: array(object<ExperimentOptions["prompt_versions"][number]>({ prompt_version: ID_LABEL, scripted_inputs: positiveInt }), 50),
  models: array(object<ExperimentOptions["models"][number]>({ model: MODEL, price_version: string(64) }), 200),
  models_truncated: boolean,
  limits: object<ExperimentOptions["limits"]>({
    max_cases: positiveInt,
    max_concurrent_runs: positiveInt,
    run_time_limit_seconds: positiveInt,
    budget_usd_max: decimalString,
    budget_usd_decimal_places: count,
  }),
});

const rateDecoder: Decoder<Rate> = (v, path) => {
  const r = object<Rate>({ value: nullable(rate), numerator: count, denominator: count })(v, path);
  if (r.numerator > r.denominator) throw new DecodeError(`${path}.numerator`, "at most the denominator");
  if ((r.value === null) !== (r.denominator === 0)) throw new DecodeError(`${path}.value`, "null exactly when the denominator is 0");
  return r;
};

const tokenSum: Decoder<TokenSum> = (v, path) => {
  const t = object<TokenSum>({ sum: nullable(count), n: count })(v, path);
  if ((t.sum === null) !== (t.n === 0)) throw new DecodeError(`${path}.sum`, "null exactly when no result reported tokens");
  return t;
};

const variantDecoder: Decoder<Variant> = (v, path) => {
  const x = object<Variant>({
    slot: literal("a", "b"),
    prompt_version: ID_LABEL,
    results: count,
    passed: count,
    failed: count,
    not_evaluated: count,
    not_evaluated_by_code: codeCounts(code),
    pass_rate: rateDecoder,
    duration_ms: object<Variant["duration_ms"]>({ n: count, p50: nullable(nonNegativeNumber), p95: nullable(nonNegativeNumber), p95_low_sample: boolean }),
    tokens: object<Variant["tokens"]>({ input: tokenSum, output: tokenSum }),
    cost: object<Variant["cost"]>({
      known_cost_usd: nullable(decimalString),
      computed: count,
      unknown: count,
      unknown_by_reason: codeCounts(literal(...COST_UNKNOWN_REASONS)),
      complete: boolean,
    }),
  })(v, path);
  // §5.9 definitions, checked rather than trusted.
  if (x.passed + x.failed + x.not_evaluated !== x.results) throw new DecodeError(`${path}.results`, "passed + failed + not evaluated");
  if (sumOf(x.not_evaluated_by_code) !== x.not_evaluated) throw new DecodeError(`${path}.not_evaluated_by_code`, "codes summing to not_evaluated");
  if (x.pass_rate.numerator !== x.passed || x.pass_rate.denominator !== x.passed + x.failed) throw new DecodeError(`${path}.pass_rate`, "passed / (passed + failed)");
  if (x.cost.computed + x.cost.unknown !== x.results) throw new DecodeError(`${path}.cost`, "computed + unknown = results");
  if (sumOf(x.cost.unknown_by_reason) !== x.cost.unknown) throw new DecodeError(`${path}.cost.unknown_by_reason`, "reasons summing to unknown");
  if (x.cost.complete !== (x.results > 0 && x.cost.unknown === 0)) throw new DecodeError(`${path}.cost.complete`, "complete exactly when every result is priced");
  if ((x.cost.known_cost_usd === null) !== (x.cost.computed === 0)) throw new DecodeError(`${path}.cost.known_cost_usd`, "a known cost exactly when a result is priced");
  if (x.duration_ms.n > x.results || x.tokens.input.n > x.results || x.tokens.output.n > x.results) throw new DecodeError(`${path}`, "sample counts within the results");
  if ((x.duration_ms.p50 === null) !== (x.duration_ms.n === 0) || (x.duration_ms.p95 === null) !== (x.duration_ms.n === 0)) throw new DecodeError(`${path}.duration_ms`, "percentiles exactly when sampled");
  return x;
};

const qualityDecoder: Decoder<QualityComparison> = (v, path) => {
  const q = object<QualityComparison>({
    status: literal("complete", "partial"),
    paired_cases: count,
    excluded_by_design: count,
    unpaired_cases: count,
    a_passed_on_paired: count,
    b_passed_on_paired: count,
    higher_pass_rate: nullable(literal("a", "b", "equal")),
    reason: nullable(string(500)),
  })(v, path);
  const complete = q.status === "complete";
  // A better version is named only on complete evidence (ADR-25).
  if ((q.higher_pass_rate !== null) !== complete || (q.reason === null) !== complete) throw new DecodeError(`${path}.higher_pass_rate`, "a verdict exactly when complete");
  if (q.a_passed_on_paired > q.paired_cases || q.b_passed_on_paired > q.paired_cases) throw new DecodeError(`${path}`, "passes within the paired cases");
  if (complete && (q.unpaired_cases > 0 || q.paired_cases === 0)) throw new DecodeError(`${path}.status`, "complete only with paired cases and none unpaired");
  if (complete) {
    const expected = q.a_passed_on_paired > q.b_passed_on_paired ? "a" : q.b_passed_on_paired > q.a_passed_on_paired ? "b" : "equal";
    if (q.higher_pass_rate !== expected) throw new DecodeError(`${path}.higher_pass_rate`, "the version with more passes on paired cases");
  }
  return q;
};

const costComparisonDecoder: Decoder<CostComparison> = (v, path) => {
  const c = object<CostComparison>({ status: literal("complete", "incomplete"), difference_usd_b_minus_a: nullable(signedDecimalString), reason: nullable(string(500)) })(v, path);
  const complete = c.status === "complete";
  if ((c.difference_usd_b_minus_a !== null) !== complete || (c.reason === null) !== complete) throw new DecodeError(`${path}.difference_usd_b_minus_a`, "a difference exactly when complete");
  return c;
};

const resultDecoder: Decoder<ResultRow> = (v, path) => {
  const r = object<ResultRow>({
    test_case_id: positiveInt,
    prompt_version: ID_LABEL,
    slot: literal("a", "b"),
    outcome: literal(...OUTCOMES),
    passed: nullable(boolean),
    detail_code: code,
    evaluation_detail: nullable(string(EXPERIMENT_LIMITS.evaluationDetail)),
    duration_ms: nullable(count),
    input_tokens: nullable(count),
    output_tokens: nullable(count),
    cost_usd: nullable(decimalString),
    cost_status: literal("computed", "unknown"),
    cost_unknown_reason: nullable(literal(...COST_UNKNOWN_REASONS)),
    provenance: literal("real", "synthetic"),
  })(v, path);
  const outcome = r.passed === true ? "passed" : r.passed === false ? "failed" : "not_evaluated";
  if (r.outcome !== outcome) throw new DecodeError(`${path}.outcome`, "the outcome of passed");
  if ((r.detail_code === "pass") !== (r.passed === true)) throw new DecodeError(`${path}.detail_code`, "pass exactly when passed");
  if ((r.cost_usd !== null) !== (r.cost_status === "computed") || (r.cost_unknown_reason !== null) !== (r.cost_status === "unknown")) {
    throw new DecodeError(`${path}.cost_status`, "the ledger's cost rules");
  }
  return r;
};

export const experimentReportDecoder: Decoder<ExperimentReport> = (v, path) => {
  const d = object<ExperimentReport>({
    contract_version: literal(REPORT_CONTRACT),
    experiment: (x, p) => checkState(object<ExperimentReport["experiment"]>(summaryFields)(x, p), p),
    evaluation: object<ExperimentReport["evaluation"]>({ method: literal("deterministic_rules"), rule_contract: literal("llminsight.rule.v1"), language_model_used: literal(false) }),
    provenance: object<ExperimentReport["provenance"]>({ label: literal("none", "synthetic", "real", "mixed"), synthetic: count, real: count, simulated_provider: boolean }),
    cases: object<ExperimentReport["cases"]>({ count, ids_identical_across_versions: boolean }),
    variants: (x, p) => {
      const list = array(variantDecoder, 2)(x, p);
      if (list.length !== 2 || list[0]?.slot !== "a" || list[1]?.slot !== "b") throw new DecodeError(p, "variants a and b");
      return list as [Variant, Variant];
    },
    comparison: object<ExperimentReport["comparison"]>({ quality: qualityDecoder, cost: costComparisonDecoder }),
    limitations: array(object<ExperimentReport["limitations"][number]>({ code, text: string(500) }), 20),
    results: array(resultDecoder, MAX_RESULTS),
  })(v, path);
  const { experiment: e, variants, results, provenance } = d;
  const [va, vb] = variants;
  if (va.prompt_version !== e.prompt_version_a || vb.prompt_version !== e.prompt_version_b || e.prompt_version_a === e.prompt_version_b) {
    throw new DecodeError(`${path}.variants`, "the experiment's two prompt versions");
  }
  if (va.results + vb.results !== results.length) throw new DecodeError(`${path}.results`, "one row per counted result");
  const seen = new Set<string>();
  const caseIds = new Set<number>();
  const tally = { a: { passed: 0, failed: 0, not_evaluated: 0 }, b: { passed: 0, failed: 0, not_evaluated: 0 } };
  for (const [i, r] of results.entries()) {
    const expected = r.slot === "a" ? e.prompt_version_a : e.prompt_version_b;
    if (r.prompt_version !== expected) throw new DecodeError(`${path}.results[${i}].slot`, "the slot of its prompt version");
    const key = `${r.test_case_id}:${r.slot}`;
    if (seen.has(key)) throw new DecodeError(`${path}.results[${i}]`, "one result per case and version");
    seen.add(key);
    caseIds.add(r.test_case_id);
    tally[r.slot][r.outcome] += 1;
  }
  for (const v of variants) {
    const t = tally[v.slot];
    if (t.passed !== v.passed || t.failed !== v.failed || t.not_evaluated !== v.not_evaluated) throw new DecodeError(`${path}.variants`, "counts matching the result rows");
  }
  if (d.cases.count !== caseIds.size) throw new DecodeError(`${path}.cases.count`, "the number of distinct cases");
  // Comparisons exist only for a finished run; cost only when both versions are fully priced.
  const finished = e.state === "finished";
  if (d.comparison.quality.status === "complete" && (!finished || !d.cases.ids_identical_across_versions)) throw new DecodeError(`${path}.comparison.quality.status`, "complete only for a finished run on identical cases");
  if ((d.comparison.cost.status === "complete") !== (finished && va.cost.complete && vb.cost.complete)) throw new DecodeError(`${path}.comparison.cost.status`, "complete exactly when both versions are fully priced");
  if (provenance.synthetic + provenance.real !== results.length) throw new DecodeError(`${path}.provenance`, "one label per result");
  const label = results.length === 0 ? "none" : provenance.real === 0 ? "synthetic" : provenance.synthetic === 0 ? "real" : "mixed";
  if (provenance.label !== label) throw new DecodeError(`${path}.provenance.label`, "the label of the result counts");
  // T39: a mock experiment is simulated and every one of its results is synthetic.
  if (e.mode === "mock" && (!provenance.simulated_provider || provenance.real > 0)) throw new DecodeError(`${path}.provenance`, "synthetic results for a mock experiment");
  return d;
};
