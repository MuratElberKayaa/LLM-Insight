/** Decoders for the Phase 4B review, dataset and test-case responses (./review-types.ts). */

import { LosslessInt } from "../json";
import { array, boolean, count, DecodeError, isoUtc, literal, nullable, object, positiveInt, string, type Decoder } from "../validate";
import {
  ISSUE_TYPES,
  LIMITS,
  REDACTION_CATEGORIES,
  REVIEW_STATUSES,
  REVIEWER_DECISIONS,
  RULE_TYPES,
  type CaseList,
  type CreatedCase,
  type CreatedReview,
  type Dataset,
  type DatasetList,
  type FailureReview,
  type JsonValue,
  type MaskedCounts,
  type ReviewCaseLink,
  type ReviewSummary,
  type TestCase,
  type UpdatedReview,
} from "./review-types";
import { spanId, traceId } from "./ids";

// Backend bounds: 200 reviews per trace detail, 200 datasets and 200 cases per page.
export const MAX_REVIEWS = 200;
export const MAX_CASE_LINKS = 2000;
export const MAX_DATASETS = 200;
export const MAX_CASES = 200;
const MAX_JSON_DEPTH = 32;
const MAX_JSON_NODES = 20_000;

const reviewFields = {
  id: positiveInt,
  trace_id: traceId,
  span_id: nullable(spanId),
  issue_type: literal(...ISSUE_TYPES),
  evidence: string(LIMITS.evidence),
  hypothesis: string(LIMITS.hypothesis),
  review_status: literal(...REVIEW_STATUSES),
  reviewer_decision: literal(...REVIEWER_DECISIONS),
  created_by: string(LIMITS.actor),
  created_at: isoUtc,
  updated_at: isoUtc,
};

const caseLink: Decoder<ReviewCaseLink> = object<ReviewCaseLink>({ id: positiveInt, dataset_id: positiveInt, dataset_name: string(LIMITS.datasetName) });

function checked<T extends { review_status: string; reviewer_decision: string }>(decoder: Decoder<T>): Decoder<T> {
  return (v, path) => {
    const r = decoder(v, path);
    // §4.9 table check: a closed review always carries a decision.
    if (r.review_status === "closed" && r.reviewer_decision === "pending") throw new DecodeError(`${path}.reviewer_decision`, "a decision on a closed review");
    return r;
  };
}

export const reviewDecoder: Decoder<FailureReview> = checked(object<FailureReview>({ ...reviewFields, test_cases: array(caseLink, MAX_CASE_LINKS) }));

export const updatedReviewDecoder: Decoder<UpdatedReview> = checked(object<UpdatedReview>(reviewFields));

export const reviewSummaryDecoder: Decoder<ReviewSummary> = (v, path) => {
  const s = object<ReviewSummary>({ total: count, not_closed: count })(v, path);
  if (s.not_closed > s.total) throw new DecodeError(`${path}.not_closed`, "at most the total");
  return s;
};

const maskedCounts: Decoder<MaskedCounts> = (v, path) => {
  if (typeof v !== "object" || v === null || Array.isArray(v)) throw new DecodeError(path, "an object");
  const out: MaskedCounts = {};
  for (const [key, value] of Object.entries(v)) {
    const category = literal(...REDACTION_CATEGORIES)(key, `${path}.${key}`);
    out[category] = positiveInt(value, `${path}.${key}`);
  }
  return out;
};

export const createdReviewDecoder: Decoder<CreatedReview> = (v, path) => {
  const base = reviewDecoder(v, path);
  const redaction = object<CreatedReview["redaction"]>({
    applied: literal(true),
    masked: object<CreatedReview["redaction"]["masked"]>({ evidence: maskedCounts, hypothesis: maskedCounts }),
  })((v as { redaction?: unknown }).redaction, `${path}.redaction`);
  return { ...base, redaction };
};

export const datasetDecoder: Decoder<Dataset> = object<Dataset>({ id: positiveInt, name: string(LIMITS.datasetName), created_at: isoUtc, case_count: count });

