/**
 * Live E2E for Phase 4D: browser → Next.js (pages and the experiment Server Action) → FastAPI →
 * PostgreSQL, nothing mocked. The full improvement loop on run-owned SYNTHETIC data:
 *
 *   failing trace → automatic diagnostics → human failure review → decision → sanitized test
 *   input with an explicit expected result → approved test case → golden dataset → two
 *   deterministic variants → explicit mock-only run → persisted results → per-case comparison →
 *   counts checked against PostgreSQL directly (scripts/dev/e2e_fixtures.py experiment-counts).
 *
 * The human case's input is the approved SYNTHETIC golden fixture G02 (backend
 * app/services/experiments/golden.py), typed into the form: the scripted mock answers only the
 * golden inputs (R65), so this is what makes the human case evaluable. The other golden cases
 * are added through the API by this test as their approver, like scripts/dev/seed_golden_dataset.py.
 * The global teardown deletes the run's projects, which removes every review, dataset, case and
 * experiment created here.
 */
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { backendPython } from "../setup/e2e-global-setup";
import { API, FOREIGN_PROJECT, PROJECT, SHOTS, TOKEN, u } from "./e2e-env";

/* eslint-disable @typescript-eslint/no-explicit-any */
const RUN = process.env.LLMI_E2E_RUN ?? "local";
const REPO = path.resolve(__dirname, "..", "..", "..");
const A = "golden-prompt-v1";
const B = "golden-prompt-v2";

interface GoldenCase {
  key: string;
  body: string; // exact JSON text of POST /api/v1/datasets/{id}/cases
}

/** The golden fixtures as the backend defines them, with exact numbers (never re-serialized). */
function goldenCases(): GoldenCase[] {
  const code = [
    "import json, sys",
    "sys.path.insert(0, 'backend')",
    "from app.core import exact_json",
    "from app.services.experiments import golden",
    `print(json.dumps([{'key': c.key, 'body': exact_json.dumps(p)} for c, p in zip(golden.CASES, golden.case_payloads('e2e-${RUN}-golden-fixture'))]))`,
  ].join("\n");
  return JSON.parse(execFileSync(backendPython(), ["-c", code], { cwd: REPO, encoding: "utf8", timeout: 60_000 }).trim()) as GoldenCase[];
}

/** Counts read straight from PostgreSQL, for an experiment of this run only. */
function dbCounts(experimentId: number): any {
  const state = process.env.LLMI_E2E_STATE;
  expect(state, "the global setup's state file").toBeTruthy();
  const out = execFileSync(backendPython(), [path.join(REPO, "scripts", "dev", "e2e_fixtures.py"), "experiment-counts", "--state", state!, "--experiment", String(experimentId)], {
    cwd: REPO,
    encoding: "utf8",
    timeout: 60_000,
  });
  return JSON.parse(out.trim().split(/\r?\n/).pop() ?? "{}");
}

async function pause(): Promise<void> {
  await new Promise((r) => setTimeout(r, 150)); // stay under the shared /api/v1 rate limit
}

async function api(request: APIRequestContext, pathAndQuery: string, project = PROJECT, status = 200): Promise<any> {
  await pause();
  const sep = pathAndQuery.includes("?") ? "&" : "?";
  const res = await request.get(`${API}${pathAndQuery}${sep}project=${project}`, { headers: { Authorization: `Bearer ${TOKEN}` } });
  expect(res.status(), pathAndQuery).toBe(status);
  return res.json();
}

async function apiPost(request: APIRequestContext, pathAndQuery: string, body: string, project = PROJECT): Promise<{ status: number; json: any }> {
  await pause();
  const res = await request.post(`${API}${pathAndQuery}?project=${project}`, { headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" }, data: body });
  return { status: res.status(), json: await res.json() };
}

async function shot(page: Page, name: string): Promise<void> {
  await page.waitForLoadState("networkidle");
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });
}

