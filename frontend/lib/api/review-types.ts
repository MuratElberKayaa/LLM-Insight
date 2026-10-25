/**
 * Phase 4B response types: human failure reviews (CR-03, ARCHITECTURE.md §4.9), datasets and
 * human-approved test cases (§4.8, §4.10), field for field as served by
 * `backend/app/api/{traces,reviews,datasets}.py`. Review text and test-case content are typed
 * by people and redacted by the server; telemetry never held prompt or completion content.
 */

import type { LosslessInt } from "../json";

export const ISSUE_TYPES = ["retrieval_missing", "chunk_boundary", "prompt_instruction", "generation_mismatch", "schema_violation", "unknown"] as const;
export const REVIEW_STATUSES = ["open", "in_review", "closed"] as const;
export const REVIEWER_DECISIONS = ["pending", "hypothesis_supported", "hypothesis_rejected", "inconclusive"] as const;
export const RULE_TYPES = ["json_schema", "field_match", "exact", "regex"] as const;
/** Rule contract `llminsight.rule.v1` (Phase 4C, ADR-23): every rule carries `version: 1`. */
export const RULE_VERSION = 1;
export const OUTPUT_FORMATS = ["text", "json"] as const;
export const MATCH_MODES = ["full", "search"] as const;
/** Reviews whose decision may become a test case (a rejected one needs an expected output). */
export const CONVERTIBLE_DECISIONS = ["hypothesis_supported", "hypothesis_rejected"] as const;
export const REDACTION_CATEGORIES = ["credential", "email", "iban", "phone", "number"] as const;

export type IssueType = (typeof ISSUE_TYPES)[number];
export type ReviewStatus = (typeof REVIEW_STATUSES)[number];
export type ReviewerDecision = (typeof REVIEWER_DECISIONS)[number];
export type RuleType = (typeof RULE_TYPES)[number];
export type OutputFormat = (typeof OUTPUT_FORMATS)[number];
export type MatchMode = (typeof MATCH_MODES)[number];
export type RedactionCategory = (typeof REDACTION_CATEGORIES)[number];

/** Field limits of §4.8–4.10 (the server enforces them; the forms mirror them). */
export const LIMITS = {
  evidence: 4000,
  hypothesis: 2000,
  actor: 100,
  datasetName: 100,
  sanitizedInput: 8000,
  assertionJson: 16 * 1024,
  pattern: 1000,
} as const;

/** Human-readable labels; the stored values stay the CR-03 enums. */
export const ISSUE_TYPE_LABELS: Record<IssueType, string> = {
  retrieval_missing: "Retrieval missing",
  chunk_boundary: "Chunk boundary",
  prompt_instruction: "Prompt instruction",
  generation_mismatch: "Generation mismatch",
  schema_violation: "Schema violation",
  unknown: "Unknown",
};
export const STATUS_LABELS: Record<ReviewStatus, string> = { open: "Open", in_review: "In review", closed: "Closed" };
export const DECISION_LABELS: Record<ReviewerDecision, string> = {
  pending: "Pending: no decision yet",
  hypothesis_supported: "Hypothesis supported by the evidence",
  hypothesis_rejected: "Hypothesis rejected",
  inconclusive: "Inconclusive",
};
export const RULE_LABELS: Record<RuleType, string> = {
  json_schema: "JSON Schema",
  field_match: "Field match against the expected output",
  exact: "Exact match with the expected output",
  regex: "Regular expression",
};

export interface ReviewCaseLink {
  id: number;
  dataset_id: number;
  dataset_name: string;
}

export interface FailureReview {
  id: number;
  trace_id: string;
  span_id: string | null;
  issue_type: IssueType;
  evidence: string;
  hypothesis: string;
  review_status: ReviewStatus;
  reviewer_decision: ReviewerDecision;
  created_by: string;
  created_at: string;
  updated_at: string;
  test_cases: ReviewCaseLink[];
}

/** Per list item: reviews of the trace; `not_closed` are `open` or `in_review`. */
export interface ReviewSummary {
  total: number;
  not_closed: number;
}

export type MaskedCounts = Partial<Record<RedactionCategory, number>>;

/** `POST /api/v1/failure-reviews` → 201. */
export interface CreatedReview extends FailureReview {
  redaction: { applied: true; masked: { evidence: MaskedCounts; hypothesis: MaskedCounts } };
}

/** `PATCH /api/v1/failure-reviews/{id}` → 200 (no case links). */
export type UpdatedReview = Omit<FailureReview, "test_cases">;

export interface Dataset {
  id: number;
  name: string;
  created_at: string;
  case_count: number;
}

export interface DatasetList {
  items: Dataset[];
  truncated: boolean;
  limit: number;
}

/** Any JSON value as stored in `expected_output` / `rule` (integers above 2^53 stay exact). */
export type JsonValue = string | number | boolean | null | LosslessInt | JsonValue[] | { [key: string]: JsonValue };

export interface TestCase {
  id: number;
  dataset_id: number;
  origin_trace_id: string | null;
  origin_review_id: number | null;
  sanitized_input: string;
  expected_output: JsonValue;
  rule: { [key: string]: JsonValue } & { type: RuleType };
  redaction_applied: boolean;
  approved_by: string;
  approved_at: string;
  created_at: string;
}

export interface CaseList {
  dataset: Dataset;
  items: TestCase[];
  next_after: number | null;
  limit: number;
}

/** `POST /api/v1/datasets/{id}/cases` → 201. */
export interface CreatedCase extends TestCase {
  redaction: { applied: true; masked: { sanitized_input: MaskedCounts } };
}
