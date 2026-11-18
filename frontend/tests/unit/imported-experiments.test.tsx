import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { ImportedList, ImportedReportView } from "../../components/experiments/imported-report";
import { importedReportDecoder, LIVE_RULES } from "../../lib/api/imported-experiments";
import { decode } from "../../lib/validate";
import { isWritePage } from "../../lib/request-guard";

function fixture() {
  const cases = Array.from({ length: 6 }, (_, i) => ["v1", "v2"].map((variant, j) => {
    const passed = i < (j === 0 ? 3 : 5);
    return { case_id: `t0${i + 1}`, variant, prompt_version: `live-ticket-${variant}`,
      trace_id: (i * 2 + j + 1).toString(16).padStart(32, "0"), passed,
      rules: { ...Object.fromEntries(LIVE_RULES.map(k => [k, true])), expected_priority: passed },
      client_latency_ms: 10, input_tokens: 100, output_tokens: 20 };
  })).flat();
  return { id: 2, project: 7, run_id: "a".repeat(32), model: "openai/gpt-oss-20b", provider: "groq",
    suite: "ticket-ab-v1", rule_version: "ticket-category-priority-v1", cases,
    groups: ["v1", "v2"].map((variant, i) => ({ variant, prompt_version: `live-ticket-${variant}`,
      passed: i === 0 ? 3 : 5, evaluated: 6, client_p50_ms: 10, input_tokens: 600, output_tokens: 120,
      missing_input_usage: 0, missing_output_usage: 0 })),
    started_at: "2026-11-12T23:12:00Z", finished_at: "2026-11-12T23:21:00Z", imported_at: "2026-11-13T01:00:00Z",
    cost_status: "unknown", evaluation_source: "operator_import", provenance: "real_asserted",
    inputs: "synthetic", winner: null, statistical_significance: "not_computed" };
}

describe("imported live comparisons", () => {
  it("renders model links, paired outcomes, all trace links, source and sample caveats", () => {
    const report = importedReportDecoder(fixture(), "$");
    const list = renderToStaticMarkup(<ImportedList items={[report]} />);
    assert.match(list, /experiments\/live\/2\?project=7/);
    assert.match(list, /3 \/ 6/);
    assert.match(list, /5 \/ 6/);
    const html = renderToStaticMarkup(<ImportedReportView report={report} />);
    assert.equal((html.match(/Open trace/g) ?? []).length, 12);
    assert.match(html, /Expected priority/);
    assert.match(html, /no general winner/);
    assert.match(html, /raw responses are not stored/);
    assert.match(html, /Unknown/);
    assert.doesNotMatch(html, /\$0|<form/);
    assert.equal(isWritePage("/experiments/live/2"), false);
  });
  it("rejects incomplete pairs, reused traces, mismatched totals and invented winners", () => {
    let data = fixture();
    data.cases.pop();
    assert.equal(decode(importedReportDecoder, data).ok, false);
    data = fixture(); data.cases[0]!.trace_id = data.cases[1]!.trace_id;
    assert.equal(decode(importedReportDecoder, data).ok, false);
    data = fixture(); data.groups[0]!.passed = 6;
    assert.equal(decode(importedReportDecoder, data).ok, false);
    data = fixture(); data.cases[0]!.passed = false;
    assert.equal(decode(importedReportDecoder, data).ok, false);
    assert.equal(decode(importedReportDecoder, { ...fixture(), winner: "v2" }).ok, false);
  });
});
