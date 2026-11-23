/**
 * Phase 10B (ADR-55): M20 client time to first chunk. The decoder ties `available`, the reason
 * and the percentiles together; the overview and Compare state a value only when one was recorded.
 * SYNTHETIC fixtures only.
 */
/* eslint-disable @typescript-eslint/no-explicit-any -- wire-form mutations in decoder tests */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { ComparisonTable } from "../../components/models/model-comparison";
import { ttfcSentence } from "../../components/overview/kpi-grid";
import { modelsCompareDecoder, ttfcDecoder } from "../../lib/api/schema";
import type { TimeToFirstChunk } from "../../lib/api/types";
import { decode } from "../../lib/validate";
import { json, makeCompare, NO_TTFC } from "../fixtures/overview";

const RECORDED: TimeToFirstChunk = {
  available: true,
  reason: null,
  detail: null,
  streaming_attempts: 4,
  n: 3,
  p50_ms: 200,
  p95_ms: 380,
  low_sample: { p95: true },
};
const NOT_RECORDED: TimeToFirstChunk = { ...NO_TTFC, reason: "not_recorded", detail: "streaming attempts exist but none recorded gen_ai.response.time_to_first_chunk", streaming_attempts: 2 };

describe("Phase 10B decoder", () => {
  it("accepts the three states", () => {
    for (const t of [RECORDED, NOT_RECORDED, NO_TTFC]) assert.equal(decode(ttfcDecoder, json(t)).ok, true);
  });

  const broken: [string, (t: any) => void][] = [
    ["a value without availability", (t) => (t.available = false)],
    ["availability without a value", (t) => ((t.n = 0), (t.p50_ms = null), (t.p95_ms = null))],
    ["a reason beside a value", (t) => ((t.reason = "not_recorded"), (t.detail = "x"))],
    ["a percentile missing", (t) => (t.p95_ms = null)],
    ["p50 above p95", (t) => (t.p50_ms = 500)],
    ["a wrong low-sample flag", (t) => (t.low_sample.p95 = false)],
    ["a zero standing in for unknown", (t) => ((t.available = false), (t.reason = "no_streaming_attempts"), (t.detail = "x"), (t.n = 0), (t.p50_ms = 0), (t.p95_ms = 0))],
    ["an unknown reason", (t) => ((t.available = false), (t.reason = "estimated"), (t.detail = "x"), (t.n = 0), (t.p50_ms = null), (t.p95_ms = null))],
  ];
  for (const [name, mutate] of broken) {
    it(`refuses ${name}`, () => {
      const t = json(RECORDED) as any;
      mutate(t);
      assert.equal(decode(ttfcDecoder, t).ok, false);
    });
  }

  it("refuses reasons that contradict the streaming count", () => {
    assert.equal(decode(ttfcDecoder, { ...json(NO_TTFC), streaming_attempts: 1 }).ok, false);
    assert.equal(decode(ttfcDecoder, { ...json(NOT_RECORDED), streaming_attempts: 0 }).ok, false);
  });

  it("refuses more values than attempts in a Compare group", () => {
    const doc = json(makeCompare()) as any;
    doc.groups[1].time_to_first_chunk = { ...json(RECORDED), streaming_attempts: 2, n: 3 };
    assert.equal(decode(modelsCompareDecoder, doc).ok, false);
  });
});

describe("Phase 10B presentation", () => {
  it("states a value, a missing recording or no streaming, never an estimate", () => {
    assert.equal(ttfcSentence(RECORDED), "Time to first chunk p50 200.0 ms, p95 380.0 ms (n 3, low sample).");
    assert.equal(ttfcSentence(NOT_RECORDED), "Time to first chunk: not recorded by 2 streaming attempt(s).");
    assert.equal(ttfcSentence(NO_TTFC), "Time to first chunk: no streaming attempt in scope.");
  });

  it("adds a first-chunk line to Compare only for groups with streaming attempts", () => {
    const c = makeCompare();
    c.groups[0]!.time_to_first_chunk = RECORDED;
    c.groups[0]!.attempts = 4;
    c.groups[1]!.time_to_first_chunk = { ...NOT_RECORDED, streaming_attempts: 1 };
    const m = renderToStaticMarkup(<ComparisonTable data={c} />);
    assert.match(m, /first chunk p50 200.0 ms · p95 380.0 ms · n 3/);
    assert.match(m, /first chunk not recorded \(1 streaming\)/);
    assert.equal((renderToStaticMarkup(<ComparisonTable data={makeCompare()} />).match(/data-testid="group-ttfc"/g) ?? []).length, 0);
  });
});
