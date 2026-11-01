/**
 * Phase 6C-C: controlled refresh, Real (asserted) / Synthetic / Unknown, provider and model
 * filters, 429 cooldown and outage behaviour, against the REAL stack: browser → Next.js →
 * FastAPI → PostgreSQL, nothing mocked.
 *
 * Data: the run-owned providers project (scripts/dev/e2e_fixtures.py, `--set phase6c`): openai
 * (ok + failed), gcp.gemini synthetic, gcp.gemini with an asserted `real` claim, a `real` claim
 * without a response id (the server stores it as unknown), and a span without provider, model or
 * claim. `add-trace` sends one more SYNTHETIC trace during a test. No provider is ever called, no
 * provider key exists here, and the real gemini-live project is never read or touched.
 *
 * Timers: `page.clock` fakes the browser clock, so the 30 s interval, the backoff and the 429
 * cooldown are checked without waiting; every refresh is still a real server render.
 */
import { execFileSync } from "node:child_process";
import path from "node:path";
import { expect, test, type APIRequestContext, type Page, type Request } from "@playwright/test";
import { backendPython } from "../setup/e2e-global-setup";
import { API, FOREIGN_PROJECT, PROVIDERS_PROJECT, SHOTS, TOKEN, u } from "./e2e-env";

/* eslint-disable @typescript-eslint/no-explicit-any */
const REPO = path.resolve(__dirname, "..", "..", "..");
const P = PROVIDERS_PROJECT;
const AUTH = { Authorization: `Bearer ${TOKEN}` };
const LIMITED_API = "http://127.0.0.1:8002";
const LIMITED_UI = "http://127.0.0.1:3005";
const OUTAGE_UI = "http://127.0.0.1:3002";
const WRONG_TOKEN_UI = "http://127.0.0.1:3003";

test.describe.configure({ mode: "serial" });

/** A refresh is an RSC request that is not a link prefetch. */
const isRefresh = (r: Request) => r.headers().rsc === "1" && r.headers()["next-router-prefetch"] === undefined;

function countRefreshes(page: Page): { n: number } {
  const c = { n: 0 };
  page.on("request", (r) => {
    if (isRefresh(r)) c.n += 1;
  });
  return c;
}

async function services(page: Page): Promise<string[]> {
  await expect(page.getByTestId("trace-table")).toBeVisible(); // evaluateAll does not wait (R72)
  return (await page.getByTestId("trace-table").getByTestId("trace-link").evaluateAll((els) => els.map((e) => e.querySelector("span")?.textContent ?? ""))).sort();
}

async function api(request: APIRequestContext, pathAndQuery: string): Promise<any> {
  await new Promise((r) => setTimeout(r, 150)); // stay under the shared /api/v1 rate limit
  const sep = pathAndQuery.includes("?") ? "&" : "?";
  const res = await request.get(`${API}${pathAndQuery}${sep}project=${P}`, { headers: AUTH });
  expect(res.status(), pathAndQuery).toBe(200);
  return res.json();
}

function addTrace(): string {
  const state = process.env.LLMI_E2E_STATE;
  expect(state, "the global setup's state file").toBeTruthy();
  const out = execFileSync(backendPython(), [path.join(REPO, "scripts", "dev", "e2e_fixtures.py"), "add-trace", "--state", state!], { cwd: REPO, encoding: "utf8", timeout: 120_000 });
  const id = (JSON.parse(out.trim().split(/\r?\n/).pop() ?? "{}") as { trace?: string }).trace ?? "";
  expect(id).toMatch(/^[0-9a-f]{32}$/);
  return id;
}

async function fetchedAt(page: Page): Promise<string> {
  return (await page.getByTestId("fetched-at").getAttribute("data-fetched-at")) ?? "";
}

async function shot(page: Page, name: string): Promise<void> {
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });
}

