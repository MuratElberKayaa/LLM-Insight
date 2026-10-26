/**
 * Live E2E for Phase 4B: browser → Next.js Server Actions → FastAPI → PostgreSQL. A human
 * records a failure review on a real (SYNTHETIC, run-owned) failing trace, records a decision,
 * creates a dataset and approves a test case; every step is checked against the backend API.
 * Nothing is mocked. The global setup creates this run's projects and the teardown deletes
 * them (R41), which removes their reviews, datasets and test cases with them.
 */
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import path from "node:path";
import { API, FOREIGN_PROJECT, PROJECT, SHOTS, TOKEN, u } from "./e2e-env";

/* eslint-disable @typescript-eslint/no-explicit-any */
const RUN = process.env.LLMI_E2E_RUN ?? "local";
const EMAIL = "jane.roe@example.invalid";
const PHONE = "+90 532 123 45 67";
const XSS = `<script>window.__llmiXss=1</script><img src=x onerror="window.__llmiXss=1">`;

async function api(request: APIRequestContext, pathAndQuery: string, project = PROJECT): Promise<any> {
  await new Promise((r) => setTimeout(r, 150)); // stay under the shared /api/v1 rate limit
  const sep = pathAndQuery.includes("?") ? "&" : "?";
  const res = await request.get(`${API}${pathAndQuery}${sep}project=${project}`, { headers: { Authorization: `Bearer ${TOKEN}` } });
  expect(res.status(), pathAndQuery).toBe(200);
  return res.json();
}

/** A failing trace of this run's main project (the seeded edge set has several). */
async function failingTrace(request: APIRequestContext, skip: string[] = []): Promise<string> {
  const list = await api(request, "/api/v1/traces?status=error&limit=200");
  const t = list.items.find((x: any) => !skip.includes(x.trace_id) && x.reviews.total === 0);
  expect(t, "a failing, unreviewed trace seeded by the global setup").toBeTruthy();
  return t.trace_id as string;
}

