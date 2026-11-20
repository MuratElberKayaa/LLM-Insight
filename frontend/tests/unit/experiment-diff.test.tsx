/**
 * UI-5 (experiment diff): the per-case outcome change is a plain reading of two stored outcomes;
 * not-evaluated or missing results are never counted as a pass or a fail; the version cards show
 * stored values only; the diff table keeps the selectors the e2e suite uses.
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { CaseDiffTable } from "../../components/experiments/case-diff-table";
import { caseChange, changeCounts, VersionCards } from "../../components/experiments/experiment-diff";
import type { ExperimentReport, Outcome, Variant } from "../../lib/api/experiment-types";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);
const o = (outcome: Outcome) => ({ outcome, detail_code: outcome === "passed" ? "pass" : outcome === "failed" ? "label_mismatch" : "no_output" });

describe("caseChange", () => {
  it("reads the two stored outcomes", () => {
    assert.equal(caseChange(o("passed"), o("failed")), "pass_to_fail");
    assert.equal(caseChange(o("failed"), o("passed")), "fail_to_pass");
    assert.equal(caseChange(o("passed"), o("passed")), "same");
    assert.equal(caseChange(o("failed"), o("failed")), "same");
  });

  it("never treats a missing or not-evaluated result as a pass or a fail", () => {
    for (const [a, b] of [
      [null, o("passed")],
      [o("failed"), null],
      [o("not_evaluated"), o("passed")],
      [o("passed"), o("not_evaluated")],
      [o("not_evaluated"), o("not_evaluated")],
    ] as const) {
      assert.equal(caseChange(a, b), "not_comparable");
    }
  });

  it("counts every row exactly once", () => {
    const rows = [
      { a: o("passed"), b: o("failed") },
      { a: o("failed"), b: o("passed") },
      { a: o("passed"), b: o("passed") },
      { a: null, b: o("passed") },
    ];
    assert.deepEqual(changeCounts(rows), { pass_to_fail: 1, fail_to_pass: 1, same: 1, not_comparable: 1 });
  });
});

describe("CaseDiffTable", () => {
  const rows = [
    { id: 1, a: o("passed"), b: o("failed") },
    { id: 2, a: o("failed"), b: o("passed") },
    { id: 3, a: o("passed"), b: o("passed") },
    { id: 4, a: o("not_evaluated"), b: null },
  ];
  const props = { rows, experimentId: 42, project: "7", versionA: "v1", versionB: "v2", identical: true };

  it("shows every case by default with the selectors the e2e suite uses", () => {
    const m = html(<CaseDiffTable {...props} />);
    assert.equal((m.match(/data-testid="case-result-row"/g) ?? []).length, 4);
    assert.match(m, /data-testid="case-result-row" data-case-id="1" data-change="pass_to_fail"/);
    assert.match(m, /href="\/experiments\/42\/cases\/3\?project=7"/);
    assert.match(m, /td[^>]*data-slot="a"[\s\S]*?data-outcome="passed"/);
    assert.match(m, /data-testid="case-results-table"/);
    assert.match(m, /aria-pressed="true"[^>]*data-filter="all"/);
    assert.match(m, /not a significance test/);
  });

  it("labels each change with text and counts it in the filter", () => {
    const m = html(<CaseDiffTable {...props} />);
    for (const label of ["Pass → Fail", "Fail → Pass", "Same outcome", "Not comparable"]) assert.match(m, new RegExp(label));
    assert.match(m, /data-filter="pass_to_fail"[^>]*>Pass → Fail<span[^>]*>1<\/span>/);
  });

  it("a filter keeps only the matching cases and says how many are shown", () => {
    const m = html(<CaseDiffTable {...props} initialFilter="pass_to_fail" />);
    assert.equal((m.match(/data-testid="case-result-row"/g) ?? []).length, 1);
    assert.match(m, /Showing 1 of 4\./);
  });

  it("disables a filter with no cases", () => {
    const m = html(<CaseDiffTable {...props} rows={rows.slice(2)} />);
    assert.match(m, /disabled=""[^>]*data-filter="pass_to_fail"/);
  });
});

describe("VersionCards", () => {
  const variant = (slot: "a" | "b", passed: number, failed: number, ne: number): Variant => ({
    slot,
    prompt_version: `golden-prompt-${slot}`,
    results: passed + failed + ne,
    passed,
    failed,
    not_evaluated: ne,
    not_evaluated_by_code: ne > 0 ? { no_output: ne } : {},
    pass_rate: { value: passed + failed === 0 ? null : passed / (passed + failed), numerator: passed, denominator: passed + failed },
    pass_rate_wilson_95: passed + failed === 0 ? null : { low: 0, high: 1 }, // not decoded here; display only
    duration_ms: { n: 3, p50: 120, p95: 300, p95_low_sample: true },
    tokens: { input: { sum: 900, n: 3 }, output: { sum: null, n: 0 } },
    cost: { known_cost_usd: null, computed: 0, unknown: 3, unknown_by_reason: { price_missing: 3 }, complete: false },
  });

  it("shows A as baseline and B as compared with stored rates, counts and an unknown total cost", () => {
    const report = { variants: [variant("a", 2, 1, 0), variant("b", 1, 1, 1)] } as unknown as ExperimentReport;
    const m = html(<VersionCards report={report} />);
    assert.match(m, /A · baseline/);
    assert.match(m, /B · compared/);
    assert.match(m, /data-testid="version-a-pass-rate">66\.67%/);
    assert.match(m, /data-testid="version-b-pass-rate">50%/);
    assert.match(m, /1 passed · 1 failed · 1 not evaluated/);
    assert.match(m, /Total unknown\./);
    assert.doesNotMatch(m, /\$0/);
  });

  it("an undefined pass rate reads n/a, not 0%", () => {
    const report = { variants: [variant("a", 0, 0, 2), variant("b", 1, 0, 0)] } as unknown as ExperimentReport;
    assert.match(html(<VersionCards report={report} />), /data-testid="version-a-pass-rate"><span[^>]*>n\/a<\/span>/);
  });
});
