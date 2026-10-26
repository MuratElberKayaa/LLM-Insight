/**
 * Phase 4D: the experiment pages. Pure units and server-rendered components; the browser flow
 * against the real backend is tests/e2e/phase4d.spec.ts. All values are SYNTHETIC.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, it } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { ExperimentForm } from "../../components/experiments/experiment-form";
import { CaseResultsTable, chartable, ComparisonTable, ComparisonVerdict, OutcomeSection, ReportSummary, ReportWarnings, reportWarnings } from "../../components/experiments/experiment-report";
import { SyntheticBanner, VariantCost } from "../../components/experiments/values";
import { writeFailure } from "../../lib/action-state";
import { experimentListDecoder, experimentOptionsDecoder, experimentReportDecoder } from "../../lib/api/experiment-schema";
import type { ExperimentOptions, ExperimentReport } from "../../lib/api/experiment-types";
import { CONFLICT_CODES } from "../../lib/api/result";
import type { Dataset } from "../../lib/api/review-types";
import { parseBudget, parseExperimentForm } from "../../lib/experiment-form";
import { parseJsonLossless } from "../../lib/json";
import { isWritePage } from "../../lib/request-guard";
import { experimentPath, getJson, sendJson } from "../../lib/server/backend";
import type { DashboardConfig } from "../../lib/server/config";
import { createExperiment } from "../../lib/server/experiments";
import { decode } from "../../lib/validate";

const ROOT = path.resolve(__dirname, "..", "..", "..");
const TOKEN = "SYNTHETIC-test-token-0123456789abcdef0123456789";
const config: DashboardConfig = { baseUrl: new URL("http://127.0.0.1:8000"), token: TOKEN, defaultProjectId: "7", timeoutMs: 200 };
const XSS = `<script>alert("x")</script><img src=x onerror=alert(1)>`;
const html = (el: React.ReactElement) => renderToStaticMarkup(el);
/* eslint-disable @typescript-eslint/no-explicit-any */
const clone = (v: unknown): any => JSON.parse(JSON.stringify(v));

// ---------------------------------------------------------------- SYNTHETIC wire fixtures
function result(id: number, slot: "a" | "b", passed: boolean | null, code: string, cost: string | null) {
  return {
    test_case_id: id,
    prompt_version: slot === "a" ? "golden-prompt-v1" : "golden-prompt-v2",
    slot,
    outcome: passed === true ? "passed" : passed === false ? "failed" : "not_evaluated",
    passed,
    detail_code: code,
    evaluation_detail: passed === true ? null : `${code}: SYNTHETIC fixed text`,
    duration_ms: cost === null ? null : 400,
    input_tokens: cost === null ? null : 50,
    output_tokens: cost === null ? null : 15,
    cost_usd: cost,
    cost_status: cost === null ? "unknown" : "computed",
    cost_unknown_reason: cost === null ? "usage_missing" : null,
    provenance: "synthetic",
  };
}

