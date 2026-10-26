/**
 * Phase 5A: the dashboard half of T43 (request rate limit), against a REAL backend.
 *
 * playwright.config.ts starts a second backend on 127.0.0.1:8001 (same database, same token)
 * whose `/api/v1` bucket holds 5 tokens and refills one every 1000 s, and a dashboard on
 * 127.0.0.1:3004 that reads through it. Both buckets are keyed by client address, and the
 * dashboard server and this test both call from 127.0.0.1, so the test can drain the bucket
 * the dashboard uses. The main backend (:8000) has its own bucket and is used here only to
 * check what was stored. The backend half of T43 is
 * backend/tests/integration/test_phase5a_rate_limit.py.
 */
import { expect, test, type APIRequestContext } from "@playwright/test";
import { API, PROJECT, TOKEN } from "./e2e-env";

const LIMITED_API = "http://127.0.0.1:8001";
const LIMITED_UI = "http://127.0.0.1:3004";
const RUN = process.env.LLMI_E2E_RUN ?? "run";
const AUTH = { Authorization: `Bearer ${TOKEN}` };

test.describe.configure({ mode: "serial" });

/** Take tokens from the limited backend's bucket until it answers 429. */
async function drain(request: APIRequestContext): Promise<number> {
  for (let i = 0; i < 20; i++) {
    const r = await request.get(`${LIMITED_API}/api/v1/projects`, { headers: AUTH });
    if (r.status() === 429) {
      expect(await r.json()).toEqual({ error: { code: "rate_limited", message: "too many requests; retry later" } });
      return Number(r.headers()["retry-after"]);
    }
    expect(r.status()).toBe(200);
  }
  throw new Error("the limited backend never answered 429");
}

async function datasetNames(request: APIRequestContext): Promise<string[]> {
  const r = await request.get(`${API}/api/v1/datasets?project=${PROJECT}`, { headers: AUTH });
  expect(r.status()).toBe(200);
  return ((await r.json()).items as { name: string }[]).map((d) => d.name);
}

test("a write refused by the /api/v1 rate limit says nothing was stored, and nothing is (T43)", async ({ page, request }) => {
  const name = `e2e-${RUN}-t43-refused`;
  await page.goto(`${LIMITED_UI}/datasets?project=${PROJECT}`);
  await expect(page.getByTestId("dataset-form")).toBeVisible();
  await page.getByTestId("dataset-name").fill(name);

  const retryAfter = await drain(request);
  expect(retryAfter).toBeGreaterThanOrEqual(1);
  await page.getByTestId("dataset-submit").click();
  const result = page.getByTestId("dataset-form-result");
  await expect(result).toHaveAttribute("data-outcome", "error");
  await expect(result).toHaveText("Too many requests in a short time. Nothing was stored; retry shortly.");
  expect(await datasetNames(request)).not.toContain(name);
});

test("rate-limited reads show the fixed 429 state with the backend's Retry-After, never a token or body (T43)", async ({ page, request }) => {
  await drain(request);
  for (const p of ["/", "/traces", "/datasets", "/experiments"]) {
    await page.goto(`${LIMITED_UI}${p}?project=${PROJECT}`);
    const state = page.locator('[data-state="error"][data-error-kind="rate_limited"]').first();
    await expect(state, p).toBeVisible();
    await expect(state, p).toContainText("Backend rate limit reached");
    await expect(state, p).toContainText("Too many API requests in a short time (429).");
    await expect(state, p).toContainText(/Retry after \d{1,4} s\./);
    const html = await page.content();
    expect(html, p).not.toContain(TOKEN);
    expect(html, p).not.toContain("too many requests; retry later"); // the backend's own body
    // No page falls back to another state or invents data while the backend refuses.
    await expect(page.locator('[data-state="error"]:not([data-error-kind="rate_limited"])'), p).toHaveCount(0);
  }
});