// ---------------------------------------------------------------- provenance
test("Real (asserted), Synthetic and Unknown are backend filters over stored source fields only", async ({ page, request }) => {
  await page.goto(u("/traces?provenance=real", P));
  expect(await services(page)).toEqual(["phase6c.gemini-asserted-real"]);
  const badge = page.getByTestId("trace-table").locator('[data-provenance="real"]');
  await expect(badge).toHaveText("Real (asserted)");
  await expect(badge).toHaveAttribute("title", /asserted, not verified/);
  await expect(page.getByRole("link", { name: "Real (asserted) only" })).toHaveAttribute("aria-current", "true");
  await expect(page.locator("main")).not.toContainText(/verified real|provenance verified/i);
  await shot(page, "01-traces-real-asserted");

  // A `real` claim without a response id is stored as unknown, never as real; so is no claim.
  await page.goto(u("/traces?provenance=unknown", P));
  expect(await services(page)).toEqual(["phase6c.claim-downgraded", "phase6c.unlabelled"]);
  await page.goto(u("/traces?provenance=synthetic", P));
  expect(await services(page)).toEqual(["phase6c.gemini", "phase6c.openai", "phase6c.openai-error"]);

  // The page shows exactly what the backend returns for the same filter (not a page-local filter).
  const w = page.getByTestId("window");
  const win = { from: (await w.getAttribute("data-from")) ?? "", to: (await w.getAttribute("data-to")) ?? "" };
  const real = await api(request, `/api/v1/traces?${new URLSearchParams({ ...win, provenance: "real" })}`);
  expect(real.items.map((t: any) => t.provenance_verified)).toEqual([false]);
});

// ---------------------------------------------------------------- provider / model
test("provider and model filters are exact backend filters, combine with provenance, and suggest observed values only", async ({ page, request }) => {
  await page.goto(u("/traces", P));
  const select = page.getByTestId("provider-select");
  const values = await select.locator("option").evaluateAll((els) => els.map((e) => (e as HTMLOptionElement).value));
  expect(values.sort()).toEqual(["", "gcp.gemini", "openai", "unknown"].sort());
  await expect(select.locator('option[value="unknown"]')).toHaveText(/unknown \(no provider name reported\)/);
  await expect(page.getByTestId("scope-note")).toContainText("1 attempt reported no model");

  await select.selectOption("gcp.gemini");
  await page.getByTestId("scope-apply").click();
  await expect(page).toHaveURL(/provider=gcp\.gemini/);
  expect(await services(page)).toEqual(["phase6c.claim-downgraded", "phase6c.gemini", "phase6c.gemini-asserted-real"]);
  await expect(page.getByTestId("provider-select")).toHaveValue("gcp.gemini"); // stable after the round trip

  // Provider + model + provenance: one call must match provider and model; provenance on top.
  await page.getByRole("link", { name: "Real (asserted) only" }).click();
  await expect(page).toHaveURL(/provenance=real/);
  await expect(page).toHaveURL(/provider=gcp\.gemini/);
  expect(await services(page)).toEqual(["phase6c.gemini-asserted-real"]);
  await page.goto(u("/traces?provider=openai&model=gemini-3.1-flash-lite", P));
  await expect(page.locator('[data-state="empty"]')).toContainText("with the selected filters");
  await page.goto(u("/traces?provider=unknown", P));
  expect(await services(page)).toEqual(["phase6c.unlabelled"]);
  await shot(page, "02-traces-provider-unknown");

  // Overview and Models: the same filter, applied by the backend to its aggregates.
  await page.goto(u("/?provider=gcp.gemini", P));
  await expect(page.getByTestId("latest-trace")).toContainText("gcp.gemini");
  const win = { from: (await page.getByTestId("window").getAttribute("data-from")) ?? "", to: (await page.getByTestId("window").getAttribute("data-to")) ?? "" };
  const o = await api(request, `/api/v1/overview?${new URLSearchParams({ ...win, provider: "gcp.gemini" })}`);
  expect(o.filters.provider).toBe("gcp.gemini");
  expect(o.inference.attempts.total).toBe(3);
  await shot(page, "03-overview-provider-gemini");
  await page.goto(u("/models?provider=gcp.gemini", P));
  const table = page.getByTestId("compare-table");
  await expect(table).toContainText("gcp.gemini");
  await expect(table).not.toContainText("openai");
  // Gemini cost is never computed (ADR-35): unknown, never $0.
  await expect(table.locator('[data-cost-state="unknown"]').first()).toBeVisible();
  await expect(table).not.toContainText(/\$0(\.0+)?(?![\d.])/);
  await shot(page, "04-models-provider-gemini");

  // A malformed value is refused and named, never forwarded.
  await page.goto(u("/traces?provider=open%3Aai", P));
  await expect(page.getByTestId("ignored-notice")).toContainText("provider");
});

