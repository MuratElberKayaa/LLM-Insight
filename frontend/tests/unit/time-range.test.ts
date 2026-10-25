import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  MAX_WINDOW_MS,
  RANGE_KEYS,
  backendParams,
  formValueFromUtc,
  isUtcInstant,
  overviewHref,
  parseOverviewQuery,
  resolveWindow,
  utcFromFormValue,
} from "../../lib/time-range";

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
    assert.deepEqual(parseOverviewQuery({ range: "7d", provenance: "synthetic" }), {
      project: { kind: "default" },
      window: { kind: "preset", range: "7d" },
      provenance: "synthetic",
      ignored: [],
      windowProblem: null,
    });
  });
  it("falls back to defaults for unsupported values and says so", () => {
    const q = parseOverviewQuery({ range: "90d", provenance: "<script>" });
    assert.deepEqual(q.window, { kind: "preset", range: "24h" });
    assert.equal(q.provenance, "all");
    assert.deepEqual(q.ignored.sort(), ["provenance", "range"]);
    assert.deepEqual(parseOverviewQuery({}).ignored, []);
  });
  it("treats repeated parameters as invalid", () => {
    const q = parseOverviewQuery({ range: ["7d", "31d"] });
    assert.deepEqual(q.window, { kind: "preset", range: "24h" });
    assert.deepEqual(q.ignored, ["range"]);
  });
  it("reads the project: absent → default, an id → explicit, anything else → invalid (never another project)", () => {
    assert.deepEqual(parseOverviewQuery({ project: "42" }).project, { kind: "explicit", id: "42" });
    for (const bad of ["0", "-1", "1.5", "01", "<script>", "", "9223372036854775808", " 1"]) {
      assert.deepEqual(parseOverviewQuery({ project: bad }).project, { kind: "invalid" }, bad);
    }
    assert.deepEqual(parseOverviewQuery({ project: ["1", "2"] }).project, { kind: "invalid" });
    assert.deepEqual(parseOverviewQuery({ project: "9223372036854775807" }).project, { kind: "explicit", id: "9223372036854775807" });
  });
});

describe("custom UTC ranges", () => {
  const F = "2026-10-20T08:00:00Z";
  const T = "2026-10-21T08:00:00Z";
  it("accepts link values (ISO Z) and form values (named *_utc, UTC by name)", () => {
    assert.deepEqual(parseOverviewQuery({ from: F, to: T }).window, { kind: "custom", from: F, to: T });
    assert.deepEqual(parseOverviewQuery({ from_utc: "2026-10-20T08:00", to_utc: "2026-10-21T08:00" }).window, { kind: "custom", from: F, to: T });
    assert.deepEqual(parseOverviewQuery({ from_utc: "2026-10-20T08:00:30", to_utc: "2026-10-21T08:00" }).window, { kind: "custom", from: "2026-10-20T08:00:30Z", to: T });
  });
  it("refuses naive link values, offsets, impossible dates, reversed, empty and > 31-day ranges with a reason", () => {
    const cases: [Record<string, string>, string][] = [
      [{ from: "2026-10-20T08:00:00", to: T }, "format"],
      [{ from: "2026-10-20T08:00:00+03:00", to: T }, "format"],
      [{ from: "2026-02-30T00:00:00Z", to: T }, "format"],
      [{ from_utc: "2026-10-20 08:00", to_utc: "2026-10-21T08:00" }, "format"],
      [{ from_utc: "javascript:alert(1)", to_utc: "2026-10-21T08:00" }, "format"],
      [{ from: T, to: F }, "order"],
      [{ from: F, to: F }, "order"],
      [{ from: F }, "incomplete"],
      [{ from_utc: "", to_utc: "2026-10-21T08:00" }, "incomplete"],
      [{ from: "2026-09-01T00:00:00Z", to: "2026-10-02T00:00:00.000001Z" }, "too_long"],
    ];
    for (const [params, problem] of cases) {
      const q = parseOverviewQuery(params);
      assert.deepEqual(q.window, { kind: "preset", range: "24h" }, JSON.stringify(params));
      assert.equal(q.windowProblem, problem, JSON.stringify(params));
      assert.ok(q.ignored.includes("from/to"));
    }
  });
  it("accepts exactly 31 days and keeps a custom range over a preset", () => {
    const q = parseOverviewQuery({ from: "2026-09-01T00:00:00Z", to: "2026-10-02T00:00:00Z", range: "7d" });
    assert.equal(q.window.kind, "custom");
    assert.equal(q.windowProblem, null);
  });
  it("validates real instants and round-trips form values", () => {
    assert.equal(isUtcInstant("2026-10-25T09:00:00.123456Z"), true);
    assert.equal(isUtcInstant("2026-10-25T09:00:00.1234567Z"), false);
    assert.equal(isUtcInstant("2026-13-01T00:00:00Z"), false);
    assert.equal(isUtcInstant("1969-12-31T23:59:59Z"), false);
    assert.equal(utcFromFormValue("2026-10-25T09:05"), "2026-10-25T09:05:00Z");
    assert.equal(utcFromFormValue("2026-10-25T24:00"), null);
    assert.equal(formValueFromUtc("2026-10-25T09:05:00.000000Z"), "2026-10-25T09:05");
  });
});

describe("backendParams", () => {
  it("sends UTC ISO timestamps with Z and the provenance filter only when set", () => {
    assert.deepEqual(backendParams({ window: { kind: "preset", range: "24h" }, provenance: "all" }, NOW), {
      from: "2026-10-24T15:00:00.000Z",
      to: "2026-10-25T15:00:00.000Z",
    });
    assert.equal(backendParams({ window: { kind: "preset", range: "6h" }, provenance: "mixed" }, NOW).provenance, "mixed");
  });
  it("sends a custom window unchanged", () => {
    const w = { kind: "custom" as const, from: "2026-10-20T08:00:00Z", to: "2026-10-21T08:00:00Z" };
    assert.deepEqual(backendParams({ window: w, provenance: "all" }, NOW), { from: w.from, to: w.to });
  });
});

describe("overviewHref", () => {
  it("omits defaults, puts the project first and encodes the rest", () => {
    assert.equal(overviewHref({ project: null, window: { kind: "preset", range: "24h" }, provenance: "all" }), "/");
    assert.equal(overviewHref({ project: null, window: { kind: "preset", range: "31d" }, provenance: "real" }), "/?range=31d&provenance=real");
    assert.equal(overviewHref({ project: "2", window: { kind: "preset", range: "7d" }, provenance: "all" }), "/?project=2&range=7d");
    assert.equal(
      overviewHref({ project: "2", window: { kind: "custom", from: "2026-10-20T08:00:00Z", to: "2026-10-21T08:00:00Z" }, provenance: "all" }),
      "/?project=2&from=2026-10-20T08%3A00%3A00Z&to=2026-10-21T08%3A00%3A00Z",
    );
  });
});