function watch(page: Page): { problems: string[]; bodies: string[] } {
  const problems: string[] = [];
  const bodies: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error" || m.type() === "warning") problems.push(`${m.type()}: ${m.text().slice(0, 200)}`);
  });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message.slice(0, 200)}`));
  page.on("dialog", (d) => {
    problems.push(`dialog: ${d.message()}`);
    void d.dismiss();
  });
  page.on("response", async (res) => {
    try {
      bodies.push(await res.text());
    } catch {
      /* no body */
    }
  });
  return { problems, bodies };
}

async function failingTrace(request: APIRequestContext): Promise<string> {
  const list = await api(request, "/api/v1/traces?status=error&limit=200");
  const t = list.items.find((x: any) => x.reviews.total === 0);
  expect(t, "a failing, unreviewed trace seeded by the global setup").toBeTruthy();
  return t.trace_id as string;
}

async function experiments(request: APIRequestContext, project = PROJECT): Promise<any[]> {
  return (await api(request, "/api/v1/experiments", project)).items;
}

/** Choose the run's values in the setup form (the dataset may be preselected). */
async function configure(page: Page, datasetId: number, model: string): Promise<void> {
  await page.getByLabel("Golden dataset", { exact: true }).selectOption(String(datasetId));
  await page.getByLabel("Prompt version A", { exact: true }).selectOption(A);
  await page.getByLabel("Prompt version B", { exact: true }).selectOption(B);
  await page.getByLabel("Model", { exact: true }).selectOption(model);
}

test.beforeAll(() => {
  expect(TOKEN.length, "frontend/.env.local must hold the server-side token").toBeGreaterThan(31);
});

test("failed trace → review → approved case → golden dataset → mock A/B → results checked against PostgreSQL", async ({ page, request }) => {
  test.setTimeout(240_000);
  const { problems, bodies } = watch(page);
  const golden = goldenCases();
  const g02 = golden.find((c) => c.key === "G02");
  expect(g02).toBeTruthy();
  const g02Body = JSON.parse(g02!.body);
  const traceId = await failingTrace(request);

  // 1–2. The trace and its automatic diagnostics: separate, labelled, unverified, no form.
  await page.goto(u(`/traces/${traceId}`));
  const machine = page.getByTestId("diagnostics-panel");
  await expect(machine.getByTestId("diagnostics-label")).toHaveText("Automatic · Unverified · Not a human review");
  await expect(machine.locator("form, select, textarea, input")).toHaveCount(0);
  await expect(page.getByTestId("review-state")).toContainText("not reviewed yet");
  await shot(page, "phase4d-01-trace-diagnostics");

  // 3. An explicit human failure review.
  await page.getByLabel("Issue type").selectOption("schema_violation");
  await page.getByLabel("Evidence").fill("SYNTHETIC evidence: validate_output failed rule json_schema (priority missing)");
  await page.getByLabel("Hypothesis").fill("SYNTHETIC hypothesis: prompt v1 does not ask for the priority field");
  await page.getByLabel("Reviewer", { exact: true }).fill(`e2e-${RUN}`);
  await page.getByTestId("review-submit").click();
  await expect(page.getByTestId("review-form-result")).toHaveAttribute("data-outcome", "success");
  const reviews = (await api(request, `/api/v1/traces/${traceId}`)).reviews;
  expect(reviews).toHaveLength(1);
  const reviewId = reviews[0].id as number;

  // 4. The human decision.
  const card = page.getByTestId("review-card");
  await card.getByTestId("decision-status").selectOption("closed");
  await card.getByTestId("decision-decision").selectOption("hypothesis_supported");
  await card.getByTestId("decision-submit").click();
  await expect(page.getByTestId(`decision-result-${reviewId}`)).toHaveAttribute("data-outcome", "success");

  // 5–6. Sanitized input and explicit expected result, approved into a new golden dataset.
  await card.getByTestId("convert-link").click();
  await expect(page).toHaveURL(new RegExp(`/traces/${traceId}/reviews/${reviewId}/test-case`));
  const datasetName = `e2e-${RUN}-golden-ab`;
  await page.getByLabel("New dataset name").fill(datasetName);
  await page.getByTestId("dataset-submit").click();
  await expect(page.getByTestId("dataset-form-result")).toHaveAttribute("data-outcome", "success");
  const dataset = (await api(request, "/api/v1/datasets")).items.find((d: any) => d.name === datasetName);
  expect(dataset).toBeTruthy();
  await page.getByLabel("Dataset", { exact: true }).selectOption(String(dataset.id));
  await page.getByLabel("Test input").fill(g02Body.sanitized_input);
  await page.getByLabel("How the output will be checked").selectOption("json_schema");
  await page.getByLabel("JSON Schema").fill(JSON.stringify(g02Body.rule.schema));
  await page.getByLabel("Expected output (JSON)").fill(JSON.stringify(g02Body.expected_output));
  await page.getByLabel("Approved by").fill(`e2e-${RUN}`);
  await page.getByLabel(/I approve storing this test case/).check();
  await page.getByTestId("case-submit").click();
  await expect(page.getByTestId("case-form-result")).toHaveAttribute("data-outcome", "success");
  let cases = (await api(request, `/api/v1/datasets/${dataset.id}/cases`)).items;
  expect(cases).toHaveLength(1);
  const humanCase = cases[0];
  expect(humanCase).toMatchObject({ origin_trace_id: traceId, origin_review_id: reviewId, sanitized_input: g02Body.sanitized_input, expected_output: g02Body.expected_output });

  // The rest of the golden set (the approved SYNTHETIC fixtures), approved by this test.
  for (const c of golden.filter((x) => x.key !== "G02")) {
    const r = await apiPost(request, `/api/v1/datasets/${dataset.id}/cases`, c.body);
    expect(r.status, c.key).toBe(201);
  }
  cases = (await api(request, `/api/v1/datasets/${dataset.id}/cases`)).items;
  expect(cases).toHaveLength(12);
  const caseIds = cases.map((c: any) => c.id as number).sort((x: number, y: number) => x - y);

  // 7. The golden dataset page links to the setup, with the dataset preselected.
  await page.goto(u(`/datasets/${dataset.id}`));
  await expect(page.getByTestId("test-case")).toHaveCount(12);
  await page.getByTestId("dataset-new-experiment").click();
  await expect(page).toHaveURL(new RegExp(`/experiments/new\\?project=${PROJECT}&dataset=${dataset.id}`));
  expect(await experiments(request)).toEqual([]); // opening the setup ran nothing

  // 8. Two deterministic variants; the execution mode is visible and mock only.
  await expect(page.getByTestId("execution-mode")).toContainText("mock only");
  await expect(page.getByTestId("execution-mode")).toContainText("not available");
  await expect(page.locator('[name="mode"]')).toHaveCount(0);
  await expect(page.getByTestId("experiment-case-count")).toContainText("12 test cases → 24 simulated calls");
  const options = await api(request, "/api/v1/experiment-options");
  expect(options.modes).toEqual(["mock"]);
  const model = (options.models.find((m: any) => m.model === "gpt-5-mini") ?? options.models[0])?.model as string;
  expect(model, "a model with a stored price (python -m app.cli seed-prices)").toBeTruthy();
  await page.getByLabel("Prompt version A", { exact: true }).selectOption(A);
  await page.getByLabel("Prompt version B", { exact: true }).selectOption(A);
  await expect(page.getByTestId("experiment-same-version")).toBeVisible();
  await page.getByLabel("Prompt version B", { exact: true }).selectOption(B);
  await page.getByLabel("Model", { exact: true }).selectOption(model);
  await expect(page.getByTestId("experiment-review-mode")).toContainText("Mock only");
  await page.getByTestId("experiment-submit").click(); // not confirmed
  await expect(page.getByTestId("experiment-form-result")).toContainText("Tick the box to confirm a mock-only run");
  expect(await experiments(request)).toEqual([]);
  await shot(page, "phase4d-02-experiment-setup");

  // 9. The explicit start; a double click starts exactly one run.
  await page.getByTestId("experiment-confirm").check();
  await page.getByTestId("experiment-submit").dblclick();
  await expect(page.getByTestId("experiment-form-result")).toHaveAttribute("data-outcome", "success", { timeout: 30_000 });
  await expect(page.getByTestId("experiment-form-result")).toContainText("24 results (24 SYNTHETIC, 0 real)");
  const listed = await experiments(request);
  expect(listed).toHaveLength(1);
  const experimentId = listed[0].id as number;
  expect(listed[0]).toMatchObject({ dataset_id: dataset.id, prompt_version_a: A, prompt_version_b: B, model, mode: "mock", state: "finished", results: 24 });
  await expect(page.getByTestId("experiment-confirm")).not.toBeChecked(); // cleared after success

  // 10. The persisted results page.
  await page.getByTestId("experiment-form-result").getByRole("link", { name: "Open the experiment results" }).click();
  await expect(page).toHaveURL(new RegExp(`/experiments/${experimentId}\\?project=${PROJECT}`));
  await expect(page.getByTestId("experiment-synthetic-banner")).toContainText("SYNTHETIC · mock provider · not a real model");
  await expect(page.getByTestId("summary-dataset")).toContainText(datasetName);
  await expect(page.getByTestId("summary-version-a")).toHaveText(A);
  await expect(page.getByTestId("summary-version-b")).toHaveText(B);
  await expect(page.getByTestId("summary-provenance").locator("[data-provenance]")).toHaveAttribute("data-provenance", "synthetic");
  await expect(page.getByTestId("summary-provenance")).toContainText("24 synthetic, 0 real");

  // No winner and no cost difference on incomplete evidence (G12 timed out under B).
  await expect(page.getByTestId("quality-verdict").locator("[data-status]")).toHaveAttribute("data-status", "partial");
  await expect(page.getByTestId("quality-verdict").locator("[data-status]")).toHaveAttribute("data-higher", "none");
  await expect(page.getByTestId("quality-verdict")).toContainText("No better version is named");
  await expect(page.getByTestId("cost-verdict")).toContainText("Not compared");
  await expect(page.getByTestId("cost-b")).toHaveAttribute("data-cost", "incomplete");
  await expect(page.getByTestId("cost-b")).toContainText("Total unknown.");
  await expect(page.getByTestId("experiment-warnings")).toContainText("provider_timeout");
  await expect(page.getByTestId("outcome-chart")).toBeVisible();

  // 11. Both variants on the same test case ids, row by row, against the API report.
  const report = await api(request, `/api/v1/experiments/${experimentId}`);
  const table = page.getByTestId("comparison-table");
  for (const [slot, v] of [
    ["a", report.variants[0]],
    ["b", report.variants[1]],
  ] as const) {
    for (const key of ["passed", "failed", "not_evaluated"] as const) {
      await expect(table.locator(`tr[data-row="${key}"] td[data-slot="${slot}"]`)).toContainText(String(v[key]));
    }
    await expect(table.locator(`tr[data-row="pass_rate"] td[data-slot="${slot}"]`)).toContainText(`(${v.pass_rate.numerator} / ${v.pass_rate.denominator})`);
  }
  const rows = page.getByTestId("case-result-row");
  await expect(rows).toHaveCount(12);
  expect((await rows.evaluateAll((els) => els.map((e) => Number(e.getAttribute("data-case-id"))))).sort((x, y) => x - y)).toEqual(caseIds);
  for (const r of report.results) {
    await expect(page.locator(`[data-testid="case-result-row"][data-case-id="${r.test_case_id}"] td[data-slot="${r.slot}"] [data-outcome]`).first()).toHaveAttribute("data-outcome", r.outcome);
  }
  await shot(page, "phase4d-03-experiment-results");

  // 12. Counts straight from PostgreSQL (bypassing the API) equal what the page showed.
  const db = dbCounts(experimentId);
  expect(db).toMatchObject({ experiment: experimentId, project: Number(PROJECT), mode: "mock", finished: true, results: 24, provenance: { synthetic: 24 } });
  expect(db.versions[A]).toMatchObject({ passed: 7, failed: 4, not_evaluated: 1, case_ids: caseIds });
  expect(db.versions[B]).toMatchObject({ passed: 4, failed: 6, not_evaluated: 2, case_ids: caseIds });
  expect([report.variants[0].passed, report.variants[0].failed, report.variants[0].not_evaluated]).toEqual([7, 4, 1]);
  expect([report.variants[1].passed, report.variants[1].failed, report.variants[1].not_evaluated]).toEqual([4, 6, 2]);
  expect(report.variants[0].pass_rate).toEqual({ value: 0.636364, numerator: 7, denominator: 11 });
  expect(report.variants[1].pass_rate).toEqual({ value: 0.4, numerator: 4, denominator: 10 });

  // Test case detail of the human-approved case: lineage, rule version, both outcomes.
  await page.locator(`[data-testid="case-result-row"][data-case-id="${humanCase.id}"]`).getByTestId("case-result-link").click();
  await expect(page).toHaveURL(new RegExp(`/experiments/${experimentId}/cases/${humanCase.id}`));
  await expect(page.getByTestId("case-result-a").locator("[data-outcome]").first()).toHaveAttribute("data-outcome", "failed");
  await expect(page.getByTestId("case-detail-a")).toContainText("schema_violation");
  await expect(page.getByTestId("case-result-b").locator("[data-outcome]").first()).toHaveAttribute("data-outcome", "passed");
  await expect(page.getByTestId("case-provenance-a")).toHaveText("synthetic");
  await expect(page.getByTestId("case-rule")).toContainText("json_schema");
  await expect(page.getByTestId("case-rule")).toContainText("version 1");
  await expect(page.getByTestId("case-origin")).toContainText(`review #${reviewId}`);
  await expect(page.getByTestId("case-input")).toHaveText(g02Body.sanitized_input);
  await expect(page.getByTestId("no-output-notice")).toContainText("never stored");
  await shot(page, "phase4d-04-case-detail");

  // The experiment list shows it; opening pages wrote nothing more.
  await page.goto(u("/experiments"));
  await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Experiments" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByTestId("experiment-link")).toHaveText(`#${experimentId}`);
  expect(await experiments(request)).toHaveLength(1);

  // No credential in any response; no console errors, CSP violations or dialogs.
  const leaks = bodies.filter((b) => b.includes(TOKEN) || /LLMI_API_TOKEN|LLMI_ADMIN_TOKEN|Bearer\s+[A-Za-z0-9]/.test(b));
  expect(leaks).toEqual([]);
  expect(problems).toEqual([]);
});

