/**
 * Live E2E for Phase 3C: model comparison, project selector and isolation, R40 trace models,
 * custom UTC ranges, auto-refresh and hostile inputs. Browser → Next.js → FastAPI →
 * PostgreSQL with this run's SYNTHETIC projects (global setup); expectations come from direct
 * backend API calls, nothing is mocked.
 */
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import path from "node:path";
import { formatUsd } from "../../lib/decimal";
import { formatInt } from "../../lib/format";
import { API, FOREIGN_PROJECT, HOSTILE_PROJECT, PROJECT, SHOTS, TOKEN, u } from "./e2e-env";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Win = { from: string; to: string };

async function api(request: APIRequestContext, pathAndQuery: string, project: string | null = PROJECT): Promise<any> {
  await new Promise((r) => setTimeout(r, 150)); // paced under the shared /api/v1 rate limit
  const sep = pathAndQuery.includes("?") ? "&" : "?";
  const url = project === null ? `${API}${pathAndQuery}` : `${API}${pathAndQuery}${sep}project=${project}`;
  const res = await request.get(url, { headers: { Authorization: `Bearer ${TOKEN}` } });
  expect(res.status(), pathAndQuery).toBe(200);
  return res.json();
}

async function pageWindow(page: Page): Promise<Win> {
  const el = page.getByTestId("window");
  return { from: (await el.getAttribute("data-from")) ?? "", to: (await el.getAttribute("data-to")) ?? "" };
}

async function shot(page: Page, name: string): Promise<void> {
  await expect(page.locator('[role="status"][aria-label^="Loading"]')).toHaveCount(0);
  await page.waitForLoadState("networkidle");
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });
}

function noXss(page: Page) {
  return page.evaluate(() => (window as unknown as { __llmiXss?: number }).__llmiXss);
}

test.beforeAll(() => {
  expect(TOKEN.length, "frontend/.env.local must hold the server-side token").toBeGreaterThan(31);
  expect(new Set([PROJECT, FOREIGN_PROJECT, HOSTILE_PROJECT]).size).toBe(3);
});

// ---------------------------------------------------------------- model comparison
test("model comparison shows the backend groups exactly, in backend order", async ({ page, request }) => {
  await page.goto(u("/models?range=31d"));
  await expect(page.getByRole("heading", { level: 1, name: "Model comparison" })).toBeVisible();
  const w = await pageWindow(page);
  const data = await api(request, `/api/v1/models/compare?${new URLSearchParams({ from: w.from, to: w.to, limit: "50" })}`);
  expect(data.groups.length).toBeGreaterThanOrEqual(3); // gpt-5.4-mini (priced), mock (unpriced), synthetic.alt (failed)
  const rows = page.getByTestId("compare-table").locator("tbody tr");
  await expect(rows).toHaveCount(data.groups.length);
  for (const [i, g] of data.groups.entries()) {
    const row = rows.nth(i);
    await expect(row.getByTestId("group-name")).toContainText(g.provider);
    if (g.requested_model) await expect(row.getByTestId("group-name")).toContainText(g.requested_model);
    await expect(row.getByTestId("group-attempts")).toHaveText(formatInt(g.attempts));
    const cost = row.getByTestId("group-cost");
    if (g.cost.known_cost_usd === null) {
      await expect(cost).toHaveAttribute("data-cost-state", "unknown");
      await expect(cost).not.toContainText("$");
    } else {
      await expect(cost.locator("[data-exact]")).toHaveAttribute("data-exact", g.cost.known_cost_usd);
      await expect(cost).toContainText(formatUsd(g.cost.known_cost_usd)!.text);
    }
    const tokens = row.getByTestId("group-tokens");
    if (g.tokens.input_tokens === null) await expect(tokens).toContainText("unknown");
    else await expect(tokens).toContainText(formatInt(g.tokens.input_tokens));
  }
  // The failed third-provider attempt has no usage: its cost and tokens are unknown, never 0.
  const alt = data.groups.find((g: any) => g.provider === "synthetic.alt");
  expect(alt.cost.known_cost_usd).toBeNull();
  expect(alt.tokens.input_tokens).toBeNull();
  await expect(page.getByTestId("compare-not-ranked")).toContainText("no group is presented as the best");
  await expect(page.getByTestId("compare-synthetic")).toBeVisible();
  await expect(page.locator('[data-chart="group-attempts"] svg.recharts-surface')).toBeVisible();
  await expect(page.locator("main")).not.toContainText(/best model|winner|recommended/i);
  await shot(page, "models-desktop");
});

