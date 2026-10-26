/**
 * Phase 4D response types: mock A/B experiments, field for field as served by
 * `backend/app/api/experiments.py` (ARCHITECTURE.md §5.9–§5.10). The report is
 * `llminsight.experiment_report.v1`, built by the backend from stored rows only; the dashboard
 * shows it and never recomputes a metric. Money stays a decimal string, unknown stays null.
 */

export const REPORT_CONTRACT = "llminsight.experiment_report.v1";
export const OPTIONS_CONTRACT = "llminsight.experiment_options.v1";
export const EXPERIMENT_MODES = ["mock", "real"] as const;
export const OUTCOMES = ["passed", "failed", "not_evaluated"] as const;
export const COST_UNKNOWN_REASONS = ["price_missing", "usage_missing", "usage_inconsistent"] as const;

export type ExperimentMode = (typeof EXPERIMENT_MODES)[number];
export type Outcome = (typeof OUTCOMES)[number];
export type CostUnknownReason = (typeof COST_UNKNOWN_REASONS)[number];
export type Slot = "a" | "b";

/** Field limits of §4.11–4.12. */
export const EXPERIMENT_LIMITS = { promptVersion: 64, model: 256, evaluationDetail: 1000, maxCases: 200 } as const;

/** Fixed text for the backend's outcome codes (`ARCHITECTURE.md` §5.9). Unknown codes are shown as the code itself. */
export const DETAIL_CODE_LABELS: Record<string, string> = {
  pass: "Passed the rule",
  schema_violation: "Output violates the JSON Schema",
  field_mismatch: "A listed field differs from the expected output",
  exact_mismatch: "Output differs from the expected output",
  regex_no_match: "Pattern did not match",
  output_not_json: "Output is not valid JSON",
  output_not_object: "Output is not a JSON object",
  output_invalid_text: "Output is not valid text",
  rule_unsupported: "Rule not readable (unsupported version)",
  expected_output_missing: "No approved expected output",
  expected_output_invalid: "Expected output does not satisfy its rule",
  output_too_large: "Output exceeds the evaluation size limit",
  output_limits_exceeded: "Output exceeds the JSON limits",
  evaluation_limit_exceeded: "Evaluation work limit reached",
  provider_timeout: "Simulated call timed out",
  provider_error: "Simulated call failed",
  mock_no_scripted_output: "Mock has no scripted answer for this input",
  budget_exhausted: "Not executed: budget reached",
};

export const COST_REASON_LABELS: Record<CostUnknownReason, string> = {
  price_missing: "no stored price",
  usage_missing: "no usage reported",
  usage_inconsistent: "usage inconsistent",
};

export interface ExperimentSummary {
  id: number;
  dataset_id: number;
  dataset_name: string;
  prompt_version_a: string;
  prompt_version_b: string;
  model: string;
  mode: ExperimentMode;
  budget_usd: string | null;
  started_at: string;
  finished_at: string | null;
  state: "finished" | "running";
  results: number;
}

export interface ExperimentList {
  items: ExperimentSummary[];
  truncated: boolean;
  limit: number;
}

export interface ExperimentOptions {
  contract_version: typeof OPTIONS_CONTRACT;
  modes: "mock"[];
  real_mode: { available: false; code: string };
  provider: { kind: "scripted_mock"; simulates: string; provenance: "synthetic" };
  prompt_versions: { prompt_version: string; scripted_inputs: number }[];
  models: { model: string; price_version: string }[];
  models_truncated: boolean;
  limits: {
    max_cases: number;
    max_concurrent_runs: number;
    run_time_limit_seconds: number;
    budget_usd_max: string;
    budget_usd_decimal_places: number;
  };
}

export interface Rate {
  value: number | null;
  numerator: number;
  denominator: number;
}

export interface TokenSum {
  sum: number | null;
  n: number;
}

export interface Variant {
  slot: Slot;
  prompt_version: string;
  results: number;
  passed: number;
  failed: number;
  not_evaluated: number;
  not_evaluated_by_code: Partial<Record<string, number>>;
  pass_rate: Rate;
  duration_ms: { n: number; p50: number | null; p95: number | null; p95_low_sample: boolean };
  tokens: { input: TokenSum; output: TokenSum };
  cost: {
    known_cost_usd: string | null;
    computed: number;
    unknown: number;
    unknown_by_reason: Partial<Record<CostUnknownReason, number>>;
    complete: boolean;
  };
}

export interface QualityComparison {
  status: "complete" | "partial";
  paired_cases: number;
  excluded_by_design: number;
  unpaired_cases: number;
  a_passed_on_paired: number;
  b_passed_on_paired: number;
  higher_pass_rate: "a" | "b" | "equal" | null;
  reason: string | null;
}

export interface CostComparison {
  status: "complete" | "incomplete";
  difference_usd_b_minus_a: string | null;
  reason: string | null;
}

export interface ResultRow {
  test_case_id: number;
  prompt_version: string;
  slot: Slot;
  outcome: Outcome;
  passed: boolean | null;
  detail_code: string;
  evaluation_detail: string | null;
  duration_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: string | null;
  cost_status: "computed" | "unknown";
  cost_unknown_reason: CostUnknownReason | null;
  provenance: "real" | "synthetic";
}

export interface ExperimentReport {
  contract_version: typeof REPORT_CONTRACT;
  experiment: {
    id: number;
    dataset_id: number;
    dataset_name: string;
    prompt_version_a: string;
    prompt_version_b: string;
    model: string;
    mode: ExperimentMode;
    budget_usd: string | null;
    started_at: string;
    finished_at: string | null;
    state: "finished" | "running";
  };
  evaluation: { method: "deterministic_rules"; rule_contract: "llminsight.rule.v1"; language_model_used: false };
  provenance: { label: "none" | "synthetic" | "real" | "mixed"; synthetic: number; real: number; simulated_provider: boolean };
  cases: { count: number; ids_identical_across_versions: boolean };
  variants: [Variant, Variant];
  comparison: { quality: QualityComparison; cost: CostComparison };
  limitations: { code: string; text: string }[];
  results: ResultRow[];
}
