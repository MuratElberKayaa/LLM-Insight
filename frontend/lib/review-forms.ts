/**
 * Phase 4B form parsing for the review and test-case Server Actions. Pure (no I/O), so it is
 * unit-tested directly. Everything in a FormData is untrusted: each field is read once, as a
 * string, checked against the same rules the backend enforces (the backend stays
 * authoritative), and turned into the exact JSON body of one write endpoint.
 *
 * Nothing here fills a field from telemetry or from a diagnostic finding: every value comes
 * from what the person typed or chose in the form (reports/phase-4b.md §2.4).
 */

import { isProjectId } from "./project";
import { parseJsonLossless, LosslessInt, InexactNumber } from "./json";
import {
  ISSUE_TYPES,
  LIMITS,
  MATCH_MODES,
  OUTPUT_FORMATS,
  REVIEW_STATUSES,
  REVIEWER_DECISIONS,
  RULE_TYPES,
  RULE_VERSION,
  type IssueType,
  type ReviewerDecision,
  type ReviewStatus,
  type RuleType,
} from "./api/review-types";

const TRACE_ID_RE = /^[0-9a-f]{32}$/;
const SPAN_ID_RE = /^[0-9a-f]{16}$/;
const RECORD_ID_RE = /^[1-9][0-9]{0,18}$/;
const PATH_RE = /^[A-Za-z0-9_-]{1,64}(?:\.[A-Za-z0-9_-]{1,64}){0,9}$/;
// Bidi overrides, zero-width and other format characters, controls other than tab/newline.
// Written as escapes so this source file holds no invisible character itself.
const UNSAFE_TEXT_RE = /[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u00ad\u061c\u180e\u200b-\u200f\u2028-\u202e\u2060-\u206f\ufeff\ufff9-\ufffb]/;
const UNSAFE_LABEL_RE = /[\u0000-\u001f\u007f-\u009f\u00ad\u061c\u180e\u200b-\u200f\u2028-\u202e\u2060-\u206f\ufeff\ufff9-\ufffb]/;

export type Parsed<T> = { ok: true; value: T } | { ok: false; message: string; fields: string[] };

function fail<T>(field: string, message: string): Parsed<T> {
  return { ok: false, message, fields: [field] };
}

/** One string field; a missing field or a File is "". */
function str(fd: FormData, name: string): string {
  const v = fd.get(name);
  return typeof v === "string" ? v : "";
}

function isBigintId(value: string): boolean {
  return RECORD_ID_RE.test(value) && BigInt(value) <= 2n ** 63n - 1n;
}

function freeText(fd: FormData, name: string, label: string, max: number): Parsed<string> {
  const text = str(fd, name).replace(/\r\n?/g, "\n").trim();
  if (text.length === 0) return fail(name, `${label} is required.`);
  if (text.length > max) return fail(name, `${label} must be at most ${max} characters.`);
  if (UNSAFE_TEXT_RE.test(text)) return fail(name, `${label} contains control or invisible formatting characters.`);
  return { ok: true, value: text };
}

function labelText(fd: FormData, name: string, label: string, max: number): Parsed<string> {
  const text = str(fd, name).trim();
  if (text.length === 0 || text.length > max) return fail(name, `${label} must be 1–${max} characters.`);
  if (UNSAFE_LABEL_RE.test(text)) return fail(name, `${label} must be a single line without control characters.`);
  return { ok: true, value: text };
}

function project(fd: FormData): Parsed<string> {
  const p = str(fd, "project");
  return isProjectId(p) ? { ok: true, value: p } : fail("project", "The project of this page is not valid.");
}

function oneOf<T extends string>(fd: FormData, name: string, values: readonly T[], message: string): Parsed<T> {
  const v = str(fd, name);
  return (values as readonly string[]).includes(v) ? { ok: true, value: v as T } : fail(name, message);
}

// ---------------------------------------------------------------- failure review (CR-03)
export interface ReviewInput {
  project: string;
  body: { trace_id: string; span_id?: string; issue_type: IssueType; evidence: string; hypothesis: string; created_by: string };
}

