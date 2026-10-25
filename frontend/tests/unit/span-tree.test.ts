import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, it } from "node:test";
import { traceDetailDecoder } from "../../lib/api/trace-schema";
import type { TraceDetail } from "../../lib/api/trace-types";
import { LosslessInt, parseJsonLossless } from "../../lib/json";
import { buildTraceView, formatAttributeValue, formatDigits } from "../../lib/span-tree";
import { decode } from "../../lib/validate";
import { BASE_NS, makeDetail, makeSpan, makeUsage, retryDetail, sid } from "../fixtures/traces";

const ids = (d: TraceDetail) => buildTraceView(d).rows.map((r) => r.spanId);
const shuffle = <T>(a: T[]): T[] => [...a].reverse();

describe("span hierarchy order", () => {
  it("walks depth-first, siblings by start time, regardless of response order", () => {
    const spans = [
      makeSpan({ id: 1, start: 0n, end: 100n }),
      makeSpan({ id: 2, parent: 1, start: 10n, end: 50n, kind: "INTERNAL" }),
      makeSpan({ id: 3, parent: 2, start: 20n, end: 30n }),
      makeSpan({ id: 4, parent: 1, start: 60n, end: 90n }),
      makeSpan({ id: 5, parent: 3, start: 21n, end: 29n }),
    ];
    const expected = [sid(1), sid(2), sid(3), sid(5), sid(4)];
    assert.deepEqual(ids(makeDetail(spans)), expected);
    assert.deepEqual(ids(makeDetail(shuffle(spans))), expected);
    assert.deepEqual(
      buildTraceView(makeDetail(spans)).rows.map((r) => r.depth),
      [0, 1, 2, 3, 1],
    );
  });

  it("orders equal start times by span id, deterministically", () => {
    const spans = [makeSpan({ id: 1, start: 0n, end: 100n }), makeSpan({ id: 0xb, parent: 1, start: 5n, end: 9n }), makeSpan({ id: 0xa, parent: 1, start: 5n, end: 20n })];
    assert.deepEqual(ids(makeDetail(spans)), [sid(1), sid(0xa), sid(0xb)]);
    assert.deepEqual(ids(makeDetail(shuffle(spans))), [sid(1), sid(0xa), sid(0xb)]);
  });

  it("compares start times as BigInt (values that a float would merge)", () => {
    // BASE_NS + 1 and BASE_NS + 2 are the same JS number; BigInt keeps them apart.
    assert.equal(Number(BASE_NS + 1n), Number(BASE_NS + 2n));
    const spans = [makeSpan({ id: 1, start: 0n, end: 100n }), makeSpan({ id: 9, parent: 1, start: 1n, end: 5n }), makeSpan({ id: 8, parent: 1, start: 2n, end: 5n })];
    assert.deepEqual(ids(makeDetail(spans)), [sid(1), sid(9), sid(8)]);
  });

  it("supports several roots, orphans and spans whose parent was cut by truncation", () => {
    const spans = [
      makeSpan({ id: 1, start: 0n, end: 10n }),
      makeSpan({ id: 2, start: 5n, end: 15n }),
      makeSpan({ id: 3, parent: 0xdead, role: "orphan", start: 7n, end: 9n }),
      makeSpan({ id: 4, parent: 3, start: 8n, end: 9n }),
      makeSpan({ id: 5, parent: 0xbeef, role: "child", start: 9n, end: 12n }),
    ];
    const v = buildTraceView(makeDetail(spans));
    assert.deepEqual(
      v.rows.map((r) => [r.spanId, r.placement, r.depth]),
      [
        [sid(1), "root", 0],
        [sid(2), "root", 0],
        [sid(3), "orphan", 0],
        [sid(4), "child", 1],
        [sid(5), "parent_not_returned", 0],
      ],
    );
    assert.deepEqual(v.counts.topLevel, { root: 2, child: 0, orphan: 1, parent_not_returned: 1, unreachable: 0 });
  });

  it("terminates on a parent cycle and shows every span once (no clean root required)", () => {
    const spans = [
      makeSpan({ id: 1, start: 0n, end: 50n }),
      makeSpan({ id: 0xa, parent: 0xb, start: 10n, end: 20n, kind: "INTERNAL" }),
      makeSpan({ id: 0xb, parent: 0xa, start: 12n, end: 22n, kind: "INTERNAL" }),
    ];
    const v = buildTraceView(makeDetail(spans));
    assert.deepEqual(
      v.rows.map((r) => [r.spanId, r.placement, r.depth]),
      [
        [sid(1), "root", 0],
        [sid(0xa), "unreachable", 0],
        [sid(0xb), "child", 1],
      ],
    );
    const onlyCycle = buildTraceView(makeDetail(spans.slice(1)));
    assert.equal(onlyCycle.rows.length, 2);
    assert.equal(onlyCycle.counts.topLevel.root, 0);
  });

  it("handles a deep chain without recursion limits", () => {
    const spans = [makeSpan({ id: 1, start: 0n, end: 10_000n })];
    for (let i = 2; i <= 1500; i += 1) spans.push(makeSpan({ id: i, parent: i - 1, start: BigInt(i), end: BigInt(i + 1) }));
    const v = buildTraceView(makeDetail(spans));
    assert.equal(v.rows.length, 1500);
    assert.equal(v.rows[1499]!.depth, 1499);
  });

  it("an empty span list produces an empty view", () => {
    const v = buildTraceView(makeDetail([]));
    assert.equal(v.rows.length, 0);
    assert.equal(v.calls.length, 0);
  });
});