test("a project switch keeps window and provenance, resets provider and model, and shows only the new project", async ({ page }) => {
  await page.goto(u("/traces?provenance=synthetic&provider=gcp.gemini&model=gemini-3.1-flash-lite", P));
  expect(await services(page)).toEqual(["phase6c.gemini"]);
  await page.getByTestId("project-select").selectOption(FOREIGN_PROJECT);
  await page.getByTestId("project-selector").getByRole("button", { name: "Switch" }).click();
  await expect(page).toHaveURL(new RegExp(`project=${FOREIGN_PROJECT}`));
  await expect(page).toHaveURL(/provenance=synthetic/);
  await expect(page).not.toHaveURL(/provider=|model=/);
  const list = await services(page);
  expect(list.length).toBeGreaterThan(0);
  for (const s of list) expect(s.startsWith("phase6c.")).toBe(false);
  await expect(page.getByTestId("project-current")).toHaveAttribute("data-project-id", FOREIGN_PROJECT);
});

// ---------------------------------------------------------------- controlled refresh
test("manual refresh re-reads; auto-refresh is off until enabled, then a new synthetic trace appears after one controlled refresh", async ({ page }) => {
  await page.clock.install();
  await page.goto(u("/traces", P));
  const control = page.getByTestId("auto-refresh");
  await expect(control.getByRole("combobox")).toHaveValue("off");
  const before = await fetchedAt(page);
  await page.getByTestId("refresh-button").click();
  await expect.poll(() => fetchedAt(page)).not.toBe(before);
  await expect(page.getByTestId("last-success")).toContainText("Last successful load");

  const refreshes = countRefreshes(page);
  const id = addTrace();
  await page.clock.fastForward(5 * 60_000);
  expect(refreshes.n, "no polling while off").toBe(0);
  await expect(page.locator(`[data-testid="trace-link"][data-trace-id="${id}"]`)).toHaveCount(0);

  await control.getByRole("combobox").selectOption("30");
  await expect(page.getByTestId("auto-refresh-status")).toContainText("Re-reads stored telemetry; not live.");
  await page.clock.fastForward(29_000);
  expect(refreshes.n, "nothing before the interval").toBe(0);
  await page.clock.fastForward(2_000);
  await expect(page.getByTestId("trace-table").locator(`[data-testid="trace-link"][data-trace-id="${id}"]`)).toBeVisible();
  expect(refreshes.n).toBe(1);
  await expect(page.getByTestId("provider-select")).toHaveValue(""); // filters untouched by the refresh
  await shot(page, "05-traces-auto-refresh");

  await control.getByRole("combobox").selectOption("off");
  const seen = refreshes.n;
  await page.clock.fastForward(10 * 60_000);
  expect(refreshes.n, "switching off stops it").toBe(seen);
});