export function parseReviewForm(fd: FormData): Parsed<ReviewInput> {
  const p = project(fd);
  if (!p.ok) return p;
  const traceId = str(fd, "trace_id");
  if (!TRACE_ID_RE.test(traceId)) return fail("trace_id", "The trace of this page is not valid.");
  const spanId = str(fd, "span_id");
  if (spanId !== "" && !SPAN_ID_RE.test(spanId)) return fail("span_id", "Choose a span of this trace, or none.");
  const issue = oneOf(fd, "issue_type", ISSUE_TYPES, "Choose one of the listed issue types.");
  if (!issue.ok) return issue;
  const evidence = freeText(fd, "evidence", "Evidence", LIMITS.evidence);
  if (!evidence.ok) return evidence;
  const hypothesis = freeText(fd, "hypothesis", "Hypothesis", LIMITS.hypothesis);
  if (!hypothesis.ok) return hypothesis;
  const createdBy = labelText(fd, "created_by", "Reviewer name", LIMITS.actor);
  if (!createdBy.ok) return createdBy;
  return {
    ok: true,
    value: {
      project: p.value,
      body: {
        trace_id: traceId,
        ...(spanId === "" ? {} : { span_id: spanId }),
        issue_type: issue.value,
        evidence: evidence.value,
        hypothesis: hypothesis.value,
        created_by: createdBy.value,
      },
    },
  };
}

export interface DecisionInput {
  project: string;
  traceId: string;
  reviewId: string;
  body: { review_status: ReviewStatus; reviewer_decision: ReviewerDecision };
}

export function parseDecisionForm(fd: FormData): Parsed<DecisionInput> {
  const p = project(fd);
  if (!p.ok) return p;
  const traceId = str(fd, "trace_id");
  if (!TRACE_ID_RE.test(traceId)) return fail("trace_id", "The trace of this page is not valid.");
  const reviewId = str(fd, "review_id");
  if (!isBigintId(reviewId)) return fail("review_id", "The review is not valid.");
  const status = oneOf(fd, "review_status", REVIEW_STATUSES, "Choose a review status.");
  if (!status.ok) return status;
  const decision = oneOf(fd, "reviewer_decision", REVIEWER_DECISIONS, "Choose a reviewer decision.");
  if (!decision.ok) return decision;
  if (status.value === "closed" && decision.value === "pending") {
    return { ok: false, message: "A closed review needs a decision other than pending.", fields: ["review_status", "reviewer_decision"] };
  }
  return { ok: true, value: { project: p.value, traceId, reviewId, body: { review_status: status.value, reviewer_decision: decision.value } } };
}

// ---------------------------------------------------------------- dataset
export interface DatasetInput {
  project: string;
  body: { name: string };
}

export function parseDatasetForm(fd: FormData): Parsed<DatasetInput> {
  const p = project(fd);
  if (!p.ok) return p;
  const name = labelText(fd, "name", "Dataset name", LIMITS.datasetName);
  if (!name.ok) return name;
  return { ok: true, value: { project: p.value, body: { name: name.value } } };
}

// ---------------------------------------------------------------- test case (§4.10, rule contract v1, D4)
export interface CaseInput {
  project: string;
  datasetId: string;
  traceId: string;
  body: {
    approved: true;
    approved_by: string;
    sanitized_input: string;
    expected_output: unknown;
    rule: Record<string, unknown>;
    origin_trace_id: string;
    origin_review_id: number;
  };
}

/** User-typed JSON: valid, finite, and every number exactly representable: an integer beyond ±2^53 or a
 * decimal with more digits than a JavaScript number keeps would be sent as a different value, so it is refused
 * (the backend stores numbers exactly; send such values through the API directly). */
export function parseUserJson(text: string, field: string, label: string): Parsed<unknown> {
  if (new TextEncoder().encode(text).length > LIMITS.assertionJson) return fail(field, `${label} must be at most ${LIMITS.assertionJson} bytes.`);
  let value: unknown;
  try {
    value = parseJsonLossless(text, { refuseInexact: true });
  } catch {
    return fail(field, `${label} is not valid JSON.`);
  }
  const stack: unknown[] = [value];
  while (stack.length > 0) {
    const v = stack.pop();
    if (v instanceof LosslessInt) return fail(field, `${label} contains an integer too large to keep exactly.`);
    if (v instanceof InexactNumber) return fail(field, `${label} contains a number with more digits than this form can keep exactly.`);
    if (Array.isArray(v)) stack.push(...v);
    else if (typeof v === "object" && v !== null) stack.push(...Object.values(v));
  }
  return { ok: true, value };
}