/** Three cases: #1 passes under both, #2 fails under A and passes under B, #3 times out under B. */
function wireReport(): any {
  const results = [
    result(1, "a", true, "pass", "0.0000165000"),
    result(1, "b", true, "pass", "0.0000165000"),
    result(2, "a", false, "schema_violation", "0.0000140000"),
    result(2, "b", true, "pass", "0.0000180000"),
    result(3, "a", false, "output_not_json", "0.0000150000"),
    result(3, "b", null, "provider_timeout", null),
  ];
  return {
    contract_version: "llminsight.experiment_report.v1",
    experiment: {
      id: 42,
      dataset_id: 3,
      dataset_name: "golden-synthetic-v1",
      prompt_version_a: "golden-prompt-v1",
      prompt_version_b: "golden-prompt-v2",
      model: "gpt-5-mini",
      mode: "mock",
      budget_usd: null,
      started_at: "2026-10-26T10:00:00.000001Z",
      finished_at: "2026-10-26T10:00:00.120001Z",
      state: "finished",
    },
    evaluation: { method: "deterministic_rules", rule_contract: "llminsight.rule.v1", language_model_used: false },
    provenance: { label: "synthetic", synthetic: 6, real: 0, simulated_provider: true },
    cases: { count: 3, ids_identical_across_versions: true },
    variants: [
      {
        slot: "a",
        prompt_version: "golden-prompt-v1",
        results: 3,
        passed: 1,
        failed: 2,
        not_evaluated: 0,
        not_evaluated_by_code: {},
        pass_rate: { value: 0.333333, numerator: 1, denominator: 3 },
        duration_ms: { n: 3, p50: 400, p95: 400, p95_low_sample: true },
        tokens: { input: { sum: 150, n: 3 }, output: { sum: 45, n: 3 } },
        cost: { known_cost_usd: "0.0000455000", computed: 3, unknown: 0, unknown_by_reason: {}, complete: true },
      },
      {
        slot: "b",
        prompt_version: "golden-prompt-v2",
        results: 3,
        passed: 2,
        failed: 0,
        not_evaluated: 1,
        not_evaluated_by_code: { provider_timeout: 1 },
        pass_rate: { value: 1, numerator: 2, denominator: 2 },
        duration_ms: { n: 2, p50: 400, p95: 400, p95_low_sample: true },
        tokens: { input: { sum: 100, n: 2 }, output: { sum: 30, n: 2 } },
        cost: { known_cost_usd: "0.0000345000", computed: 2, unknown: 1, unknown_by_reason: { usage_missing: 1 }, complete: false },
      },
    ],
    comparison: {
      quality: {
        status: "partial",
        paired_cases: 2,
        excluded_by_design: 0,
        unpaired_cases: 1,
        a_passed_on_paired: 1,
        b_passed_on_paired: 2,
        higher_pass_rate: null,
        reason: "some cases were evaluated under only one prompt version (timeout, provider error, no scripted output, a limit or the budget), so the versions are not compared",
      },
      cost: { status: "incomplete", difference_usd_b_minus_a: null, reason: "at least one prompt version has results with unknown cost; a partially priced total is not compared with another total" },
    },
    limitations: [{ code: "synthetic_mock", text: "Outputs come from a deterministic mock provider with scripted SYNTHETIC answers." }],
    results,
  };
}

/** The same experiment without case #3: complete evidence, B passes more, both fully priced. */
function wireComplete(): any {
  const r = wireReport();
  r.results = r.results.filter((x: any) => x.test_case_id !== 3);
  r.cases.count = 2;
  r.provenance.synthetic = 4;
  const [a, b] = r.variants;
  Object.assign(a, { results: 2, passed: 1, failed: 1, pass_rate: { value: 0.5, numerator: 1, denominator: 2 }, duration_ms: { n: 2, p50: 400, p95: 400, p95_low_sample: true } });
  a.tokens = { input: { sum: 100, n: 2 }, output: { sum: 30, n: 2 } };
  a.cost = { known_cost_usd: "0.0000305000", computed: 2, unknown: 0, unknown_by_reason: {}, complete: true };
  Object.assign(b, { results: 2, passed: 2, failed: 0, not_evaluated: 0, not_evaluated_by_code: {}, pass_rate: { value: 1, numerator: 2, denominator: 2 } });
  b.cost = { known_cost_usd: "0.0000345000", computed: 2, unknown: 0, unknown_by_reason: {}, complete: true };
  r.comparison.quality = { status: "complete", paired_cases: 2, excluded_by_design: 0, unpaired_cases: 0, a_passed_on_paired: 1, b_passed_on_paired: 2, higher_pass_rate: "b", reason: null };
  r.comparison.cost = { status: "complete", difference_usd_b_minus_a: "0.0000040000", reason: null };
  return r;
}

function decoded(wire: unknown): ExperimentReport {
  const d = decode(experimentReportDecoder, parseJsonLossless(JSON.stringify(wire)));
  assert.ok(d.ok, d.ok ? "" : d.path);
  return d.value;
}

function rejectedAt(wire: unknown): string {
  const d = decode(experimentReportDecoder, parseJsonLossless(JSON.stringify(wire)));
  assert.equal(d.ok, false, "the report should be refused");
  return d.ok ? "" : d.path;
}

