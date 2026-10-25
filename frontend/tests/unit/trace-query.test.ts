import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  backToListHref,
  firstPageState,
  nextPageState,
  parseTraceListQuery,
  traceDetailHref,
  traceListHref,
  traceListParams,
  withFilter,
  listStateOf,
  type ListState,
} from "../../lib/trace-query";

const listState = (params: Record<string, string>): ListState => {
  const q = parseTraceListQuery(params);
  assert.deepEqual(q.ignored, []);
  return listStateOf(q);
};

const NOW = new Date("2026-10-25T08:20:31.123Z");
const FROM = "2026-10-24T09:00:00.000000Z";
const TO = "2026-10-25T09:00:00.000000Z";

describe("parseTraceListQuery", () => {
  it("defaults: last 24 h, all statuses and provenance, no model, 25 per page, first page", () => {
    const q = parseTraceListQuery({});
    assert.deepEqual(q, {
      project: { kind: "default" },
      window: { kind: "preset", range: "24h" },
      status: "all",
      provenance: "all",
      model: null,
      limit: 25,
      cursor: null,
      page: 1,
      ignored: [],
      windowProblem: null,
    });
  });

  it("accepts the filters the backend implements", () => {
    const q = parseTraceListQuery({ range: "7d", status: "error", provenance: "mixed", model: "gpt-5.4-mini-2026-03-17", limit: "10" });
    assert.deepEqual(q.window, { kind: "preset", range: "7d" });
    assert.equal(q.status, "error");
    assert.equal(q.provenance, "mixed");
    assert.equal(q.model, "gpt-5.4-mini-2026-03-17");
    assert.equal(q.limit, 10);
    assert.deepEqual(q.ignored, []);
  });

  it("accepts a custom UTC window of at most 31 days with a cursor and page", () => {
    const q = parseTraceListQuery({ from: FROM, to: TO, cursor: "djE6MTc5MjkxMzM4NzQyODM0Njo1NmQ1", page: "3" });
    assert.deepEqual(q.window, { kind: "custom", from: FROM, to: TO });
    assert.equal(q.cursor, "djE6MTc5MjkxMzM4NzQyODM0Njo1NmQ1");
    assert.equal(q.page, 3);
  });

  it("refuses windows the backend would refuse (naive, offsets, > 6 fraction digits, reversed, > 31 days)", () => {
    for (const [from, to] of [
      ["2026-10-24T09:00:00", TO],
      ["2026-10-24T09:00:00+03:00", TO],
      ["2026-10-24T09:00:00.1234567Z", TO],
      [TO, FROM],
      [TO, TO],
      ["2026-09-01T00:00:00Z", "2026-10-25T00:00:00Z"],
      [FROM, undefined],
      ["1729753200", TO],
    ] as const) {
      const q = parseTraceListQuery({ from, to });
      assert.equal(q.window.kind, "preset", `${from} → ${to}`);
      assert.ok(q.ignored.includes("from/to"));
    }
  });

  it("ignores unsupported or hostile values with a notice, never forwarding them", () => {
    const q = parseTraceListQuery({
      range: "999d",
      status: "<script>",
      provenance: "real'--",
      model: "<script>alert(1)</script>",
      limit: "1000",
      cursor: "x".repeat(10),
      provider: "openai",
    });
    assert.deepEqual(q.window, { kind: "preset", range: "24h" });
    assert.equal(q.status, "all");
    assert.equal(q.provenance, "all");
    assert.equal(q.model, null);
    assert.equal(q.limit, 25);
    assert.equal(q.cursor, null, "a cursor outside a fixed window is dropped");
    assert.deepEqual(q.ignored.sort(), ["cursor", "limit", "model", "provenance", "range", "status"].sort());
  });

  it("treats repeated parameters as unsupported", () => {
    const q = parseTraceListQuery({ status: ["ok", "error"] });
    assert.equal(q.status, "all");
    assert.deepEqual(q.ignored, ["status"]);
  });
});

describe("backend parameters and links", () => {
  it("resolves a preset at render time, aligned like the overview", () => {
    const p = traceListParams(parseTraceListQuery({ status: "ok" }), NOW);
    assert.deepEqual(p, { from: "2026-10-24T09:00:00.000Z", to: "2026-10-25T09:00:00.000Z", limit: "25", status: "ok" });
  });

  it("sends only validated values, including the opaque cursor", () => {
    const p = traceListParams(parseTraceListQuery({ from: FROM, to: TO, cursor: "abc_DEF-123", provenance: "synthetic", model: "mock-chat-small" }), NOW);
    assert.deepEqual(p, { from: FROM, to: TO, limit: "25", provenance: "synthetic", model: "mock-chat-small", cursor: "abc_DEF-123" });
  });

  it("the next page freezes the backend-echoed window and advances the page", () => {
    const state = listState({ range: "6h", status: "error" });
    const next = nextPageState(state, { from: FROM, to: TO }, "CURSOR1");
    assert.equal(traceListHref(next), `/traces?from=${encodeURIComponent(FROM)}&to=${encodeURIComponent(TO)}&status=error&cursor=CURSOR1&page=2`);
    const round = parseTraceListQuery(Object.fromEntries(new URL(`http://x${traceListHref(next)}`).searchParams));
    assert.equal(round.cursor, "CURSOR1");
    assert.equal(round.page, 2);
    assert.equal(traceListHref(firstPageState(next)), `/traces?from=${encodeURIComponent(FROM)}&to=${encodeURIComponent(TO)}&status=error`);
  });

  it("changing a filter restarts paging", () => {
    const state = listState({ from: FROM, to: TO, cursor: "C", page: "4" });
    const changed = withFilter(state, { status: "error" });
    assert.equal(changed.cursor, null);
    assert.equal(changed.page, 1);
  });

  it("detail links remember the listing, and back links rebuild only canonical parameters", () => {
    const state = listState({ range: "7d", status: "error" });
    const href = traceDetailHref("56d5c0b2144b3ec23fa58c9561fbbff0", state);
    assert.equal(href, "/traces/56d5c0b2144b3ec23fa58c9561fbbff0?list=range%3D7d%26status%3Derror");
    const list = new URL(`http://x${href}`).searchParams.get("list") ?? "";
    assert.equal(backToListHref(list), "/traces?range=7d&status=error");
  });

  it("a crafted back link cannot leave /traces or smuggle parameters", () => {
    for (const evil of ["javascript:alert(1)", "//evil.example/x", "range=7d&next=https://evil.example", "status=<script>", "%0d%0aSet-Cookie:x", "a".repeat(3000)]) {
      const href = backToListHref(evil);
      assert.match(href, /^\/traces(\?[A-Za-z0-9=&%._-]*)?$/, evil);
      assert.doesNotMatch(href, /evil|script|javascript|next=/);
    }
    assert.equal(backToListHref(undefined), "/traces");
    assert.equal(backToListHref(["a", "b"]), "/traces");
  });
});
