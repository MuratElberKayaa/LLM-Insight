import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { modelsCompareDecoder, overviewDecoder } from "../../lib/api/schema";
import { decode } from "../../lib/validate";
import { readFileSync } from "node:fs";
import path from "node:path";
import { json, makeCompare, makeEmptyOverview, makeOverview } from "../fixtures/overview";

// Compiled to .test-build/tests/unit; the JSON fixtures stay in the source tree.
const FIXTURES = path.resolve(__dirname, "..", "..", "..", "tests", "fixtures");

// Fixtures are mutated into deliberately malformed shapes, so they are untyped here.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Mutation = [name: string, path: string, mutate: (o: any) => void];

describe("overview decoder", () => {
  it("accepts a documented response and keeps money as strings", () => {
    const r = decode(overviewDecoder, json(makeOverview()));
    assert.equal(r.ok, true);
    if (r.ok) {
      assert.equal(typeof r.value.inference.cost.total.known_cost_usd, "string");
      assert.equal(r.value.inference.cost.total.known_cost_usd, "0.0012345000");
    }
  });

  it("accepts an empty window with nulls (T29)", () => {
    const r = decode(overviewDecoder, json(makeEmptyOverview()));
    assert.equal(r.ok, true);
    if (r.ok) {
      assert.equal(r.value.inference.cost.total.known_cost_usd, null);
      assert.equal(r.value.inference.cost.total.cost_complete, null);
      assert.equal(r.value.inference.errors.total.rate, null);
    }
  });

  it("ignores unknown extra fields (forward compatible)", () => {
    const o = json(makeOverview());
    o.future_field = { anything: true };
    assert.equal(decode(overviewDecoder, o).ok, true);
  });

  const mutations: Mutation[] = [
    ["money as a float", "$.inference.cost.total.known_cost_usd", (o) => (o.inference.cost.total.known_cost_usd = 0.0012345)],
    ["money in exponent form", "$.inference.cost.total.known_cost_usd", (o) => (o.inference.cost.total.known_cost_usd = "1e-5")],
    ["unknown cost turned into 0 number", "$.trend.items[0].known_cost_usd", (o) => (o.trend.items[0].known_cost_usd = 0)],
    ["negative count", "$.traces.count.total", (o) => (o.traces.count.total = -1)],
    ["fractional count", "$.inference.attempts.total", (o) => (o.inference.attempts.total = 1.5)],
    ["rate above 1", "$.inference.errors.total.rate", (o) => (o.inference.errors.total.rate = 1.2)],
    ["missing provenance bucket", "$.traces.count.by_provenance.mixed", (o) => delete o.traces.count.by_provenance.mixed],
    ["missing field", "$.inference.tokens.total.output_tokens", (o) => delete o.inference.tokens.total.output_tokens],
    ["provenance_verified true", "$.provenance_verified", (o) => (o.provenance_verified = true)],
    ["non-UTC timestamp", "$.window.from", (o) => (o.window.from = "2026-10-24T15:00:00+03:00")],
    ["unknown bucket width", "$.trend.bucket", (o) => (o.trend.bucket = "minute")],
    ["unknown currency", "$.inference.cost.total.currency", (o) => (o.inference.cost.total.currency = "EUR")],
    ["oversized label", "$.inference.errors.by_error_type[0].error_type", (o) => (o.inference.errors.by_error_type[0].error_type = "x".repeat(300))],
    ["array instead of object", "$.inference", (o) => (o.inference = [])],
    ["too many trend buckets", "$.trend.items", (o) => (o.trend.items = Array.from({ length: 801 }, () => o.trend.items[0]))],
  ];
  for (const [name, path, mutate] of mutations) {
    it(`rejects ${name} at ${path}`, () => {
      const o = json(makeOverview());
      mutate(o);
      const r = decode(overviewDecoder, o);
      assert.equal(r.ok, false);
      if (!r.ok) assert.equal(r.path, path);
    });
  }

  it("rejects non-objects", () => {
    for (const v of [null, 1, "x", []]) assert.equal(decode(overviewDecoder, v).ok, false);
  });
});

describe("models/compare decoder", () => {
  it("accepts a documented response with null model labels", () => {
    const r = decode(modelsCompareDecoder, json(makeCompare()));
    assert.equal(r.ok, true);
    if (r.ok) assert.equal(r.value.groups[1]?.response_model, null);
  });
  it("rejects a group without its provider", () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const o = json(makeCompare()) as any;
    o.groups[0].provider = null;
    const r = decode(modelsCompareDecoder, o);
    assert.equal(r.ok, false);
    if (!r.ok) assert.equal(r.path, "$.groups[0].provider");
  });
});

describe("recorded real backend responses (contract fixtures)", () => {
  // Recorded from the live backend during the Phase 3A round trip (synthetic telemetry).
  const load = (name: string): unknown => (JSON.parse(readFileSync(path.join(FIXTURES, name), "utf8")) as { response: unknown }).response;

  it("decodes the recorded /api/v1/overview response", () => {
    const r = decode(overviewDecoder, load("overview.live.json"));
    assert.equal(r.ok, true, r.ok ? "" : r.path);
    if (r.ok) {
      assert.equal(r.value.provenance_verified, false);
      assert.equal(r.value.traces.count.by_provenance.synthetic, r.value.traces.count.total);
      assert.equal(r.value.trend.bucket, "hour");
      assert.equal(r.value.trend.items.length, 24);
    }
  });

  it("decodes the recorded /api/v1/models/compare response", () => {
    const r = decode(modelsCompareDecoder, load("models-compare.live.json"));
    assert.equal(r.ok, true, r.ok ? "" : r.path);
    if (r.ok) assert.ok(r.value.groups.length > 0);
  });
});
