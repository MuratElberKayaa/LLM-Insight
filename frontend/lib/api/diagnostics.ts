/**
 * `GET /api/v1/traces/{trace_id}/diagnostics` (Phase 4A, ARCHITECTURE.md §5.6): types and a
 * strict decoder for contract `llminsight.diagnostics.v1`. Any other contract version is
 * refused rather than guessed at. These are automatic, metadata-only observations: every
 * hypothesis is `verified: false`, no language model and no content was used, and nothing
 * here is a human review or feeds one (Phase 4B, reports/phase-4b.md §2.4).
 */

import { array, boolean, count, DecodeError, literal, nullable, object, positiveInt, string, type Decoder } from "../validate";
import { spanId, traceId } from "./ids";
import { jsonValue } from "./review-schema";
import type { JsonValue } from "./review-types";
import { SPAN_KINDS, SPAN_ROLES, SPAN_STATUSES, TRACE_STATUSES, type SpanKind, type SpanRole, type SpanStatus, type TraceStatus } from "./trace-types";
import { TRACE_PROVENANCES, USAGE_PROVENANCES, type TraceProvenance, type UsageProvenance } from "./types";

export const DIAGNOSTICS_CONTRACT = "llminsight.diagnostics.v1";
const MAX_FINDINGS = 100;
const MAX_EVIDENCE = 25;
const MAX_LIST = 64;
const TEXT = string(2000);
const CODE = string(128);

export interface Limitation {
  code: string;
  text: string;
}

export interface EvidenceSpan {
  span_id: string;
  parent_span_id: string | null;
  role: SpanRole;
  kind: SpanKind;
  status: SpanStatus;
  error_type: string | null;
  duration_ns: string;
  provenance: UsageProvenance;
  provenance_verified: false;
  inference: JsonValue;
  usage: JsonValue;
  validation: JsonValue;
}

export interface Finding {
  id: string;
  category: string;
  group: string;
  subject: { type: "trace" | "logical_call" | "span" };
  trigger: string;
  observed_facts: { name: string; value: JsonValue }[];
  evidence: EvidenceSpan[];
  evidence_total: number;
  evidence_truncated: boolean;
  hypothesis: { code: string; statement: string; error_interpretation: { code: string; statement: string } | null; verified: false };
  established: string[];
  not_established: string[];
  limitations: Limitation[];
  data_complete: boolean;
  investigation_steps: string[];
}

export interface Diagnostics {
  contract_version: typeof DIAGNOSTICS_CONTRACT;
  ruleset_version: string;
  method: { kind: "deterministic_rules"; language_model_used: false; content_used: false };
  trace: { trace_id: string; status: TraceStatus; provenance: TraceProvenance; provenance_verified: false; span_count: number };
  coverage: { complete: boolean; spans_stored: number; spans_analyzed: number; spans_truncated: boolean; usage_truncated: boolean; limitations: Limitation[] };
  findings: Finding[];
  findings_total: number;
  findings_truncated: boolean;
  not_assessed: { category: string; reason: string }[];
}

const limitation: Decoder<Limitation> = object<Limitation>({ code: CODE, text: TEXT });

const nanos: Decoder<string> = (v, path) => {
  if (typeof v !== "string" || !/^\d{1,20}$/.test(v)) throw new DecodeError(path, "a nanosecond decimal string");
  return v;
};

const evidence: Decoder<EvidenceSpan> = object<EvidenceSpan>({
  span_id: spanId,
  parent_span_id: nullable(spanId),
  role: literal(...SPAN_ROLES),
  kind: literal(...SPAN_KINDS),
  status: literal(...SPAN_STATUSES),
  error_type: nullable(string(256)),
  duration_ns: nanos,
  provenance: literal(...USAGE_PROVENANCES),
  provenance_verified: literal(false),
  inference: jsonValue,
  usage: jsonValue,
  validation: jsonValue,
});

const finding: Decoder<Finding> = (v, path) => {
  const f = object<Finding>({
    id: string(256),
    category: CODE,
    group: CODE,
    subject: object<Finding["subject"]>({ type: literal("trace", "logical_call", "span") }),
    trigger: TEXT,
    observed_facts: array(object<Finding["observed_facts"][number]>({ name: CODE, value: jsonValue }), MAX_LIST),
    evidence: array(evidence, MAX_EVIDENCE),
    evidence_total: count,
    evidence_truncated: boolean,
    hypothesis: object<Finding["hypothesis"]>({
      code: CODE,
      statement: TEXT,
      error_interpretation: nullable(object<{ code: string; statement: string }>({ code: CODE, statement: TEXT })),
      verified: literal(false),
    }),
    established: array(TEXT, MAX_LIST),
    not_established: array(TEXT, MAX_LIST),
    limitations: array(limitation, MAX_LIST),
    data_complete: boolean,
    investigation_steps: array(TEXT, MAX_LIST),
  })(v, path);
  if (f.evidence_total < f.evidence.length || f.evidence_truncated !== f.evidence_total > f.evidence.length) {
    throw new DecodeError(`${path}.evidence_total`, "a consistent evidence count");
  }
  return f;
};

export const diagnosticsDecoder: Decoder<Diagnostics> = (v, path) => {
  const d = object<Diagnostics>({
    contract_version: literal(DIAGNOSTICS_CONTRACT),
    ruleset_version: CODE,
    method: object<Diagnostics["method"]>({ kind: literal("deterministic_rules"), language_model_used: literal(false), content_used: literal(false) }),
    trace: object<Diagnostics["trace"]>({
      trace_id: traceId,
      status: literal(...TRACE_STATUSES),
      provenance: literal(...TRACE_PROVENANCES),
      provenance_verified: literal(false),
      span_count: positiveInt,
    }),
    coverage: object<Diagnostics["coverage"]>({
      complete: boolean,
      spans_stored: count,
      spans_analyzed: count,
      spans_truncated: boolean,
      usage_truncated: boolean,
      limitations: array(limitation, MAX_LIST),
    }),
    findings: array(finding, MAX_FINDINGS),
    findings_total: count,
    findings_truncated: boolean,
    not_assessed: array(object<{ category: string; reason: string }>({ category: CODE, reason: TEXT }), MAX_LIST),
  })(v, path);
  if (d.findings_total < d.findings.length || d.findings_truncated !== d.findings_total > d.findings.length) {
    throw new DecodeError(`${path}.findings_total`, "a consistent finding count");
  }
  return d;
};