const OPTIONS: ExperimentOptions = {
  contract_version: "llminsight.experiment_options.v1",
  modes: ["mock"],
  real_mode: { available: false, code: "real_llm_not_allowed" },
  provider: { kind: "scripted_mock", simulates: "openai", provenance: "synthetic" },
  prompt_versions: [
    { prompt_version: "golden-prompt-v1", scripted_inputs: 12 },
    { prompt_version: "golden-prompt-v2", scripted_inputs: 12 },
  ],
  models: [{ model: "gpt-5-mini", price_version: "openai-2026-10-24" }],
  models_truncated: false,
  limits: { max_cases: 200, max_concurrent_runs: 4, run_time_limit_seconds: 15, budget_usd_max: "9999999999.9999999999", budget_usd_decimal_places: 10 },
};

function form(values: Record<string, string>): FormData {
  const fd = new FormData();
  for (const [k, v] of Object.entries(values)) fd.set(k, v);
  return fd;
}
const VALID = { project: "7", dataset_id: "3", prompt_version_a: "golden-prompt-v1", prompt_version_b: "golden-prompt-v2", model: "gpt-5-mini", confirm_mock: "yes" };

// ---------------------------------------------------------------- report decoder
describe("experiment report decoder: the report's own definitions are checked", () => {
  it("accepts a consistent partial report and a consistent complete one", () => {
    const partial = decoded(wireReport());
    assert.equal(partial.comparison.quality.higher_pass_rate, null);
    assert.equal(partial.variants[1].pass_rate.denominator, 2); // not evaluated excluded (T40)
    assert.equal(decoded(wireComplete()).comparison.quality.higher_pass_rate, "b");
  });

  const mutations: [string, (r: any) => void][] = [
    ["a winner on partial evidence", (r) => (r.comparison.quality.higher_pass_rate = "b")],
    ["a NULL result in the denominator", (r) => (r.variants[1].pass_rate = { value: 0.666667, numerator: 2, denominator: 3 })],
    ["counts that do not add up", (r) => (r.variants[0].passed = 2)],
    ["a cost difference with unknown cost", (r) => (r.comparison.cost = { status: "complete", difference_usd_b_minus_a: "-0.0000110000", reason: null })],
    ["unknown cost shown as a known zero", (r) => (r.variants[1].cost = { ...r.variants[1].cost, known_cost_usd: "0", computed: 0, unknown: 3, unknown_by_reason: { usage_missing: 3 } })],
    ["a partially priced variant claiming completeness", (r) => (r.variants[1].cost.complete = true)],
    ["scientific notation for money", (r) => (r.results[0].cost_usd = "1.65E-5")],
    ["a real result in a mock experiment (T39)", (r) => ((r.results[0].provenance = "real"), (r.provenance = { label: "mixed", synthetic: 5, real: 1, simulated_provider: true }))],
    ["a mock experiment not marked as simulated", (r) => (r.provenance.simulated_provider = false)],
    ["a duplicate (case, version) row", (r) => (r.results[1] = { ...r.results[0] })],
    ["an outcome contradicting passed", (r) => (r.results[0].outcome = "failed")],
    ["a computed cost without a value", (r) => (r.results[0].cost_usd = null)],
    ["the wrong contract", (r) => (r.contract_version = "llminsight.experiment_report.v2")],
    ["a language model judge", (r) => (r.evaluation.language_model_used = true)],
    ["a finished state without finished_at", (r) => (r.experiment.finished_at = null)],
    ["a variant of another prompt version", (r) => (r.variants[0].prompt_version = "golden-prompt-v9")],
    ["a wrong label for the provenance counts", (r) => (r.provenance.label = "real")],
  ];
  for (const [name, mutate] of mutations) {
    it(`refuses ${name}`, () => {
      const r = clone(wireReport());
      mutate(r);
      assert.ok(rejectedAt(r).startsWith("$"));
    });
  }

  it("refuses a complete verdict naming the version with fewer passes", () => {
    const r = wireComplete();
    r.comparison.quality.higher_pass_rate = "a";
    rejectedAt(r);
  });
});