test("model comparison filters re-query the backend and live in the URL", async ({ page, request }) => {
  await page.goto(u("/models"));
  await page.getByRole("link", { name: "Failed attempts only", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/models\\?project=${PROJECT}&status=error$`));
  const w = await pageWindow(page);
  const failed = await api(request, `/api/v1/models/compare?${new URLSearchParams({ from: w.from, to: w.to, limit: "50", status: "error" })}`);
  await expect(page.getByTestId("compare-table").locator("tbody tr")).toHaveCount(failed.groups.length);
  for (const g of failed.groups) expect(g.errors.failed).toBe(g.attempts);

  await page.getByRole("link", { name: "Mixed only" }).click();
  await expect(page).toHaveURL(/provenance=mixed/);
  await expect(page.getByText("No inference attempts in this window")).toBeVisible(); // no usage row is `mixed`

  await page.goto(u("/models?limit=10&range=7d"));
  await expect(page.getByRole("link", { name: "Top 10 groups" })).toHaveAttribute("aria-current", "true");
  const w7 = await pageWindow(page);
  expect(Date.parse(w7.to) - Date.parse(w7.from)).toBe(7 * 24 * 3_600_000);
});

test("custom UTC range: the form sets the window, invalid ranges are refused with a reason", async ({ page }) => {
  await page.goto(u("/models"));
  const form = page.getByTestId("range-form");
  await form.locator("summary").click();
  await form.locator('input[name="from_utc"]').fill("2026-10-20T06:30");
  await form.locator('input[name="to_utc"]').fill("2026-10-22T06:30");
  await form.getByRole("button", { name: "Apply range" }).click();
  await expect(page).toHaveURL(/from_utc=2026-10-20T06%3A30/);
  expect(await pageWindow(page)).toEqual({ from: "2026-10-20T06:30:00.000000Z", to: "2026-10-22T06:30:00.000000Z" });
  await expect(page.getByTestId("window")).toContainText("custom range");
  // The browser runs in Europe/Istanbul: the window is still the UTC values entered.

  await page.goto(u("/?from=2026-10-25T00:00:00Z&to=2026-10-24T00:00:00Z"));
  await expect(page.getByTestId("ignored-notice")).toContainText("the start must be before the end");
  await page.goto(u("/traces?from=2026-09-01T00:00:00Z&to=2026-10-02T00:00:00.000001Z"));
  await expect(page.getByTestId("ignored-notice")).toContainText("31-day maximum");
  await page.goto(u("/models?from_utc=2026-02-30T00%3A00&to_utc=2026-03-02T00%3A00"));
  await expect(page.getByTestId("ignored-notice")).toContainText("not a UTC date and time");
  await expect(page.getByRole("link", { name: "Last 24 hours" })).toHaveAttribute("aria-current", "true");
});

// ---------------------------------------------------------------- project selector and isolation
test("the selector lists this run's projects; hostile project names render as text", async ({ page, request }) => {
  const dialogs: string[] = [];
  page.on("dialog", (d) => {
    dialogs.push(d.message());
    void d.dismiss();
  });
  const listing = await api(request, "/api/v1/projects?limit=200", null);
  const hostile = listing.items.find((p: any) => String(p.id) === HOSTILE_PROJECT);
  expect(hostile.name).toContain("<script>");
  await page.goto(u("/models", HOSTILE_PROJECT));
  await expect(page.getByTestId("project-current")).toHaveAttribute("data-project-id", HOSTILE_PROJECT);
  await expect(page.getByTestId("project-name")).toHaveText(hostile.name);
  await expect(page.getByText("No inference attempts in this window")).toBeVisible();
  const select = page.getByTestId("project-select");
  for (const id of [PROJECT, FOREIGN_PROJECT, HOSTILE_PROJECT]) await expect(select.locator(`option[value="${id}"]`)).toHaveCount(1);
  await expect(select.locator(`option[value="${HOSTILE_PROJECT}"]`)).toHaveText(`${hostile.name} (#${HOSTILE_PROJECT})`);
  expect(await noXss(page)).toBeUndefined();
  expect(await page.locator("main img, main script, header img").count()).toBe(0);
  expect(dialogs).toEqual([]);
});

test("switching project re-scopes every page and leaves no old-project data on screen", async ({ page, request }) => {
  const main = await api(request, "/api/v1/traces?limit=200");
  const foreign = await api(request, "/api/v1/traces?limit=200", FOREIGN_PROJECT);
  expect(main.items.length).toBeGreaterThan(5);
  expect(foreign.items.map((t: any) => t.service_name)).toEqual(["phase3b.isolation"]);

  await page.goto(u("/traces?range=7d&status=error"));
  await expect(page.getByTestId("trace-table").locator("[data-trace-id]").first()).toBeVisible();
  await page.getByTestId("project-select").selectOption(FOREIGN_PROJECT);
  await page.getByRole("button", { name: "Switch" }).click();
  // Safe state survives (window, status); the project changes; no cursor is carried.
  await expect(page).toHaveURL(new RegExp(`/traces\\?range=7d&status=error&project=${FOREIGN_PROJECT}$`));
  await expect(page.getByTestId("project-current")).toHaveAttribute("data-project-id", FOREIGN_PROJECT);
  const html = await page.content();
  for (const t of main.items) expect(html.includes(t.trace_id), `main-project trace ${t.service_name} after the switch`).toBe(false);

  // From a later cursor page: the switch keeps the filters but never the cursor or page.
  await page.goto(u("/traces?limit=10"));
  await page.getByTestId("next-page").click();
  await expect(page).toHaveURL(/cursor=.*&page=2$/);
  await page.getByTestId("project-select").selectOption(FOREIGN_PROJECT);
  await page.getByRole("button", { name: "Switch" }).click();
  await expect(page).toHaveURL(new RegExp(`/traces\\?from=[^&]+&to=[^&]+&limit=10&project=${FOREIGN_PROJECT}$`));
  await expect(page.getByTestId("pagination")).toContainText("Page 1");

  await page.goto(u("/traces", FOREIGN_PROJECT));
  await expect(page.getByTestId("trace-table").locator("[data-trace-id]")).toHaveCount(1);
  await expect(page.getByTestId("trace-table")).toContainText("phase3b.isolation");

  // The navigation keeps the explicit project on every page.
  for (const name of ["Overview", "Models", "Traces"]) {
    await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name })).toHaveAttribute("href", new RegExp(`\\?project=${FOREIGN_PROJECT}$`));
  }
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Overview" }).click();
  await expect(page.getByTestId("project-current")).toHaveAttribute("data-project-id", FOREIGN_PROJECT);
  const w = await pageWindow(page);
  const o = await api(request, `/api/v1/overview?${new URLSearchParams(w)}`, FOREIGN_PROJECT);
  await expect(page.getByTestId("traces-value")).toHaveText(formatInt(o.traces.count.total));
});

