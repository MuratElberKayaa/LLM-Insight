import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { LosslessInt, parseJsonLossless } from "../../lib/json";
import { INT64_MAX, axisPercent, formatDurationNs, formatExactMs, formatIsoNs, parseNs } from "../../lib/nanos";

describe("parseNs (int64 nanosecond strings)", () => {
  it("accepts decimal strings up to 2^63-1 and keeps every digit", () => {
    assert.equal(parseNs("1792913387428346901"), 1792913387428346901n);
    assert.equal(parseNs("0"), 0n);
    assert.equal(parseNs(INT64_MAX.toString()), INT64_MAX);
  });
  it("rejects anything else", () => {
    for (const bad of ["", "-1", "1.5", "1e9", " 1", "9223372036854775808", "12345678901234567890", "0x10"]) assert.equal(parseNs(bad), null, bad);
  });
  it("a JS number would have lost the digits (why strings are used)", () => {
    assert.notEqual(String(Number("1792913387428346901")), "1792913387428346901");
  });
});

describe("formatDurationNs", () => {
  it("formats exact values without rounding up", () => {
    assert.equal(formatDurationNs(0n), "0 ns");
    assert.equal(formatDurationNs(850n), "850 ns");
    assert.equal(formatDurationNs(1_000n), "0.001 ms");
    assert.equal(formatDurationNs(1_000_001n), "1.000001 ms");
    assert.equal(formatDurationNs(11_262_300n), "11.2623 ms");
    assert.equal(formatDurationNs(999_999_999n), "999.999999 ms");
    assert.equal(formatDurationNs(1_284_000_000n), "1.284 s");
    assert.equal(formatDurationNs(59_999_999_999n), "59.999 s");
    assert.equal(formatDurationNs(125_100_000_000n), "2 min 5.1 s");
  });
  it("shows exact milliseconds with 6 digits", () => {
    assert.equal(formatExactMs(11_262_300n), "11.262300 ms");
    assert.equal(formatExactMs(1n), "0.000001 ms");
  });
});

describe("formatIsoNs", () => {
  it("keeps all nine fraction digits from the backend string (no Date round trip)", () => {
    assert.equal(formatIsoNs("2026-10-25T07:29:47.428346901Z"), "2026-10-25 07:29:47.428346901 UTC");
    assert.equal(formatIsoNs("2026-10-25T07:29:47.428346Z"), "2026-10-25 07:29:47.428346000 UTC");
    assert.equal(formatIsoNs("2026-10-25T07:29:47Z"), "2026-10-25 07:29:47.000000000 UTC");
    assert.equal(formatIsoNs("2026-10-25T07:29:47.428346901Z", 3), "2026-10-25 07:29:47.428 UTC");
  });
  it("is independent of the machine time zone", () => {
    assert.equal(formatIsoNs("2026-10-25T23:59:59.999999999Z"), "2026-10-25 23:59:59.999999999 UTC");
  });
});

describe("axisPercent (waterfall geometry from BigInt)", () => {
  const base = 1792913387428346901n;
  it("places a span exactly relative to an epoch-ns axis above 2^53", () => {
    const { left, width } = axisPercent(base + 25_000_000n, base + 75_000_000n, base, 100_000_000n);
    assert.equal(left, 25);
    assert.equal(width, 50);
  });
  it("resolves a 1 ns offset on a 10 µs axis", () => {
    assert.equal(axisPercent(base + 1n, base + 2n, base, 10_000n).left, 0.01);
  });
  it("clamps spans outside the axis and handles a zero-length axis", () => {
    assert.deepEqual(axisPercent(base - 10n, base + 200n, base, 100n), { left: 0, width: 100 });
    assert.deepEqual(axisPercent(base, base, base, 0n), { left: 0, width: 0 });
  });
});

describe("parseJsonLossless", () => {
  it("keeps integers above 2^53 exact and leaves other numbers alone", () => {
    const v = parseJsonLossless('{"big":4611686018427387905,"safe":120,"float":1.5,"neg":-9223372036854775807,"s":"4611686018427387905"}') as Record<string, unknown>;
    assert.ok(v.big instanceof LosslessInt);
    assert.equal((v.big as LosslessInt).digits, "4611686018427387905");
    assert.equal(v.safe, 120);
    assert.equal(v.float, 1.5);
    assert.ok(v.neg instanceof LosslessInt);
    assert.equal(v.s, "4611686018427387905");
  });
  it("does not turn large floats into integers", () => {
    const v = parseJsonLossless('{"x":1e300}') as Record<string, unknown>;
    assert.equal(v.x, 1e300);
  });
});
