import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { ErrorState } from "../../components/states";
import { AttemptsTable, LogicalCalls, ProvenanceNotice, SpanTable, StatusBreakdown, TraceFacts } from "../../components/traces/trace-detail-sections";
import { TraceFilters } from "../../components/traces/trace-filters";
import { TraceListView } from "../../components/traces/trace-list";
import { ROW_BATCH, Waterfall } from "../../components/traces/waterfall";
import type { TraceDetail } from "../../lib/api/trace-types";
import { LosslessInt } from "../../lib/json";
import { buildTraceView } from "../../lib/span-tree";
import { parseTraceListQuery, type ListState } from "../../lib/trace-query";
import { makeDetail, makeList, makeSpan, makeTrace, makeUsage, retryDetail, tid } from "../fixtures/traces";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);
const state = (params: Record<string, string> = {}): ListState => {
  const { ignored, ...s } = parseTraceListQuery(params);
  assert.deepEqual(ignored, []);
  return s;
};
const XSS = `<script>alert("x")</script><img src=x onerror=alert(1)>`;
const assertEscaped = (m: string) => {
  assert.doesNotMatch(m, /<script>/);
  assert.doesNotMatch(m, /<img /);
  assert.doesNotMatch(m, /onerror=alert\(1\)>/);
  assert.match(m, /&lt;script&gt;/);
};

describe("trace list", () => {
  it("renders backend values: id, service, ns-derived duration, status, counts and cost", () => {
    const t = makeTrace({ trace_id: tid(7), service_name: "svc-a", status: "error", span_count: 3 });
    const m = html(<TraceListView data={makeList([t])} state={state()} />);
    assert.match(m, new RegExp(`data-trace-id="${tid(7)}"`));
    assert.match(m, />svc-a</);
    assert.match(m, /31\.8533 ms/);
    assert.match(m, /data-trace-status="error"/);
    assert.match(m, /\$0\.0001875/);
    assert.match(m, /href="\/traces\/a{31}7"/, "detail link for the default listing has no list parameter");
  });

  it("shows unknown cost as unknown (never $0) and counts unpriced calls", () => {
    const t = makeTrace({ usage: { inference_calls: 2, input_tokens: null, output_tokens: "48", calls_with_unknown_tokens: 2, known_cost_usd: null, unknown_cost_calls: 2 } });
    const m = html(<TraceListView data={makeList([t])} state={state()} />);
    assert.match(m, /data-cost-state="unknown"/);
    assert.doesNotMatch(m, /\$0/);
    assert.match(m, /2 of 2 unpriced/);
    assert.match(m, /2 calls unknown/);
  });

  it("distinguishes a computed zero from unknown and marks incomplete sums", () => {
    const zero = makeTrace({ usage: { inference_calls: 1, input_tokens: "0", output_tokens: "0", calls_with_unknown_tokens: 0, known_cost_usd: "0.0000000000", unknown_cost_calls: 0 } });
    assert.match(html(<TraceListView data={makeList([zero])} state={state()} />), /\$0\.00/);
    const partial = makeTrace({ usage: { inference_calls: 3, input_tokens: "10", output_tokens: "5", calls_with_unknown_tokens: 0, known_cost_usd: "0.0015750000", unknown_cost_calls: 2 } });
    const m = html(<TraceListView data={makeList([partial])} state={state()} />);
    assert.match(m, /data-cost-state="incomplete"/);
    assert.match(m, /2 of 3 unpriced/);
  });

  it("labels synthetic, mixed and unknown provenance with text (T48)", () => {
    const items = (["synthetic", "mixed", "unknown", "real"] as const).map((p, i) => makeTrace({ trace_id: tid(i + 1), provenance: p }));
    const m = html(<TraceListView data={makeList(items)} state={state()} />);
    for (const p of ["synthetic", "mixed", "unknown", "real"]) assert.match(m, new RegExp(`data-provenance="${p}"`));
    assert.match(m, />Synthetic</);
    assert.match(m, />Mixed</);
    assert.match(m, />Unknown</);
  });

  it("paginates with the echoed window frozen and the cursor carried", () => {
    const m = html(<TraceListView data={makeList([makeTrace()], "NEXT_cursor-1")} state={state({ status: "error" })} />);
    assert.match(m, /data-testid="next-page"/);
    assert.match(m, /href="\/traces\?from=2026-10-24T09%3A00%3A00\.000000Z&amp;to=2026-10-25T09%3A00%3A00\.000000Z&amp;status=error&amp;cursor=NEXT_cursor-1&amp;page=2"/);
    const last = html(<TraceListView data={makeList([makeTrace()], null)} state={state()} />);
    assert.match(last, /end of the window/);
    assert.doesNotMatch(last, /data-testid="next-page"/);
  });

  it("detail links keep the list state for the back link", () => {
    const m = html(<TraceListView data={makeList([makeTrace()])} state={state({ range: "7d", provenance: "synthetic" })} />);
    assert.match(m, /\?list=range%3D7d%26provenance%3Dsynthetic/);
  });

  it("escapes hostile service names and prompt versions (T41)", () => {
    const m = html(<TraceListView data={makeList([makeTrace({ service_name: XSS, prompt_version: XSS })])} state={state()} />);
    assertEscaped(m);
  });

  it("filters expose only backend-supported options and carry state", () => {
    const m = html(<TraceFilters state={state({ range: "7d", status: "error", model: "gpt-5.4-mini" })} />);
    assert.match(m, /aria-labelledby="trace-status-label"/);
    assert.match(m, /aria-labelledby="trace-prov-label"/);
    assert.match(m, /name="model"/);
    assert.match(m, /value="gpt-5\.4-mini"/);
    assert.doesNotMatch(m, /provider/i, "the API has no provider filter");
    assert.match(m, /Clear model/);
  });
});

