import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { CostDetails, ErrorTypes, ProvenanceTable } from "../../components/overview/breakdowns";
import { DataNoticeBanner } from "../../components/overview/data-notice";
import { KpiGrid } from "../../components/overview/kpi-grid";
import { ModelGroups } from "../../components/overview/model-groups";
import { EmptyState, ErrorState } from "../../components/states";
import type { ApiFailureKind } from "../../lib/api/result";
import { toTrendPoints } from "../../lib/trend";
import { makeCompare, makeEmptyOverview, makeOverview } from "../fixtures/overview";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);

/** Text content of the element carrying data-testid, tags stripped. */
function testIdText(markup: string, id: string): string {
  const m = new RegExp(`data-testid="${id}"[^>]*>(.*?)</p>`, "s").exec(markup);
  assert.ok(m, `missing ${id}`);
  return (m[1] ?? "").replace(/<[^>]+>/g, "").replace(/&#x27;/g, "'").trim();
}

function testIdState(markup: string, id: string): string | undefined {
  return new RegExp(`data-testid="${id}" data-state="([a-z_]+)"`).exec(markup)?.[1];
}

describe("KpiGrid", () => {
  it("renders the backend values without recomputing them", () => {
    const m = html(<KpiGrid data={makeOverview()} />);
    assert.equal(testIdText(m, "traces-value"), "4");
    assert.equal(testIdText(m, "inference-attempts-value"), "6");
    assert.equal(testIdText(m, "inference-error-rate-value"), "16.67%");
    assert.equal(testIdText(m, "trace-error-rate-value"), "25%");
    assert.equal(testIdText(m, "inference-latency-value"), "300.5 ms");
    assert.equal(testIdText(m, "known-cost-value"), "$0.0012345");
    assert.match(m, /Incomplete: 2 of 6 unpriced/);
    assert.match(m, /Logical calls: 5/);
    assert.match(m, /Low sample \(p95\)/);
  });

  it("shows 'No data' (never 0 or $0.00) for an empty window", () => {
    const m = html(<KpiGrid data={makeEmptyOverview()} />);
    for (const id of ["trace-error-rate", "inference-error-rate", "inference-latency", "trace-latency", "tokens", "known-cost"]) {
      assert.equal(testIdState(m, `${id}-value`), "no_data", id);
    }
    assert.doesNotMatch(m, /\$0\.00/);
    assert.doesNotMatch(m, /0%/);
  });

  it("distinguishes unknown cost from a computed zero", () => {
    const unknown = makeOverview();
    unknown.inference.cost.total = { ...unknown.inference.cost.total, known_cost_usd: null, computed_cost_calls: 0, unknown_cost_calls: 6, cost_complete: false, known_cost_per_call_usd: null };
    const mu = html(<KpiGrid data={unknown} />);
    assert.equal(testIdState(mu, "known-cost-value"), "unknown");
    assert.equal(testIdText(mu, "known-cost-value"), "Unknown: no call priced");
    assert.doesNotMatch(mu, /\$0\.00/);

    const zero = makeOverview();
    zero.inference.cost.total = { ...zero.inference.cost.total, known_cost_usd: "0.0000000000", computed_cost_calls: 6, unknown_cost_calls: 0, cost_complete: true, known_cost_per_call_usd: "0.0000000000" };
    const mz = html(<KpiGrid data={zero} />);
    assert.equal(testIdState(mz, "known-cost-value"), "known");
    assert.equal(testIdText(mz, "known-cost-value"), "$0.00");
    assert.doesNotMatch(mz, /Incomplete/);
  });

  it("keeps unknown token sums as 'unknown'", () => {
    const o = makeOverview();
    o.inference.tokens.total = { ...o.inference.tokens.total, input_tokens: null, calls_with_unknown_input_tokens: 6 };
    const m = html(<KpiGrid data={o} />);
    assert.match(testIdText(m, "tokens-value"), /^unknown/);
  });
});

describe("Provenance (T48)", () => {
  it("identifies synthetic-only windows with a SYNTHETIC banner", () => {
    const m = html(<DataNoticeBanner data={makeOverview()} />);
    assert.match(m, /data-notice="synthetic_only"/);
    assert.match(m, />Synthetic</);
    assert.match(m, /Synthetic telemetry only/);
    assert.match(m, /not verified by the server/);
    assert.match(m, /all provenance/);
  });

  it("renders no banner for an empty window (the empty state explains it)", () => {
    assert.equal(html(<DataNoticeBanner data={makeEmptyOverview()} />), "");
  });

  it("never shows mixed traces as inference attempts and takes totals from the backend", () => {
    const o = makeOverview();
    o.traces.count = { total: 5, by_provenance: { real: 1, synthetic: 2, mixed: 1, unknown: 1 } };
    const m = html(<ProvenanceTable data={o} />);
    assert.match(m, /data-provenance="mixed"/);
    assert.match(m, /n\/a/);
    assert.match(m, /All provenance/);
    // The total row shows the backend's total (5), not a frontend sum.
    assert.match(m, /All provenance<\/th><td[^>]*>5<\/td>/);
  });
});

describe("XSS: telemetry labels render as text (T41)", () => {
  const payload = `<script>alert("x")</script><img src=x onerror=alert(1)>`;
  it("escapes error types", () => {
    const o = makeOverview();
    o.inference.errors.by_error_type = [{ error_type: payload, count: 1 }];
    const m = html(<ErrorTypes data={o} />);
    assert.doesNotMatch(m, /<script>/);
    assert.doesNotMatch(m, /<img/);
    assert.match(m, /&lt;script&gt;/);
  });
  it("escapes model, provider and prompt-version labels", () => {
    const c = makeCompare();
    c.groups[0] = { ...c.groups[0]!, provider: payload, requested_model: payload, response_model: payload, prompt_version: payload };
    const m = html(<ModelGroups result={{ ok: true, data: c }} />);
    assert.doesNotMatch(m, /<script>/);
    assert.doesNotMatch(m, /<img/);
  });
  it("escapes price-version labels", () => {
    const o = makeOverview();
    o.inference.price_versions.items = [{ provider: payload, model: payload, price_version: payload, calls: 1 }];
    assert.doesNotMatch(html(<CostDetails data={o} />), /<script>|<img/);
  });
});

describe("ModelGroups", () => {
  it("shows unknown cost as unknown and null labels as not reported", () => {
    const m = html(<ModelGroups result={{ ok: true, data: makeCompare() }} />);
    assert.match(m, /unknown<span class="sr-only"> \(no priced attempt\)/);
    assert.match(m, /not reported/);
    assert.match(m, /\$0\.0012345/);
  });
  it("degrades to an error panel when the compare request failed", () => {
    const m = html(<ModelGroups result={{ ok: false, error: { kind: "server_error", status: 500 } }} />);
    assert.match(m, /data-error-kind="server_error"/);
    assert.match(m, /role="alert"/);
  });
});

describe("States", () => {
  it("renders an accessible empty state", () => {
    const m = html(<EmptyState title="No telemetry in this window">Run the demo.</EmptyState>);
    assert.match(m, /role="status"/);
    assert.match(m, /No telemetry in this window/);
  });
  it("has fixed copy for every failure kind", () => {
    const kinds: ApiFailureKind[] = ["config", "unreachable", "timeout", "unauthorized", "not_found", "invalid_request", "rate_limited", "storage_unavailable", "server_error", "unexpected_status", "malformed"];
    for (const kind of kinds) {
      const m = html(<ErrorState error={{ kind }} resource="the overview metrics" />);
      assert.match(m, new RegExp(`data-error-kind="${kind}"`));
      assert.match(m, /role="alert"/);
    }
    assert.match(html(<ErrorState error={{ kind: "unreachable" }} resource="x" />), /Backend unreachable/);
    assert.match(html(<ErrorState error={{ kind: "unauthorized", status: 401 }} resource="x" />), /never shown/);
  });
});

describe("toTrendPoints", () => {
  it("keeps a bucket without computed cost as null (no bar), not zero", () => {
    const points = toTrendPoints(makeOverview().trend);
    assert.equal(points[0]?.knownCost, null);
    assert.equal(points[0]?.knownCostText, null);
    assert.equal(points[0]?.unknownCostCalls, 2);
    assert.equal(points[1]?.knownCostText, "$0.0012345");
    assert.equal(points[1]?.succeededAttempts, 3);
    assert.equal(points[1]?.failedAttempts, 1);
    assert.equal(points[1]?.label, "2026-10-25 14:00–15:00 UTC");
  });
});
