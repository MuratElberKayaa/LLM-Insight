/**
 * Phase 4C Gate A in the dashboard: rule contract v1 form controls, review integrity (frozen decision,
 * conversion eligibility) and the new conflict messages. Server-rendered components and pure units; all
 * values are SYNTHETIC.
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { TestCaseForm } from "../../components/datasets/test-case-form";
import { DecisionForm } from "../../components/reviews/decision-form";
import { ReviewSection } from "../../components/reviews/review-section";
import { writeFailure } from "../../lib/action-state";
import { CONFLICT_CODES } from "../../lib/api/result";
import type { FailureReview } from "../../lib/api/review-types";
import { InexactNumber, parseJsonLossless } from "../../lib/json";
import { parseDecisionForm } from "../../lib/review-forms";

const TRACE = "a".repeat(31) + "1";
const html = (el: React.ReactElement) => renderToStaticMarkup(el);

function review(over: Partial<FailureReview> = {}): FailureReview {
  return {
    id: 11,
    trace_id: TRACE,
    span_id: null,
    issue_type: "schema_violation",
    evidence: "SYNTHETIC evidence",
    hypothesis: "SYNTHETIC hypothesis",
    review_status: "open",
    reviewer_decision: "hypothesis_supported",
    created_by: "reviewer-a",
    created_at: "2026-10-26T10:00:00.000001Z",
    updated_at: "2026-10-26T10:00:00.000001Z",
    test_cases: [],
    ...over,
  };
}

function section(r: FailureReview): string {
  return html(<ReviewSection project="7" traceId={TRACE} reviews={[r]} truncated={false} spans={[]} spanHref={(s) => `#${s}`} convertHref={(id) => `/convert/${id}`} />);
}

describe("review integrity in the review card", () => {
  it("offers conversion only for supported or rejected hypotheses", () => {
    for (const decision of ["hypothesis_supported", "hypothesis_rejected"] as const) {
      assert.match(section(review({ reviewer_decision: decision })), /data-testid="convert-link"/, decision);
    }
    const pending = section(review({ reviewer_decision: "pending" }));
    assert.match(pending, /needs a reviewer decision first/);
    const inconclusive = section(review({ reviewer_decision: "inconclusive" }));
    assert.doesNotMatch(inconclusive, /data-testid="convert-link"/);
    assert.match(inconclusive, /An inconclusive review cannot become a test case/);
  });

  it("freezes the decision once a test case cites the review, keeping the status editable", () => {
    const frozen = section(review({ test_cases: [{ id: 5, dataset_id: 3, dataset_name: "golden" }] }));
    assert.match(frozen, /data-testid="decision-frozen"/);
    assert.match(frozen, /frozen: a test case cites this review/);
    assert.match(frozen, /<input type="hidden" name="reviewer_decision" value="hypothesis_supported"\/>/);
    assert.doesNotMatch(frozen, /data-testid="decision-decision"/);
    assert.match(frozen, /data-testid="decision-status"/);
    assert.match(frozen, /Save status/);
    const open = section(review());
    assert.match(open, /data-testid="decision-decision"/);
    assert.doesNotMatch(open, /decision-frozen/);
  });

  it("a frozen form still submits the stored decision unchanged", () => {
    const m = html(<DecisionForm project="7" traceId={TRACE} reviewId={11} status="open" decision="hypothesis_rejected" frozen />);
    const fd = new FormData();
    for (const [, name, value] of m.matchAll(/<input type="hidden" name="([a-z_]+)" value="([^"]*)"/g)) fd.set(name!, value!);
    fd.set("review_status", "closed");
    const parsed = parseDecisionForm(fd);
    assert.ok(parsed.ok);
    assert.deepEqual(parsed.value.body, { review_status: "closed", reviewer_decision: "hypothesis_rejected" });
  });
});

describe("conflict messages for the Phase 4C codes", () => {
  it("has a fixed message for every documented 409 code", () => {
    for (const code of CONFLICT_CODES) {
      const state = writeFailure({ kind: "conflict", code }, "the change");
      assert.ok(state.status === "error" && state.message.length > 20, code);
    }
    const frozen = writeFailure({ kind: "conflict", code: "review_decision_frozen" }, "the decision");
    assert.ok(frozen.status === "error" && /can no longer change/.test(frozen.message));
    const eligible = writeFailure({ kind: "conflict", code: "review_not_eligible" }, "the test case");
    assert.ok(eligible.status === "error" && /supported, or rejected/.test(eligible.message));
  });
});

describe("rule contract v1 controls in the test-case form", () => {
  it("starts with no rule chosen, so no v1 parameter control is preselected", () => {
    const m = html(<TestCaseForm project="7" traceId={TRACE} reviewId={11} datasets={[]} />);
    assert.match(m, /llminsight\.rule\.v1/);
    assert.doesNotMatch(m, /rule_output_format|rule_match/); // shown only after a rule is chosen
    assert.match(m, /It must satisfy the rule above/);
  });
});

describe("exact numbers in user-typed JSON", () => {
  it("marks only decimals that JavaScript would change", () => {
    const value = parseJsonLossless('[0.1, 1.10, 0.1000000000000000000001, 2]', { refuseInexact: true }) as unknown[];
    assert.equal(value[0], 0.1);
    assert.equal(value[1], 1.1);
    assert.ok(value[2] instanceof InexactNumber && value[2].source === "0.1000000000000000000001");
    assert.equal(value[3], 2);
    // Response decoding is unchanged: without the option nothing is marked.
    assert.equal((parseJsonLossless("[0.1000000000000000000001]") as unknown[])[0], 0.1);
  });
});
