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
    inputs: "synthetic", winner: null, statistical_significance: "exact_mcnemar",
    // Phase 9D: as the backend derives it (3/6 vs 5/6; t04, t05 pass only under v2: p = 0.5).
    statistics: { derived_at_read: true,
      variants: [{ variant: "v1", passed: 3, evaluated: 6, wilson_95: { low: 0.187616, high: 0.812384 } },
        { variant: "v2", passed: 5, evaluated: 6, wilson_95: { low: 0.436497, high: 0.969947 } }],
      paired_test: { method: "exact_mcnemar", paired_cases: 6, v1_only: 0, v2_only: 2, p_value: 0.5, significant_at_0_05: false, min_discordant_for_0_05: 6 } } };
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

describe("Phase 9D: statistics derived from the live A/B evidence", () => {
  it("shows the intervals and the exact test, and still names no winner", () => {
    const html = renderToStaticMarkup(<ImportedReportView report={importedReportDecoder(fixture(), "$")} />);
    assert.match(html, /95 % interval \(Wilson\)/);
    assert.equal((html.match(/data-testid="wilson-interval"/g) ?? []).length, 2);
    assert.match(html, /18\.76% – 81\.24%/);
    assert.match(html, /0 passed only under V1, 2 only under V2; p = 0\.500\./);
    assert.match(html, /with fewer than 6 disagreeing cases no split can reach p &lt; 0\.05/);
    assert.match(html, /no general winner is named/);
  });

  type Wire = ReturnType<typeof fixture>;
  const broken: [string, (d: Wire) => void][] = [
    ["the stored 'not computed' label", (d) => (d.statistical_significance = "not_computed")],
    ["statistics that are not derived at read", (d) => (d.statistics.derived_at_read = false)],
    ["a pass count that differs from the groups", (d) => (d.statistics.variants[1]!.passed = 4)],
    ["discordant pairs that contradict the passes", (d) => (d.statistics.paired_test.v2_only = 3)],
    ["significance with two discordant pairs", (d) => ((d.statistics.paired_test.significant_at_0_05 = true), (d.statistics.paired_test.p_value = 0.01))],
    ["an inverted interval", (d) => (d.statistics.variants[0]!.wilson_95 = { low: 0.9, high: 0.1 })],
    ["missing statistics", (d) => Reflect.deleteProperty(d, "statistics")],
  ];
  for (const [name, mutate] of broken) {
    it(`refuses ${name}`, () => {
      const data = fixture();
      mutate(data);
      assert.equal(decode(importedReportDecoder, data).ok, false);
    });
  }
});
