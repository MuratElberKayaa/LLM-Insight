/**
 * Phase 10C (ADR-56): pseudonymous sessions. The decoder accepts only `s1_` hashes and
 * consistent aggregates; the table links each session to its traces; the trace list parses
 * and forwards a session filter and refuses anything that is not a hash. SYNTHETIC values.
 */
/* eslint-disable @typescript-eslint/no-explicit-any -- wire-form mutations in decoder tests */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { SessionTable } from "../../components/sessions/session-table";
import { sessionListDecoder, type SessionList } from "../../lib/api/sessions";
import { getJson, type BackendPath } from "../../lib/server/backend";
import type { DashboardConfig } from "../../lib/server/config";
import { listStateOf, parseTraceListQuery, traceListParams, traceListSearch } from "../../lib/trace-query";
import { decode } from "../../lib/validate";

const H1 = "s1_" + "a".repeat(32);
const H2 = "s1_" + "b".repeat(32);

function list(): SessionList {
  return {
    project: 7,
    window: { from: "2026-11-20T00:00:00.000000Z", to: "2026-11-21T00:00:00.000000Z" },
    pseudonymous: true,
    items: [
      { session: H1, traces: 2, error_traces: 1, first_seen: "2026-11-20T11:00:01.000000Z", last_seen: "2026-11-20T11:00:02.000000Z", attempts: 2, input_tokens: 200, unknown_input_tokens: 0, output_tokens: 40, unknown_output_tokens: 0, known_cost_usd: null, computed_cost_calls: 0, unknown_cost_calls: 2, cost_complete: false },
      { session: H2, traces: 1, error_traces: 0, first_seen: "2026-11-20T11:00:03.000000Z", last_seen: "2026-11-20T11:00:03.000000Z", attempts: 1, input_tokens: 100, unknown_input_tokens: 0, output_tokens: 20, unknown_output_tokens: 0, known_cost_usd: "0.0000120000", computed_cost_calls: 1, unknown_cost_calls: 0, cost_complete: true },
    ],
    truncated: false,
    limit: 50,
  };
}
const wire = () => structuredClone(list()) as any;

describe("Phase 10C decoder", () => {
  it("accepts consistent pseudonymous aggregates", () => {
    const r = decode(sessionListDecoder, wire());
    assert.equal(r.ok, true, r.ok ? "" : r.path);
  });

  const broken: [string, (d: any) => void][] = [
    ["a raw conversation id", (d) => (d.items[0].session = "conv-1234")],
    ["an uppercase or short hash", (d) => (d.items[0].session = "s1_" + "A".repeat(32))],
    ["a duplicate session", (d) => (d.items[1].session = H1)],
    ["cost counts that do not add up", (d) => (d.items[0].unknown_cost_calls = 1)],
    ["a known cost without a priced call", (d) => (d.items[0].known_cost_usd = "0")],
    ["completeness with unknown cost", (d) => (d.items[0].cost_complete = true)],
    ["more failed traces than traces", (d) => (d.items[1].error_traces = 2)],
    ["first seen after last seen", (d) => (d.items[0].first_seen = "2026-11-20T12:00:00.000000Z")],
    ["a non-pseudonymous response", (d) => (d.pseudonymous = false)],
    ["more items than the limit", (d) => (d.limit = 1)],
  ];
  for (const [name, mutate] of broken) {
    it(`refuses ${name}`, () => {
      const d = wire();
      mutate(d);
      assert.equal(decode(sessionListDecoder, d).ok, false);
    });
  }
});

describe("Phase 10C presentation", () => {
  it("links each session to its traces in the same window and keeps unknown cost unknown", () => {
    const m = renderToStaticMarkup(<SessionTable data={list()} project="7" />);
    assert.match(m, new RegExp(`href="/traces\\?project=7&amp;from=2026-11-20T00%3A00%3A00.000000Z&amp;to=2026-11-21T00%3A00%3A00.000000Z&amp;session=${H1}"`));
    assert.match(m, /unknown/);
    assert.match(m, /2 attempt\(s\) with unknown cost/);
    assert.doesNotMatch(m, /\$0(?!\.0000)/);
    assert.match(m, /1 failed/);
  });

  it("says when the list is truncated", () => {
    const d = list();
    d.truncated = true;
    assert.match(renderToStaticMarkup(<SessionTable data={d} project={null} />), /Only the 50 sessions with the most traces are listed/);
  });
});

describe("Phase 10C trace-list filter", () => {
  it("parses, forwards and links a session hash", () => {
    const q = parseTraceListQuery({ project: "7", session: H1 });
    assert.equal(q.session, H1);
    assert.equal(traceListParams(q, new Date("2026-11-20T12:00:00Z")).session, H1);
    assert.match(traceListSearch(listStateOf(q)), new RegExp(`session=${H1}`));
  });

  it("ignores anything that is not a session hash and never forwards it", () => {
    const q = parseTraceListQuery({ session: "conv-user@example.com" });
    assert.equal(q.session, null);
    assert.ok(q.ignored.includes("session"));
    assert.equal(traceListParams(q, new Date()).session, undefined);
  });

  it("allows exactly the sessions backend path", async () => {
    const config: DashboardConfig = { baseUrl: new URL("http://127.0.0.1:8000"), token: "SYNTHETIC-test-token-0123456789abcdef0123456789", defaultProjectId: "7", timeoutMs: 200 };
    const urls: string[] = [];
    const fetch = (async (input: string | URL | Request) => {
      urls.push(input.toString());
      return new Response(JSON.stringify(list()), { status: 200, headers: { "content-type": "application/json" } });
    }) as typeof globalThis.fetch;
    const ok = await getJson(config, "/api/v1/sessions", { project: "7" }, { limit: "50" }, sessionListDecoder, { fetch });
    assert.equal(ok.ok, true, ok.ok ? "" : JSON.stringify(ok.error));
    const refused = await getJson(config, "/api/v1/sessions/raw" as BackendPath, { project: "7" }, {}, sessionListDecoder, { fetch });
    assert.equal(refused.ok, false);
    assert.equal(urls.length, 1);
  });
});
