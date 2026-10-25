/**
 * Phase 4B: human failure review and Trace-to-Testset in the dashboard. Pure units and
 * server-rendered components; the browser flow is covered by tests/e2e/phase4b.spec.ts.
 * All values are SYNTHETIC.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, it } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { CaseList } from "../../components/datasets/case-list";
import { TestCaseForm } from "../../components/datasets/test-case-form";
import { DiagnosticsPanel } from "../../components/reviews/diagnostics-panel";
import { ReviewSection } from "../../components/reviews/review-section";
import { ReviewCell } from "../../components/traces/trace-list";
import { invalidForm, writeFailure } from "../../lib/action-state";
import { diagnosticsDecoder, type Diagnostics } from "../../lib/api/diagnostics";
import { caseListDecoder, createdCaseDecoder, createdReviewDecoder, datasetListDecoder, reviewDecoder, reviewSummaryDecoder, testCaseDecoder } from "../../lib/api/review-schema";
import type { FailureReview, TestCase } from "../../lib/api/review-types";
import { traceDetailDecoder, traceListDecoder } from "../../lib/api/trace-schema";
import { LosslessInt, parseJsonLossless } from "../../lib/json";
import { actionRequestProblem, isAllowedMethod, isWritePage, type ActionRequest } from "../../lib/request-guard";
import { parseCaseForm, parseDatasetForm, parseDecisionForm, parseReviewForm, parseUserJson } from "../../lib/review-forms";
import { reviewPath, sendJson } from "../../lib/server/backend";
import type { DashboardConfig } from "../../lib/server/config";
import { decode } from "../../lib/validate";
import { makeDetail, makeList, makeSpan, makeTrace, tid, toWire } from "../fixtures/traces";

const TOKEN = "SYNTHETIC-test-token-0123456789abcdef0123456789";
const config: DashboardConfig = { baseUrl: new URL("http://127.0.0.1:8000"), token: TOKEN, defaultProjectId: "7", timeoutMs: 200 };
const TRACE = "a".repeat(31) + "1";
const SPAN = "b".repeat(15) + "1";
const XSS = `<script>alert("x")</script><img src=x onerror=alert(1)>`;
const html = (el: React.ReactElement) => renderToStaticMarkup(el);

function review(over: Partial<FailureReview> = {}): FailureReview {
  return {
    id: 11,
    trace_id: TRACE,
    span_id: null,
    issue_type: "schema_violation",
    evidence: "SYNTHETIC: validate_output failed rule json_schema:summary_v1",
    hypothesis: "SYNTHETIC: prompt v1 may lack an explicit JSON instruction",
    review_status: "open",
    reviewer_decision: "pending",
    created_by: "reviewer-a",
    created_at: "2026-10-25T18:00:00.123456Z",
    updated_at: "2026-10-25T18:00:00.123456Z",
    test_cases: [],
    ...over,
  };
}

function testCase(over: Partial<TestCase> = {}): TestCase {
  return {
    id: 5,
    dataset_id: 3,
    origin_trace_id: TRACE,
    origin_review_id: 11,
    sanitized_input: "SYNTHETIC: summarize 'v2 adds dark mode'",
    expected_output: { summary: "SYNTHETIC" },
    rule: { version: 1, type: "json_schema", schema: { type: "object" } },
    redaction_applied: true,
    approved_by: "reviewer-a",
    approved_at: "2026-10-25T18:05:00.000001Z",
    created_at: "2026-10-25T18:05:00.000001Z",
    ...over,
  };
}

function form(values: Record<string, string>): FormData {
  const fd = new FormData();
  for (const [k, v] of Object.entries(values)) fd.set(k, v);
  return fd;
}

// ---------------------------------------------------------------- request guard (CSRF)
describe("request guard: Server Action POSTs", () => {
  const ok: ActionRequest = { host: "127.0.0.1:3001", origin: "http://127.0.0.1:3001", secFetchSite: "same-origin", nextAction: "7f3a9c0d1e2b4a5f6789abcdef0123456789abcd" };

  it("allows POST only on the pages that host forms", () => {
    assert.equal(isAllowedMethod("POST"), false);
    for (const p of [`/traces/${TRACE}`, `/traces/${TRACE}/reviews/12/test-case`, "/datasets"]) assert.equal(isWritePage(p), true, p);
    for (const p of ["/", "/traces", "/models", "/datasets/3", `/traces/${TRACE}/diagnostics`, `/traces/${TRACE.toUpperCase()}`, `/traces/${TRACE}/reviews/0/test-case`, "/api/v1/datasets"]) {
      assert.equal(isWritePage(p), false, p);
    }
  });

  it("accepts a same-origin Server Action request", () => {
    assert.equal(actionRequestProblem(ok), null);
    assert.equal(actionRequestProblem({ ...ok, secFetchSite: null }), null, "older browsers omit Sec-Fetch-Site");
    assert.equal(actionRequestProblem({ ...ok, host: "localhost:3001", origin: "http://localhost:3001" }), null);
  });

  it("refuses cross-site, origin-less and non-action posts", () => {
    const cases: [Partial<ActionRequest>, string][] = [
      [{ origin: null }, "origin missing"],
      [{ origin: "https://evil.example" }, "cross-origin"],
      [{ origin: "http://127.0.0.1:3002" }, "cross-origin"],
      [{ origin: "http://localhost:3001" }, "cross-origin"],
      [{ origin: "null" }, "origin malformed"],
      [{ origin: "file:///x" }, "cross-origin"],
      [{ secFetchSite: "cross-site" }, "cross-site"],
      [{ secFetchSite: "same-site" }, "cross-site"],
      [{ nextAction: null }, "not a server action"],
      [{ nextAction: "x" }, "not a server action"],
      [{ nextAction: "<script>" }, "not a server action"],
      [{ host: "evil.example:3001", origin: "http://evil.example:3001" }, "host"],
    ];
    for (const [change, reason] of cases) assert.equal(actionRequestProblem({ ...ok, ...change }), reason, JSON.stringify(change));
  });
});

// ---------------------------------------------------------------- form parsing
describe("review form parsing", () => {
  const valid = {
    project: "7",
    trace_id: TRACE,
    span_id: "",
    issue_type: "schema_violation",
    evidence: "  SYNTHETIC evidence\r\nline 2 ",
    hypothesis: "SYNTHETIC hypothesis",
    created_by: " reviewer-a ",
  };

  it("builds exactly the CR-03 body from what the person entered", () => {
    const r = parseReviewForm(form(valid));
    assert.ok(r.ok);
    assert.deepEqual(r.value, {
      project: "7",
      body: { trace_id: TRACE, issue_type: "schema_violation", evidence: "SYNTHETIC evidence\nline 2", hypothesis: "SYNTHETIC hypothesis", created_by: "reviewer-a" },
    });
    const withSpan = parseReviewForm(form({ ...valid, span_id: SPAN }));
    assert.ok(withSpan.ok && withSpan.value.body.span_id === SPAN);
  });

  it("refuses anything outside the contract, naming the field", () => {
    const cases: [Record<string, string>, string][] = [
      [{ issue_type: "" }, "issue_type"],
      [{ issue_type: "inference_call_failed" }, "issue_type"], // a diagnostic category is not an issue type
      [{ issue_type: "root_cause" }, "issue_type"],
      [{ project: "0" }, "project"],
      [{ trace_id: "nope" }, "trace_id"],
      [{ span_id: "XYZ" }, "span_id"],
      [{ evidence: "   " }, "evidence"],
      [{ evidence: "x".repeat(4001) }, "evidence"],
      [{ hypothesis: "hidden\u202etext" }, "hypothesis"],
      [{ created_by: "two\nlines" }, "created_by"],
      [{ created_by: "x".repeat(101) }, "created_by"],
    ];
    for (const [change, field] of cases) {
      const r = parseReviewForm(form({ ...valid, ...change }));
      assert.ok(!r.ok, JSON.stringify(change));
      assert.deepEqual(r.fields, [field]);
    }
  });

  it("decision: enums only, and closed needs a decision", () => {
    const base = { project: "7", trace_id: TRACE, review_id: "11", review_status: "in_review", reviewer_decision: "inconclusive" };
    const r = parseDecisionForm(form(base));
    assert.ok(r.ok && r.value.reviewId === "11");
    assert.deepEqual(r.ok && r.value.body, { review_status: "in_review", reviewer_decision: "inconclusive" });
    const closedPending = parseDecisionForm(form({ ...base, review_status: "closed", reviewer_decision: "pending" }));
    assert.ok(!closedPending.ok && closedPending.fields.length === 2);
    for (const change of [{ review_status: "approved" }, { reviewer_decision: "proven" }, { review_id: "0" }, { review_id: "99999999999999999999" }]) {
      assert.equal(parseDecisionForm(form({ ...base, ...change })).ok, false, JSON.stringify(change));
    }
  });

  it("dataset names are single-line labels", () => {
    assert.ok(parseDatasetForm(form({ project: "7", name: " regression v1 " })).ok);
    assert.equal(parseDatasetForm(form({ project: "7", name: "a\nb" })).ok, false);
    assert.equal(parseDatasetForm(form({ project: "7", name: "" })).ok, false);
  });
});

describe("test case form parsing", () => {
  const valid = {
    project: "7",
    dataset_id: "3",
    trace_id: TRACE,
    review_id: "11",
    sanitized_input: "SYNTHETIC input",
    rule_type: "json_schema",
    rule_schema: '{"type":"object","required":["summary"]}',
    expected_output: '{"summary":"SYNTHETIC"}',
    approved_by: "reviewer-a",
    approve: "yes",
  };

  it("sends approved: true only after the explicit approval, with the typed content", () => {
    const r = parseCaseForm(form(valid));
    assert.ok(r.ok);
    assert.deepEqual(r.value.body, {
      approved: true,
      approved_by: "reviewer-a",
      sanitized_input: "SYNTHETIC input",
      expected_output: { summary: "SYNTHETIC" },
      rule: { version: 1, type: "json_schema", schema: { type: "object", required: ["summary"] } },
      origin_trace_id: TRACE,
      origin_review_id: 11,
    });
    for (const approve of ["", "on", "true", "no"]) {
      const refused = parseCaseForm(form({ ...valid, approve }));
      assert.ok(!refused.ok && refused.fields[0] === "approve", approve);
    }
    const missing = form(valid);
    missing.delete("approve");
    assert.equal(parseCaseForm(missing).ok, false);
  });

  it("builds each rule type as contract llminsight.rule.v1 and refuses malformed parameters", () => {
    const exact = parseCaseForm(form({ ...valid, rule_type: "exact", rule_output_format: "json" }));
    assert.ok(exact.ok && JSON.stringify(exact.value.body.rule) === '{"version":1,"type":"exact","output_format":"json"}');
    const exactText = parseCaseForm(form({ ...valid, rule_type: "exact", rule_output_format: "text", expected_output: '"ACK"' }));
    assert.ok(exactText.ok && JSON.stringify(exactText.value.body.rule) === '{"version":1,"type":"exact","output_format":"text"}');
    const fields = parseCaseForm(form({ ...valid, rule_type: "field_match", rule_fields: "summary, meta.lang" }));
    assert.ok(fields.ok);
    assert.deepEqual(fields.value.body.rule, { version: 1, type: "field_match", fields: ["summary", "meta.lang"] });
    const regex = parseCaseForm(form({ ...valid, rule_type: "regex", rule_pattern: "^\\{", rule_match: "full" }));
    assert.ok(regex.ok);
    assert.deepEqual(regex.value.body.rule, { version: 1, type: "regex", pattern: "^\\{", match: "full" });
    const noExpected = parseCaseForm(form({ ...valid, expected_output: "  " }));
    assert.ok(noExpected.ok && noExpected.value.body.expected_output === null);
    const bad: [Record<string, string>, string][] = [
      [{ rule_type: "" }, "rule_type"],
      [{ rule_type: "llm_judge" }, "rule_type"],
      [{ rule_schema: "{not json" }, "rule_schema"],
      [{ rule_schema: "[1]" }, "rule_schema"],
      [{ rule_type: "field_match", rule_fields: "a b..c" }, "rule_fields"],
      [{ rule_type: "field_match", rule_fields: "a,a" }, "rule_fields"],
      [{ rule_type: "regex", rule_pattern: "", rule_match: "full" }, "rule_pattern"],
      [{ rule_type: "regex", rule_pattern: "a" }, "rule_match"],
      [{ rule_type: "regex", rule_pattern: "a", rule_match: "contains" }, "rule_match"],
      [{ rule_type: "exact" }, "rule_output_format"],
      [{ rule_type: "exact", rule_output_format: "xml" }, "rule_output_format"],
      [{ expected_output: '{"price": 0.1000000000000000000001}' }, "expected_output"],
      [{ expected_output: '{"n": 12345678901234567890}' }, "expected_output"],
      [{ expected_output: "NaN" }, "expected_output"],
      [{ dataset_id: "" }, "dataset_id"],
      [{ review_id: "abc" }, "review_id"],
      [{ sanitized_input: "" }, "sanitized_input"],
    ];
    for (const [change, field] of bad) {
      const r = parseCaseForm(form({ ...valid, ...change }));
      assert.ok(!r.ok, JSON.stringify(change));
      assert.equal(r.fields[0], field, JSON.stringify(change));
    }
  });

  it("parseUserJson keeps exact values or refuses", () => {
    const r = parseUserJson('{"a":[1,2.5,"x",null,true]}', "f", "F");
    assert.ok(r.ok);
    assert.equal(parseUserJson("9007199254740993", "f", "F").ok, false);
    // Decimals: refused only when JavaScript would send a different value.
    for (const exact of ["0.1", "1.10", "2.5e3", "-0.0", "1E-7", "123.456"]) assert.ok(parseUserJson(`[${exact}]`, "f", "F").ok, exact);
    for (const inexact of ["0.1000000000000000000001", "3.14159265358979323846", "1.00000000000000001"]) {
      const refused = parseUserJson(`[${inexact}]`, "f", "F");
      assert.ok(!refused.ok && /more digits/.test(refused.message), inexact);
    }
    assert.equal(parseUserJson("x".repeat(20_000), "f", "F").ok, false);
  });
});

// ---------------------------------------------------------------- decoders
describe("Phase 4B decoders", () => {
  it("decode reviews, summaries and created responses", () => {
    assert.ok(decode(reviewDecoder, review()).ok);
    assert.ok(decode(reviewDecoder, review({ test_cases: [{ id: 1, dataset_id: 2, dataset_name: "d" }] })).ok);
    const closedPending = decode(reviewDecoder, review({ review_status: "closed", reviewer_decision: "pending" }));
    assert.deepEqual(closedPending, { ok: false, path: "$.reviewer_decision" });
    assert.equal(decode(reviewDecoder, { ...review(), issue_type: "root_cause" }).ok, false);
    assert.equal(decode(reviewSummaryDecoder, { total: 1, not_closed: 2 }).ok, false);
    const created = decode(createdReviewDecoder, { ...review(), redaction: { applied: true, masked: { evidence: { email: 1 }, hypothesis: {} } } });
    assert.ok(created.ok && created.value.redaction.masked.evidence.email === 1);
    assert.equal(decode(createdReviewDecoder, { ...review(), redaction: { applied: true, masked: { evidence: { name: 1 }, hypothesis: {} } } }).ok, false);
  });

  it("trace list items carry review counts and the detail carries reviews", () => {
    const list = toWire(makeList([makeTrace()])) as { items: Record<string, unknown>[] };
    assert.ok(decode(traceListDecoder, list).ok);
    delete list.items[0]!.reviews;
    assert.deepEqual(decode(traceListDecoder, list), { ok: false, path: "$.items[0].reviews" });
    const detail = toWire(makeDetail([makeSpan({ id: 1, start: 0n, end: 1000n })])) as Record<string, unknown>;
    assert.ok(decode(traceDetailDecoder, { ...detail, reviews: [review()] }).ok);
    const missing = { ...detail };
    delete missing.reviews_truncated;
    assert.equal(decode(traceDetailDecoder, missing).ok, false);
  });

  it("decode datasets and test cases; big integers stay exact", () => {
    assert.ok(decode(datasetListDecoder, { items: [{ id: 3, name: "d", created_at: "2026-10-25T18:00:00.000001Z", case_count: 0 }], truncated: false, limit: 200 }).ok);
    const big = { ...testCase(), expected_output: { n: new LosslessInt("12345678901234567890") } };
    const r = decode(testCaseDecoder, big);
    assert.ok(r.ok && (r.value.expected_output as { n: LosslessInt }).n.digits === "12345678901234567890");
    assert.equal(decode(testCaseDecoder, { ...testCase(), redaction_applied: false }).ok, false, "a case is always redacted");
    assert.equal(decode(testCaseDecoder, { ...testCase(), rule: { type: "llm_judge" } }).ok, false);
    assert.equal(decode(testCaseDecoder, { ...testCase(), origin_trace_id: null }).ok, false, "a review-origin case names its trace");
    assert.ok(decode(testCaseDecoder, { ...testCase(), origin_trace_id: null, origin_review_id: null }).ok);
    const dataset = { id: 3, name: "d", created_at: "2026-10-25T18:00:00.000001Z", case_count: 1 };
    assert.ok(decode(caseListDecoder, { dataset, items: [testCase()], next_after: null, limit: 50 }).ok);
    assert.equal(decode(caseListDecoder, { dataset, items: [testCase({ dataset_id: 4 })], next_after: null, limit: 50 }).ok, false);
    assert.ok(decode(createdCaseDecoder, { ...testCase(), redaction: { applied: true, masked: { sanitized_input: { phone: 2 } } } }).ok);
  });

  it("decodes recorded real diagnostics responses (contract fixture)", () => {
    const file = path.resolve(__dirname, "..", "..", "..", "tests", "fixtures", "diagnostics.live.json");
    const recorded = (parseJsonLossless(readFileSync(file, "utf8")) as { response: Record<string, unknown> }).response;
    for (const [name, body] of Object.entries(recorded)) {
      const r = decode(diagnosticsDecoder, body);
      assert.equal(r.ok, true, `${name}: ${r.ok ? "" : r.path}`);
      if (r.ok) {
        assert.ok(r.value.findings.length > 0, name);
        for (const f of r.value.findings) assert.equal(f.hypothesis.verified, false);
      }
    }
  });

  it("diagnostics: only contract v1, always unverified, no model, no content", () => {
    const d = diagnosticsFixture();
    assert.ok(decode(diagnosticsDecoder, d).ok);
    assert.equal(decode(diagnosticsDecoder, { ...d, contract_version: "llminsight.diagnostics.v2" }).ok, false);
    assert.equal(decode(diagnosticsDecoder, { ...d, method: { ...d.method, language_model_used: true } }).ok, false);
    assert.equal(decode(diagnosticsDecoder, { ...d, method: { ...d.method, content_used: true } }).ok, false);
    const verified = structuredClone(d);
    (verified.findings[0]!.hypothesis as { verified: boolean }).verified = true;
    assert.deepEqual(decode(diagnosticsDecoder, verified), { ok: false, path: "$.findings[0].hypothesis.verified" });
  });
});

function diagnosticsFixture(): Diagnostics & { method: Diagnostics["method"] & { inputs: string[] } } {
  return {
    contract_version: "llminsight.diagnostics.v1",
    ruleset_version: "llminsight.diagnostics.rules.v1",
    method: { kind: "deterministic_rules", language_model_used: false, content_used: false, inputs: ["traces", "spans", "usage_records"] },
    trace: { trace_id: TRACE, status: "error", provenance: "synthetic", provenance_verified: false, span_count: 2 },
    coverage: { complete: true, spans_stored: 2, spans_analyzed: 2, spans_truncated: false, usage_truncated: false, limitations: [{ code: "stored_metadata_only", text: "Only stored metadata." }] },
    findings: [
      {
        id: `application_validation_failed:span:${SPAN}`,
        category: "application_validation_failed",
        group: "application_signal",
        subject: { type: "span" },
        trigger: "llminsight.validation.passed is false",
        observed_facts: [{ name: "rule", value: "json_schema:summary_v1" }],
        evidence: [
          {
            span_id: SPAN,
            parent_span_id: null,
            role: "root",
            kind: "INTERNAL",
            status: "ERROR",
            error_type: null,
            duration_ns: "1000",
            provenance: "synthetic",
            provenance_verified: false,
            inference: null,
            usage: null,
            validation: { rule: "json_schema:summary_v1", passed: false },
          },
        ],
        evidence_total: 1,
        evidence_truncated: false,
        hypothesis: { code: "output_failed_application_check", statement: "Possible explanation (not verified): the output did not meet the check.", error_interpretation: null, verified: false },
        established: ["The span recorded a failed validation."],
        not_established: ["Why the output failed."],
        limitations: [],
        data_complete: true,
        investigation_steps: ["Compare the output with the rule."],
      },
    ],
    findings_total: 1,
    findings_truncated: false,
    not_assessed: [{ category: "root_cause", reason: "Not derivable from metadata." }],
  };
}

// ---------------------------------------------------------------- write client
describe("sendJson", () => {
  let warnings: string[] = [];
  const originalWarn = console.warn;
  beforeEach(() => {
    warnings = [];
    console.warn = (...args: unknown[]) => warnings.push(args.map(String).join(" "));
  });
  afterEach(() => {
    console.warn = originalWarn;
    for (const w of warnings) assert.ok(!w.includes(TOKEN) && !w.includes("SYNTHETIC body"), "token or body leaked into a log line");
  });

  interface Call {
    url: URL;
    init: RequestInit;
  }
  function fake(respond: (url: URL, init: RequestInit) => Response, calls: Call[]): typeof fetch {
    return (async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(input instanceof Request ? input.url : input.toString());
      calls.push({ url, init: init ?? {} });
      return respond(url, init ?? {});
    }) as typeof fetch;
  }
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

  it("POSTs JSON with the bearer token to the allowlisted path and decodes the answer", async () => {
    const calls: Call[] = [];
    const body = { trace_id: TRACE, issue_type: "unknown", evidence: "SYNTHETIC body", hypothesis: "h", created_by: "c" };
    const created = { ...review(), redaction: { applied: true, masked: { evidence: {}, hypothesis: {} } } };
    const r = await sendJson(config, "POST", "/api/v1/failure-reviews", "7", body, createdReviewDecoder, { fetch: fake(() => json(created, 201), calls) });
    assert.ok(r.ok);
    const { url, init } = calls[0]!;
    assert.equal(url.href, "http://127.0.0.1:8000/api/v1/failure-reviews?project=7");
    assert.equal(init.method, "POST");
    assert.equal(init.redirect, "error");
    assert.equal(init.cache, "no-store");
    assert.deepEqual(init.headers, { Authorization: `Bearer ${TOKEN}`, Accept: "application/json", "Content-Type": "application/json" });
    assert.deepEqual(JSON.parse(String(init.body)), body);
  });

  it("refuses unlisted method/path pairs and missing project scope without a request", async () => {
    const calls: Call[] = [];
    const deps = { fetch: fake(() => json({}), calls) };
    const attempts = [
      sendJson(config, "PATCH", "/api/v1/failure-reviews", "7", {}, reviewDecoder, deps),
      sendJson(config, "POST", "/api/v1/failure-reviews/5", "7", {}, reviewDecoder, deps),
      sendJson(config, "POST", "/api/v1/datasets/0/cases", "7", {}, reviewDecoder, deps),
      sendJson(config, "POST", "/api/v1/traces" as never, "7", {}, reviewDecoder, deps),
      sendJson(config, "POST", "/api/v1/datasets", "abc", {}, reviewDecoder, deps),
      sendJson(config, "POST", "/api/v1/datasets", "", {}, reviewDecoder, deps),
    ];
    for (const r of await Promise.all(attempts)) assert.equal(!r.ok && r.error.kind, "config");
    assert.equal(calls.length, 0);
    assert.equal(reviewPath("5"), "/api/v1/failure-reviews/5");
    assert.equal(reviewPath("05"), null);
    assert.equal(reviewPath("9223372036854775808"), null);
  });

  it("maps 409 to a documented conflict code only, and 413/422/404", async () => {
    const send = (status: number, body: unknown) =>
      sendJson(config, "POST", "/api/v1/datasets", "7", { name: "d" }, datasetListDecoder, { fetch: fake(() => json(body, status), []) });
    const conflict = await send(409, { error: { code: "duplicate_dataset", message: "x" } });
    assert.deepEqual(!conflict.ok && conflict.error, { kind: "conflict", status: 409, code: "duplicate_dataset" });
    const unknownCode = await send(409, { error: { code: "<script>", message: "x" } });
    assert.deepEqual(!unknownCode.ok && unknownCode.error, { kind: "conflict", status: 409 });
    assert.equal((await send(413, {})).ok === false && (await send(413, {})).ok, false);
    const invalid = await send(422, { error: { code: "invalid_request", fields: ["name"] } });
    assert.deepEqual(!invalid.ok && invalid.error, { kind: "invalid_request", status: 422, fields: ["name"] });
    const missing = await send(404, { error: { code: "not_found" } });
    assert.equal(!missing.ok && missing.error.kind, "not_found");
  });
});

describe("write failure messages", () => {
  it("say plainly when the outcome is unknown and when nothing was stored", () => {
    const timeout = writeFailure({ kind: "timeout" }, "the review");
    assert.ok(timeout.status === "error" && timeout.outcomeUnknown && /may or may not have been stored/.test(timeout.message));
    const dup = writeFailure({ kind: "conflict", status: 409, code: "duplicate_test_case" }, "the test case");
    assert.ok(dup.status === "error" && !dup.outcomeUnknown && /already has a test case/.test(dup.message));
    const notDecided = writeFailure({ kind: "conflict", status: 409, code: "review_not_decided" }, "the test case");
    assert.ok(notDecided.status === "error" && /decision/.test(notDecided.message));
    const invalid = writeFailure({ kind: "invalid_request", fields: ["approved"] }, "the test case");
    assert.ok(invalid.status === "error" && /approved/.test(invalid.message) && /Nothing was stored/.test(invalid.message));
    assert.deepEqual(invalidForm("m", ["f"]), { status: "error", message: "m", fields: ["f"], outcomeUnknown: false });
  });
});

// ---------------------------------------------------------------- components
describe("review section", () => {
  const props = {
    project: "7",
    traceId: TRACE,
    truncated: false,
    spans: [makeSpan({ id: 1, start: 0n, end: 1000n, name: XSS })],
    spanHref: (s: string) => `/traces/${TRACE}?project=7&span=${s}#waterfall-heading`,
    convertHref: (id: number) => `/traces/${TRACE}/reviews/${id}/test-case?project=7`,
  };

  it("states when a trace is not reviewed and offers an empty form with no preselected issue type", () => {
    const m = html(<ReviewSection {...props} reviews={[]} />);
    assert.match(m, /data-testid="reviews-empty"/);
    assert.match(m, /Not reviewed yet/);
    assert.match(m, /<option value="" disabled="" selected="">Choose an issue type/);
    assert.doesNotMatch(m, /<textarea[^>]*>[^<]+<\/textarea>/, "no textarea is prefilled");
    assert.match(m, /Human decision/);
    // A hostile span name in the span picker is text.
    assert.doesNotMatch(m, /<script>/);
    assert.match(m, /&lt;script&gt;/);
  });

  it("renders stored review text as text, with the hypothesis labelled as not a root cause", () => {
    const m = html(<ReviewSection {...props} reviews={[review({ evidence: XSS, hypothesis: XSS, created_by: XSS, span_id: SPAN })]} />);
    assert.doesNotMatch(m, /<script>/);
    assert.doesNotMatch(m, /<img /);
    assert.match(m, /not a verified root cause/);
    assert.match(m, new RegExp(`span=${SPAN}#waterfall-heading`));
    assert.match(m, /data-testid="convert-unavailable"/, "an undecided review cannot be converted");
    assert.doesNotMatch(m, /data-testid="convert-link"/);
  });

  it("offers conversion only after a decision, and lists linked test cases", () => {
    const decided = review({ reviewer_decision: "hypothesis_supported", test_cases: [{ id: 5, dataset_id: 3, dataset_name: XSS }] });
    const m = html(<ReviewSection {...props} reviews={[decided]} />);
    assert.match(m, /data-testid="convert-link"/);
    assert.match(m, new RegExp(`href="/traces/${TRACE}/reviews/11/test-case\\?project=7"`));
    assert.match(m, /href="\/datasets\/3\?project=7"/);
    assert.doesNotMatch(m, /<script>/);
  });

  it("list cells tell reviewed and unreviewed traces apart", () => {
    assert.match(html(<ReviewCell reviews={{ total: 0, not_closed: 0 }} />), /Not reviewed/);
    assert.match(html(<ReviewCell reviews={{ total: 2, not_closed: 1 }} />), /2 reviews · 1 not closed/);
    assert.match(html(<ReviewCell reviews={{ total: 1, not_closed: 0 }} />), /1 review · all closed/);
  });
});

describe("diagnostics panel", () => {
  const spanHref = (s: string) => `/traces/${TRACE}?span=${s}`;

  it("is labelled automatic and unverified, links evidence spans, and contains no form", () => {
    const m = html(<DiagnosticsPanel result={{ ok: true, data: diagnosticsFixture() }} spanHref={spanHref} />);
    assert.match(m, /Automatic · Unverified · Not a human review/);
    assert.match(m, /Possible explanation \(not verified\)/);
    assert.match(m, new RegExp(`href="/traces/${TRACE}\\?span=${SPAN}"`));
    assert.doesNotMatch(m, /<form|<select|<textarea|<input/, "the machine panel offers no way to write a review");
    assert.doesNotMatch(m, /issue_type|schema_violation/, "no diagnostic is shown as a CR-03 issue type");
  });

  it("renders hostile fact values as text and degrades on failure", () => {
    const d = diagnosticsFixture();
    d.findings[0]!.observed_facts = [{ name: "rule", value: XSS }];
    const m = html(<DiagnosticsPanel result={{ ok: true, data: d }} spanHref={spanHref} />);
    assert.doesNotMatch(m, /<script>/);
    const failed = html(<DiagnosticsPanel result={{ ok: false, error: { kind: "timeout" } }} spanHref={spanHref} />);
    assert.match(failed, /data-error-kind="timeout"/);
  });
});

describe("test case form and case list", () => {
  it("starts empty: no dataset, rule or content preselected, approval unticked", () => {
    const m = html(<TestCaseForm project="7" traceId={TRACE} reviewId={11} datasets={[{ id: 3, name: XSS, created_at: "2026-10-25T18:00:00.000001Z", case_count: 0 }]} />);
    assert.match(m, /<option value="" disabled="" selected="">Choose a dataset/);
    assert.match(m, /<option value="" disabled="" selected="">Choose a rule/);
    assert.doesNotMatch(m, /checked=""/);
    assert.doesNotMatch(m, /<textarea[^>]*>[^<]+<\/textarea>/);
    assert.match(m, /name="trace_id" value="a{31}1"/);
    assert.match(m, /name="review_id" value="11"/);
    assert.doesNotMatch(m, /<script>/);
  });

  it("shows provenance and stored content as text", () => {
    const m = html(<CaseList project="7" cases={[testCase({ sanitized_input: XSS, approved_by: XSS }), testCase({ id: 6, origin_trace_id: null, origin_review_id: null, expected_output: null })]} />);
    assert.doesNotMatch(m, /<script>/);
    assert.match(m, new RegExp(`href="/traces/${TRACE}\\?project=7"`));
    assert.match(m, /review #11/);
    assert.match(m, /Standalone case/);
    assert.match(m, /Not evaluated yet/);
    assert.match(m, /this case is never evaluated/);
  });
});

void tid;
