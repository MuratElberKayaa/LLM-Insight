import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { decimalToChartNumber, formatDecimal, formatUsd, isZero, parseDecimal, roundHalfEven } from "../../lib/decimal";
import { formatBucketRange, formatBucketTick, formatCompact, formatInt, formatMs, formatPercent, formatUtcMinute, formatUtcTime } from "../../lib/format";

describe("formatPercent (rates from the backend, 6 decimals)", () => {
  it("formats with at most two decimals", () => {
    assert.equal(formatPercent(0.25), "25%");
    assert.equal(formatPercent(0.166667), "16.67%");
    assert.equal(formatPercent(0.333333), "33.33%");
    assert.equal(formatPercent(0.1), "10%");
  });
  it("keeps exact 0% and 100%, and never rounds a non-zero rate to 0% or 100%", () => {
    assert.equal(formatPercent(0), "0%");
    assert.equal(formatPercent(1), "100%");
    assert.equal(formatPercent(0.000001), "<0.01%");
    assert.equal(formatPercent(0.999999), ">99.99%");
  });
  it("returns null for an unknown rate (no denominator), never 0%", () => {
    assert.equal(formatPercent(null), null);
  });
});

describe("formatMs", () => {
  it("chooses a readable unit", () => {
    assert.equal(formatMs(0.042), "0.042 ms");
    assert.equal(formatMs(8.25), "8.25 ms");
    assert.equal(formatMs(412.345), "412.3 ms");
    assert.equal(formatMs(1240), "1.24 s");
    assert.equal(formatMs(126_000), "2.1 min");
  });
  it("returns null for an unknown percentile", () => {
    assert.equal(formatMs(null), null);
  });
});

describe("integers", () => {
  it("groups thousands and compacts axis ticks", () => {
    assert.equal(formatInt(1234567), "1,234,567");
    assert.equal(formatCompact(950), "950");
    assert.equal(formatCompact(1200), "1.2K");
  });
});

describe("UTC date formatting (independent of the machine time zone)", () => {
  it("formats ISO timestamps in UTC", () => {
    assert.equal(formatUtcMinute("2026-10-25T14:05:09.123456Z"), "2026-10-25 14:05 UTC");
    assert.equal(formatUtcTime(new Date("2026-10-25T03:04:05Z")), "03:04:05 UTC");
  });
  it("labels hourly and daily buckets", () => {
    assert.equal(formatBucketTick("2026-10-25T14:00:00.000000Z", "hour"), "14:00");
    assert.equal(formatBucketTick("2026-10-25T00:00:00.000000Z", "day"), "Oct 25");
    assert.equal(formatBucketRange("2026-10-25T23:00:00.000000Z", "hour"), "2026-10-25 23:00–00:00 UTC");
    assert.equal(formatBucketRange("2026-10-25T00:00:00.000000Z", "day"), "2026-10-25 (UTC day)");
  });
});

describe("decimal money (no float on the display path)", () => {
  it("parses and formats exactly", () => {
    const d = parseDecimal("1234.5000000000");
    assert.ok(d);
    assert.equal(formatDecimal(d), "1,234.5");
    assert.equal(formatDecimal(d, 2), "1,234.50");
    assert.equal(parseDecimal("1e-5"), null);
    assert.equal(parseDecimal("abc"), null);
  });
  it("shows small costs exactly instead of rounding them to $0.00", () => {
    assert.deepEqual(formatUsd("0.0001875000"), { text: "$0.0001875", exact: "$0.0001875", approximate: false });
    assert.deepEqual(formatUsd("0.0012345000"), { text: "$0.0012345", exact: "$0.0012345", approximate: false });
  });
  it("shows a computed zero as $0.00 (distinct from unknown, which is null upstream)", () => {
    const zero = formatUsd("0.0000000000");
    assert.equal(zero?.text, "$0.00");
    assert.equal(isZero(parseDecimal("0.0000000000")!), true);
  });
  it("rounds long values half-even and marks them approximate, keeping the exact value", () => {
    const f = formatUsd("12.3456785000");
    assert.equal(f?.text, "≈ $12.345678"); // ...785 → half-even to ...78
    assert.equal(f?.exact, "$12.3456785");
    assert.equal(f?.approximate, true);
    assert.equal(formatUsd("0.1234565000")?.text, "≈ $0.123456");
    assert.equal(formatUsd("0.1234575000")?.text, "≈ $0.123458");
  });
  it("handles values beyond float precision exactly", () => {
    const f = formatUsd("123456789012345678.1234567891");
    assert.equal(f?.exact, "$123,456,789,012,345,678.1234567891");
  });
  it("rounds half-even at the boundary", () => {
    const r = (s: string, p: number) => formatDecimal(roundHalfEven(parseDecimal(s)!, p), p);
    assert.equal(r("0.125", 2), "0.12");
    assert.equal(r("0.135", 2), "0.14");
    assert.equal(r("0.1251", 2), "0.13");
    assert.equal(r("2.5", 0), "2");
    assert.equal(r("3.5", 0), "4");
  });
  it("converts to a number for chart geometry only, keeping null as null", () => {
    assert.equal(decimalToChartNumber(null), null);
    assert.equal(decimalToChartNumber("0.0012345000"), 0.0012345);
  });
});