describe("trace detail sections", () => {
  it("states trace-level error, span failures and recovery separately, without a cause", () => {
    const d = retryDetail();
    const m = html(<StatusBreakdown trace={d.trace} view={buildTraceView(d)} />);
    assert.match(m, /Trace level:<\/span> Error/);
    assert.match(m, /1 span with status ERROR \(1 inference attempt, 0 application spans\)/);
    assert.match(m, /1 recovered after a failed attempt/);
    assert.match(m, /cause is not inferred/);
    assert.doesNotMatch(m, /because|caused by|root cause/i);
  });

  it("lists physical attempts per logical call with the recorded error.type", () => {
    const m = html(<LogicalCalls view={buildTraceView(retryDetail())} />);
    assert.match(m, /data-outcome="recovered"/);
    assert.match(m, /error\.type 429/);
    assert.match(m, /#1/);
    assert.match(m, /#2/);
  });

  it("attempts table keeps unknown tokens and cost unknown", () => {
    const m = html(<AttemptsTable view={buildTraceView(retryDetail())} usageTruncated={false} />);
    assert.match(m, /usage not reported/);
    assert.match(m, /no verified price for this model/);
    const body = m.slice(m.indexOf("<tbody>"));
    assert.ok(body.length > 0);
    assert.doesNotMatch(body, /\$0/);
  });

  it("provenance notice for every trace provenance; real is an unverified claim", () => {
    for (const p of ["synthetic", "mixed", "unknown", "real"] as const) {
      const m = html(<ProvenanceNotice provenance={p} />);
      assert.match(m, new RegExp(`data-provenance="${p}"`));
      assert.match(m, /not verified by the server/);
    }
    assert.match(html(<ProvenanceNotice provenance="synthetic" />), /Synthetic trace/);
    assert.match(html(<ProvenanceNotice provenance="mixed" />), /never counted as real/);
  });

  it("facts show nanosecond start and the stored status", () => {
    const d = retryDetail();
    const m = html(<TraceFacts trace={d.trace} returnedSpans={3} spansTruncated={false} />);
    assert.match(m, /07:29:47\.428346901 UTC/);
    assert.match(m, /data-trace-status="error"/);
  });

  it("escapes hostile labels everywhere a span, model, provider or attribute appears (T41)", () => {
    const spans = [
      makeSpan({ id: 1, start: 0n, end: 100n, name: XSS, attributes: { "http.route": XSS, big: new LosslessInt("4611686018427387905") } }),
      makeSpan({ id: 2, parent: 1, start: 1n, end: 50n, name: XSS, status: "ERROR", errorType: XSS, provenance: "synthetic" }),
    ];
    spans[0]!.resource_attributes = { "service.name": XSS, "deployment.environment.name": XSS };
    spans[0]!.scope_name = XSS;
    const usage = [makeUsage(2, { provider: XSS, requested_model: XSS, response_model: XSS, call_id: XSS, provider_response_id: XSS, price_version: XSS })];
    const d: TraceDetail = makeDetail(spans, usage, { service_name: XSS, prompt_version: XSS });
    const view = buildTraceView(d);
    for (const el of [
      <TraceFacts key="f" trace={d.trace} returnedSpans={2} spansTruncated={false} />,
      <Waterfall key="w" view={view} initialSelected={null} />,
      <Waterfall key="w2" view={view} initialSelected={view.rows[1]!.spanId} />,
      <LogicalCalls key="c" view={view} />,
      <AttemptsTable key="a" view={view} usageTruncated={false} />,
      <SpanTable key="s" view={view} />,
    ]) {
      assertEscaped(html(el));
    }
    assert.match(html(<Waterfall view={view} initialSelected={null} />), /4611686018427387905/);
  });
});

describe("waterfall (server-rendered markup of the client component)", () => {
  it("is an ARIA tree with levels, sizes, positions and one tab stop", () => {
    const view = buildTraceView(retryDetail());
    const m = html(<Waterfall view={view} initialSelected={null} />);
    assert.match(m, /role="tree"/);
    assert.equal((m.match(/role="treeitem"/g) ?? []).length, 3);
    assert.match(m, /aria-level="1"[^>]*aria-selected="true"[^>]*aria-expanded="true"[^>]*aria-setsize="1"[^>]*aria-posinset="1"[^>]*tabindex="0"/);
    assert.equal((m.match(/tabindex="0"/g) ?? []).length, 1);
    assert.match(m, /aria-level="2"/);
    assert.match(m, /LLM · attempt 2/);
    assert.match(m, /Span with status ERROR/, "legend");
    assert.match(m, /data-testid="span-details"/);
    assert.match(m, /not captured or stored/);
  });

  it("selects the span from the URL when it exists, else the first row", () => {
    const view = buildTraceView(retryDetail());
    const second = view.rows[2]!.spanId;
    assert.match(html(<Waterfall view={view} initialSelected={second} />), new RegExp(`data-testid="span-details" data-span-id="${second}"`));
    assert.match(html(<Waterfall view={view} initialSelected={"f".repeat(16)} />), new RegExp(`data-testid="span-details" data-span-id="${view.rows[0]!.spanId}"`));
  });

  it("renders a bounded number of rows for a large trace", () => {
    const spans = [makeSpan({ id: 1, start: 0n, end: 1_000_000n })];
    for (let i = 2; i <= 800; i += 1) spans.push(makeSpan({ id: i, parent: 1, start: BigInt(i), end: BigInt(i + 10), kind: "INTERNAL" }));
    const m = html(<Waterfall view={buildTraceView(makeDetail(spans, [], {}, 1_000_000n))} initialSelected={null} />);
    assert.equal((m.match(/role="treeitem"/g) ?? []).length, ROW_BATCH);
    assert.match(m, new RegExp(`Showing ${ROW_BATCH} of 800 rows`));
    assert.match(m, /data-testid="show-more-rows"/);
  });

  it("an empty trace says so instead of drawing a chart", () => {
    assert.match(html(<Waterfall view={buildTraceView(makeDetail([]))} initialSelected={null} />), /returned no spans/);
  });

  it("bars carry geometry only; no telemetry string reaches a style attribute", () => {
    const view = buildTraceView(makeDetail([makeSpan({ id: 1, start: 0n, end: 100n, name: "a;background:url(x)" })], [], {}, 100n));
    const m = html(<Waterfall view={view} initialSelected={null} />);
    for (const style of m.match(/style="[^"]*"/g) ?? []) assert.match(style, /^style="(?:[a-z-]+:[0-9.%A-Za-z()\s,-]+;?)+"$/, style);
    assert.doesNotMatch(m, /style="[^"]*url\(/);
  });
});

describe("error states for trace reads", () => {
  it("fixed copy without backend bodies for every failure kind", () => {
    const m = html(<ErrorState error={{ kind: "unauthorized", status: 401 }} resource="the trace list" />);
    assert.match(m, /data-error-kind="unauthorized"/);
    assert.match(m, /never shown/);
    const u = html(<ErrorState error={{ kind: "unreachable" }} resource="this trace" />);
    assert.match(u, /Backend unreachable/);
  });
});
