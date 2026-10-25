/**
 * Live E2E: browser → Next.js (127.0.0.1:3001) → FastAPI backend → PostgreSQL.
 * Every expectation is computed from a direct backend API call for the same window; nothing
 * here is a mocked response. Pages are opened for this run's project (`?project=`, created
 * and seeded by the global setup, R41).
 */
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import http from "node:http";
import path from "node:path";
import { formatUsd } from "../../lib/decimal";
import { formatInt, formatMs, formatPercent } from "../../lib/format";
import { API, PROJECT, SHOTS, TOKEN, u } from "./e2e-env";

/* eslint-disable @typescript-eslint/no-explicit-any */
async function api(request: APIRequestContext, endpoint: "overview" | "models/compare", window: { from: string; to: string }, extra: Record<string, string> = {}): Promise<any> {
  const params = new URLSearchParams({ project: PROJECT, from: window.from, to: window.to, ...extra });
  const res = await request.get(`${API}/api/v1/${endpoint}?${params}`, { headers: { Authorization: `Bearer ${TOKEN}` } });
  expect(res.status()).toBe(200);
  return res.json();
}

async function pageWindow(page: Page): Promise<{ from: string; to: string }> {
  const el = page.getByTestId("window");
  return { from: (await el.getAttribute("data-from")) ?? "", to: (await el.getAttribute("data-to")) ?? "" };
}

async function shot(page: Page, name: string): Promise<void> {
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });
}

test.beforeAll(() => {
  expect(TOKEN.length, "frontend/.env.local must hold the server-side token").toBeGreaterThan(31);
});

test("overview renders the values the backend API returns for the same window", async ({ page, request }) => {
  await page.goto(u("/"));
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
  await expect(page.getByTestId("backend-status")).toHaveAttribute("data-health", "ready");
  const window = await pageWindow(page);
  const o = await api(request, "overview", window);

  if (o.traces.count.total === 0 && o.inference.attempts.total === 0) {
    await expect(page.getByText("No telemetry in this window")).toBeVisible();
    await shot(page, "overview-empty");
    return;
  }

  await expect(page.getByTestId("traces-value")).toHaveText(formatInt(o.traces.count.total));
  await expect(page.getByTestId("inference-attempts-value")).toHaveText(formatInt(o.inference.attempts.total));
  const infRate = formatPercent(o.inference.errors.total.rate);
  if (infRate) await expect(page.getByTestId("inference-error-rate-value")).toHaveText(infRate);
  const traceRate = formatPercent(o.traces.error_rate.total.rate);
  if (traceRate) await expect(page.getByTestId("trace-error-rate-value")).toHaveText(traceRate);
  const p95 = formatMs(o.inference.latency.total.p95_ms);
  if (p95) await expect(page.getByTestId("inference-latency-value")).toHaveText(p95);

  // Cost: a known value is shown exactly as the backend's decimal string; unknown stays unknown.
  const cost = o.inference.cost.total;
  const costValue = page.getByTestId("known-cost-value");
  if (cost.known_cost_usd === null) {
    await expect(costValue).toHaveAttribute("data-state", "unknown");
    await expect(costValue).not.toContainText("$0");
  } else {
    await expect(costValue).toHaveText(formatUsd(cost.known_cost_usd)!.text);
  }
  if (cost.unknown_cost_calls > 0) {
    await expect(page.locator('[data-metric="known-cost"]')).toContainText(`Incomplete: ${formatInt(cost.unknown_cost_calls)} of ${formatInt(cost.n)} unpriced`);
  }

  // Trend: the table view lists exactly the backend buckets with the same counts.
  const attemptsTable = page.getByTestId("table-attempts");
  await page.locator('[data-chart="chart-attempts"] summary').click();
  const rows = attemptsTable.locator("tbody tr");
  await expect(rows).toHaveCount(o.trend.items.length);
  for (const [i, item] of o.trend.items.entries()) {
    const cells = rows.nth(i).locator("td");
    await expect(cells.nth(0)).toHaveText(formatInt(item.inference_attempts));
    await expect(cells.nth(1)).toHaveText(formatInt(item.failed_attempts));
  }
  await page.locator('[data-chart="chart-cost"] summary').click();
  const costRows = page.getByTestId("table-cost").locator("tbody tr");
  for (const [i, item] of o.trend.items.entries()) {
    const cell = costRows.nth(i).locator("td").first();
    if (item.known_cost_usd === null) await expect(cell).toContainText("none computed");
    else await expect(cell).toHaveText(formatUsd(item.known_cost_usd, 10)!.exact);
  }
  // The chart itself rendered bars (SVG) in the browser.
  await expect(page.locator('[data-chart="chart-attempts"] svg.recharts-surface')).toBeVisible();

  // Model groups: one row per backend group.
  const cmp = await api(request, "models/compare", window, { limit: "10" });
  await expect(page.getByTestId("table-model-groups").locator("tbody tr")).toHaveCount(Math.max(1, cmp.groups.length));
});

