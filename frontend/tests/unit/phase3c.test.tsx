/**
 * Phase 3C: project context, model comparison, R40 trace models, custom ranges and bounded
 * auto-refresh. Pure modules and server-rendered components; SYNTHETIC fixtures only.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, it } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { ComparisonCharts, ComparisonNotices, ComparisonTable } from "../../components/models/model-comparison";
import { ProjectSelector } from "../../components/project-selector";
import { RangeForm } from "../../components/range-form";
import { ErrorState } from "../../components/states";
import { TraceListView, TraceModelsCell } from "../../components/traces/trace-list";
import { modelsCompareDecoder, projectListDecoder } from "../../lib/api/schema";
import { traceListDecoder } from "../../lib/api/trace-schema";
import type { ModelsCompare, ProjectList } from "../../lib/api/types";
import { AUTO_REFRESH_MAX_MS, PENDING_RECHECK_MS, decideRefresh, parseInterval } from "../../lib/auto-refresh";
import { compareNotice, groupLabel, toGroupPoints } from "../../lib/model-groups";
import { compareParams, modelHref, modelStateOf, parseModelQuery, withModelFilter } from "../../lib/model-query";
import { hiddenFields, isProjectId, parseProjectParam, projectHref, projectLinkValue } from "../../lib/project";
import type { ProjectContext } from "../../lib/server/context";
import { backToListHref, listStateFromBackParam, listStateOf, parseTraceListQuery, restartFields, traceDetailHref, traceListHref, withFilter } from "../../lib/trace-query";
import { decode } from "../../lib/validate";
import { json, makeCompare } from "../fixtures/overview";
import { makeList, makeTrace, toWire } from "../fixtures/traces";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);
const NOW = new Date("2026-10-25T14:37:12.345Z");
const XSS = `<script>window.__llmiXss=1</script><img src=x onerror=alert(1)>`;
const assertEscaped = (m: string) => {
  assert.doesNotMatch(m, /<script>/);
  assert.doesNotMatch(m, /<img /);
};

// ---------------------------------------------------------------- project parameter
describe("project parameter", () => {
  it("accepts positive BIGINT ids only", () => {
    for (const ok of ["1", "42", "9223372036854775807"]) assert.equal(isProjectId(ok), true, ok);
    for (const bad of ["0", "01", "-1", "1e3", "1.0", "9223372036854775808", "", " 1", "1 ", "１", "0x1"]) assert.equal(isProjectId(bad), false, bad);
  });
  it("absent → default, valid → explicit, invalid or repeated → invalid; links keep explicit only", () => {
    assert.deepEqual(parseProjectParam(undefined), { kind: "default" });
    assert.deepEqual(parseProjectParam("7"), { kind: "explicit", id: "7" });
    assert.deepEqual(parseProjectParam(["7", "8"]), { kind: "invalid" });
    assert.deepEqual(parseProjectParam("7;DROP"), { kind: "invalid" });
    assert.equal(projectLinkValue({ kind: "explicit", id: "7" }), "7");
    assert.equal(projectLinkValue({ kind: "default" }), null);
    assert.equal(projectLinkValue({ kind: "invalid" }), null);
    assert.equal(projectHref("/models", "7"), "/models?project=7");
    assert.equal(projectHref("/models", null), "/models");
  });
  it("hidden form fields carry the canonical query minus the fields a form sets", () => {
    assert.deepEqual(hiddenFields("project=2&range=7d&provenance=mixed", ["project"]), [
      ["range", "7d"],
      ["provenance", "mixed"],
    ]);
  });
});

// ---------------------------------------------------------------- trace links with a project
describe("trace links keep the project and never carry it inside list", () => {
  const state = listStateOf(parseTraceListQuery({ project: "2", range: "7d", status: "error" }));
  it("list and detail links", () => {
    assert.equal(traceListHref(state), "/traces?project=2&range=7d&status=error");
    assert.equal(traceDetailHref("56d5c0b2144b3ec23fa58c9561fbbff0", state), "/traces/56d5c0b2144b3ec23fa58c9561fbbff0?project=2&list=range%3D7d%26status%3Derror");
  });
  it("the back link uses the detail page's own project, whatever list says", () => {
    assert.equal(backToListHref("project=9&range=7d", "2"), "/traces?project=2&range=7d");
    assert.equal(backToListHref("project=9&range=7d", null), "/traces?range=7d");
    assert.equal(listStateFromBackParam(undefined, "3").project, "3");
  });
  it("an invalid project never becomes a link", () => {
    assert.equal(listStateOf(parseTraceListQuery({ project: "<script>" })).project, null);
  });
  it("changing a filter keeps the project and restarts paging", () => {
    const paged = listStateOf(parseTraceListQuery({ project: "2", from: "2026-10-24T09:00:00.000000Z", to: "2026-10-25T09:00:00.000000Z", cursor: "C1", page: "3" }));
    const changed = withFilter(paged, { status: "ok" });
    assert.equal(changed.project, "2");
    assert.equal(changed.cursor, null);
    assert.equal(changed.page, 1);
  });
  it("forms that leave the listing (project switch, custom range) never carry a cursor or page", () => {
    const paged = listStateOf(parseTraceListQuery({ project: "2", from: "2026-10-24T09:00:00.000000Z", to: "2026-10-25T09:00:00.000000Z", status: "error", cursor: "C1", page: "3" }));
    assert.deepEqual(restartFields(paged, ["project"]), [
      ["from", "2026-10-24T09:00:00.000000Z"],
      ["to", "2026-10-25T09:00:00.000000Z"],
      ["status", "error"],
    ]);
    assert.deepEqual(restartFields(paged, ["range", "from", "to"]), [
      ["project", "2"],
      ["status", "error"],
    ]);
  });
  it("custom form fields set a frozen window for the list too", () => {
    const q = parseTraceListQuery({ from_utc: "2026-10-24T09:00", to_utc: "2026-10-25T09:00" });
    assert.deepEqual(q.window, { kind: "custom", from: "2026-10-24T09:00:00Z", to: "2026-10-25T09:00:00Z" });
  });
});

// ---------------------------------------------------------------- model query
describe("model comparison query", () => {
  it("defaults, supported values and backend parameters", () => {
    const d = parseModelQuery({});
    assert.deepEqual(modelStateOf(d), { project: null, window: { kind: "preset", range: "24h" }, provenance: "all", status: "all", limit: 50 });
    assert.deepEqual(compareParams(d, NOW), { from: "2026-10-24T15:00:00.000Z", to: "2026-10-25T15:00:00.000Z", limit: "50" });
    const q = parseModelQuery({ project: "4", range: "7d", provenance: "synthetic", status: "error", limit: "200" });
    assert.deepEqual(q.ignored, []);
    assert.deepEqual(compareParams(q, NOW), { from: "2026-10-19T00:00:00.000Z", to: "2026-10-26T00:00:00.000Z", limit: "200", provenance: "synthetic", status: "error" });
    assert.equal(modelHref(modelStateOf(q)), "/models?project=4&range=7d&provenance=synthetic&status=error&limit=200");
  });
  it("ignores hostile or unsupported values and never forwards them", () => {
    const q = parseModelQuery({ provenance: "real'--", status: "<script>", limit: "201", model: "x", range: "1y" });
    assert.deepEqual(q.ignored.sort(), ["limit", "provenance", "range", "status"]);
    assert.deepEqual(compareParams(q, NOW), { from: "2026-10-24T15:00:00.000Z", to: "2026-10-25T15:00:00.000Z", limit: "50" });
  });
  it("filter links keep the project", () => {
    const s = modelStateOf(parseModelQuery({ project: "4" }));
    assert.equal(modelHref(withModelFilter(s, { status: "error" })), "/models?project=4&status=error");
  });
});

// ---------------------------------------------------------------- decoders
describe("decoders for the Phase 3C additions", () => {
  it("project list: bounded, ids, names as text", () => {
    const ok: ProjectList = { items: [{ id: 1, name: XSS, created_at: "2026-10-24T10:00:00.000000Z" }], truncated: false, limit: 200 };
    assert.equal(decode(projectListDecoder, ok).ok, true);
    for (const bad of [
      { ...ok, items: [{ ...ok.items[0], id: 0 }] },
      { ...ok, items: [{ ...ok.items[0], id: "1" }] },
      { ...ok, items: [{ ...ok.items[0], name: "x".repeat(201) }] },
      { ...ok, items: Array.from({ length: 201 }, () => ok.items[0]) },
      { ...ok, truncated: "no" },
    ]) {
      assert.equal(decode(projectListDecoder, bad).ok, false);
    }
  });

  it("trace usage.models: several combinations; inconsistent summaries are rejected", () => {
    const multi = makeTrace({
      usage: {
        ...makeTrace().usage,
        inference_calls: 3,
        models: {
          items: [
            { provider: "openai", requested_model: "gpt-5.4-mini", response_model: "gpt-5.4-mini-2026-03-17", calls: 2 },
            { provider: "synthetic.other", requested_model: null, response_model: null, calls: 1 },
          ],
          distinct: 2,
          truncated: false,
        },
      },
    });
    assert.equal(decode(traceListDecoder, toWire(makeList([multi]))).ok, true);
    const mutate = (f: (m: Record<string, unknown>) => void) => {
      const w = json(toWire(makeList([multi]))) as { items: { usage: { models: Record<string, unknown> } }[] };
      f(w.items[0]!.usage.models);
      return decode(traceListDecoder, w).ok;
    };
    assert.equal(mutate((m) => (m.truncated = true)), false, "truncated without hidden combinations");
    assert.equal(mutate((m) => (m.distinct = 1)), false, "fewer distinct than listed");
    assert.equal(mutate((m) => ((m.items as { calls: number }[])[0]!.calls = 5)), false, "more calls than inference_calls");
    assert.equal(mutate((m) => ((m.items as { calls: number }[])[0]!.calls = 1)), false, "complete listing must account for every call");
    assert.equal(mutate((m) => ((m.items as { calls: number }[])[0]!.calls = 0)), false, "zero-call combination");
    assert.equal(mutate((m) => delete m.items), false);
    assert.equal(
      mutate((m) => {
        m.distinct = 12;
        m.truncated = true;
      }),
      true,
      "a truncated listing may account for fewer calls",
    );
  });

  it("compare groups: tokens and usage source required, group counts must add up", () => {
    assert.equal(decode(modelsCompareDecoder, json(makeCompare())).ok, true);
    const mutate = (f: (g: Record<string, unknown>) => void) => {
      const w = json(makeCompare()) as { groups: Record<string, unknown>[] };
      f(w.groups[0]!);
      return decode(modelsCompareDecoder, w);
    };
    const cases: [string, (g: Record<string, unknown>) => void][] = [
      ["no tokens", (g) => delete g.tokens],
      ["no usage source", (g) => delete g.usage_source],
      ["failed + succeeded ≠ attempts", (g) => ((g.errors as { failed: number }).failed = 3)],
      ["computed + unknown ≠ attempts", (g) => ((g.cost as { computed_cost_calls: number }).computed_cost_calls = 1)],
      ["complete with unknown calls", (g) => ((g.cost as { cost_complete: boolean }).cost_complete = false)],
      ["unknown cost stored as zero", (g) => ((g.cost as { known_cost_usd: string | null }).known_cost_usd = null)],
      ["token n ≠ attempts", (g) => ((g.tokens as { n: number }).n = 9)],
      ["float tokens", (g) => ((g.tokens as { input_tokens: number }).input_tokens = 1.5)],
    ];
    for (const [name, f] of cases) assert.equal(mutate(f).ok, false, name);
  });
});

// ---------------------------------------------------------------- auto-refresh
describe("auto-refresh decisions", () => {
  const base = { intervalMs: 30_000, startedAt: 0, lastAt: 0, now: 30_000, pending: false, hidden: false };
  it("refreshes once per interval, never while a request is pending", () => {
    assert.deepEqual(decideRefresh(base), { action: "refresh" });
    assert.deepEqual(decideRefresh({ ...base, now: 10_000 }), { action: "wait", delayMs: 20_000 });
    assert.deepEqual(decideRefresh({ ...base, pending: true }), { action: "wait", delayMs: PENDING_RECHECK_MS });
  });
  it("postpones while hidden and stops after the bound", () => {
    assert.deepEqual(decideRefresh({ ...base, hidden: true }), { action: "wait", delayMs: 30_000 });
    assert.deepEqual(decideRefresh({ ...base, now: AUTO_REFRESH_MAX_MS }), { action: "stop" });
    assert.deepEqual(decideRefresh({ ...base, now: AUTO_REFRESH_MAX_MS, pending: true }), { action: "stop" });
  });
  it("accepts only the offered intervals", () => {
    assert.equal(parseInterval("30"), 30);
    assert.equal(parseInterval("5"), null);
    assert.equal(parseInterval("off"), null);
  });
});

// ---------------------------------------------------------------- components
function compareWith(patch: (c: ModelsCompare) => void): ModelsCompare {
  const c = makeCompare();
  patch(c);
  return c;
}

describe("model comparison components", () => {
  it("shows exact decimals, unknown (never $0), incomplete cost and estimated tokens", () => {
    const m = html(<ComparisonTable data={makeCompare()} />);
    assert.match(m, /data-exact="0.0012345000"/);
    assert.match(m, /data-cost-state="complete"/);
    assert.match(m, /data-cost-state="unknown"/);
    assert.doesNotMatch(m, /\$0(\.0+)?(?![\d.])/, "no unknown cost shown as $0");
    assert.match(m, /0 priced · 2 unknown/);
    assert.match(m, /\(2 no price\)/);
    assert.match(m, /1 estimated/);
    assert.match(m, /unknown: 2 in \/ 0 out of 2/);
    assert.match(m, /subsets not reported/);
    const incomplete = compareWith((c) => {
      const g = c.groups[0]!;
      g.cost = { ...g.cost, computed_cost_calls: 3, unknown_cost_calls: 1, unknown_cost_by_reason: { price_missing: 0, usage_missing: 1, usage_inconsistent: 0 }, cost_complete: false };
    });
    const mi = html(<ComparisonTable data={incomplete} />);
    assert.match(mi, /data-cost-state="incomplete"/);
    assert.match(mi, /Incomplete/);
  });

  it("states that groups are not ranked, flags low samples and labels synthetic data", () => {
    const n = html(<ComparisonNotices data={makeCompare()} />);
    assert.match(n, /Groups are not ranked/);
    assert.match(n, /no group is presented as the best/);
    assert.match(n, /fewer than 20 samples/);
    assert.match(n, /SYNTHETIC data only/);
    assert.doesNotMatch(n, /\bbest model\b|\bwinner\b|\brecommended\b/i);
    const mixed = compareWith((c) => (c.groups[0]!.by_provenance = { real: 1, synthetic: 3, unknown: 0 }));
    assert.match(html(<ComparisonNotices data={mixed} />), /Contains SYNTHETIC data/);
    assert.deepEqual(compareNotice(mixed).present, ["real", "synthetic"]);
  });

  it("keeps the backend group order in charts and labels missing models", () => {
    const pts = toGroupPoints(makeCompare().groups);
    assert.deepEqual(
      pts.map((p) => p.attempts),
      [4, 2],
    );
    assert.equal(groupLabel(makeCompare().groups[1]!), "mock · mock-model → not reported");
    const m = html(<ComparisonCharts data={makeCompare()} />);
    assert.match(m, /Show data table/);
  });

  it("escapes hostile group labels", () => {
    const hostile = compareWith((c) => {
      c.groups[0]!.provider = XSS;
      c.groups[0]!.prompt_version = XSS;
    });
    assertEscaped(html(<ComparisonTable data={hostile} />));
    assertEscaped(html(<ComparisonCharts data={hostile} />));
  });
});

describe("trace list models column (R40)", () => {
  it("lists every combination with its calls, marks hidden ones, and never picks one", () => {
    const models = {
      items: [
        { provider: "openai", requested_model: "gpt-5.4-mini", response_model: "gpt-5.4-mini-2026-03-17", calls: 3 },
        { provider: "synthetic.other", requested_model: "m-x", response_model: "m-x", calls: 1 },
        { provider: "openai", requested_model: "gpt-5-mini", response_model: null, calls: 1 },
        { provider: "openai", requested_model: "gpt-4.1-mini", response_model: "gpt-4.1-mini", calls: 1 },
      ],
      distinct: 6,
      truncated: true,
    };
    const m = html(<TraceModelsCell models={models} calls={8} />);
    assert.equal((m.match(/data-testid="trace-model"/g) ?? []).length, 3);
    assert.match(m, /data-distinct="6"/);
    assert.match(m, /\+3 more combinations/);
    assert.match(m, /synthetic\.other/);
    assert.match(m, /×3/);
    assert.match(m, /unknown/, "a missing response model is unknown, not inferred");
    assert.match(html(<TraceModelsCell models={{ items: [], distinct: 0, truncated: false }} calls={0} />), /none/);
  });
  it("renders in the table and the mobile cards, escaped", () => {
    const t = makeTrace({ usage: { ...makeTrace().usage, models: { items: [{ provider: XSS, requested_model: XSS, response_model: null, calls: 1 }], distinct: 1, truncated: false } } });
    const m = html(<TraceListView data={makeList([t])} state={listStateOf(parseTraceListQuery({}))} />);
    assert.equal((m.match(/data-testid="trace-models"/g) ?? []).length, 2);
    assertEscaped(m);
  });
});

function ctx(projects: ProjectContext["projects"], projectId = "2", explicit = true): ProjectContext {
  return { projectId, explicit, projects };
}

describe("project selector", () => {
  const listing: ProjectList = {
    items: [
      { id: 1, name: "phase3a-demo", created_at: "2026-10-24T10:00:00.000000Z" },
      { id: 2, name: XSS, created_at: "2026-10-24T11:00:00.000000Z" },
    ],
    truncated: false,
    limit: 200,
  };
  it("is a GET form with the current project selected, hostile names escaped, and safe hidden state only", () => {
    const m = html(<ProjectSelector context={ctx({ ok: true, data: listing })} action="/traces" hidden={[["range", "7d"]]} />);
    const form = /<form [^>]*data-testid="project-selector"[^>]*>/.exec(m)?.[0] ?? "";
    assert.match(form, /method="get"/);
    assert.match(form, /action="\/traces"/);
    assert.match(m, /<input type="hidden" name="range" value="7d"\/>/);
    assert.match(m, /<select name="project"/);
    assert.match(m, /<option value="2" selected="">/);
    assert.match(m, /data-project-id="2"/);
    assertEscaped(m);
    assert.doesNotMatch(m, /name="cursor"|name="page"|name="span"/);
  });
  it("keeps an unlisted current project selectable and says so; a failed listing degrades only the selector", () => {
    assert.match(html(<ProjectSelector context={ctx({ ok: true, data: listing }, "77")} action="/" hidden={[]} />), /#77 \(not in the listing\)/);
    const failed = html(<ProjectSelector context={ctx({ ok: false, error: { kind: "unreachable" } }, "1", false)} action="/" hidden={[]} />);
    assert.match(failed, /data-testid="project-list-error" data-error-kind="unreachable"/);
    assert.match(failed, /Project list unavailable \(backend unreachable\)/);
    assert.doesNotMatch(failed, /role="alert"/, "an outage is reported once, by the page");
    assert.match(failed, /configured default/);
  });
  it("renders nothing without a resolved project", () => {
    assert.equal(html(<ProjectSelector context={{ projectId: null, explicit: false, projects: null }} action="/" hidden={[]} />), "");
  });
  it("invalid project copy says nothing was sent and nothing was substituted", () => {
    const m = html(<ErrorState error={{ kind: "invalid_project" }} resource="x" />);
    assert.match(m, /Not a valid project ID/);
    assert.match(m, /was not sent to the backend/);
    assert.match(m, /no other project was substituted/);
  });
});

describe("custom range form", () => {
  it("uses UTC-named datetime fields, keeps hidden state and prefills the shown window", () => {
    const m = html(<RangeForm action="/models" hidden={[["project", "2"]]} window={{ kind: "preset", range: "24h" }} shown={{ from: "2026-10-24T15:00:00.000000Z", to: "2026-10-25T15:00:00.000000Z" }} />);
    assert.match(m, /name="from_utc"/);
    assert.match(m, /name="to_utc"/);
    assert.match(m, /value="2026-10-24T15:00"/);
    assert.match(m, /<input type="hidden" name="project" value="2"\/>/);
    assert.match(m, /UTC/);
  });
});

describe("no new backend surface in the dashboard", () => {
  it("the Phase 3C pages add no route handler or Server Function", () => {
    const root = path.resolve(__dirname, "..", "..", "..");
    for (const f of ["app/models/page.tsx", "app/page.tsx", "app/traces/page.tsx", "app/traces/[traceId]/page.tsx", "components/auto-refresh.tsx", "components/project-selector.tsx"]) {
      const src = readFileSync(path.join(root, f), "utf8");
      assert.doesNotMatch(src, /["']use server["']/, f);
      assert.doesNotMatch(src, /dangerouslySetInnerHTML/, f);
      assert.doesNotMatch(src, /localStorage|sessionStorage|document\.cookie/, f);
    }
  });
});
