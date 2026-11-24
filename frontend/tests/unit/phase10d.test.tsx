/**
 * Phase 10D (ADR-57): exact error fingerprints on the overview. The decoder ties counts and
 * fields together; the section names patterns as counts of stored rows, never a root cause,
 * and says when the overview's filters do not apply. SYNTHETIC values only.
 */
/* eslint-disable @typescript-eslint/no-explicit-any -- wire-form mutations in decoder tests */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { ErrorPatternsSection } from "../../components/overview/error-patterns";
import { errorPatternsDecoder, type ErrorPatterns } from "../../lib/api/error-patterns";
import { decode } from "../../lib/validate";

const T2 = "10d" + "0".repeat(28) + "2";
const T3 = "10d" + "0".repeat(28) + "3";

function data(): ErrorPatterns {
  const seen = (s: string) => `2026-11-20T12:00:0${s}.000000Z`;
  return {
    project: 7,
    window: { from: "2026-11-20T00:00:00.000000Z", to: "2026-11-21T00:00:00.000000Z" },
    method: "exact_fingerprint",
    fingerprint: ["source", "provider", "model", "tool_name", "error_type"],
    error_spans: 6,
    patterns: [
      { source: "inference", provider: "openai", model: "gpt-4o-mini-2024-07-18", tool_name: null, error_type: "429", error_class: "http_429", spans: 3, traces: 2, first_seen: seen("1"), last_seen: seen("2"), latest_trace_id: T2 },
      { source: "tool", provider: null, model: null, tool_name: "search_kb", error_type: null, error_class: "not_recorded", spans: 2, traces: 1, first_seen: seen("3"), last_seen: seen("3"), latest_trace_id: T3 },
      { source: "agent", provider: "openai", model: null, tool_name: null, error_type: "_OTHER", error_class: "not_interpreted", spans: 1, traces: 1, first_seen: seen("3"), last_seen: seen("3"), latest_trace_id: T3 },
    ],
    truncated: false,
    limit: 10,
  };
}
const wire = () => structuredClone(data()) as any;

describe("Phase 10D decoder", () => {
  it("accepts consistent patterns", () => {
    const r = decode(errorPatternsDecoder, wire());
    assert.equal(r.ok, true, r.ok ? "" : r.path);
  });

  const broken: [string, (d: any) => void][] = [
    ["spans that do not add up", (d) => (d.error_spans = 7)],
    ["more traces than spans", (d) => (d.patterns[1].traces = 3)],
    ["a model on a tool pattern", (d) => (d.patterns[1].model = "x")],
    ["a tool name on an inference pattern", (d) => (d.patterns[0].tool_name = "search_kb")],
    ["a class without an error type", (d) => (d.patterns[1].error_class = "timeout")],
    ["patterns out of order", (d) => d.patterns.reverse()],
    ["an unknown source", (d) => (d.patterns[2].source = "subagent")],
    ["a malformed trace id", (d) => (d.patterns[0].latest_trace_id = "abc")],
    ["another method", (d) => (d.method = "similarity")],
  ];
  for (const [name, mutate] of broken) {
    it(`refuses ${name}`, () => {
      const d = wire();
      mutate(d);
      assert.equal(decode(errorPatternsDecoder, d).ok, false);
    });
  }

  it("allows fewer listed spans than the total only when truncated", () => {
    const d = wire();
    d.patterns.pop();
    assert.equal(decode(errorPatternsDecoder, d).ok, false);
    d.truncated = true;
    assert.equal(decode(errorPatternsDecoder, d).ok, true);
  });
});

describe("Phase 10D presentation", () => {
  it("lists patterns with their trace link and says they are not root causes", () => {
    const m = renderToStaticMarkup(<ErrorPatternsSection result={{ ok: true, data: data() }} project="7" filtered={false} />);
    assert.match(m, /6 error span\(s\) grouped by exact fingerprint/);
    assert.match(m, /it is not a root cause/);
    assert.match(m, new RegExp(`href="/traces/${T2}\\?project=7"`));
    assert.match(m, /openai · gpt-4o-mini-2024-07-18/);
    assert.match(m, /search_kb/);
    assert.match(m, /http 429/);
    assert.doesNotMatch(m, /filters do not apply/);
  });

  it("says when the overview filters do not apply", () => {
    const m = renderToStaticMarkup(<ErrorPatternsSection result={{ ok: true, data: data() }} project={null} filtered />);
    assert.match(m, /filters do not apply here/);
  });

  it("renders nothing without errors and an error state on a failed read", () => {
    const none = { ...data(), error_spans: 0, patterns: [] };
    assert.equal(renderToStaticMarkup(<ErrorPatternsSection result={{ ok: true, data: none }} project={null} filtered={false} />), "");
    assert.equal(renderToStaticMarkup(<ErrorPatternsSection result={null} project={null} filtered={false} />), "");
    assert.match(renderToStaticMarkup(<ErrorPatternsSection result={{ ok: false, error: { kind: "server_error", status: 500 } }} project={null} filtered={false} />), /error patterns/);
  });
});