test("synthetic telemetry is labeled and never presented as real (T48)", async ({ page, request }) => {
  await page.goto(u("/"));
  const o = await api(request, "overview", await pageWindow(page));
  const t = o.traces.count.by_provenance;
  if (o.traces.count.total === 0) test.skip(true, "no telemetry in the window");
  const notice = page.getByTestId("data-notice");
  await expect(notice).toBeVisible();
  if (t.synthetic > 0 && t.real === 0 && t.mixed === 0 && t.unknown === 0) {
    await expect(notice).toHaveAttribute("data-notice", "synthetic_only");
    await expect(notice).toContainText("Synthetic telemetry only");
  }
  await expect(notice).toContainText("all provenance");
  await expect(notice).toContainText("not verified");
  for (const p of ["real", "synthetic", "mixed", "unknown"]) {
    await expect(notice.locator(`[data-provenance="${p}"]`)).toContainText(formatInt(t[p]));
  }
  await expect(page.getByTestId("table-provenance")).toContainText("All provenance");
});

test("time range and provenance controls re-run the backend queries", async ({ page, request }) => {
  await page.goto(u("/"));
  const before = await pageWindow(page);
  await page.getByRole("link", { name: "Last 7 days" }).click();
  await expect(page).toHaveURL(new RegExp(`\\?project=${PROJECT}&range=7d$`));
  await expect(page.getByRole("link", { name: "Last 7 days" })).toHaveAttribute("aria-current", "true");
  const after = await pageWindow(page);
  expect(after.from).not.toBe(before.from);
  expect(Date.parse(after.to) - Date.parse(after.from)).toBe(7 * 24 * 3_600_000);
  expect(after.to).toMatch(/T00:00:00\.000000Z$/); // daily buckets end at a UTC midnight
  const o = await api(request, "overview", after);
  expect(o.trend.bucket).toBe("day");
  if (o.traces.count.total > 0) await expect(page.getByTestId("traces-value")).toHaveText(formatInt(o.traces.count.total));

  await page.getByRole("link", { name: "Synthetic only" }).click();
  await expect(page).toHaveURL(new RegExp(`\\?project=${PROJECT}&range=7d&provenance=synthetic$`));
  const filtered = await api(request, "overview", await pageWindow(page), { provenance: "synthetic" });
  expect(filtered.provenance_label).toBe("synthetic only");
  if (filtered.traces.count.total > 0) {
    await expect(page.getByTestId("data-notice")).toContainText("synthetic only");
    await expect(page.getByTestId("traces-value")).toHaveText(formatInt(filtered.traces.count.total));
  }

  await page.getByRole("link", { name: "Real only" }).click();
  await expect(page).toHaveURL(/provenance=real$/);
  const real = await api(request, "overview", await pageWindow(page), { provenance: "real" });
  if (real.traces.count.total === 0 && real.inference.attempts.total === 0) {
    await expect(page.getByText("No telemetry in this window")).toBeVisible();
    await expect(page.getByText(/with provenance “real”/)).toBeVisible();
  }
});