describe("waterfall timing", () => {
  it("offsets and durations are exact nanoseconds on an epoch above 2^53", () => {
    const spans = [makeSpan({ id: 1, start: 0n, end: 100_000_000n }), makeSpan({ id: 2, parent: 1, start: 1_000_001n, end: 26_000_018n })];
    const v = buildTraceView(makeDetail(spans, [], {}, 100_000_000n));
    const child = v.rows[1]!;
    assert.equal(child.offsetLabel, "+1.000001 ms");
    assert.equal(child.durationLabel, "25.000017 ms");
    assert.equal(child.durationExact, "25.000017 ms");
    assert.equal(child.startNs, (BASE_NS + 1_000_001n).toString());
    assert.equal(child.left, 1);
    assert.equal(child.width, 25);
    assert.match(child.startLabel, /\.\d{9} UTC$/);
    assert.deepEqual(
      v.axis.ticks.map((t) => t.label),
      ["0", "25 ms", "50 ms", "75 ms", "100 ms"],
    );
  });

  it("a zero-duration trace does not divide by zero", () => {
    const v = buildTraceView(makeDetail([makeSpan({ id: 1, start: 0n, end: 0n })], [], {}, 0n));
    assert.equal(v.rows[0]!.left, 0);
    assert.equal(v.rows[0]!.width, 0);
    assert.equal(v.rows[0]!.durationLabel, "0 ns");
  });
});