describe("experiment options and list decoders", () => {
  it("accepts mock-only options and refuses any offer of real mode", () => {
    assert.ok(decode(experimentOptionsDecoder, clone(OPTIONS)).ok);
    for (const mutate of [(o: any) => (o.modes = ["mock", "real"]), (o: any) => (o.modes = ["real"]), (o: any) => (o.real_mode.available = true), (o: any) => (o.provider.provenance = "real")]) {
      const o = clone(OPTIONS);
      mutate(o);
      assert.equal(decode(experimentOptionsDecoder, o).ok, false);
    }
  });

  it("decodes the list and refuses a finished state without finished_at", () => {
    const item = { id: 42, dataset_id: 3, dataset_name: "d", prompt_version_a: "a", prompt_version_b: "b", model: "m", mode: "mock", budget_usd: "1.5000000000", started_at: "2026-10-26T10:00:00.000001Z", finished_at: "2026-10-26T10:00:01.000001Z", state: "finished", results: 24 };
    assert.ok(decode(experimentListDecoder, { items: [item], truncated: false, limit: 50 }).ok);
    assert.equal(decode(experimentListDecoder, { items: [{ ...item, finished_at: null }], truncated: false, limit: 50 }).ok, false);
    assert.equal(decode(experimentListDecoder, { items: [{ ...item, results: 401 }], truncated: false, limit: 50 }).ok, false);
  });
});

// ---------------------------------------------------------------- form parsing (mock only)
describe("experiment form: explicit, mock-only submissions", () => {
  it("builds the exact body, always with mode mock", () => {
    const p = parseExperimentForm(form(VALID));
    assert.ok(p.ok);
    assert.deepEqual(p.value, { project: "7", body: { dataset_id: 3, prompt_version_a: "golden-prompt-v1", prompt_version_b: "golden-prompt-v2", model: "gpt-5-mini", mode: "mock" } });
  });

  it("ignores a submitted mode: a crafted mode=real still sends mock", () => {
    const p = parseExperimentForm(form({ ...VALID, mode: "real", budget_usd: "1" }));
    assert.ok(p.ok);
    assert.equal(p.value.body.mode, "mock");
    assert.equal(p.value.body.budget_usd, 1);
    assert.doesNotMatch(readFileSync(path.join(ROOT, "lib", "experiment-form.ts"), "utf8"), /str\(fd, "mode"\)|get\("mode"\)/);
  });

  it("requires the explicit confirmation and two different versions", () => {
    const { confirm_mock: _omit, ...unconfirmed } = VALID;
    void _omit;
    const noConfirm = parseExperimentForm(form(unconfirmed));
    assert.ok(!noConfirm.ok && noConfirm.fields[0] === "confirm_mock");
    const same = parseExperimentForm(form({ ...VALID, prompt_version_b: VALID.prompt_version_a }));
    assert.ok(!same.ok && same.fields.length === 2);
    for (const [field, value] of [
      ["project", "abc"],
      ["dataset_id", "0"],
      ["dataset_id", "99999999999999999"],
      ["prompt_version_a", "has space"],
      ["model", ""],
    ] as const) {
      const p = parseExperimentForm(form({ ...VALID, [field]: value }));
      assert.ok(!p.ok && p.fields[0] === field, `${field}=${value}`);
    }
  });

  it("accepts only exact positive budgets with at most 10 decimal places", () => {
    for (const [text, value] of [
      ["", null],
      ["0.01", 0.01],
      ["  2.5 ", 2.5],
      ["0.0000000001", 1e-10],
      ["9999999999", 9999999999],
    ] as const) {
      const b = parseBudget(text);
      assert.ok(b.ok && b.value === value, text);
    }
    for (const text of ["0", "0.0", "-1", "1e-3", "00.5", "0.12345678901", "abc", "12345678901", "9999999999.9999999999"]) {
      assert.equal(parseBudget(text).ok, false, text);
    }
  });
});

