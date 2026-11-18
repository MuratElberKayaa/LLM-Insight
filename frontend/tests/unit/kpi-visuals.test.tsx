/**
 * UI-3 (KPI tiles): sparklines, the per-provenance error-rate split and the error-type chips only
 * re-draw backend values; empty buckets never look like small values; nothing is rescaled.
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { KpiGrid } from "../../components/overview/kpi-grid";
import { ErrorTypeChips, RateSplit, Sparkline } from "../../components/overview/kpi-visuals";
import { makeEmptyOverview, makeOverview } from "../fixtures/overview";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);
// Bar heights only (each bar is a <span title=…>; the container div has its own height).
const heights = (m: string) => [...m.matchAll(/<span title="[^"]*"[^>]*style="height:(\d+)px"/g)].map((x) => Number(x[1]));

describe("Sparkline", () => {
  const points = [
    { label: "2026-11-11 (UTC day)", value: 0 },
    { label: "2026-11-12 (UTC day)", value: 84 },
    { label: "2026-11-13 (UTC day)", value: 12 },
  ];

  it("draws one bar per bucket on a 0..max scale, with a named peak", () => {
    const m = html(<Sparkline points={points} noun="Traces" testId="s" />);
    assert.match(m, /data-buckets="3" data-max="84"/);
    assert.match(m, /role="img" aria-label="Traces per bucket: 2 of 3 buckets with data; peak 84 in 2026-11-12 \(UTC day\)"/);
    assert.deepEqual(heights(m), [2, 36, 5]);
  });

  it("an empty bucket is a grid-colored stub, not a small value", () => {
    const m = html(<Sparkline points={points} noun="Traces" testId="s" />);
    const first = /<span title="2026-11-11[^"]*"[^>]*class="([^"]*)"/.exec(m)?.[1] ?? "";
    assert.match(first, /bg-grid/);
    assert.doesNotMatch(first, /bg-series-1/);
  });

  it("states an all-empty window and renders nothing without buckets", () => {
    const m = html(<Sparkline points={points.map((p) => ({ ...p, value: 0 }))} noun="Traces" testId="s" />);
    assert.match(m, /aria-label="Traces per bucket: none in 3 buckets"/);
    assert.equal(html(<Sparkline points={[]} noun="Traces" testId="s" />), "");
  });
});

describe("RateSplit", () => {
  it("uses an absolute 0–100% scale and leaves out empty provenances", () => {
    const m = html(
      <RateSplit
        testId="r"
        unit="traces"
        rows={[
          { provenance: "real", n: 99, rate: 0.030303 },
          { provenance: "synthetic", n: 28, rate: 0.392857 },
          { provenance: "mixed", n: 0, rate: null },
        ]}
      />,
    );
    assert.match(m, /Real \(asserted\)/);
    assert.match(m, /Synthetic/);
    assert.doesNotMatch(m, /Mixed/);
    assert.match(m, /width:3\.03%/);
    assert.match(m, /width:39\.29%/);
    assert.match(m, /3.03%/);
    assert.match(m, /39.29%/);
  });

  it("is omitted when only one provenance has data (the headline already is that rate)", () => {
    assert.equal(html(<RateSplit testId="r" unit="traces" rows={[{ provenance: "synthetic", n: 4, rate: 0.25 }, { provenance: "real", n: 0, rate: null }]} />), "");
  });
});

describe("ErrorTypeChips", () => {
  it("shows the first three in backend order, then a count of the rest", () => {
    const m = html(
      <ErrorTypeChips
        truncated={false}
        items={[
          { error_type: "429", count: 5 },
          { error_type: "500", count: 5 },
          { error_type: null, count: 4 },
          { error_type: "504", count: 3 },
        ]}
      />,
    );
    assert.ok(m.indexOf("429") < m.indexOf("500"));
    assert.match(m, /not reported/);
    assert.doesNotMatch(m, /504/);
    assert.match(m, /\+1 more/);
  });
});

describe("KpiGrid visuals", () => {
  it("adds trend sparklines from the backend buckets and keeps the headline values", () => {
    const m = html(<KpiGrid data={makeOverview()} />);
    assert.match(m, /data-testid="kpi-traces-spark" data-buckets="2" data-max="3"/);
    assert.match(m, /data-testid="kpi-attempts-spark" data-buckets="2" data-max="4"/);
    assert.match(m, /data-testid="kpi-error-types"/);
    assert.match(m, /RateLimitError/);
    // The fixture is synthetic only: no per-provenance split.
    assert.doesNotMatch(m, /kpi-trace-error-split/);
  });

  it("draws nothing for an empty window", () => {
    const m = html(<KpiGrid data={makeEmptyOverview()} />);
    assert.doesNotMatch(m, /kpi-traces-spark|kpi-attempts-spark|kpi-error-types|kpi-trace-error-split/);
  });
});