test("complete evidence: the backend's verdict and cost difference are shown, and match PostgreSQL", async ({ page, request }) => {
  test.setTimeout(120_000);
  const golden = goldenCases();
  const created = await apiPost(request, "/api/v1/datasets", JSON.stringify({ name: `e2e-${RUN}-complete` }));
  expect(created.status).toBe(201);
  const datasetId = created.json.id as number;
  for (const c of golden.filter((x) => x.key === "G02" || x.key === "G03")) {
    expect((await apiPost(request, `/api/v1/datasets/${datasetId}/cases`, c.body)).status).toBe(201);
  }
  const options = await api(request, "/api/v1/experiment-options");
  const model = (options.models.find((m: any) => m.model === "gpt-5-mini") ?? options.models[0]).model as string;
  const before = (await experiments(request)).length;

  await page.goto(u("/experiments/new"));
  await configure(page, datasetId, model);
  await page.getByLabel("Budget (USD)", { exact: true }).fill("0.01");
  await expect(page.getByTestId("experiment-review-budget")).toContainText("$0.01 USD, checked before execution");
  // Keyboard only: tick the confirmation with Space, start with Enter on the button.
  await page.getByTestId("experiment-confirm").focus();
  await page.keyboard.press("Space");
  await page.getByTestId("experiment-submit").focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("experiment-form-result")).toHaveAttribute("data-outcome", "success", { timeout: 30_000 });
  const list = await experiments(request);
  expect(list).toHaveLength(before + 1);
  const id = list[0].id as number;
  expect(list[0].budget_usd).toBe("0.0100000000");

  await page.goto(u(`/experiments/${id}`));
  const verdict = page.getByTestId("quality-verdict").locator("[data-status]");
  await expect(verdict).toHaveAttribute("data-status", "complete");
  await expect(verdict).toHaveAttribute("data-higher", "b");
  await expect(page.getByTestId("quality-verdict")).toContainText(`version B (${B}) passed more`);
  await expect(page.getByTestId("quality-verdict")).toContainText("no significance test");
  const report = await api(request, `/api/v1/experiments/${id}`);
  expect(report.comparison.cost.status).toBe("complete");
  await expect(page.getByTestId("cost-verdict")).toContainText("B − A =");
  await expect(page.getByTestId("cost-a")).toHaveAttribute("data-cost", "complete");
  await expect(page.locator("[data-testid=experiment-warnings]")).toHaveCount(1); // only the low-sample p95 notes
  const db = dbCounts(id);
  expect(db.versions[A]).toMatchObject({ passed: 0, failed: 2, not_evaluated: 0 });
  expect(db.versions[B]).toMatchObject({ passed: 2, failed: 0, not_evaluated: 0 });
  expect(db.versions[A].case_ids).toEqual(db.versions[B].case_ids);
  await shot(page, "phase4d-05-complete-comparison");
});

