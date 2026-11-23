/**
 * Phase 10F (ADR-59): alert rule states on the overview. The decoder ties each state to its
 * value, threshold and sample size; the strip names firing rules and says that nothing is sent.
 * SYNTHETIC values only.
 */
/* eslint-disable @typescript-eslint/no-explicit-any -- wire-form mutations in decoder tests */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { AlertStrip } from "../../components/overview/alert-strip";
import { alertListDecoder, type AlertList } from "../../lib/api/alerts";
import { decode } from "../../lib/validate";

function list(): AlertList {
  return {
    project: 7,
    window: { from: "2026-11-20T00:00:00.000000Z", to: "2026-11-21T00:00:00.000000Z" },
    contract_version: "llminsight.alert_rules.v1",
    delivery: "none",
    rules: [
      { id: "err", metric: "inference_error_rate", comparison: ">=", threshold: "0.05", min_n: 20, n: 40, value: "0.1", state: "firing", lower_bound: false },
      { id: "lat", metric: "inference_latency_p95_ms", comparison: ">=", threshold: "10000", min_n: 20, n: 40, value: "900.5", state: "ok", lower_bound: false },
      { id: "ttfc", metric: "time_to_first_chunk_p95_ms", comparison: ">=", threshold: "2000", min_n: 20, n: 0, value: null, state: "insufficient_data", lower_bound: false },
      { id: "cost", metric: "known_cost_usd", comparison: ">=", threshold: "1.00", min_n: 1, n: 10, value: "0.9", state: "ok", lower_bound: true },
    ],
    firing: 1,
  };
}
const wire = () => structuredClone(list()) as any;

describe("Phase 10F decoder", () => {
  it("accepts consistent rule states", () => {
    const r = decode(alertListDecoder, wire());
    assert.equal(r.ok, true, r.ok ? "" : r.path);
  });

  const broken: [string, (d: any) => void][] = [
    ["a firing rule below its threshold", (d) => (d.rules[0].value = "0.01")],
    ["an ok rule at its threshold", (d) => (d.rules[1].value = "10000")],
    ["a decided state without enough data", (d) => (d.rules[1].n = 5)],
    ["a decided state without a value", (d) => (d.rules[1].value = null)],
    ["a firing lower bound", (d) => (d.rules[0].lower_bound = true)],
    ["a wrong firing count", (d) => (d.firing = 2)],
    ["duplicate rule ids", (d) => (d.rules[1].id = "err")],
    ["a delivery channel", (d) => (d.delivery = "webhook")],
    ["an unknown metric", (d) => (d.rules[0].metric = "cpu")],
    ["a float threshold", (d) => (d.rules[0].threshold = 0.05)],
  ];
  for (const [name, mutate] of broken) {
    it(`refuses ${name}`, () => {
      const d = wire();
      mutate(d);
      assert.equal(decode(alertListDecoder, d).ok, false);
    });
  }
});

describe("Phase 10F presentation", () => {
  it("names the firing rules and says no notification is sent", () => {
    const m = renderToStaticMarkup(<AlertStrip result={{ ok: true, data: list() }} />);
    assert.match(m, /1 alert rule\(s\) firing in this window/);
    assert.match(m, /Inference error rate 0\.1 ≥ 0\.05/);
    assert.match(m, /no notification is sent/);
    assert.doesNotMatch(m, /lat<\/code>/); // an ok rule is not listed as firing
  });

  it("stays quiet when nothing fires and mentions data gaps and lower bounds", () => {
    const d = list();
    d.rules[0] = { ...d.rules[0]!, value: "0.01", state: "ok" };
    d.firing = 0;
    const m = renderToStaticMarkup(<AlertStrip result={{ ok: true, data: d }} />);
    assert.match(m, /Alert rules: none firing \(3 checked, 1 without enough data\); a cost rule is checked on a lower bound/);
    assert.match(m, /data-firing="0"/);
  });

  it("renders nothing on a failed or missing read", () => {
    assert.equal(renderToStaticMarkup(<AlertStrip result={null} />), "");
    assert.equal(renderToStaticMarkup(<AlertStrip result={{ ok: false, error: { kind: "server_error", status: 500 } }} />), "");
  });
});