test("a refresh refused with 429 keeps the last good data visibly stale; neither the timer nor the button retries before Retry-After", async ({ page, request }) => {
  await page.clock.install();
  await page.goto(`${LIMITED_UI}/traces?project=${P}`);
  expect(await services(page)).toContain("phase6c.gemini");
  // Drain the limited backend's bucket (the dashboard server and this test share 127.0.0.1).
  let retryAfter = 0;
  for (let i = 0; i < 20 && retryAfter === 0; i++) {
    const r = await request.get(`${LIMITED_API}/api/v1/projects`, { headers: AUTH });
    if (r.status() === 429) retryAfter = Number(r.headers()["retry-after"]);
    else expect(r.status()).toBe(200);
  }
  expect(retryAfter).toBeGreaterThan(300);

  await page.getByTestId("refresh-button").click();
  await expect(page.locator('[data-state="error"][data-error-kind="rate_limited"]').first()).toBeVisible();
  await expect(page.getByTestId("stale-notice")).toContainText("Showing earlier data: the latest refresh failed");
  await expect(page.locator('[data-live="stale"]')).toBeVisible();
  await expect(page.getByTestId("stale-data").getByTestId("trace-link").first()).toBeVisible();
  await expect(page.locator('[data-state="empty"]'), "a refused read is never an empty result").toHaveCount(0);
  const button = page.getByTestId("refresh-button");
  await expect(button).toBeDisabled();
  await expect(button).toHaveText(/Retry in \d+ s/);
  await shot(page, "06-traces-429-stale");

  const refreshes = countRefreshes(page);
  await page.getByTestId("auto-refresh").getByRole("combobox").selectOption("30");
  await expect(page.getByTestId("auto-refresh-status")).toContainText("Waiting for the backend rate limit (429) until");
  await button.click({ force: true }); // a disabled button: the click must not send anything
  await page.clock.fastForward(5 * 60_000);
  expect(refreshes.n, "no request before Retry-After has passed").toBe(0);
  await expect(button).toBeDisabled();
});

test("backend unavailable: an error state, never zero data; auto-refresh backs off instead of retrying in a loop", async ({ page }) => {
  await page.clock.install();
  await page.goto(`${OUTAGE_UI}/?project=${P}`);
  await expect(page.locator('[data-state="error"][data-error-kind="unreachable"]')).toBeVisible();
  await expect(page.getByTestId("latest-trace")).toHaveCount(0);
  await expect(page.locator('[data-state="empty"]')).toHaveCount(0);
  const refreshes = countRefreshes(page);
  await page.getByTestId("auto-refresh").getByRole("combobox").selectOption("30");
  await expect(page.getByTestId("auto-refresh-status")).toContainText("The last refresh failed; next try in 60 s.");
  await page.clock.fastForward(58_000);
  expect(refreshes.n, "backed off: not at 30 s").toBe(0);
  await page.clock.fastForward(3_000);
  await expect.poll(() => refreshes.n).toBe(1);
  await expect(page.locator('[data-state="error"][data-error-kind="unreachable"]')).toBeVisible();
  await expect(page.getByTestId("auto-refresh-status")).toContainText("next try in 120 s");
  await shot(page, "07-overview-backend-unavailable");
});

test("rejected credentials stop polling after one attempt instead of repeating it", async ({ page }) => {
  await page.clock.install();
  await page.goto(`${WRONG_TOKEN_UI}/traces?project=${P}`);
  await expect(page.locator('[data-state="error"][data-error-kind="unauthorized"]')).toBeVisible();
  const refreshes = countRefreshes(page);
  await page.getByTestId("auto-refresh").getByRole("combobox").selectOption("30");
  await page.clock.fastForward(31_000);
  await expect.poll(() => refreshes.n).toBe(1);
  await expect(page.getByTestId("auto-refresh-status")).toContainText("Stopped: the backend rejected the dashboard's credentials");
  await expect(page.getByTestId("auto-refresh").getByRole("combobox")).toHaveValue("off");
  await page.clock.fastForward(30 * 60_000);
  expect(refreshes.n).toBe(1);
  await expect(page.locator("[data-live=stale]")).toHaveCount(0);
});