test("real mode, cross-origin, method and project-isolation attempts are refused and store nothing", async ({ page, request }) => {
  const before = await experiments(request);
  const datasets = (await api(request, "/api/v1/datasets")).items;
  const datasetId = datasets[0]?.id ?? 1;

  // The backend refuses mode=real even with a budget (no real executor; flag off).
  const real = await apiPost(request, "/api/v1/experiments", JSON.stringify({ dataset_id: datasetId, prompt_version_a: A, prompt_version_b: B, model: "gpt-5-mini", mode: "real", budget_usd: 1 }));
  expect(real.status).toBe(409);
  expect(["real_llm_not_allowed", "real_mode_unavailable"]).toContain(real.json.error.code);

  // CSRF: the setup page accepts only same-origin Server Action POSTs.
  await page.goto(u("/experiments/new"));
  const target = `http://127.0.0.1:3001/experiments/new?project=${PROJECT}`;
  const body = "1_project=1&1_mode=real";
  for (const headers of [
    { Origin: "https://evil.example", "Next-Action": "7f3a9c0d1e2b4a5f6789abcdef0123456789abcd", "Sec-Fetch-Site": "cross-site" },
    { "Next-Action": "7f3a9c0d1e2b4a5f6789abcdef0123456789abcd" },
    { Origin: "http://127.0.0.1:3001" },
  ]) {
    const res = await request.post(target, { headers: { "Content-Type": "application/x-www-form-urlencoded", ...headers }, data: body, maxRedirects: 0 });
    expect(res.status(), JSON.stringify(headers)).toBe(403);
  }
  for (const p of ["/experiments", `/experiments/${before[0]?.id ?? 1}`]) {
    const res = await request.post(`http://127.0.0.1:3001${p}?project=${PROJECT}`, { headers: { Origin: "http://127.0.0.1:3001" }, data: body });
    expect(res.status(), p).toBe(405);
  }
  expect(await experiments(request)).toEqual(before);

  // Another project cannot see this project's experiment, through the page or the API.
  if (before.length > 0) {
    const id = before[0].id as number;
    await page.goto(u(`/experiments/${id}`, FOREIGN_PROJECT));
    await expect(page.getByTestId("experiment-not-found").locator('[data-error-kind="not_found"]')).toBeVisible();
    await page.goto(u(`/experiments/${id}/cases/1`, FOREIGN_PROJECT));
    await expect(page.locator('[data-state="error"]')).toBeVisible();
    await api(request, `/api/v1/experiments/${id}`, FOREIGN_PROJECT, 404);
    expect(await experiments(request, FOREIGN_PROJECT)).toEqual([]);
  }
  // A foreign dataset id is refused by the backend for this project (one indistinguishable 404).
  const foreign = await apiPost(request, "/api/v1/datasets", JSON.stringify({ name: `e2e-${RUN}-foreign-ds` }), FOREIGN_PROJECT);
  expect(foreign.status).toBe(201);
  const cross = await apiPost(request, "/api/v1/experiments", JSON.stringify({ dataset_id: foreign.json.id, prompt_version_a: A, prompt_version_b: B, model: "gpt-5-mini", mode: "mock" }));
  expect(cross.status).toBe(404);
  // Invalid ids are neither sent nor echoed.
  await page.goto(u("/experiments/abc"));
  await expect(page.locator('[data-error-kind="invalid_request"]')).toBeVisible();
  expect(await experiments(request)).toEqual(before);
});

