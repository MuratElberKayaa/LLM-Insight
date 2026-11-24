/**
 * UI-R2 (trace and agent explorer): span streaming facts and duration ranks come from stored
 * values only; agent steps are told apart and a possible loop is a hypothesis at the backend's
 * threshold; the inspector links diagnostics and reviews of the selected span; the trace rail
 * marks the open trace and keeps the listing; overview links carry only filters with the same
 * meaning. SYNTHETIC fixtures only; no content is stored or shown.
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { ContextBar } from "../../components/context-bar";
import { KpiGrid } from "../../components/overview/kpi-grid";
import { LatestTrace } from "../../components/overview/latest-trace";
import { AgentSteps } from "../../components/traces/trace-detail-sections";
import { TraceRail } from "../../components/traces/trace-rail";
import { Waterfall } from "../../components/traces/waterfall";
import { LOOP_CATEGORY, REPEATED_TOOL_CALLS, analyseAgent, findingAnchor, findingsBySpan, stepFamily } from "../../lib/agent-analysis";
import type { Diagnostics, Finding } from "../../lib/api/diagnostics";
import type { SpanRecord, TraceDetail } from "../../lib/api/trace-types";
import { overviewTraceListState, projectContext, scopeEntries } from "../../lib/context-links";
import { SLOWEST_MARKED, buildTraceView } from "../../lib/span-tree";
import { traceListHref, traceListSearch, type ListState } from "../../lib/trace-query";
import { makeOverview } from "../fixtures/overview";
import { makeDetail, makeList, makeSpan, makeTrace, makeUsage, retryDetail, sid, tid } from "../fixtures/traces";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);
const count = (m: string, re: RegExp) => (m.match(re) ?? []).length;
const tool = (id: number, name: string, start: bigint, status: SpanRecord["status"] = "UNSET") =>
  makeSpan({ id, parent: 1, start, end: start + 10n, kind: "INTERNAL", status, name: `execute_tool ${name}`, attributes: { "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": name } });

/** An agent that calls `lookup` exactly five times (the threshold), `search` twice, an unnamed tool five times, one LLM, one retrieval, one MCP step. */
function loopDetail(): TraceDetail {
  const spans: SpanRecord[] = [
    makeSpan({ id: 1, start: 0n, end: 10_000n, kind: "INTERNAL", name: "invoke_agent triage", attributes: { "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "triage" } }),
    makeSpan({ id: 2, parent: 1, start: 5n, end: 4_000n, name: "chat gpt-5.4-mini", attributes: { "gen_ai.operation.name": "chat" } }),
    makeSpan({ id: 3, parent: 1, start: 4_100n, end: 4_200n, kind: "INTERNAL", name: "retrieval kb", attributes: { "gen_ai.operation.name": "retrieval", "gen_ai.data_source.id": "kb" } }),
    makeSpan({ id: 4, parent: 1, start: 4_300n, end: 4_400n, kind: "CLIENT", name: "tools/list", attributes: { "mcp.method.name": "tools/list" } }),
  ];
  let id = 10;
  for (let i = 0; i < 5; i += 1) spans.push(tool(id++, "lookup", 5_000n + BigInt(i * 20), i === 4 ? "ERROR" : "UNSET"));
  for (let i = 0; i < 2; i += 1) spans.push(tool(id++, "search", 6_000n + BigInt(i * 20)));
  for (let i = 0; i < 5; i += 1) {
    spans.push(makeSpan({ id: id++, parent: 1, start: 7_000n + BigInt(i * 20), end: 7_010n + BigInt(i * 20), kind: "INTERNAL", name: "execute_tool", attributes: { "gen_ai.operation.name": "execute_tool" } }));
  }
  return makeDetail(spans, [makeUsage(2)], {}, 10_000n);
}

function loopDiagnostics(detail: TraceDetail): Diagnostics {
  const lookup = detail.spans.filter((s) => s.genai_step.name === "lookup");
  const finding: Finding = {
    id: "repeated_tool_calls:trace",
    category: LOOP_CATEGORY,
    group: "agent_signal",
    subject: { type: "trace" },
    trigger: "one tool name executed 5 or more times",
    observed_facts: [{ name: "threshold", value: 5 }],
    evidence: lookup.map((s) => ({
      span_id: s.span_id,
      parent_span_id: s.parent_span_id,
      role: s.role,
      kind: s.kind,
      status: s.status,
      error_type: null,
      duration_ns: s.duration_ns,
      provenance: "unknown",
      provenance_verified: false,
      inference: null,
      usage: null,
      validation: null,
    })),
    evidence_total: lookup.length,
    evidence_truncated: false,
    hypothesis: { code: "possible_agent_loop", statement: "Possible explanation (not verified).", error_interpretation: null, verified: false },
    established: [],
    not_established: [],
    limitations: [],
    data_complete: true,
    investigation_steps: [],
  };
  return {
    contract_version: "llminsight.diagnostics.v1",
    ruleset_version: "llminsight.diagnostics.rules.v1",
    method: { kind: "deterministic_rules", language_model_used: false, content_used: false },
    trace: { trace_id: detail.trace.trace_id, status: "error", provenance: "unknown", provenance_verified: false, span_count: detail.spans.length },
    coverage: { complete: true, spans_stored: detail.spans.length, spans_analyzed: detail.spans.length, spans_truncated: false, usage_truncated: false, limitations: [] },
    findings: [finding],
    findings_total: 1,
    findings_truncated: false,
    not_assessed: [],
  };
}

describe("span view: streaming and duration ranks", () => {
  it("reads time to first chunk and the stream flag only as stored (seconds → ms), never estimates", () => {
    const spans = [
      makeSpan({ id: 1, start: 0n, end: 1000n }),
      makeSpan({ id: 2, parent: 1, start: 0n, end: 900n, attributes: { "gen_ai.request.stream": true, "gen_ai.response.time_to_first_chunk": 0.412 } }),
      makeSpan({ id: 3, parent: 1, start: 0n, end: 900n, attributes: { "gen_ai.request.stream": true } }),
      makeSpan({ id: 4, parent: 1, start: 0n, end: 900n, attributes: { "gen_ai.request.stream": false } }),
      makeSpan({ id: 5, parent: 1, start: 0n, end: 900n, attributes: { "gen_ai.response.time_to_first_chunk": "0.2" } }),
    ];
    const rows = buildTraceView(makeDetail(spans, [], {}, 1000n)).rows;
    const s = (n: number) => rows.find((r) => r.spanId === sid(n))!.streaming;
    assert.equal(Math.round(s(2).ttftMs! * 1000) / 1000, 412);
    assert.deepEqual(s(3), { requested: true, ttftMs: null });
    assert.deepEqual(s(4), { requested: false, ttftMs: null });
    assert.deepEqual(s(5), { requested: null, ttftMs: null }, "a string is not a recorded number");
  });

  it("ranks the three longest non-root spans with a positive duration; ties keep start order", () => {
    const spans = [
      makeSpan({ id: 1, start: 0n, end: 1000n }),
      makeSpan({ id: 2, parent: 1, start: 0n, end: 100n }),
      makeSpan({ id: 3, parent: 1, start: 100n, end: 600n }),
      makeSpan({ id: 4, parent: 1, start: 600n, end: 700n }),
      makeSpan({ id: 5, parent: 1, start: 700n, end: 950n }),
      makeSpan({ id: 6, parent: 1, start: 950n, end: 950n }),
    ];
    const rows = buildTraceView(makeDetail(spans, [], {}, 1000n)).rows;
    const rank = (n: number) => rows.find((r) => r.spanId === sid(n))!.slowRank;
    assert.equal(SLOWEST_MARKED, 3);
    // Durations: #2 100, #3 500, #4 100, #5 250, #6 0. The 100-tie goes to #2 (earlier start).
    assert.deepEqual([1, 2, 3, 4, 5, 6].map(rank), [null, 3, 1, null, 2, null]);
    assert.equal(rank(1), null, "the root is never ranked");
    assert.equal(rank(6), null, "a zero-length span is never ranked");
  });
});

describe("agent analysis", () => {
  it("tells the five step families apart from the backend's classification and the usage rows", () => {
    const d = loopDetail();
    const a = analyseAgent(buildTraceView(d).rows, d.agent_summary, null);
    assert.deepEqual(a.families, { agent: 1, llm: 1, tool: 12, retrieval: 1, mcp: 1, app: 0 });
    assert.equal(stepFamily({ inference: null, step: { kind: null, name: null, mcp_method: null } }), "app");
    // A usage row makes an LLM attempt even when no gen_ai.operation.name was recorded (retryDetail spans have none).
    const retry = buildTraceView(retryDetail()).rows;
    assert.equal(retry[1]!.step.kind, null);
    assert.equal(stepFamily(retry[1]!), "llm");
  });

  it("flags a possible loop only for a named tool at the backend's threshold (5), with repeats", () => {
    const d = loopDetail();
    const a = analyseAgent(buildTraceView(d).rows, d.agent_summary, null);
    assert.equal(REPEATED_TOOL_CALLS, 5);
    assert.deepEqual(a.loopTools, ["lookup"], "the unnamed tool (5 runs) cannot be grouped, as in the backend rule");
    const byName = Object.fromEntries(a.tools.map((t) => [t.name ?? "(unnamed)", t]));
    assert.equal(byName.lookup!.repeats, 4);
    assert.equal(byName.lookup!.possibleLoop, true, "exactly at the threshold");
    assert.equal(byName.search!.possibleLoop, false);
    assert.equal(byName["(unnamed)"]!.possibleLoop, false);
    assert.equal(a.loopFindingId, null);
    assert.equal(analyseAgent(buildTraceView(d).rows, d.agent_summary, loopDiagnostics(d)).loopFindingId, "repeated_tool_calls:trace");
  });

  it("maps findings to their evidence spans with a safe anchor", () => {
    const d = loopDetail();
    const map = findingsBySpan(loopDiagnostics(d));
    assert.equal(Object.keys(map).length, 5);
    assert.equal(findingAnchor("repeated_tool_calls:trace"), "finding-repeated_tool_calls-trace");
    assert.equal(findingAnchor('x"><img>'), "finding-x---img-");
    assert.deepEqual(findingsBySpan(null), {});
  });

  it("the agent section states the loop as a hypothesis, links the finding and never shows tool content", () => {
    const d = loopDetail();
    const a = analyseAgent(buildTraceView(d).rows, d.agent_summary, loopDiagnostics(d));
    const m = html(<AgentSteps summary={d.agent_summary} spansTruncated={false} analysis={a} />);
    assert.match(m, /data-testid="agent-families"/);
    assert.match(m, /data-family="tool" data-count="12"/);
    assert.match(m, /data-testid="agent-loop-notice" data-tools="1"/);
    assert.match(m, /Possible loop \(hypothesis, not verified\)/);
    assert.match(m, /ran 5 or more times/);
    assert.match(m, /may be intended/);
    assert.match(m, /href="#finding-repeated_tool_calls-trace"/);
    assert.equal(count(m, /data-testid="tool-loop-flag"/g), 1);
    assert.match(m, /data-testid="tool-repeats">×5</);
    assert.doesNotMatch(m, /root cause/i);
    // Without diagnostics the notice still links the diagnostics section.
    const b = analyseAgent(buildTraceView(d).rows, d.agent_summary, null);
    assert.match(html(<AgentSteps summary={d.agent_summary} spansTruncated={false} analysis={b} />), /href="#diagnostics-heading"/);
  });

  it("no loop notice below the threshold", () => {
    const spans = [makeSpan({ id: 1, start: 0n, end: 1000n }), ...[0, 1, 2, 3].map((i) => tool(10 + i, "lookup", BigInt(i * 20)))];
    const d = makeDetail(spans, [], {}, 1000n);
    const a = analyseAgent(buildTraceView(d).rows, d.agent_summary, null);
    assert.deepEqual(a.loopTools, []);
    assert.doesNotMatch(html(<AgentSteps summary={d.agent_summary} spansTruncated={false} analysis={a} />), /agent-loop-notice|tool-loop-flag/);
  });
});

describe("waterfall: families, ranks and the span inspector", () => {
  it("badges every row with its family, colors tool steps apart, and marks the longest spans", () => {
    const d = loopDetail();
    const view = buildTraceView(d);
    const m = html(<Waterfall view={view} initialSelected={null} />);
    assert.equal(count(m, /role="treeitem"[^>]*data-family="/g), view.rows.length);
    assert.match(m, /data-family="mcp"/);
    assert.match(m, /data-testid="span-bar"[^>]*class="[^"]*bg-series-3/);
    assert.equal(count(m, /data-testid="span-slow"/g), SLOWEST_MARKED);
    assert.match(m, /Tool, retrieval or MCP step/);
    assert.match(m, /data-action="collapse-all"/);
    assert.match(m, /data-testid="explorer-panel-switch"/);
  });

  it("the inspector shows the family, the recorded TTFT, diagnostics on the span and its reviews", () => {
    const spans = [
      makeSpan({ id: 1, start: 0n, end: 1000n }),
      makeSpan({ id: 2, parent: 1, start: 0n, end: 900n, attributes: { "gen_ai.operation.name": "chat", "gen_ai.request.stream": true, "gen_ai.response.time_to_first_chunk": 0.25 } }),
    ];
    const view = buildTraceView(makeDetail(spans, [makeUsage(2)], {}, 1000n));
    const findings = { [sid(2)]: [{ anchor: "finding-x", category: "provider_error", hypothesis: "h" }] };
    const m = html(<Waterfall view={view} initialSelected={sid(2)} findingsBySpan={findings} reviewsBySpan={{ [sid(2)]: 2 }} reviewsTotal={3} />);
    assert.match(m, /data-testid="span-details-family" data-family="llm"/);
    assert.match(m, /data-testid="span-ttft" data-ttft-state="recorded">250\.0 ms</);
    assert.match(m, /data-testid="span-findings" data-count="1"/);
    assert.match(m, /href="#finding-x"/);
    assert.match(m, /Unverified hypothesis/);
    assert.match(m, /data-testid="span-reviews" data-count="2">2 review\(s\) anchored on this span · 3 on the trace/);
  });

  it("states a missing TTFT and an empty diagnostics list instead of inventing values", () => {
    const spans = [makeSpan({ id: 1, start: 0n, end: 1000n }), makeSpan({ id: 2, parent: 1, start: 0n, end: 900n, attributes: { "gen_ai.request.stream": true } })];
    const view = buildTraceView(makeDetail(spans, [makeUsage(2)], {}, 1000n));
    const m = html(<Waterfall view={view} initialSelected={sid(2)} />);
    assert.match(m, /data-ttft-state="not_recorded">not recorded/);
    assert.match(m, /data-testid="span-findings" data-count="0"/);
    assert.match(m, /No review is anchored on this span · 0 on the trace/);
    const plain = buildTraceView(makeDetail([makeSpan({ id: 1, start: 0n, end: 10n }), makeSpan({ id: 2, parent: 1, start: 0n, end: 5n })], [makeUsage(2)], {}, 10n));
    assert.match(html(<Waterfall view={plain} initialSelected={sid(2)} />), /data-ttft-state="unknown">unknown \(streaming flag not recorded\)/);
  });

  it("keeps the existing tree semantics and test ids (one tab stop, legend, details)", () => {
    const m = html(<Waterfall view={buildTraceView(retryDetail())} initialSelected={null} />);
    assert.equal(count(m, /tabindex="0"/g), 1);
    assert.match(m, /aria-label="Selected span"/);
    assert.match(m, /Span with status ERROR/);
    assert.match(m, /LLM · attempt 2/);
  });
});

describe("trace rail", () => {
  const state: ListState = { project: "7", window: { kind: "preset", range: "24h" }, status: "error", provenance: "all", model: null, provider: "openai", session: null, limit: 25, cursor: null, page: 1 };
  const items = [makeTrace({ trace_id: tid(1), service_name: "svc-a", status: "error" }), makeTrace({ trace_id: tid(2), service_name: "svc-b", provenance: "synthetic" })];

  it("marks the open trace, keeps the listing in every link and pages without leaving the trace", () => {
    const m = html(<TraceRail result={{ ok: true, data: makeList(items, "CURSOR1") }} currentId={tid(2)} state={state} />);
    assert.match(m, /aria-label="Traces in this list"/);
    assert.equal(count(m, /aria-current="page"/g), 1);
    assert.match(m, new RegExp(`aria-current="page"[^>]*data-testid="rail-trace" data-trace-id="${tid(2)}"`));
    const list = encodeURIComponent(traceListSearch(state, false));
    assert.ok(m.includes(`href="/traces/${tid(1)}?project=7&amp;list=${list}"`), "rows keep the listing");
    assert.match(m, new RegExp(`data-testid="rail-next"`));
    assert.match(m, new RegExp(`href="/traces/${tid(2)}\\?project=7&amp;list=[^"]*cursor%3DCURSOR1`), "next page stays on the open trace");
    assert.ok(m.includes(`href="${traceListHref(state).replaceAll("&", "&amp;")}"`), "open the full list");
    assert.match(m, /status error/);
    assert.match(m, /provider openai/);
    assert.match(m, /Synthetic/);
    assert.doesNotMatch(m, /rail-not-listed/);
  });

  it("says when the open trace is not on the page, and degrades alone when the list read fails", () => {
    assert.match(html(<TraceRail result={{ ok: true, data: makeList(items) }} currentId={tid(9)} state={state} />), /The open trace is not on this page/);
    const down = html(<TraceRail result={{ ok: false, error: { kind: "unreachable" } }} currentId={tid(1)} state={state} />);
    assert.match(down, /data-state="unavailable"/);
    assert.match(down, /The trace itself is shown/);
    assert.match(html(<TraceRail result={{ ok: true, data: makeList([]) }} currentId={tid(1)} state={state} />), /data-state="empty"/);
  });
});

describe("context carried from the overview", () => {
  it("keeps project, window, provenance, provider and model; status only when asked; no cursor", () => {
    const s = overviewTraceListState({ project: "7", window: { kind: "custom", from: "2026-11-12T00:00:00Z", to: "2026-11-14T00:00:00Z" }, provenance: "real", provider: "groq", model: "m-1" });
    assert.equal(traceListHref(s), "/traces?project=7&from=2026-11-12T00%3A00%3A00Z&to=2026-11-14T00%3A00%3A00Z&provenance=real&model=m-1&provider=groq");
    const e = overviewTraceListState({ project: null, window: { kind: "preset", range: "7d" }, provenance: "all" }, "error");
    assert.equal(traceListHref(e), "/traces?range=7d&status=error");
  });

  it("the trace KPIs link to the same scope, and only when there are traces", () => {
    const links = { traces: "/traces?project=7", errors: "/traces?project=7&status=error" };
    const m = html(<KpiGrid data={makeOverview()} links={links} />);
    assert.match(m, /data-testid="kpi-traces-link"/);
    assert.match(m, /data-testid="kpi-error-traces-link" href="\/traces\?project=7&amp;status=error"/);
    assert.doesNotMatch(html(<KpiGrid data={makeOverview()} />), /kpi-traces-link/);
  });

  it("recent traces open with the overview's listing", () => {
    const s = overviewTraceListState({ project: "7", window: { kind: "preset", range: "7d" }, provenance: "synthetic" });
    const m = html(<LatestTrace result={{ ok: true, data: makeList([makeTrace({ trace_id: tid(1) })]) }} fetchedAt={new Date()} project="7" filtered listState={s} />);
    assert.ok(m.includes(`href="/traces/${tid(1)}?project=7&amp;list=${encodeURIComponent("range=7d&provenance=synthetic")}"`));
    assert.ok(m.includes(`href="/traces?project=7&amp;range=7d&amp;provenance=synthetic"`));
  });

  it("the context bar names the scope as text, with the local-only notice", () => {
    const entries = scopeEntries({
      projectId: "7",
      projects: { ok: true, data: { items: [{ id: 7, name: "<b>demo</b>", created_at: "2026-11-01T00:00:00Z" }], truncated: false, limit: 200 } },
      window: { kind: "preset", range: "24h" },
      shown: null,
      provenance: "real",
      provider: "groq",
      status: "error",
    });
    assert.deepEqual(
      entries.map((e) => e.key),
      ["project", "window", "provenance", "status", "provider"],
    );
    const m = html(<ContextBar items={entries} />);
    assert.match(m, /#7 · &lt;b&gt;demo&lt;\/b&gt;/);
    assert.match(m, /Real \(asserted\) only/);
    assert.match(m, /Local only · metadata only · not live/);
    assert.equal(projectContext(null, null), "not resolved");
    assert.equal(projectContext("9", { ok: false, error: { kind: "unreachable" } }), "#9");
  });
});
