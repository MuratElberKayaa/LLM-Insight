import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { MAX_WINDOW_MS, RANGE_KEYS, backendParams, overviewHref, parseOverviewQuery, resolveWindow } from "../../lib/time-range";

const NOW = new Date("2026-10-25T14:37:12.345Z");

describe("resolveWindow", () => {
  it("aligns hourly presets to the next UTC hour boundary", () => {
    const w = resolveWindow("24h", NOW);
    assert.equal(w.to.toISOString(), "2026-10-25T15:00:00.000Z");
    assert.equal(w.from.toISOString(), "2026-10-24T15:00:00.000Z");
    const six = resolveWindow("6h", NOW);
    assert.equal(six.from.toISOString(), "2026-10-25T09:00:00.000Z");
  });
  it("aligns daily presets to the next UTC midnight", () => {
    const w = resolveWindow("7d", NOW);
    assert.equal(w.to.toISOString(), "2026-10-26T00:00:00.000Z");
    assert.equal(w.from.toISOString(), "2026-10-19T00:00:00.000Z");
  });
  it("puts a boundary instant in the bucket that starts there", () => {
    const w = resolveWindow("24h", new Date("2026-10-25T14:00:00.000Z"));
    assert.equal(w.to.toISOString(), "2026-10-25T15:00:00.000Z");
  });
  it("never exceeds the backend's 31-day limit and always contains now", () => {
    for (const key of RANGE_KEYS) {
      const w = resolveWindow(key, NOW);
      assert.ok(w.to.getTime() - w.from.getTime() <= MAX_WINDOW_MS, key);
      assert.ok(w.from <= NOW && NOW < w.to, key);
    }
    const max = resolveWindow("31d", NOW);
    assert.equal(max.to.getTime() - max.from.getTime(), MAX_WINDOW_MS);
  });
  it("is independent of the machine time zone (UTC arithmetic only)", () => {
    const w = resolveWindow("7d", new Date("2026-03-29T01:30:00Z")); // a DST change day in Europe
    assert.equal(w.to.toISOString(), "2026-03-30T00:00:00.000Z");
  });
});

describe("parseOverviewQuery", () => {
  it("accepts supported values", () => {
    assert.deepEqual(parseOverviewQuery({ range: "7d", provenance: "synthetic" }), { range: "7d", provenance: "synthetic", ignoredInvalid: false });
  });
  it("falls back to defaults for unsupported values and says so", () => {
    assert.deepEqual(parseOverviewQuery({ range: "90d", provenance: "<script>" }), { range: "24h", provenance: "all", ignoredInvalid: true });
    assert.deepEqual(parseOverviewQuery({}), { range: "24h", provenance: "all", ignoredInvalid: false });
  });
  it("treats repeated parameters as invalid", () => {
    const q = parseOverviewQuery({ range: ["7d", "31d"] });
    assert.equal(q.range, "7d");
    assert.equal(q.ignoredInvalid, true);
  });
});

describe("backendParams", () => {
  it("sends UTC ISO timestamps with Z and the provenance filter only when set", () => {
    assert.deepEqual(backendParams({ range: "24h", provenance: "all", ignoredInvalid: false }, NOW), {
      from: "2026-10-24T15:00:00.000Z",
      to: "2026-10-25T15:00:00.000Z",
    });
    assert.equal(backendParams({ range: "6h", provenance: "mixed", ignoredInvalid: false }, NOW).provenance, "mixed");
  });
});

describe("overviewHref", () => {
  it("omits defaults and encodes the rest", () => {
    assert.equal(overviewHref({ range: "24h", provenance: "all" }), "/");
    assert.equal(overviewHref({ range: "31d", provenance: "real" }), "/?range=31d&provenance=real");
  });
});