test("experiment pages: one h1, labelled controls in tab order, captioned tables, no horizontal scroll on phones", async ({ page, request }) => {
  await page.goto(u("/experiments/new"));
  await page.waitForLoadState("networkidle");
  await expect(page.locator("h1")).toHaveCount(1);
  // Streamed content waits hidden until React reveals it; locator.focus() does not wait for that.
  await expect(page.getByTestId("execution-mode")).toBeVisible();
  if ((await page.getByTestId("experiment-form").count()) === 1) {
    for (const label of ["Golden dataset", "Prompt version A", "Prompt version B", "Model", "Budget (USD)"]) {
      const control = page.getByLabel(label, { exact: true });
      await expect(control).toBeVisible();
      await control.focus();
      await expect(control).toBeFocused();
    }
    await page.getByTestId("experiment-confirm").focus();
    await page.keyboard.press("Tab");
    await expect(page.getByTestId("experiment-submit")).toBeFocused();
  }
  const list = await experiments(request);
  const pages = ["/experiments", "/experiments/new", ...(list.length > 0 ? [`/experiments/${list[0].id}`] : [])];
  for (const width of [390, 820, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    for (const p of pages) {
      await page.goto(u(p));
      await page.waitForLoadState("networkidle");
      await expect(page.locator("h1")).toHaveCount(1);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `${p} at ${width}px`).toBeLessThanOrEqual(0);
      const unlabeled = await page.locator("table:not([hidden])").evaluateAll((ts) => ts.filter((t) => !t.querySelector("caption")?.textContent?.trim()).length);
      expect(unlabeled, p).toBe(0);
    }
  }
  if (list.length > 0) {
    await page.setViewportSize({ width: 390, height: 900 });
    await page.goto(u(`/experiments/${list[0].id}`));
    await shot(page, "phase4d-06-results-mobile");
  }
});