test("switching project from a trace detail opens the new project's list, not the old trace", async ({ page, request }) => {
  const main = await api(request, "/api/v1/traces?limit=200");
  const nested = main.items.find((t: any) => t.service_name === "phase3b.nested");
  await page.goto(u(`/traces/${nested.trace_id}?span=${"0".repeat(15)}1`));
  await expect(page.getByRole("tree")).toBeVisible();
  await page.getByTestId("project-select").selectOption(FOREIGN_PROJECT);
  await page.getByRole("button", { name: "Switch" }).click();
  await expect(page).toHaveURL(new RegExp(`/traces\\?project=${FOREIGN_PROJECT}$`));
  expect((await page.content()).includes(nested.trace_id)).toBe(false);
  // And the old trace under the foreign project is the indistinguishable 404.
  await page.goto(u(`/traces/${nested.trace_id}`, FOREIGN_PROJECT));
  await expect(page.getByTestId("trace-not-found")).toBeVisible();
  await expect(page.getByRole("treeitem")).toHaveCount(0);
});

test("invalid or unknown project ids never fall back to another project", async ({ page }) => {
  const dialogs: string[] = [];
  page.on("dialog", (d) => {
    dialogs.push(d.message());
    void d.dismiss();
  });
  for (const raw of ["%3Cscript%3Ealert(1)%3C%2Fscript%3E", "0", "-1", "1%20OR%201%3D1", "9223372036854775808"]) {
    for (const p of ["/", "/models", "/traces", `/traces/${"a".repeat(32)}`]) {
      await page.goto(`${p}?project=${raw}`);
      const err = page.locator('[data-state="error"]').first();
      await expect(err, `${p} ${raw}`).toHaveAttribute("data-error-kind", "invalid_project");
      await expect(page.getByTestId("trace-table")).toHaveCount(0);
      await expect(page.getByTestId("kpi-grid")).toHaveCount(0);
      await expect(page.getByTestId("project-selector")).toHaveCount(0);
      expect(await page.content()).not.toContain("<script>alert(1)</script>");
    }
  }
  for (const p of ["/", "/models", "/traces"]) {
    await page.goto(`${p}?project=999999999999`);
    await expect(page.locator('[data-state="error"]').first()).toHaveAttribute("data-error-kind", "not_found");
  }
  expect(dialogs).toEqual([]);
  expect(await noXss(page)).toBeUndefined();
});