export const datasetListDecoder: Decoder<DatasetList> = (v, path) => {
  const d = object<DatasetList>({ items: array(datasetDecoder, MAX_DATASETS), truncated: boolean, limit: positiveInt })(v, path);
  if (d.items.length > d.limit) throw new DecodeError(`${path}.items`, "at most limit items");
  return d;
};

/** Any JSON value, bounded in depth and size; integers above 2^53 stay `LosslessInt`. */
export const jsonValue: Decoder<JsonValue> = (v, path) => {
  let nodes = 0;
  const walk = (value: unknown, at: string, depth: number): JsonValue => {
    nodes += 1;
    if (nodes > MAX_JSON_NODES) throw new DecodeError(at, `at most ${MAX_JSON_NODES} JSON values`);
    if (value === null || typeof value === "boolean" || typeof value === "string") return value;
    if (typeof value === "number") {
      if (!Number.isFinite(value)) throw new DecodeError(at, "a finite number");
      return value;
    }
    if (value instanceof LosslessInt) return value;
    if (depth >= MAX_JSON_DEPTH) throw new DecodeError(at, `JSON nested at most ${MAX_JSON_DEPTH} levels`);
    if (Array.isArray(value)) return value.map((item, i) => walk(item, `${at}[${i}]`, depth + 1));
    if (typeof value === "object") {
      const out: { [key: string]: JsonValue } = {};
      for (const [key, item] of Object.entries(value)) {
        // defineProperty: a key such as "__proto__" stays an ordinary own property.
        Object.defineProperty(out, key, { value: walk(item, `${at}.${key}`, depth + 1), enumerable: true, writable: true, configurable: true });
      }
      return out;
    }
    throw new DecodeError(at, "a JSON value");
  };
  return walk(v, path, 0);
};

const ruleDecoder: Decoder<TestCase["rule"]> = (v, path) => {
  const value = jsonValue(v, path);
  if (typeof value !== "object" || value === null || Array.isArray(value) || value instanceof LosslessInt) throw new DecodeError(path, "a rule object");
  literal(...RULE_TYPES)(value.type, `${path}.type`);
  return value as TestCase["rule"];
};

export const testCaseDecoder: Decoder<TestCase> = (v, path) => {
  const c = object<TestCase>({
    id: positiveInt,
    dataset_id: positiveInt,
    origin_trace_id: nullable(traceId),
    origin_review_id: nullable(positiveInt),
    sanitized_input: string(LIMITS.sanitizedInput),
    expected_output: jsonValue,
    rule: ruleDecoder,
    redaction_applied: literal(true),
    approved_by: string(LIMITS.actor),
    approved_at: isoUtc,
    created_at: isoUtc,
  })(v, path);
  // D4: a case cites a trace exactly when it cites a review (a review may later be deleted).
  if (c.origin_review_id !== null && c.origin_trace_id === null) throw new DecodeError(`${path}.origin_trace_id`, "a trace for a review-origin case");
  return c;
};

export const caseListDecoder: Decoder<CaseList> = (v, path) => {
  const l = object<CaseList>({ dataset: datasetDecoder, items: array(testCaseDecoder, MAX_CASES), next_after: nullable(positiveInt), limit: positiveInt })(v, path);
  if (l.items.some((c) => c.dataset_id !== l.dataset.id)) throw new DecodeError(`${path}.items`, "cases of the requested dataset");
  return l;
};

export const createdCaseDecoder: Decoder<CreatedCase> = (v, path) => {
  const base = testCaseDecoder(v, path);
  const redaction = object<CreatedCase["redaction"]>({
    applied: literal(true),
    masked: object<CreatedCase["redaction"]["masked"]>({ sanitized_input: maskedCounts }),
  })((v as { redaction?: unknown }).redaction, `${path}.redaction`);
  return { ...base, redaction };
};

export const createdDatasetDecoder: Decoder<Dataset> = datasetDecoder;
