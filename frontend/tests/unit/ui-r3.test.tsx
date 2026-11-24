/**
 * UI-R3 (model analytics, session explorer, collapsible sidebar): the pair is chosen by a
 * validated group key and only reads the groups' own values; differences are stated, never a
 * ranking; reasoning and cached tokens stay subsets; unknown cost is never $0; the group → trace
 * link carries only filters with the same meaning; the session timeline places stored times only
 * and shows no raw id; the sidebar keeps accessible names. SYNTHETIC fixtures only.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, it } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { AutoRefresh } from "../../components/auto-refresh";
import { ComparePanel, LatencyRange } from "../../components/models/compare-panel";
import { ComparisonTable } from "../../components/models/model-comparison";
import { SessionTable } from "../../components/sessions/session-table";
import { SessionTimeline } from "../../components/sessions/session-timeline";
import { ShellFrame, SidebarToggle } from "../../components/shell-frame";
import type { SessionList } from "../../lib/api/sessions";
import type { ModelGroup } from "../../lib/api/types";
import { comparability, groupTraceLink, pairHref, parseGroupParam, selectPair } from "../../lib/model-compare";
import { groupKey, toGroupPoints } from "../../lib/model-groups";
import { formatDurationNs } from "../../lib/nanos";
import { buildSessionTimeline } from "../../lib/session-timeline";
import { traceListSearch, type ListState } from "../../lib/trace-query";
import { makeCompare } from "../fixtures/overview";
import { BASE_NS, makeList, makeTrace, tid } from "../fixtures/traces";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);
const count = (m: string, re: RegExp) => (m.match(re) ?? []).length;
const H1 = `s1_${"a".repeat(32)}`;
const H2 = `s1_${"b".repeat(32)}`;

describe("pair selection from the URL", () => {
  it("accepts a group key and refuses anything else", () => {
    const g = makeCompare().groups[0]!;
    assert.equal(parseGroupParam(groupKey(g)), groupKey(g));
    for (const bad of [undefined, ["x"], "nope", "[]", '["a",null,null]', '[1,null,null,null]', '["",null,null,null]', `["${"p".repeat(65)}",null,null,null]`, `["p","${"m".repeat(257)}",null,null]`])
      assert.equal(parseGroupParam(bad as never), null, String(bad));
  });

  it("finds the two groups among the returned ones and names a missing one", () => {
    const d = makeCompare();
    const [a, b] = d.groups;
    const p = selectPair(d, groupKey(a!), groupKey(b!));
    assert.equal(p.a, a);
    assert.equal(p.b, b);
    assert.deepEqual(p.missing, []);
    const q = selectPair(d, '["gone",null,null,null]', null);
    assert.deepEqual(q.missing, ["a"]);
    assert.equal(q.b, null);
  });

  it("pair links keep the page's filters and replace only a and b", () => {
    const href = pairHref("/models?project=7&range=7d&provenance=synthetic&a=old", "A1", null);
    assert.equal(href, "/models?project=7&range=7d&provenance=synthetic&a=A1");
    assert.equal(pairHref("/models", null, null), "/models");
  });
});

describe("comparability: differences are stated, nothing is ranked", () => {
  it("names every difference of the fixture pair", () => {
    const [a, b] = makeCompare().groups as [ModelGroup, ModelGroup];
    const d = comparability(a, b);
    const differs = d.filter((x) => !x.same).map((x) => x.aspect);
    assert.deepEqual(differs, ["provider", "requested_model", "response_model", "prompt_version", "sample", "cost", "usage_source"]);
    assert.match(d.find((x) => x.aspect === "prompt_version")!.text, /inputs were not the same/);
    assert.match(d.find((x) => x.aspect === "cost")!.text, /never \$0/);
  });

  it("a group with itself is alike everywhere except a small sample", () => {
    const a = makeCompare().groups[0]!;
    const d = comparability(a, a);
    assert.deepEqual(
      d.filter((x) => !x.same).map((x) => x.aspect),
      ["sample"],
    );
  });

  it("streaming is compared only when a side streams", () => {
    const [a, b] = makeCompare().groups as [ModelGroup, ModelGroup];
    assert.equal(comparability(a, b).some((x) => x.aspect === "streaming"), false);
    const s = { ...a, time_to_first_chunk: { ...a.time_to_first_chunk, available: true, reason: null, detail: null, streaming_attempts: 2, n: 2, p50_ms: 100, p95_ms: 120 } };
    assert.match(comparability(s, b).find((x) => x.aspect === "streaming")!.text, /Only one group streams/);
  });
});

describe("side-by-side panel", () => {
  const d = makeCompare();
  const [a, b] = d.groups as [ModelGroup, ModelGroup];
  const pair = selectPair(d, groupKey(a), groupKey(b));
  const m = html(<ComparePanel data={d} pair={pair} hidden={[["project", "7"]]} clearHref="/models?project=7" />);

  it("shows both groups' own values in one captioned table, requested → response kept", () => {
    assert.match(m, /data-testid="pair-table"/);
    assert.match(m, /<caption[^>]*>Group A and group B side by side/);
    assert.match(m, /gpt-5\.4-mini[^<]*<span aria-hidden="true"[^>]*> → <\/span><span class="sr-only"> response model: <\/span>gpt-5\.4-mini-2026-03-17/);
    assert.match(m, /25%/);
    assert.match(m, /\(1 of 4\)/);
  });

  it("keeps reasoning and cached tokens as subsets and unknown cost unknown", () => {
    assert.equal(count(m, /part of output, not added/g) + count(m, /not reported/g) > 0, true);
    assert.match(m, /data-testid="pair-cost-b" data-cost-state="unknown"/);
    const costB = m.slice(m.indexOf('data-testid="pair-cost-b"'), m.indexOf("</td>", m.indexOf('data-testid="pair-cost-b"')));
    assert.doesNotMatch(costB, /\$0/);
    assert.match(costB, /Unknown cost <span[^>]*>2<\/span>/);
  });

  it("states the differences and that no test was run; never a winner", () => {
    assert.match(m, /data-testid="pair-differences" data-unlike="7"/);
    assert.match(m, /not a controlled experiment/);
    assert.match(m, /no statistical test is run/);
    assert.doesNotMatch(m, /winner|statistically significant|best model/i);
  });

  it("the form keeps the page's filters and offers every group", () => {
    assert.match(m, /<input type="hidden" name="project" value="7"\/>/);
    assert.equal(count(m, /<option value="\[/g), 2 * d.groups.length);
  });

  it("asks for two groups when none is chosen, and says when a linked group is missing", () => {
    const none = html(<ComparePanel data={d} pair={selectPair(d, null, null)} hidden={[]} clearHref="/models" />);
    assert.match(none, /data-testid="pair-empty"/);
    assert.doesNotMatch(none, /pair-clear/);
    const gone = html(<ComparePanel data={d} pair={selectPair(d, '["gone",null,null,null]', null)} hidden={[]} clearHref="/models" />);
    assert.match(gone, /data-testid="pair-missing"[^>]*>▲ Group A from the link/);
  });

  it("latency range: shared scale, text values, and no bar without a sample", () => {
    const r = html(<LatencyRange g={a} max={600} testId="r" />);
    assert.match(r, /role="img" aria-label="p50 80\.0 ms, p95 290\.0 ms, p99 300\.0 ms, 4 samples"/);
    assert.match(r, /width:48\.3/);
    assert.match(r, /Low sample \(p95\)/);
    const empty = { ...a, latency: { n: 0, p50_ms: null, p95_ms: null, p99_ms: null, low_sample: { p95: true, p99: true } } };
    assert.match(html(<LatencyRange g={empty} max={600} testId="r" />), /No latency sample/);
  });
});

describe("group links in the comparison table", () => {
  const state = { project: "7", window: { kind: "preset" as const, range: "7d" as const } };

  it("opens the traces of the group's provider and model in the same window, nothing else", () => {
    const g = makeCompare().groups[0]!;
    const l = groupTraceLink(g, state);
    assert.equal(l.href, "/traces?project=7&range=7d&model=gpt-5.4-mini&provider=openai");
    assert.match(l.caveat, /any prompt version/);
    const noModel = groupTraceLink({ ...g, requested_model: null, response_model: null, prompt_version: null }, state);
    assert.equal(noModel.href, "/traces?project=7&range=7d&provider=openai");
    const badModel = groupTraceLink({ ...g, requested_model: "has space" }, state);
    assert.doesNotMatch(badModel.href, /model=/);
  });

  it("each row gets a traces link and A / B links when the page passes them", () => {
    const d = makeCompare();
    const m = html(<ComparisonTable data={d} links={{ traces: (g) => groupTraceLink(g, state), pairA: () => "/models?a=x", pairB: () => "/models?b=y" }} />);
    assert.equal(count(m, /data-testid="group-traces-link"/g), 2 * d.groups.length); // table + cards
    assert.equal(count(m, /data-testid="group-pair-a"/g), 2 * d.groups.length);
    assert.doesNotMatch(html(<ComparisonTable data={d} />), /group-traces-link/);
  });

  it("chart points carry p50, p95 and p99 with their low-sample flags", () => {
    const p = toGroupPoints(makeCompare().groups)[0]!;
    assert.deepEqual([p.p50, p.p95, p.p99, p.lowSample, p.lowSampleP99], [80, 290, 300, true, true]);
  });
});

describe("session timeline", () => {
  const at = (s: number, ms: number) => ({
    start_time_unix_nano: (BASE_NS + BigInt(s) * 1_000_000_000n).toString(),
    end_time_unix_nano: (BASE_NS + BigInt(s) * 1_000_000_000n + BigInt(ms) * 1_000_000n).toString(),
    duration_ns: (BigInt(ms) * 1_000_000n).toString(),
  });
  const items = makeList([
    makeTrace({ trace_id: tid(3), ...at(95, 700) }),
    makeTrace({ trace_id: tid(2), status: "error", ...at(40, 1400) }),
    makeTrace({ trace_id: tid(1), ...at(0, 900) }),
  ]).items;

  it("orders oldest first and places each trace by its stored start and end", () => {
    const t = buildSessionTimeline(items);
    assert.deepEqual(
      t.rows.map((r) => r.item.trace_id),
      [tid(1), tid(2), tid(3)],
    );
    assert.equal(t.rows[0]!.offsetLabel, "+0 ns");
    assert.equal(t.rows[1]!.offsetLabel, `+${formatDurationNs(40_000_000_000n)}`);
    assert.equal(t.spanLabel, formatDurationNs(95_700_000_000n), "first start to last end: 95 s + 0.7 s");
    assert.equal(t.rows[0]!.left, 0);
    assert.ok(t.rows[2]!.left > 90 && t.rows[2]!.left + t.rows[2]!.width <= 100.0001);
    assert.deepEqual(buildSessionTimeline([]), { rows: [], spanLabel: null });
  });

  const list: ListState = { project: "7", window: { kind: "custom", from: "2026-11-20T00:00:00.000000Z", to: "2026-11-21T00:00:00.000000Z" }, status: "all", provenance: "all", model: null, provider: null, session: H1, limit: 25, cursor: null, page: 1 };
  const row: SessionList["items"][number] = { session: H1, traces: 3, error_traces: 1, first_seen: "2026-11-20T11:00:00.000000Z", last_seen: "2026-11-20T11:01:35.000000Z", attempts: 3, input_tokens: 180, unknown_input_tokens: 0, output_tokens: 36, unknown_output_tokens: 0, known_cost_usd: null, computed_cost_calls: 0, unknown_cost_calls: 3, cost_complete: false };

  it("renders the backend's aggregates, the time-ordered traces and explorer links with the session listing", () => {
    const m = html(<SessionTimeline session={H1} row={row} traces={{ ok: true, data: makeList(items) }} list={list} />);
    assert.match(m, /data-testid="session-detail" data-session="s1_a{32}"/);
    assert.match(m, /A keyed pseudonym, not an anonymous id/);
    assert.equal(count(m, /data-testid="session-timeline-row"/g), 3);
    assert.match(m, /data-testid="session-detail-cost"[^>]*>[^$]*unknown/);
    assert.doesNotMatch(m.slice(m.indexOf("session-detail-cost")), /^\S*\$0/);
    const listParam = encodeURIComponent(traceListSearch(list, false));
    assert.ok(m.includes(`href="/traces/${tid(1)}?project=7&amp;list=${listParam}"`));
    assert.ok(m.includes("session%3Ds1_"));
  });

  it("says when the session is not listed, and when its traces cannot be read", () => {
    assert.match(html(<SessionTimeline session={H1} row={null} traces={{ ok: true, data: makeList([]) }} list={list} />), /data-testid="session-not-listed"[^]*data-state="empty"/);
    assert.match(html(<SessionTimeline session={H1} row={row} traces={{ ok: false, error: { kind: "unreachable" } }} list={list} />), /data-state="unavailable"/);
  });

  it("the session table marks the inspected session and keeps the trace link", () => {
    const data: SessionList = { project: 7, window: { from: "2026-11-20T00:00:00.000000Z", to: "2026-11-21T00:00:00.000000Z" }, pseudonymous: true, items: [row, { ...row, session: H2, traces: 1, error_traces: 0 }], truncated: false, limit: 50 };
    const m = html(<SessionTable data={data} project="7" selected={H1} inspectHref={(s) => `/sessions?project=7&session=${s.session}`} />);
    assert.equal(count(m, /data-testid="session-link"/g), 2);
    assert.equal(count(m, /aria-current="true"/g), 2); // table + card of the selected one
    assert.match(m, /data-testid="session-cards"/);
    assert.match(m, /✕ <\/span>1 failed/);
  });
});

describe("collapsible sidebar", () => {
  it("renders expanded by default with a named, stateful toggle", () => {
    const m = html(
      <ShellFrame sidebar={<aside id="app-sidebar"><SidebarToggle /></aside>}>
        <p>content</p>
      </ShellFrame>,
    );
    assert.match(m, /data-sidebar="expanded"/);
    assert.match(m, /aria-expanded="true" aria-controls="app-sidebar" aria-label="Collapse sidebar"/);
    assert.match(m, /href="#main"[^>]*>Skip to content/);
    assert.match(m, /<main id="main"/);
  });

  it("keeps link labels as visually hidden text and stores nothing", () => {
    const root = path.resolve(__dirname, "..", "..", "..");
    const nav = readFileSync(path.join(root, "components", "side-nav.tsx"), "utf8");
    assert.match(nav, /<span className="sb-label">\{item\.label\}<\/span>/);
    assert.match(nav, /<span aria-hidden="true" className="sb-tip">/);
    const css = readFileSync(path.join(root, "app", "globals.css"), "utf8");
    assert.match(css, /\[data-sidebar="collapsed"\] \.sb-label[^{]*\{[^}]*clip: rect\(0, 0, 0, 0\)/);
    assert.match(css, /a:focus-visible > \.sb-tip/, "the tooltip also shows on keyboard focus");
    const frame = readFileSync(path.join(root, "components", "shell-frame.tsx"), "utf8");
    assert.doesNotMatch(frame.replace(/\/\*[\s\S]*?\*\//g, ""), /localStorage|sessionStorage|document\.cookie/);
  });
});

describe("auto-refresh before hydration", () => {
  it("the interval select is disabled until React owns it (a change would reach no handler)", () => {
    const m = html(<AutoRefresh />);
    assert.match(m, /data-hydrated="false"/);
    assert.match(m, /<select[^>]*disabled=""/);
  });
});
