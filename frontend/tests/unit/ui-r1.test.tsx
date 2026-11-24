/**
 * UI-R1 (overview hierarchy): the KPI cards are grouped into titled sections, M20 has its own
 * card that never invents a value, the cost card shows how complete pricing is without turning
 * unknown cost into $0, the recent traces keep one provenance badge per trace, and the alert
 * rules get a titled card. SYNTHETIC fixtures only.
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { AlertsCard, firingCount } from "../../components/overview/alert-strip";
import { KpiGrid } from "../../components/overview/kpi-grid";
import { CompletenessBar } from "../../components/overview/kpi-visuals";
import { LatestTrace, RECENT_TRACES_LIMIT } from "../../components/overview/latest-trace";
import type { AlertList } from "../../lib/api/alerts";
import type { TimeToFirstChunk } from "../../lib/api/types";
import { RECENT_TRACES_LIMIT as LOADER_LIMIT } from "../../lib/server/overview";
import { makeOverview, NO_TTFC } from "../fixtures/overview";
import { makeList, makeTrace, tid } from "../fixtures/traces";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);
const card = (m: string, id: string) => {
  const start = m.indexOf(`data-metric="${id}"`);
  assert.ok(start >= 0, id);
  const end = m.indexOf("</section>", start);
  return m.slice(start, end);
};

describe("KPI sections", () => {
  it("groups the cards under three h2 headings, in the overview's reading order", () => {
    const m = html(<KpiGrid data={makeOverview()} />);
    const headings = [...m.matchAll(/<h2 id="(kpi-[a-z]+)"[^>]*>([^<]+)<\/h2>/g)].map((x) => [x[1], x[2]]);
    assert.deepEqual(headings, [
      ["kpi-traffic", "Traffic &amp; reliability"],
      ["kpi-latency", "Latency"],
      ["kpi-usage", "Tokens &amp; cost"],
    ]);
    const order = [...m.matchAll(/data-metric="([a-z-]+)"/g)].map((x) => x[1]);
    assert.deepEqual(order, ["traces", "trace-error-rate", "inference-attempts", "inference-error-rate", "inference-latency", "trace-latency", "ttft", "tokens", "known-cost"]);
    assert.equal((m.match(/data-testid="kpi-grid"/g) ?? []).length, 1);
  });
});

describe("TTFT card (M20)", () => {
  const withTtft = (t: TimeToFirstChunk) => {
    const o = makeOverview();
    o.inference.time_to_first_chunk = { ...o.inference.time_to_first_chunk, total: t };
    return card(html(<KpiGrid data={o} />), "ttft");
  };

  it("no streaming attempt: an empty state, never a 0 ms value", () => {
    const c = withTtft(NO_TTFC);
    assert.match(c, /data-testid="ttft-value" data-state="no_data"/);
    assert.match(c, /No streaming/);
    assert.doesNotMatch(c, /0 ms/);
    assert.match(c, /data-testid="ttfc-footer"/);
    assert.match(c, /no streaming attempt in scope/);
  });

  it("streaming attempts without a recorded value say so, never estimate one", () => {
    const c = withTtft({ ...NO_TTFC, reason: "not_recorded", detail: "x", streaming_attempts: 2 });
    assert.match(c, /data-state="no_data"/);
    assert.match(c, /Not recorded/);
    assert.match(c, /not recorded by 2 streaming attempt/);
    assert.match(c, /Never estimated from duration or tokens/);
  });

  it("a recorded value shows p95, p50, n of streaming attempts and the low-sample flag", () => {
    const c = withTtft({ available: true, reason: null, detail: null, streaming_attempts: 4, n: 3, p50_ms: 200, p95_ms: 380, low_sample: { p95: true } });
    assert.match(c, /data-state="known"/);
    assert.match(c, /380/);
    assert.match(c, /p50 200/);
    assert.match(c, /n = 3 of 4 streaming attempt/);
    assert.match(c, /Low sample \(p95\)/);
  });
});

describe("cost completeness", () => {
  it("draws priced vs unknown attempt counts with text labels; unknown is a count, not dollars", () => {
    const m = html(<CompletenessBar priced={4} unknown={2} testId="c" />);
    assert.match(m, /data-priced="4" data-unknown="2"/);
    assert.match(m, /aria-label="4 of 6 attempts priced, 2 with unknown cost"/);
    assert.match(m, /Priced <span[^>]*>4<\/span>/);
    assert.match(m, /Unknown cost <span[^>]*>2<\/span>/);
    assert.doesNotMatch(m, /\$/);
    assert.equal(html(<CompletenessBar priced={0} unknown={0} testId="c" />), "");
  });

  it("is part of the known-cost card next to the incomplete flag", () => {
    const c = card(html(<KpiGrid data={makeOverview()} />), "known-cost");
    assert.match(c, /data-testid="kpi-cost-completeness"/);
    assert.match(c, /Incomplete: 2 of 6 unpriced/);
  });
});

describe("recent traces", () => {
  it("the loader and the component agree on the limit", () => {
    assert.equal(LOADER_LIMIT, String(RECENT_TRACES_LIMIT));
    assert.equal(RECENT_TRACES_LIMIT, 5);
  });

  it("summarises the newest trace and lists all of them, each with its own provenance", () => {
    const items = [makeTrace({ trace_id: tid(1), provenance: "real" }), makeTrace({ trace_id: tid(2), provenance: "synthetic" }), makeTrace({ trace_id: tid(3), provenance: "mixed" })];
    const m = html(<LatestTrace result={{ ok: true, data: makeList(items) }} fetchedAt={new Date("2026-11-01T08:23:46Z")} project="7" filtered={false} />);
    assert.match(m, new RegExp(`data-trace-id="${tid(1)}"`));
    assert.match(m, /data-latest="found"/);
    assert.match(m, /data-testid="recent-traces"/);
    assert.match(m, /<caption[^>]*>The 3 newest traces in this window/);
    for (const n of [1, 2, 3]) assert.ok(m.includes(`href="/traces/${tid(n)}?project=7"`), `row ${n}`);
    const table = m.slice(m.indexOf('data-testid="recent-traces"'));
    assert.match(table, /Real \(asserted\)/);
    assert.match(table, /Synthetic/);
    assert.match(table, /Mixed/);
    assert.ok(m.includes('href="/traces?project=7"'), "link to the full list");
  });

  it("a single trace needs no table", () => {
    const m = html(<LatestTrace result={{ ok: true, data: makeList([makeTrace()]) }} fetchedAt={new Date()} project={null} filtered={false} />);
    assert.doesNotMatch(m, /recent-traces/);
  });
});

describe("alerts card", () => {
  const alerts = (state: "firing" | "ok"): AlertList => ({
    project: 7,
    window: { from: "2026-11-20T00:00:00.000000Z", to: "2026-11-21T00:00:00.000000Z" },
    contract_version: "llminsight.alert_rules.v1",
    delivery: "none",
    rules: [{ id: "err", metric: "inference_error_rate", comparison: ">=", threshold: "0.05", min_n: 20, n: 40, value: state === "firing" ? "0.1" : "0.01", state, lower_bound: false }],
    firing: state === "firing" ? 1 : 0,
  });

  it("titles the rule states and counts firing rules; a failed read shows nothing and counts 0", () => {
    const m = html(<AlertsCard result={{ ok: true, data: alerts("firing") }} />);
    assert.match(m, /<h2 id="alerts-heading"[^>]*>Alerts<\/h2>/);
    assert.match(m, /data-testid="alerts-firing" data-firing="1"/);
    assert.equal(firingCount({ ok: true, data: alerts("firing") }), 1);
    assert.equal(firingCount({ ok: true, data: alerts("ok") }), 0);
    assert.equal(firingCount({ ok: false, error: { kind: "unreachable" } }), 0);
    assert.equal(firingCount(null), 0);
    assert.equal(html(<AlertsCard result={{ ok: false, error: { kind: "unreachable" } }} />), "");
  });
});
