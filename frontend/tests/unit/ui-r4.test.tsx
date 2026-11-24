/**
 * UI-R4 (evaluation workspace): case filters apply to the loaded page only and keep the URL;
 * cases show trace → review → case as stored, with human-entered input never presented as a
 * captured conversation; mock experiments carry the "Synthetic / Offline Mock" label and imported
 * reports the read-only one; the workflow strip links only pages that exist; breadcrumbs mark the
 * current page. SYNTHETIC fixtures only.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, it } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { Breadcrumbs } from "../../components/breadcrumbs";
import { CaseList } from "../../components/datasets/case-list";
import { CaseIndex } from "../../components/datasets/case-table";
import { ImportedBadge, ModeBadge, StateBadge } from "../../components/evaluation/badges";
import { EvaluationFlow } from "../../components/evaluation/flow-strip";
import type { TestCase } from "../../lib/api/review-types";
import { NO_CASE_FILTER, applyCaseFilters, caseFacts, datasetHref, isFiltered, parseCaseFilters } from "../../lib/case-filters";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);
const count = (m: string, re: RegExp) => (m.match(re) ?? []).length;
const TRACE = "a".repeat(32);

function tc(over: Partial<TestCase> = {}): TestCase {
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

const cases = [
  tc(),
  tc({ id: 6, origin_trace_id: null, origin_review_id: null, expected_output: null, rule: { version: 1, type: "exact" } }),
  tc({ id: 7, rule: { version: 1, type: "regex", pattern: "^ok$" } }),
  tc({ id: 8, origin_review_id: null }),
];

describe("case filters", () => {
  it("parse only known values; anything else is no filter", () => {
    assert.deepEqual(parseCaseFilters({ rule: "regex", origin: "standalone", expected: "no" }), { rule: "regex", origin: "standalone", expected: "no" });
    assert.deepEqual(parseCaseFilters({ rule: "<script>", origin: ["trace", "x"], expected: "maybe" }), NO_CASE_FILTER);
    assert.equal(isFiltered(NO_CASE_FILTER), false);
  });

  it("filter the loaded cases by rule, source and expected output", () => {
    assert.deepEqual(applyCaseFilters(cases, { ...NO_CASE_FILTER, rule: "regex" }).map((c) => c.id), [7]);
    assert.deepEqual(applyCaseFilters(cases, { ...NO_CASE_FILTER, origin: "standalone" }).map((c) => c.id), [6]);
    assert.deepEqual(applyCaseFilters(cases, { ...NO_CASE_FILTER, origin: "trace" }).map((c) => c.id), [5, 7, 8]);
    assert.deepEqual(applyCaseFilters(cases, { ...NO_CASE_FILTER, expected: "no" }).map((c) => c.id), [6]);
    assert.deepEqual(applyCaseFilters(cases, NO_CASE_FILTER).map((c) => c.id), [5, 6, 7, 8]);
  });

  it("counts are over the loaded page", () => {
    const f = caseFacts(cases);
    assert.deepEqual([f.shown, f.fromTrace, f.standalone, f.withExpected, f.withoutExpected], [4, 3, 1, 3, 1]);
    assert.deepEqual(f.byRule, { json_schema: 2, field_match: 0, exact: 1, regex: 1 });
  });

  it("links keep project and paging and leave defaults out", () => {
    assert.equal(datasetHref("3", "7", "40", { rule: "regex", origin: "all", expected: "no" }), "/datasets/3?project=7&after=40&rule=regex&expected=no");
    assert.equal(datasetHref("3", null, null, NO_CASE_FILTER), "/datasets/3");
  });
});

describe("test case cards and index", () => {
  const m = html(<CaseList project="7" cases={cases} />);

  it("show trace → review → case as stored, with links to the trace and its review section", () => {
    assert.match(m, /data-testid="case-lineage" data-origin="trace"/);
    assert.ok(m.includes(`href="/traces/${TRACE}?project=7"`));
    assert.ok(m.includes(`href="/traces/${TRACE}?project=7#reviews-heading"`));
    assert.match(m, /review #11/);
    assert.match(m, /its review was removed/);
    assert.match(m, /data-origin="standalone"[^>]*>Standalone case \(no source trace\)/);
  });

  it("label the input as human-entered, never a captured conversation or rebuilt from telemetry", () => {
    assert.match(m, /human-entered, as stored after redaction; not a captured user conversation and not rebuilt from telemetry/);
    assert.equal(count(m, /data-testid="test-case"/g), 4);
    assert.match(m, /this case is never evaluated/);
  });

  it("move between cases with anchors; the first has no previous and the last no next", () => {
    assert.match(m, /id="case-5"/);
    assert.equal(count(m, /data-testid="case-prev"/g), 3);
    assert.equal(count(m, /data-testid="case-next"/g), 3);
    assert.match(m, /href="#case-6"[^>]*data-testid="case-next"/);
  });

  it("the index jumps to the cards without repeating their test ids or content", () => {
    const i = html(<CaseIndex cases={cases} />);
    assert.match(i, /<caption[^>]*>Test cases on this page/);
    assert.equal(count(i, /data-testid="case-index-link"/g), 4);
    assert.equal(count(i, /data-testid="test-case"/g), 0);
    assert.doesNotMatch(i, /summarize/);
    assert.match(i, /none: never evaluated/);
    assert.match(i, /review removed/);
  });
});

describe("evaluation labels", () => {
  it("mock experiments say Synthetic / Offline Mock; real mode says it cannot run here", () => {
    assert.match(html(<ModeBadge mode="mock" />), /data-mode="mock"[^]*Synthetic \/ Offline Mock/);
    assert.match(html(<ModeBadge mode="real" />), /this build cannot run it/);
  });

  it("imported reports are read-only; state is text with a glyph", () => {
    assert.match(html(<ImportedBadge />), /Imported report · read-only/);
    assert.match(html(<StateBadge state="finished" />), /Finished/);
    assert.match(html(<StateBadge state="running" />), /Not finished \(running or interrupted\)/);
  });

  it("the workflow strip links only the pages it is given and marks the current step", () => {
    const m = html(<EvaluationFlow current="dataset" hrefs={{ trace: "/traces?project=7", experiment: "/experiments?project=7" }} />);
    assert.match(m, /aria-label="Evaluation workflow"/);
    assert.equal(count(m, /<a /g), 2);
    assert.match(m, /aria-current="step"/);
    assert.match(m, /data-step="dataset" data-current="true"/);
    assert.match(m, /converted after an explicit approval/);
  });

  it("breadcrumbs link the trail and mark the current page", () => {
    const m = html(<Breadcrumbs items={[{ label: "Evaluate" }, { label: "Datasets", href: "/datasets?project=7" }, { label: "golden" }]} />);
    assert.match(m, /<nav aria-label="Breadcrumb"/);
    assert.match(m, /href="\/datasets\?project=7"/);
    assert.match(m, /aria-current="page">golden</);
  });
});

describe("no dataset or experiment action without a backend", () => {
  it("the evaluation pages offer only existing routes and the existing create form", () => {
    const root = path.resolve(__dirname, "..", "..", "..");
    for (const f of ["app/datasets/page.tsx", "app/datasets/[datasetId]/page.tsx", "app/experiments/page.tsx", "app/experiments/[experimentId]/page.tsx"]) {
      const src = readFileSync(path.join(root, f), "utf8");
      assert.doesNotMatch(src, /["']use server["']/, f);
      assert.doesNotMatch(src, /<button(?![^>]*type="submit")[^>]*onClick/, f);
      // The API has no delete, rename or duplicate for datasets: no control may suggest one.
      assert.doesNotMatch(src, /\b(Delete|Rename|Duplicate) (dataset|experiment)\b/i, f);
    }
  });
});