// ---------------------------------------------------------------- R40 and hostile URL values
test("R40: the trace list shows every provider/model combination of a multi-model trace", async ({ page, request }) => {
  await page.goto(u("/traces?limit=100"));
  const w = await pageWindow(page);
  const list = await api(request, `/api/v1/traces?${new URLSearchParams({ ...w, limit: "100" })}`);
  const multi = list.items.find((t: any) => t.service_name === "phase3c.multi-model");
  expect(multi.usage.models.distinct).toBe(3);
  expect(multi.usage.models.items.map((m: any) => [m.provider, m.calls])).toEqual([
    ["openai", 2],
    ["llminsight.mock", 1],
    ["synthetic.alt", 1],
  ]);
  const row = page.getByTestId("trace-table").locator("tr", { has: page.locator(`[data-trace-id="${multi.trace_id}"]`) });
  const cell = row.getByTestId("trace-models");
  await expect(cell).toHaveAttribute("data-distinct", "3");
  await expect(cell.getByTestId("trace-model")).toHaveCount(3);
  await expect(cell).toContainText("openai · gpt-5.4-mini");
  await expect(cell).toContainText("×2");
  await expect(cell).toContainText("synthetic.alt · alt-model-x");
  for (const t of list.items) {
    const c = page.getByTestId("trace-table").locator("tr", { has: page.locator(`[data-trace-id="${t.trace_id}"]`) }).getByTestId("trace-models");
    if (t.usage.inference_calls > 0) await expect(c).toHaveAttribute("data-distinct", String(t.usage.models.distinct));
  }
  await page.goto(u(`/traces/${multi.trace_id}`));
  await expect(page.getByTestId("table-attempts")).toContainText("synthetic.alt");
});

test("hostile URL values on the Phase 3C pages are refused and never executed", async ({ page }) => {
  const dialogs: string[] = [];
  page.on("dialog", (d) => {
    dialogs.push(d.message());
    void d.dismiss();
  });
  const evil = encodeURIComponent('"><img src=x onerror=alert(1)>');
  await page.goto(u(`/models?status=${evil}&provenance=${evil}&limit=${evil}&from_utc=${evil}&to_utc=javascript:alert(1)`));
  await expect(page.getByTestId("ignored-notice")).toContainText("status");
  await expect(page.getByTestId("ignored-notice")).toContainText("from/to");
  await page.goto(u(`/?range=${evil}&from=${evil}&to=${evil}`));
  await expect(page.getByTestId("ignored-notice")).toBeVisible();
  await page.goto(u(`/traces?limit=100&from_utc=${evil}`));
  await expect(page.getByTestId("ignored-notice")).toContainText("from/to");
  expect(await page.locator("main img").count()).toBe(0);
  expect(dialogs).toEqual([]);
});

// ---------------------------------------------------------------- auto-refresh
test("auto-refresh is off by default, bounded, and disabled on later cursor pages", async ({ page }) => {
  await page.clock.install();
  await page.goto(u("/models"));
  const control = page.getByTestId("auto-refresh");
  await expect(control.getByRole("combobox")).toHaveValue("off");
  await expect(control).toContainText("Off");
  // A refresh is an RSC request that is not a link prefetch.
  const isRefresh = (r: { headers(): Record<string, string> }) => r.headers().rsc === "1" && r.headers()["next-router-prefetch"] === undefined;
  const refreshes: string[] = [];
  page.on("request", (r) => {
    if (isRefresh(r)) refreshes.push(r.url());
  });
  await page.clock.fastForward(120_000);
  expect(refreshes.length, "no polling while off").toBe(0);
  await control.getByRole("combobox").selectOption("30");
  await expect(control).toContainText("stops after 30 min");
  const first = page.waitForRequest(isRefresh);
  await page.clock.fastForward(31_000);
  await first;
  await expect(page.getByTestId("compare-table")).toBeVisible();
  await page.clock.fastForward(31 * 60_000);
  await expect(control).toContainText("Stopped after 30 minutes");
  await expect(control.getByRole("combobox")).toHaveValue("off");

  await page.goto(u("/traces?limit=10"));
  await page.getByTestId("next-page").click();
  await expect(page).toHaveURL(/cursor=/);
  await expect(page.getByTestId("auto-refresh").getByRole("combobox")).toBeDisabled();
  await expect(page.getByTestId("auto-refresh")).toContainText("frozen for paging");
});