describe("inference attempts, retries and failures (as recorded)", () => {
  it("groups the physical attempts of a logical call and reports recovery", () => {
    const v = buildTraceView(retryDetail());
    assert.equal(v.calls.length, 1);
    const call = v.calls[0]!;
    assert.equal(call.outcome, "recovered");
    assert.equal(call.failed, 1);
    assert.deepEqual(
      call.attempts.map((a) => [a.attemptId, a.status, a.errorType]),
      [
        [1, "ERROR", "429"],
        [2, "UNSET", null],
      ],
    );
    assert.equal(v.counts.errorSpans, 1);
    assert.equal(v.counts.failedInference, 1);
    assert.equal(v.counts.failedApplication, 0);
    assert.equal(v.rows.filter((r) => r.inference !== null).length, 2, "retries stay separate rows");
  });

  it("distinguishes all-failed, last-failed, no-failure and ungrouped attempts", () => {
    const spans = [
      makeSpan({ id: 1, start: 0n, end: 100n, status: "ERROR", errorType: "500" }),
      makeSpan({ id: 2, parent: 1, start: 1n, end: 2n, status: "ERROR" }),
      makeSpan({ id: 3, parent: 1, start: 3n, end: 4n, status: "ERROR" }),
      makeSpan({ id: 4, parent: 1, start: 5n, end: 6n }),
      makeSpan({ id: 5, parent: 1, start: 7n, end: 8n, status: "ERROR" }),
      makeSpan({ id: 6, parent: 1, start: 9n, end: 10n, status: "OK" }),
      makeSpan({ id: 7, parent: 1, start: 11n, end: 12n }),
    ];
    const usage = [
      makeUsage(2, { call_id: "a", attempt_id: 1 }),
      makeUsage(3, { call_id: "a", attempt_id: 2 }),
      makeUsage(4, { call_id: "b", attempt_id: 1 }),
      makeUsage(5, { call_id: "b", attempt_id: 2 }),
      makeUsage(6, { call_id: "c", attempt_id: 1 }),
      makeUsage(7, { call_id: null, attempt_id: null }),
      makeUsage(99, { call_id: "d", attempt_id: 1 }),
    ];
    const v = buildTraceView(makeDetail(spans, usage));
    const outcome = Object.fromEntries(v.calls.map((c) => [c.callId ?? "none", c.outcome]));
    assert.deepEqual(outcome, { a: "all_failed", b: "last_failed", c: "no_failure", none: "no_failure", d: "status_unavailable" });
    assert.equal(v.counts.failedApplication, 1, "the failing root is an application span");
    assert.equal(v.counts.failedInference, 3);
    assert.equal(v.counts.usageWithoutSpan, 1);
  });

  it("keeps unknown cost unknown with its reason, and computed cost exact", () => {
    const spans = [makeSpan({ id: 1, start: 0n, end: 10n }), makeSpan({ id: 2, parent: 1, start: 1n, end: 2n }), makeSpan({ id: 3, parent: 1, start: 3n, end: 4n })];
    const usage = [
      makeUsage(2, { cost_status: "computed", cost_usd: "0.0001875000", cost_unknown_reason: null, price_version: "openai-2026-10-24" }),
      makeUsage(3, { cost_status: "unknown", cost_usd: null, cost_unknown_reason: "usage_missing", input_tokens: null }),
    ];
    const v = buildTraceView(makeDetail(spans, usage));
    assert.deepEqual(v.rows[1]!.inference!.cost, { kind: "computed", text: "$0.0001875", exact: "$0.0001875" });
    assert.deepEqual(v.rows[2]!.inference!.cost, { kind: "unknown", reason: "usage_missing" });
    assert.equal(v.rows[2]!.inference!.inputTokens, null);
  });
});

describe("attribute display", () => {
  it("renders values as plain text, big integers exactly", () => {
    assert.equal(formatAttributeValue(new LosslessInt("4611686018427387905")), "4611686018427387905");
    assert.equal(formatAttributeValue(["stop", "length"]), "[stop, length]");
    assert.equal(formatAttributeValue(true), "true");
    assert.equal(formatAttributeValue(0.7), "0.7");
    assert.equal(formatAttributeValue(null), "null");
    assert.equal(formatAttributeValue({ a: 1 }), "{a: 1}");
    assert.equal(formatDigits("9007199254740993"), "9,007,199,254,740,993");
  });

  it("sorts attributes by key", () => {
    const v = buildTraceView(makeDetail([makeSpan({ id: 1, start: 0n, end: 1n, attributes: { b: 1, a: "x" } })]));
    assert.deepEqual(v.rows[0]!.attributes, [
      ["a", "x"],
      ["b", "1"],
    ]);
  });

  it("the recorded nested trace builds a 4-level tree with an exact seed", () => {
    const file = path.resolve(__dirname, "..", "..", "..", "tests", "fixtures", "trace-detail-nested.live.json");
    const r = decode(traceDetailDecoder, (parseJsonLossless(readFileSync(file, "utf8")) as { response: unknown }).response);
    assert.ok(r.ok);
    const v = buildTraceView(r.value);
    assert.deepEqual(
      v.rows.map((x) => [x.name, x.depth]),
      [
        ["POST /v1/agent", 0],
        ["agent.plan", 1],
        ["agent.tool.lookup", 2],
        ["chat mock-chat-small", 3],
        ["chat gpt-5.4-mini", 2],
      ],
    );
    assert.equal(v.rows[1]!.offsetLabel, "+1.000001 ms");
    const priced = v.rows.find((x) => x.inference?.cost.kind === "computed");
    assert.ok(priced?.attributes.some(([k, val]) => k === "gen_ai.request.seed" && val === "4611686018427387905"));
  });
});