// ---------------------------------------------------------------- transport and guard
describe("backend client for experiments", () => {
  it("allows exactly the experiment reads and one write", async () => {
    assert.equal(experimentPath("42"), "/api/v1/experiments/42");
    for (const bad of ["0", "abc", "1/cases", "9223372036854775808"]) assert.equal(experimentPath(bad), null, bad);
    const calls: { url: string; method: string; body: string | undefined }[] = [];
    const fetch: typeof globalThis.fetch = async (url, init) => {
      calls.push({ url: String(url), method: init?.method ?? "GET", body: init?.body as string | undefined });
      return new Response("{}", { status: 404, headers: { "content-type": "application/json" } });
    };
    const decoder = (v: unknown) => v;
    for (const p of ["/api/v1/experiments", "/api/v1/experiments/42", "/api/v1/experiment-options"] as const) {
      await getJson(config, p, { project: "7" }, {}, decoder, { fetch });
    }
    const refused = await getJson(config, "/api/v1/experiments/42/cases" as "/api/v1/experiments/42", { project: "7" }, {}, decoder, { fetch });
    assert.ok(!refused.ok && refused.error.kind === "config");
    const put = await sendJson(config, "PATCH", "/api/v1/experiments", "7", {}, decoder, { fetch });
    assert.ok(!put.ok && put.error.kind === "config");
    assert.deepEqual(
      calls.map((c) => `${c.method} ${new URL(c.url).pathname}`),
      ["GET /api/v1/experiments", "GET /api/v1/experiments/42", "GET /api/v1/experiment-options"],
    );
  });

  it("createExperiment posts the parsed body with mode mock and the project scope", async () => {
    let seen: { url: string; init: RequestInit | undefined } | null = null;
    const fetch: typeof globalThis.fetch = async (url, init) => {
      seen = { url: String(url), init };
      return new Response(JSON.stringify(wireReport()), { status: 201, headers: { "content-type": "application/json" } });
    };
    const p = parseExperimentForm(form({ ...VALID, mode: "real" }));
    assert.ok(p.ok);
    const res = await createExperiment(config, p.value, { fetch });
    assert.ok(res.ok);
    assert.ok(seen !== null);
    const s = seen as { url: string; init: RequestInit | undefined };
    assert.equal(s.init?.method, "POST");
    assert.equal(new URL(s.url).pathname, "/api/v1/experiments");
    assert.equal(new URL(s.url).searchParams.get("project"), "7");
    assert.equal(JSON.parse(String(s.init?.body)).mode, "mock");
    assert.equal(s.init?.redirect, "error");
  });

  it("maps 429 experiments_busy and every experiment 409 to a fixed message", async () => {
    const busy: typeof globalThis.fetch = async () => new Response(JSON.stringify({ error: { code: "experiments_busy", message: "x" } }), { status: 429, headers: { "content-type": "application/json", "retry-after": "5" } });
    const r = await sendJson(config, "POST", "/api/v1/experiments", "7", {}, (v) => v, { fetch: busy });
    assert.ok(!r.ok && r.error.kind === "rate_limited" && r.error.code === "experiments_busy" && r.error.retryAfterSeconds === 5);
    const state = writeFailure(r.error, "the experiment");
    assert.ok(state.status === "error" && /Other experiments are running/.test(state.message) && /Nothing was run/.test(state.message));
    for (const code of ["dataset_empty", "dataset_too_large", "budget_unverifiable", "budget_exceeded", "real_llm_not_allowed", "real_mode_unavailable", "experiment_time_limit"] as const) {
      assert.ok((CONFLICT_CODES as readonly string[]).includes(code));
      const s = writeFailure({ kind: "conflict", code }, "the experiment");
      assert.ok(s.status === "error" && /Nothing was (run|stored)/.test(s.message), code);
    }
  });

  it("accepts Server Action POSTs only on the experiment setup page", () => {
    assert.equal(isWritePage("/experiments/new"), true);
    for (const p of ["/experiments", "/experiments/42", "/experiments/42/cases/1", "/experiments/new/x"]) assert.equal(isWritePage(p), false, p);
  });
});