// ---------------------------------------------------------------- security, accessibility, layout
test("Phase 3C pages: no credential in any response or storage, no console errors or CSP violations", async ({ page }) => {
  const bodies: string[] = [];
  const problems: string[] = [];
  page.on("response", async (res) => {
    try {
      bodies.push(await res.text());
    } catch {
      /* redirects and aborted responses have no body */
    }
  });
  page.on("console", (m) => {
    if (m.type() === "error" || m.type() === "warning") problems.push(`${m.type()}: ${m.text().slice(0, 200)}`);
  });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message.slice(0, 200)}`));
  await page.goto(u("/models"));
  await page.locator('[data-chart="group-latency"] svg.recharts-surface').hover();
  await page.getByRole("link", { name: "Last 7 days" }).click();
  await expect(page).toHaveURL(/range=7d/);
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Traces" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Traces" })).toBeVisible();
  await page.waitForLoadState("networkidle");
  expect(bodies.length).toBeGreaterThan(3);
  expect(bodies.filter((b) => b.includes(TOKEN) || /LLMI_API_TOKEN|LLMI_ADMIN_TOKEN|Bearer\s+[A-Za-z0-9]/.test(b))).toEqual([]);
  const storage = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }) + document.cookie);
  expect(storage.includes(TOKEN)).toBe(false);
  expect(problems).toEqual([]);
});

test("model comparison is keyboard accessible with labelled controls and captioned tables", async ({ page }) => {
  await page.goto(u("/models"));
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  await expect(page.locator("h1")).toHaveCount(1);
  await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Models" })).toHaveAttribute("aria-current", "page");
  for (const g of ["Time range", "Provenance", "Attempt status", "Groups"]) await expect(page.getByRole("group", { name: g })).toBeVisible();
  await expect(page.getByRole("form", { name: "Switch project" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Project" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Auto-refresh" })).toBeVisible();
  const select = page.getByRole("combobox", { name: "Project" });
  await select.focus();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Switch" })).toBeFocused();
  for (const d of await page.locator("details").all()) await d.locator("summary").click();
  const tables = page.locator("table:not([hidden])");
  expect(await tables.count()).toBeGreaterThan(1);
  expect(await tables.evaluateAll((ts) => ts.filter((t) => !t.querySelector("caption")?.textContent?.trim()).length)).toBe(0);
});

for (const [name, viewport] of [
  ["desktop", { width: 1440, height: 900 }],
  ["mobile", { width: 390, height: 844 }],
] as const) {
  test.describe(`Phase 3C pages at ${name} width`, () => {
    test.use({ viewport });
    test(`no horizontal page scroll on models, overview and traces with the selector: ${name}`, async ({ page }) => {
      await page.addInitScript(() => {
        const w = window as unknown as { __maxOverflow: number };
        w.__maxOverflow = 0;
        const sample = () => {
          const el = document.documentElement;
          if (el) w.__maxOverflow = Math.max(w.__maxOverflow, el.scrollWidth - window.innerWidth);
          requestAnimationFrame(sample);
        };
        requestAnimationFrame(sample);
      });
      for (const p of ["/models?range=31d", "/", "/traces", "/models"]) {
        await page.goto(u(p, p === "/models" ? HOSTILE_PROJECT : PROJECT));
        await expect(page.locator("h1")).toBeVisible();
        await page.waitForLoadState("networkidle");
        expect(await page.evaluate(() => (window as unknown as { __maxOverflow: number }).__maxOverflow), p).toBeLessThanOrEqual(0);
      }
      await page.goto(u("/models?range=31d"));
      if (name === "mobile") await expect(page.getByTestId("group-cards")).toBeVisible();
      else await expect(page.getByTestId("compare-table")).toBeVisible();
      await shot(page, `models-${name}`);
      await page.goto(u("/traces"));
      await shot(page, `traces-list-models-${name}`);
    });
  });
}
