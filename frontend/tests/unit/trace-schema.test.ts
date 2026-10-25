import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, it } from "node:test";
import { traceDetailDecoder, traceListDecoder } from "../../lib/api/trace-schema";
import { LosslessInt, parseJsonLossless } from "../../lib/json";
import { decode } from "../../lib/validate";
import { makeDetail, makeList, makeSpan, makeTrace, makeUsage, retryDetail, toWire } from "../fixtures/traces";

const FIXTURES = path.resolve(__dirname, "..", "..", "..", "tests", "fixtures");
/** Parsed like the dashboard parses backend bodies (lossless integers). */
const load = (name: string): unknown => (parseJsonLossless(readFileSync(path.join(FIXTURES, name), "utf8")) as { response: unknown }).response;

describe("recorded real backend responses (contract fixtures)", () => {
  it("decodes the recorded trace list", () => {
    const r = decode(traceListDecoder, load("trace-list.live.json"));
    assert.equal(r.ok, true, r.ok ? "" : r.path);
    if (r.ok) {
      assert.equal(r.value.items.length, 10);
      assert.equal(typeof r.value.next_cursor, "string", "27 traces with limit 10 has a next page");
      for (const t of r.value.items) assert.equal(t.provenance_verified, false);
    }
  });

  it("decodes the recorded nested detail and keeps the >2^53 seed attribute exact", () => {
    const r = decode(traceDetailDecoder, load("trace-detail-nested.live.json"));
    assert.equal(r.ok, true, r.ok ? "" : r.path);
    if (!r.ok) return;
    const priced = r.value.usage.find((u) => u.cost_status === "computed");
    assert.equal(priced?.cost_usd, "0.0021150000");
    assert.equal(priced?.cached_input_tokens, "200");
    assert.equal(priced?.reasoning_output_tokens, "64");
    const seed = r.value.spans.map((s) => s.attributes["gen_ai.request.seed"]).find((v) => v !== undefined);
    assert.ok(seed instanceof LosslessInt);
    assert.equal((seed as LosslessInt).digits, "4611686018427387905");
  });

  it("decodes the recorded retry detail (one failed and one successful attempt)", () => {
    const r = decode(traceDetailDecoder, load("trace-detail-retry.live.json"));
    assert.equal(r.ok, true, r.ok ? "" : r.path);
    if (r.ok) {
      assert.deepEqual(r.value.usage.map((u) => u.attempt_id).sort(), [1, 2]);
      assert.ok(r.value.spans.some((s) => s.status === "ERROR"));
    }
  });
});

describe("trace decoders reject what they cannot show faithfully", () => {
  const detail = () => toWire(retryDetail()) as ReturnType<typeof retryDetail>;
  const list = () => toWire(makeList([makeTrace()], "abc_DEF-1")) as ReturnType<typeof makeList>;
  const mutate = <T>(base: () => T, f: (v: T) => void): T => {
    const v = base();
    f(v);
    return v;
  };

  const detailCases: [string, (d: ReturnType<typeof retryDetail>) => void, string][] = [
    ["trace id with uppercase", (d) => (d.trace.trace_id = "56D5C0B2144B3EC23FA58C9561FBBFF0"), "$.trace.trace_id"],
    ["nanoseconds as a JSON number", (d) => ((d.spans[0] as unknown as Record<string, unknown>).start_time_unix_nano = 1792913387428346900), "$.spans[0].start_time_unix_nano"],
    ["negative duration string", (d) => (d.spans[0]!.duration_ns = "-1"), "$.spans[0].duration_ns"],
    ["span ending before it starts", (d) => (d.spans[1]!.end_time_unix_nano = "1"), "$.spans[1].end_time_unix_nano"],
    ["root with a parent", (d) => (d.spans[1]!.role = "root"), "$.spans[1].role"],
    ["unknown span status", (d) => ((d.spans[0] as unknown as Record<string, unknown>).status = "FAILED"), "$.spans[0].status"],
    ["unknown cost stored as zero", (d) => (d.usage[0]!.cost_usd = "0"), "$.usage[0].cost_usd"],
    ["computed cost without a value", (d) => Object.assign(d.usage[1]!, { cost_status: "computed", cost_unknown_reason: null, cost_usd: null }), "$.usage[1].cost_usd"],
    ["token count as a float", (d) => ((d.usage[1] as unknown as Record<string, unknown>).input_tokens = 1.5), "$.usage[1].input_tokens"],
    ["negative token count", (d) => ((d.usage[1] as unknown as Record<string, unknown>).input_tokens = -1), "$.usage[1].input_tokens"],
    ["provenance claimed verified", (d) => ((d.trace as unknown as Record<string, unknown>).provenance_verified = true), "$.trace.provenance_verified"],
    ["span id too short", (d) => (d.spans[2]!.span_id = "abc"), "$.spans[2].span_id"],
    ["attributes not an object", (d) => ((d.spans[0] as unknown as Record<string, unknown>).attributes = []), "$.spans[0].attributes"],
    ["missing spans_truncated", (d) => delete (d as unknown as Record<string, unknown>).spans_truncated, "$.spans_truncated"],
  ];
  for (const [name, f, at] of detailCases) {
    it(`detail: ${name}`, () => {
      const r = decode(traceDetailDecoder, mutate(detail, f));
      assert.equal(r.ok, false);
      if (!r.ok) assert.equal(r.path, at);
    });
  }

  it("list: rejects a malformed cursor and a non-hex trace id", () => {
    const a = decode(traceListDecoder, mutate(list, (l) => (l.next_cursor = "<script>")));
    assert.equal(a.ok, false);
    const b = decode(traceListDecoder, mutate(list, (l) => (l.items[0]!.trace_id = "../../admin")));
    assert.equal(b.ok, false);
    if (!b.ok) assert.equal(b.path, "$.items[0].trace_id");
  });

  it("accepts nulls for unknown values and ignores extra fields (forward compatible)", () => {
    const d = detail() as unknown as Record<string, unknown>;
    d.reviews = [{ anything: true }];
    d.future_field = 1;
    const r = decode(traceDetailDecoder, d);
    assert.equal(r.ok, true, r.ok ? "" : r.path);
  });

  it("keeps a hostile attribute key such as __proto__ as an ordinary property", () => {
    const d = detail();
    const attrs = JSON.parse('{"__proto__": "x", "constructor": "y", "gen_ai.request.model": "m"}') as Record<string, never>;
    d.spans[0]!.attributes = attrs;
    const r = decode(traceDetailDecoder, d);
    assert.equal(r.ok, true, r.ok ? "" : r.path);
    if (r.ok) {
      const a = r.value.spans[0]!.attributes;
      assert.deepEqual(Object.keys(a).sort(), ["__proto__", "constructor", "gen_ai.request.model"]);
      assert.equal(Object.getPrototypeOf(a), Object.prototype);
    }
  });

  it("accepts BIGINT token counts beyond 2^53 exactly (from the lossless parser)", () => {
    const raw = JSON.stringify(toWire(makeDetail([makeSpan({ id: 1, start: 0n, end: 10n })], [makeUsage(1)]))).replace(/("usage":\[\{[^\]]*?"input_tokens":)120/, (_m, head: string) => `${head}9007199254740993`);
    assert.match(raw, /9007199254740993/);
    const r = decode(traceDetailDecoder, parseJsonLossless(raw));
    assert.equal(r.ok, true, r.ok ? "" : r.path);
    if (r.ok) assert.equal(r.value.usage[0]!.input_tokens, "9007199254740993");
  });
});
