/**
 * Phase 9B (ADR-52): the list-price reference on Models Compare. The decoder enforces the
 * backend's group invariants; the table shows the reference in its own column with a
 * "Not billed" flag and never inside the known cost. SYNTHETIC fixtures only, no network.
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { ComparisonNotices, ComparisonTable } from "../../components/models/model-comparison";
import { decode } from "../../lib/validate";
import { modelsCompareDecoder } from "../../lib/api/schema";
import type { ModelGroup, ModelsCompare, ReferenceListPrice } from "../../lib/api/types";
import { geminiReference, json, makeCompare } from "../fixtures/overview";

const html = (node: React.ReactElement) => renderToStaticMarkup(node);

/** Each rendered cell with `data-testid`, cut at the end of its table cell or card item. */
function cellsOf(markup: string, testId: string): string[] {
  return markup
    .split(`data-testid="${testId}"`)
    .slice(1)
    .map((rest) => rest.split(/<\/td>|<\/dd>/)[0] ?? "");
}

/** The OpenAI fixture group turned into a one-attempt Gemini group with an unknown ledger cost. */
function withGemini(reference: ReferenceListPrice = geminiReference()): ModelsCompare {
  const c = makeCompare();
  const base = c.groups[1] as ModelGroup;
  const gemini: ModelGroup = {
    ...base,
    provider: "gcp.gemini",
    requested_model: "gemini-3.1-flash-lite",
    response_model: "gemini-3.1-flash-lite",
    prompt_version: "v1",
    attempts: 1,
    errors: { failed: 0, succeeded: 1, rate: 0 },
    latency: { ...base.latency, n: 1 },
    tokens: { ...base.tokens, n: 1 },
    usage_source: { provider_reported: 1, estimated: 0, unknown: 0 },
    cost: { ...base.cost, unknown_cost_calls: 1, unknown_cost_by_reason: { price_missing: 1, usage_missing: 0, usage_inconsistent: 0 } },
    by_provenance: { real: 1, synthetic: 0, unknown: 0 },
    reference_list_price: reference,
  };
  c.groups = [c.groups[0] as ModelGroup, gemini];
  return c;
}

function decodeGroup(mutate: (r: Record<string, unknown>) => void) {
  const doc = json(withGemini());
  const groups = doc.groups as Record<string, unknown>[];
  mutate((groups[1] as Record<string, Record<string, unknown>>).reference_list_price as Record<string, unknown>);
  return decode(modelsCompareDecoder, doc);
}

describe("Phase 9B decoder: the reference block", () => {
  it("accepts a computed reference and a model outside the catalogue", () => {
    const r = decode(modelsCompareDecoder, json(withGemini()));
    assert.equal(r.ok, true, r.ok ? "" : r.path);
    assert.equal(decode(modelsCompareDecoder, json(makeCompare())).ok, true);
  });

  it("requires the block on every group", () => {
    const doc = json(makeCompare());
    delete (doc.groups as Record<string, unknown>[])[0]!.reference_list_price;
    const r = decode(modelsCompareDecoder, doc);
    assert.equal(r.ok, false);
    if (!r.ok) assert.match(r.path, /reference_list_price$/);
  });

  const broken: [string, (r: Record<string, unknown>) => void][] = [
    ["an amount without availability", (r) => (r.available = false)],
    ["an available reference without an amount", (r) => (r.reference_usd = null)],
    ["a zero-call reference shown as available", (r) => ((r.calls_priced = 0), (r.calls_not_priced = 1), ((r.not_priced_by_reason as Record<string, number>).usage_missing = 1))],
    ["counts that do not add up to attempts", (r) => (r.calls_not_priced = 1)],
    ["reasons that do not add up", (r) => ((r.not_priced_by_reason as Record<string, number>).usage_missing = 1)],
    ["a float amount", (r) => (r.reference_usd = 0.000705)],
    ["a different label", (r) => (r.label = "billed")],
    ["a non-https source", (r) => (r.source_url = "http://aistudio.google.com/docs/pricing")],
    ["an unknown tier", (r) => (r.tier = "priority")],
    ["catalogue details missing for a catalogued model", (r) => (r.catalogue_version = null)],
    ["a reason beside an amount", (r) => (r.unavailable_reason = "no_priced_calls")],
  ];
  for (const [name, mutate] of broken) {
    it(`refuses ${name}`, () => assert.equal(decodeGroup(mutate).ok, false));
  }

  it("refuses catalogue details on a model outside the catalogue", () => {
    const doc = json(makeCompare());
    ((doc.groups as Record<string, Record<string, unknown>>[])[0]!.reference_list_price as Record<string, unknown>).tier = "on_demand";
    assert.equal(decode(modelsCompareDecoder, doc).ok, false);
  });
});

describe("Phase 9B table: a separate, labelled column", () => {
  it("shows the reference with Not billed while the known cost stays unknown", () => {
    const m = html(<ComparisonTable data={withGemini()} />);
    assert.match(m, /List-price reference \(USD\)/);
    const refs = cellsOf(m, "group-reference");
    assert.equal(refs.length, 4); // two table rows and two cards
    assert.match(refs[1]!, /data-reference-state="available"[^]*?\$0\.000705[^]*?Not billed/);
    assert.match(refs[1]!, /paid Standard tier · verified 2026-11-19/);
    // The ledger cell of the Gemini group still says unknown: the reference is not a cost.
    const geminiCost = cellsOf(m, "group-cost")[1]!;
    assert.match(geminiCost, /unknown/);
    assert.doesNotMatch(geminiCost, /0\.000705/);
  });

  it("names why a group has no reference and never shows $0", () => {
    const m = html(<ComparisonTable data={makeCompare()} />);
    const refs = cellsOf(m, "group-reference");
    assert.equal(refs.length, 4);
    for (const cell of refs) {
      assert.match(cell, /data-reference-state="unavailable"/);
      assert.match(cell, /not in the reference catalogue/);
      assert.doesNotMatch(cell, /\$/);
    }
  });

  it("flags a stale catalogue and counts attempts left out", () => {
    const stale = geminiReference({ stale: true, calls_priced: 1, calls_not_priced: 0 });
    const m = html(<ComparisonTable data={withGemini(stale)} />);
    assert.match(m, /data-reference-state="stale"/);
    assert.match(m, /Stale price/);
    const partial = withGemini(geminiReference());
    const g = partial.groups[1] as ModelGroup;
    g.attempts = 3;
    g.reference_list_price = geminiReference({ calls_not_priced: 2, not_priced_by_reason: { ...geminiReference().not_priced_by_reason, usage_missing: 1, cache_write_not_representable: 1 } });
    assert.match(html(<ComparisonTable data={partial} />), /2 attempts not included/);
  });

  it("explains the reference only when one is shown", () => {
    assert.match(html(<ComparisonNotices data={withGemini()} />), /data-testid="compare-reference"[^]*not billed/i);
    assert.doesNotMatch(html(<ComparisonNotices data={makeCompare()} />), /compare-reference/);
    const stale = html(<ComparisonNotices data={withGemini(geminiReference({ stale: true }))} />);
    assert.match(stale, /verified more than 30 days ago/);
  });

  it("escapes catalogue strings from the backend", () => {
    const hostile = withGemini(geminiReference({ catalogue_version: "<script>x</script>", source_url: "https://a.example/<img>" }));
    const m = html(<ComparisonTable data={hostile} />);
    assert.doesNotMatch(m, /<script>x<\/script>|<img>/);
  });
});