// ---------------------------------------------------------------- presentation
describe("experiment report presentation", () => {
  it("names no better version and no cost difference on incomplete evidence", () => {
    const r = decoded(wireReport());
    const m = html(
      <>
        <ReportWarnings report={r} />
        <ComparisonVerdict report={r} />
        <ComparisonTable report={r} />
      </>,
    );
    assert.match(m, /No better version is named/);
    assert.match(m, /data-higher="none"/);
    assert.doesNotMatch(m, /passed more/);
    assert.match(m, /Cost not compared/);
    assert.match(m, /data-cost="incomplete"/);
    assert.match(m, /Total unknown\./);
    assert.match(m, /Known part/);
    assert.doesNotMatch(m, /\$0(?![.0-9]*[1-9])/); // no zero dollar amount anywhere
    assert.match(m, /1 provider_timeout/);
    assert.match(m, /excluded from its pass rate/);
    assert.match(m, /\(2 \/ 2\)/); // B's denominator excludes the NULL result
  });

  it("states the backend's verdict on complete evidence", () => {
    const r = decoded(wireComplete());
    const m = html(<ComparisonVerdict report={r} />);
    assert.match(m, /data-higher="b"/);
    assert.match(m, /version B \(golden-prompt-v2\) passed more/);
    assert.match(m, /no significance test/);
    assert.match(m, /B − A = /);
    assert.equal(reportWarnings(r).filter((w) => /not compared/i.test(w)).length, 0);
  });

  it("shows a cost total only when every result is priced", () => {
    const complete = html(<VariantCost cost={decoded(wireComplete()).variants[0].cost} />);
    assert.match(complete, /data-cost="complete"/);
    assert.match(complete, /all 2 results priced/);
    const unknownOnly = html(<VariantCost cost={{ known_cost_usd: null, computed: 0, unknown: 2, unknown_by_reason: { price_missing: 2 }, complete: false }} />);
    assert.match(unknownOnly, /Total unknown\./);
    assert.match(unknownOnly, /No result is priced/);
    assert.match(unknownOnly, /2 no stored price/);
  });

  it("draws the outcome chart only for comparable counts, with a table", () => {
    const r = decoded(wireReport());
    assert.equal(chartable(r), true);
    assert.match(html(<OutcomeSection report={r} />), /data-testid="outcome-table"/);
    const notIdentical = clone(wireReport());
    notIdentical.cases.ids_identical_across_versions = false;
    const n = decoded(notIdentical);
    assert.equal(chartable(n), false);
    assert.match(html(<OutcomeSection report={n} />), /outcome-chart-omitted/);
  });

  it("labels the experiment SYNTHETIC and renders stored strings as text", () => {
    const wire = clone(wireReport());
    wire.experiment.dataset_name = XSS.slice(0, 100);
    const r = decoded(wire);
    const m = html(
      <>
        <SyntheticBanner mode="mock" />
        <ReportSummary report={r} project="7" />
        <CaseResultsTable report={r} project="7" />
      </>,
    );
    assert.match(m, /SYNTHETIC · mock provider · not a real model/);
    assert.match(m, /data-provenance="synthetic"/);
    assert.doesNotMatch(m, /<script>/);
    assert.match(m, /&lt;script&gt;/);
    assert.match(m, /href="\/experiments\/42\/cases\/3\?project=7"/);
    assert.match(m, /identical under both versions/);
  });
});

describe("experiment setup form", () => {
  const datasets: Dataset[] = [
    { id: 3, name: "golden-synthetic-v1", created_at: "2026-10-26T10:00:00.000001Z", case_count: 12 },
    { id: 4, name: "empty", created_at: "2026-10-26T10:00:00.000001Z", case_count: 0 },
  ];

  it("starts unconfirmed, offers no mode control and only the backend's choices", () => {
    const m = html(<ExperimentForm project="7" datasets={datasets} options={OPTIONS} initialDataset={null} />);
    assert.doesNotMatch(m, /name="mode"/);
    assert.doesNotMatch(m, /<option[^>]*>[^<]*real/i);
    assert.match(m, /name="confirm_mock"/);
    assert.doesNotMatch(m, /checked=""/);
    assert.match(m, /<option value="4" disabled="">empty \(0 cases, nothing to run\)<\/option>/);
    assert.match(m, /<option value="golden-prompt-v1">golden-prompt-v1<\/option>/);
    assert.match(m, /golden-prompt-v1: 12 inputs; golden-prompt-v2: 12 inputs/);
    assert.match(m, /gpt-5-mini \(price openai-2026-10-24\)/);
    assert.match(m, /Mock only/);
    assert.match(m, /Run mock experiment/);
  });

  it("preselects only a dataset of the listing", () => {
    assert.match(html(<ExperimentForm project="7" datasets={datasets} options={OPTIONS} initialDataset="3" />), /24 simulated calls/);
    assert.doesNotMatch(html(<ExperimentForm project="7" datasets={datasets} options={OPTIONS} initialDataset="99" />), /simulated calls \(/);
  });
});