async function reviews(request: APIRequestContext, traceId: string): Promise<any[]> {
  return (await api(request, `/api/v1/traces/${traceId}`)).reviews;
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

test.beforeAll(() => {
  expect(TOKEN.length, "frontend/.env.local must hold the server-side token").toBeGreaterThan(31);
});

test("human review → decision → dataset → approved test case, end to end", async ({ page, request }) => {
  test.setTimeout(120_000);
  const { problems, bodies } = watch(page);
  const traceId = await failingTrace(request);

  // 1. The trace is not reviewed; diagnostics are shown apart, labelled, and hold no form.
  await page.goto(u(`/traces/${traceId}`));
  await expect(page.getByTestId("review-state")).toContainText("not reviewed yet");
  const machine = page.getByTestId("diagnostics-panel");
  await expect(machine.getByTestId("diagnostics-label")).toHaveText("Automatic · Unverified · Not a human review");
  await expect(machine.locator("form, select, textarea, input")).toHaveCount(0);
  const human = page.getByTestId("human-review-section");
  await expect(human.getByText("Human decision", { exact: true })).toBeVisible();
  await expect(human.getByTestId("reviews-empty")).toBeVisible();
  await expect(page.getByLabel("Issue type")).toHaveValue(""); // nothing preselected or copied from diagnostics
  await expect(page.getByLabel("Evidence")).toHaveValue("");
  await expect(page.getByLabel("Hypothesis")).toHaveValue("");
  expect(await reviews(request, traceId)).toEqual([]); // opening the page wrote nothing
  await shot(page, "phase4b-01-trace-unreviewed");

  // 2. Refused input keeps what was typed; nothing is stored.
  await page.getByLabel("Evidence").fill(`SYNTHETIC evidence ${XSS} contact ${EMAIL}`);
  await page.getByLabel("Hypothesis").fill(`SYNTHETIC hypothesis: caller ${PHONE} retried`);
  await page.getByLabel("Reviewer", { exact: true }).fill(`e2e-${RUN}`);
  await page.getByTestId("review-submit").click();
  await expect(page.getByTestId("review-form-result")).toContainText("Choose one of the listed issue types");
  await expect(page.getByLabel("Evidence")).toHaveValue(new RegExp("SYNTHETIC evidence"));
  expect(await reviews(request, traceId)).toEqual([]);

  // 3. Keyboard-only submission: choose the issue type, then Enter in the reviewer field.
  await page.getByLabel("Issue type").focus();
  await page.getByLabel("Issue type").selectOption("schema_violation");
  await page.getByLabel("Reviewer", { exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("review-form-result")).toHaveAttribute("data-outcome", "success");
  await expect(page.getByTestId("review-form-result")).toContainText("not a verified root cause");
  await expect(page.getByTestId("review-form-result-redaction")).toContainText("1 email value in evidence");
  await expect(page.getByTestId("review-form-result-redaction")).toContainText("1 phone value in hypothesis");
  const card = page.getByTestId("review-card");
  await expect(card).toHaveCount(1);
  await expect(card.getByTestId("review-evidence-text")).toContainText("<script>window.__llmiXss=1</script>"); // shown as text
  await expect(card.getByTestId("review-evidence-text")).toContainText("[REDACTED:email]");
  await expect(card.getByTestId("review-evidence-text")).not.toContainText(EMAIL);
  expect(await page.evaluate(() => (window as unknown as { __llmiXss?: number }).__llmiXss)).toBeUndefined();
  let stored = await reviews(request, traceId);
  expect(stored).toHaveLength(1);
  expect(stored[0]).toMatchObject({ issue_type: "schema_violation", review_status: "open", reviewer_decision: "pending", created_by: `e2e-${RUN}` });
  expect(stored[0].evidence).not.toContain(EMAIL);
  expect(stored[0].hypothesis).toContain("[REDACTED:phone]");
  await expect(page.getByTestId("review-state")).toContainText("1 review (1 not closed)");
  await expect(card.getByTestId("convert-unavailable")).toBeVisible();

  // 4. A duplicate open review of the same issue type is refused (409), not stored twice.
  await page.getByTestId("new-review").locator("summary").click();
  await page.getByLabel("Issue type").selectOption("schema_violation");
  await page.getByLabel("Evidence").fill("SYNTHETIC second evidence");
  await page.getByLabel("Hypothesis").fill("SYNTHETIC second hypothesis");
  await page.getByLabel("Reviewer", { exact: true }).fill(`e2e-${RUN}`);
  await page.getByTestId("review-submit").click();
  await expect(page.getByTestId("review-form-result")).toContainText("already has a review of this issue type");
  expect(await reviews(request, traceId)).toHaveLength(1);

  // 5. Record the decision (PATCH); conversion becomes available.
  await card.getByTestId("decision-status").selectOption("in_review");
  await card.getByTestId("decision-decision").selectOption("hypothesis_supported");
  await card.getByTestId("decision-submit").click();
  await expect(page.getByTestId(`decision-result-${stored[0].id}`)).toHaveAttribute("data-outcome", "success");
  await expect(card.getByTestId("review-decision")).toHaveText("Hypothesis supported by the evidence");
  stored = await reviews(request, traceId);
  expect(stored[0]).toMatchObject({ review_status: "in_review", reviewer_decision: "hypothesis_supported" });
  await shot(page, "phase4b-02-review-recorded");

  // 6. Conversion page: nothing written on load; create a dataset; explicit approval required.
  await card.getByTestId("convert-link").click();
  await expect(page).toHaveURL(new RegExp(`/traces/${traceId}/reviews/${stored[0].id}/test-case`));
  await expect(page.getByTestId("no-content-notice")).toContainText("cannot be shown or recovered");
  const datasetName = `e2e-${RUN}-regression`;
  await page.getByLabel("New dataset name").fill(datasetName);
  await page.getByTestId("dataset-submit").click();
  await expect(page.getByTestId("dataset-form-result")).toHaveAttribute("data-outcome", "success");
  const datasets = await api(request, "/api/v1/datasets");
  const dataset = datasets.items.find((d: any) => d.name === datasetName);
  expect(dataset).toBeTruthy();
  await expect(page.getByLabel("Dataset", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Test input")).toHaveValue(""); // never prefilled from telemetry

  const fill = async () => {
    await page.getByLabel("Dataset", { exact: true }).selectOption(String(dataset.id));
    await page.getByLabel("Test input").fill(`SYNTHETIC: summarize the note "v2 adds dark mode"; caller ${PHONE}`);
    await page.getByLabel("How the output will be checked").selectOption("json_schema");
    await page.getByLabel("JSON Schema").fill('{"type":"object","required":["summary"],"properties":{"summary":{"type":"string"}}}');
    await page.getByLabel("Expected output (JSON)").fill('{"summary":"SYNTHETIC: v2 adds dark mode"}');
    await page.getByLabel("Approved by").fill(`e2e-${RUN}`);
  };
  await fill();
  await page.getByTestId("case-submit").click(); // approval box not ticked
  await expect(page.getByTestId("case-form-result")).toContainText("Tick the approval box");
  expect((await api(request, `/api/v1/datasets/${dataset.id}/cases`)).items).toEqual([]);

  await page.getByLabel(/I approve storing this test case/).check();
  await page.getByTestId("case-submit").click();
  await expect(page.getByTestId("case-form-result")).toHaveAttribute("data-outcome", "success");
  await expect(page.getByTestId("case-form-result")).toContainText("has not been evaluated");
  await expect(page.getByTestId("case-form-result-redaction")).toContainText("1 phone value in sanitized input");
  let cases = (await api(request, `/api/v1/datasets/${dataset.id}/cases`)).items;
  expect(cases).toHaveLength(1);
  expect(cases[0]).toMatchObject({ origin_trace_id: traceId, origin_review_id: stored[0].id, redaction_applied: true, approved_by: `e2e-${RUN}` });
  expect(cases[0].sanitized_input).toContain("[REDACTED:phone]");
  // Phase 4C: the form sends rule contract llminsight.rule.v1.
  expect(cases[0].rule).toEqual({ version: 1, type: "json_schema", schema: { type: "object", required: ["summary"], properties: { summary: { type: "string" } } } });
  await shot(page, "phase4b-03-test-case-approved");

  // 7. A second conversion of the same review into the same dataset is refused.
  await fill();
  await page.getByLabel(/I approve storing this test case/).check();
  await page.getByTestId("case-submit").click();
  await expect(page.getByTestId("case-form-result")).toContainText("already has a test case in this dataset");
  cases = (await api(request, `/api/v1/datasets/${dataset.id}/cases`)).items;
  expect(cases).toHaveLength(1);

  // 8. Dataset page shows provenance and the stored (redacted) content; the list tells the
  //    reviewed trace apart; the trace links its test case.
  await page.goto(u(`/datasets/${dataset.id}`));
  const tc = page.getByTestId("test-case");
  await expect(tc).toHaveCount(1);
  await expect(tc.getByTestId("case-origin-trace")).toHaveAttribute("href", u(`/traces/${traceId}`));
  await expect(tc.getByTestId("case-input-text")).toContainText("[REDACTED:phone]");
  await expect(tc).toContainText("Evaluated only by experiments");
  await shot(page, "phase4b-04-dataset");
  await page.goto(u("/traces?status=error&limit=100"));
  await expect(page.getByTestId("trace-table").locator("tr", { has: page.locator(`[data-trace-id="${traceId}"]`) }).getByTestId("review-cell")).toHaveText("1 review · 1 not closed");
  await page.goto(u(`/traces/${traceId}`));
  await expect(page.getByTestId("review-test-cases")).toContainText(datasetName);
  // Phase 4C (ADR-24): the cited review's decision is frozen; only its status stays editable.
  await expect(page.getByTestId("decision-frozen")).toContainText("frozen: a test case cites this review");
  await expect(page.getByTestId("decision-decision")).toHaveCount(0);

  // 9. No credential in any response; no console errors, CSP violations or dialogs.
  const leaks = bodies.filter((b) => b.includes(TOKEN) || /LLMI_API_TOKEN|LLMI_ADMIN_TOKEN|Bearer\s+[A-Za-z0-9]/.test(b));
  expect(leaks).toEqual([]);
  expect(problems).toEqual([]);
});

test("CSRF: cross-site, origin-less and header-less POSTs are refused before any action runs", async ({ page, request }) => {
  const traceId = await failingTrace(request);
  await page.goto(u(`/traces/${traceId}`)); // obtain nothing but a valid page; actions need their id
  const target = `http://127.0.0.1:3001/traces/${traceId}?project=${PROJECT}`;
  const body = "1_project=1&1_trace_id=x";
  const attempts: [Record<string, string>, number][] = [
    [{ Origin: "https://evil.example", "Next-Action": "7f3a9c0d1e2b4a5f6789abcdef0123456789abcd", "Sec-Fetch-Site": "cross-site" }, 403],
    [{ Origin: "https://evil.example", "Next-Action": "7f3a9c0d1e2b4a5f6789abcdef0123456789abcd" }, 403],
    [{ "Next-Action": "7f3a9c0d1e2b4a5f6789abcdef0123456789abcd" }, 403], // no Origin
    [{ Origin: "http://127.0.0.1:3001" }, 403], // a plain form post, not a Server Action
    [{ Origin: "http://127.0.0.1:3001", "Next-Action": "7f3a9c0d1e2b4a5f6789abcdef0123456789abcd", "Sec-Fetch-Site": "same-site" }, 403],
  ];
  for (const [headers, status] of attempts) {
    const res = await request.post(target, { headers: { "Content-Type": "application/x-www-form-urlencoded", ...headers }, data: body, maxRedirects: 0 });
    expect(res.status(), JSON.stringify(headers)).toBe(status);
    expect(await res.text()).toMatch(/^Forbidden: /);
  }
  const elsewhere = await request.post(`http://127.0.0.1:3001/traces?project=${PROJECT}`, { headers: { Origin: "http://127.0.0.1:3001" }, data: body });
  expect(elsewhere.status()).toBe(405);
  const del = await request.delete(target, { headers: { Origin: "http://127.0.0.1:3001" } });
  expect(del.status()).toBe(405);
  expect(await reviews(request, traceId)).toEqual([]);
});

test("project isolation: another project's trace cannot be reviewed or converted from here", async ({ page, request }) => {
  const foreign = await api(request, "/api/v1/traces?limit=200", FOREIGN_PROJECT);
  const foreignTrace = foreign.items[0].trace_id as string;
  await page.goto(u(`/traces/${foreignTrace}`));
  await expect(page.getByTestId("trace-not-found")).toBeVisible();
  await expect(page.getByTestId("review-form")).toHaveCount(0);
  await page.goto(u(`/traces/${foreignTrace}/reviews/1/test-case`));
  await expect(page.locator('[data-state="error"]')).toBeVisible();
  await expect(page.getByTestId("test-case-form")).toHaveCount(0);
  expect((await api(request, `/api/v1/traces/${foreignTrace}`, FOREIGN_PROJECT)).reviews).toEqual([]);
});

test("Phase 4B pages: keyboard reachable controls with names, one h1, captioned tables", async ({ page, request }) => {
  const traceId = await failingTrace(request);
  await page.goto(u(`/traces/${traceId}`));
  await page.waitForLoadState("networkidle"); // hydration done: focus is not reset under the test
  await expect(page.locator("h1")).toHaveCount(1);
  for (const label of ["Issue type", "Related span", "Evidence", "Hypothesis", "Reviewer"]) {
    const control = page.getByLabel(label, { exact: true });
    // Phase 4D: streamed content waits in a hidden container until React reveals it, and
    // locator.focus() does not wait for visibility (a hidden control cannot take focus).
    await expect(control).toBeVisible();
    await control.focus();
    await expect(control).toBeFocused();
  }
  // Tab order runs through the form in document order and reaches the submit button.
  await page.getByLabel("Reviewer", { exact: true }).focus();
  await page.keyboard.press("Tab");
  await expect(page.getByTestId("review-submit")).toBeFocused();
  await page.goto(u("/datasets"));
  await expect(page.locator("h1")).toHaveCount(1);
  await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Datasets" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByLabel("New dataset name")).toBeVisible(); // revealed, not the hidden streamed copy
  await page.getByLabel("New dataset name").focus();
  await page.keyboard.press("Tab");
  await expect(page.getByTestId("dataset-submit")).toBeFocused();
  const unlabeled = await page.locator("table:not([hidden])").evaluateAll((ts) => ts.filter((t) => !t.querySelector("caption")?.textContent?.trim()).length);
  expect(unlabeled).toBe(0);
});