test("loads without console errors, CSP violations or hydration warnings", async ({ page }) => {
  const problems: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error" || m.type() === "warning") problems.push(`${m.type()}: ${m.text().slice(0, 200)}`);
  });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message.slice(0, 200)}`));
  await page.goto(u("/"));
  await page.waitForLoadState("networkidle");
  await page.getByRole("link", { name: "Last 6 hours" }).click();
  await expect(page).toHaveURL(/range=6h/);
  await page.getByRole("button", { name: /Refresh/ }).click();
  await page.waitForLoadState("networkidle");
  // Hover a chart so the client-side tooltip renders too.
  await page.locator('[data-chart="chart-attempts"] svg.recharts-surface').hover();
  expect(problems).toEqual([]);
});

test("unsupported query values fall back to defaults with a notice", async ({ page }) => {
  await page.goto(u("/?range=999d&provenance=%3Cscript%3E"));
  await expect(page.getByText("Unsupported filter value ignored")).toBeVisible();
  await expect(page.getByRole("link", { name: "Last 24 hours" })).toHaveAttribute("aria-current", "true");
});

test("no credential reaches the browser (T42, live half)", async ({ page }) => {
  const bodies: string[] = [];
  page.on("response", async (res) => {
    try {
      bodies.push(await res.text());
    } catch {
      /* redirects and aborted responses have no body */
    }
  });
  await page.goto(u("/"));
  await page.getByRole("link", { name: "Last 7 days" }).click();
  await expect(page).toHaveURL(/range=7d/);
  await page.waitForLoadState("networkidle");
  expect(bodies.length).toBeGreaterThan(3);
  const leaks = bodies.filter((b) => b.includes(TOKEN) || /LLMI_API_TOKEN|LLMI_ADMIN_TOKEN|Bearer\s+[A-Za-z0-9]/.test(b));
  expect(leaks.length).toBe(0);
  const storage = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }) + document.cookie);
  expect(storage.includes(TOKEN)).toBe(false);
});

function rawRequest(method: string, host: string): Promise<number> {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: "127.0.0.1", port: 3001, path: "/", method, headers: { Host: host } }, (res) => {
      res.resume();
      resolve(res.statusCode ?? 0);
    });
    req.on("error", reject);
    req.end();
  });
}

test("local-only guard: foreign Host header and non-GET methods are refused", async () => {
  expect(await rawRequest("GET", "evil.example:3001")).toBe(421);
  expect(await rawRequest("GET", "127.0.0.1.nip.io:3001")).toBe(421);
  expect(await rawRequest("POST", "127.0.0.1:3001")).toBe(405);
  expect(await rawRequest("GET", "127.0.0.1:3001")).toBe(200);
  expect(await rawRequest("GET", "localhost:3001")).toBe(200);
});

test("backend outage produces a useful error state, not fake data", async ({ page }) => {
  await page.goto("http://127.0.0.1:3002/");
  // Next.js's route announcer also has role="alert"; select the error panel itself.
  const alert = page.getByRole("alert").filter({ has: page.locator("p") }).and(page.locator('[data-state="error"]'));
  await expect(alert).toHaveAttribute("data-error-kind", "unreachable");
  await expect(alert).toContainText("Backend unreachable");
  await expect(alert).toContainText("python -m app");
  await expect(page.getByTestId("backend-status")).toHaveAttribute("data-health", "unreachable");
  await expect(page.getByTestId("kpi-grid")).toHaveCount(0);
  await shot(page, "overview-backend-unreachable");
});

test("accessible navigation and controls", async ({ page }) => {
  await page.goto(u("/"));
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to content" });
  await expect(skip).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#main$/);
  await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("group", { name: "Time range" })).toBeVisible();
  await expect(page.getByRole("group", { name: "Provenance" })).toBeVisible();
  await expect(page.getByRole("main")).toBeVisible();
  await expect(page.locator("h1")).toHaveCount(1);
  // React's streaming SSR leaves empty `<table hidden>` placeholders; only rendered tables count.
  const tables = page.locator("table:not([hidden])");
  expect(await tables.count()).toBeGreaterThan(0);
  const unlabeledTables = await tables.evaluateAll((ts) => ts.filter((t) => !t.querySelector("caption")?.textContent?.trim()).length);
  expect(unlabeledTables).toBe(0);
});

for (const [name, viewport] of [
  ["desktop", { width: 1440, height: 900 }],
  ["tablet", { width: 834, height: 1112 }],
  ["mobile", { width: 390, height: 844 }],
] as const) {
  test.describe(`viewport ${name}`, () => {
    // Set at context creation, like a real device; resizing an open page measures the old layout.
    test.use({ viewport });
  test(`responsive layout without horizontal page scroll: ${name}`, async ({ page }) => {
    // Sample the page width repeatedly from first paint, so a transient overflow during load
    // (e.g. a chart rendering before it measures its container) also fails the test.
    await page.addInitScript(() => {
      const w = window as unknown as { __maxOverflow: number; __offenders: string[] };
      w.__maxOverflow = 0;
      w.__offenders = [];
      const clipped = (n: Element) => {
        for (let a = n.parentElement; a; a = a.parentElement) if (getComputedStyle(a).overflowX !== "visible") return true;
        return false;
      };
      const sample = () => {
        const el = document.documentElement;
        if (el) {
          const over = el.scrollWidth - window.innerWidth;
          if (over > w.__maxOverflow) {
            w.__maxOverflow = over;
            w.__offenders = [...document.querySelectorAll("body *")]
              .filter((n) => n.getBoundingClientRect().right > window.innerWidth + 1 && !clipped(n))
              .slice(0, 5)
              .map((n) => {
                const parent = n.parentElement;
                const ps = parent ? getComputedStyle(parent) : null;
                const extra = n.tagName === "TABLE" ? ` caption="${n.querySelector("caption")?.textContent ?? ""}" parent=${parent?.tagName}.${String(parent?.className).slice(0, 50)} pov=${ps?.overflowX} ppos=${ps?.position} pw=${Math.round(parent?.getBoundingClientRect().width ?? 0)}` : "";
                return `${n.tagName}.${String(n.className).slice(0, 60)} w=${Math.round(n.getBoundingClientRect().width)}${extra}`;
              });
            w.__offenders.push(`t=${Math.round(performance.now())} sw=${el.scrollWidth} iw=${window.innerWidth} cw=${el.clientWidth} body=${document.body?.childElementCount ?? "none"} ready=${document.readyState} url=${location.href}`);
          }
        }
        requestAnimationFrame(sample);
      };
      requestAnimationFrame(sample);
    });
    await page.goto(u("/"));
    await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
    await page.waitForLoadState("networkidle");
    const { overflow, offenders } = await page.evaluate(() => {
      const w = window as unknown as { __maxOverflow: number; __offenders: string[] };
      return { overflow: w.__maxOverflow, offenders: w.__offenders };
    });
    expect(overflow, `widest moment: ${offenders.join(" | ")}`).toBeLessThanOrEqual(0);
    await shot(page, `overview-${name}`);
  });
  });
}