function rule(fd: FormData, type: RuleType): Parsed<Record<string, unknown>> {
  const version = RULE_VERSION;
  if (type === "exact") {
    const format = oneOf(fd, "rule_output_format", OUTPUT_FORMATS, "Choose whether the output is compared as text or as JSON.");
    if (!format.ok) return format;
    return { ok: true, value: { version, type, output_format: format.value } };
  }
  if (type === "regex") {
    const pattern = str(fd, "rule_pattern");
    if (pattern.length === 0 || pattern.length > LIMITS.pattern) return fail("rule_pattern", `The pattern must be 1–${LIMITS.pattern} characters.`);
    const match = oneOf(fd, "rule_match", MATCH_MODES, "Choose whether the pattern must match the whole output or anywhere in it.");
    if (!match.ok) return match;
    return { ok: true, value: { version, type, pattern, match: match.value } };
  }
  if (type === "field_match") {
    const fields = str(fd, "rule_fields")
      .split(/[\s,]+/)
      .filter((f) => f.length > 0);
    if (fields.length === 0 || fields.length > 50 || !fields.every((f) => PATH_RE.test(f))) {
      return fail("rule_fields", "List 1–50 field paths such as summary or meta.lang, separated by commas.");
    }
    if (new Set(fields).size !== fields.length) return fail("rule_fields", "A field path is listed twice.");
    return { ok: true, value: { version, type, fields } };
  }
  const schema = parseUserJson(str(fd, "rule_schema").trim(), "rule_schema", "The JSON Schema");
  if (!schema.ok) return schema;
  if (typeof schema.value !== "boolean" && (typeof schema.value !== "object" || schema.value === null || Array.isArray(schema.value))) {
    return fail("rule_schema", "The JSON Schema must be an object (or true/false).");
  }
  return { ok: true, value: { version, type, schema: schema.value } };
}

export function parseCaseForm(fd: FormData): Parsed<CaseInput> {
  const p = project(fd);
  if (!p.ok) return p;
  const datasetId = str(fd, "dataset_id");
  if (!isBigintId(datasetId)) return fail("dataset_id", "Choose a dataset.");
  const traceId = str(fd, "trace_id");
  if (!TRACE_ID_RE.test(traceId)) return fail("trace_id", "The source trace is not valid.");
  const reviewId = str(fd, "review_id");
  if (!isBigintId(reviewId) || BigInt(reviewId) > BigInt(Number.MAX_SAFE_INTEGER)) return fail("review_id", "The source review is not valid.");
  const input = freeText(fd, "sanitized_input", "Test input", LIMITS.sanitizedInput);
  if (!input.ok) return input;
  const type = oneOf(fd, "rule_type", RULE_TYPES, "Choose how the output will be checked.");
  if (!type.ok) return type;
  const r = rule(fd, type.value);
  if (!r.ok) return r;
  const expectedText = str(fd, "expected_output").trim();
  let expected: unknown = null;
  if (expectedText.length > 0) {
    const e = parseUserJson(expectedText, "expected_output", "The expected output");
    if (!e.ok) return e;
    expected = e.value;
  }
  const approvedBy = labelText(fd, "approved_by", "Approver name", LIMITS.actor);
  if (!approvedBy.ok) return approvedBy;
  // The explicit approval: a test case is stored only when this box was ticked (T34).
  if (str(fd, "approve") !== "yes") return fail("approve", "Tick the approval box to store this test case.");
  return {
    ok: true,
    value: {
      project: p.value,
      datasetId,
      traceId,
      body: {
        approved: true,
        approved_by: approvedBy.value,
        sanitized_input: input.value,
        expected_output: expected,
        rule: r.value,
        origin_trace_id: traceId,
        origin_review_id: Number(reviewId),
      },
    },
  };
}
