/**
 * Live E2E for the trace explorer (Phase 3B): browser → Next.js → FastAPI → PostgreSQL.
 * Expectations come from direct backend API calls for the window the page echoes; nothing is
 * mocked. The global setup (scripts/dev/e2e_fixtures.py, R41) creates this run's projects and
 * seeds them through `/v1/traces` with current UTC timestamps (`--set edge` + `--set phase3c`
 * in the main project, `--set isolation` in the foreign one); the teardown deletes them.
 */
import { expect, test, type APIRequestContext, type Page, type TestInfo } from "@playwright/test";
import path from "node:path";
import { formatUsd } from "../../lib/decimal";
import { formatDurationNs, formatIsoNs } from "../../lib/nanos";
import { API, FOREIGN_PROJECT, PROJECT, SHOTS, TOKEN, u } from "./e2e-env";
const XSS_MARK = "window.__llmiXss=1";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Win = { from: string; to: string };

/** Direct backend read, paced under the /api/v1 rate limit (shared with the dashboard). */
async function api(request: APIRequestContext, pathAndQuery: string, project = PROJECT): Promise<any> {
  await new Promise((r) => setTimeout(r, 150));
  const sep = pathAndQuery.includes("?") ? "&" : "?";
  const res = await request.get(`${API}${pathAndQuery}${sep}project=${project}`, { headers: { Authorization: `Bearer ${TOKEN}` } });
  expect(res.status(), pathAndQuery).toBe(200);
  // Lossy JSON is fine here: tests compare strings and small counts only.
  return res.json();
}

async function listApi(request: APIRequestContext, w: Win, extra: Record<string, string> = {}): Promise<any> {
  return api(request, `/api/v1/traces?${new URLSearchParams({ from: w.from, to: w.to, ...extra })}`);
}

async function pageWindow(page: Page): Promise<Win> {
  const el = page.getByTestId("window");
  return { from: (await el.getAttribute("data-from")) ?? "", to: (await el.getAttribute("data-to")) ?? "" };
}

async function uiTraceIds(page: Page): Promise<string[]> {
  return page.getByTestId("trace-table").getByTestId("trace-link").evaluateAll((els) => els.map((e) => e.getAttribute("data-trace-id") ?? ""));
}

async function shot(page: Page, name: string, info?: TestInfo): Promise<void> {
  // Streamed content can already match assertions while hidden; wait for the swap and idle.
  await expect(page.locator('[role="status"][aria-label^="Loading"]')).toHaveCount(0);
  await page.waitForLoadState("networkidle");
  if (info) await info.attach(name, { body: await page.screenshot({ fullPage: true }), contentType: "image/png" });
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });
}

/** All traces of the 24 h list window (one API page; the seeded data set is < 200 traces). */
async function fixtures(page: Page, request: APIRequestContext): Promise<{ w: Win; items: any[]; by: (service: string) => any }> {
  await page.goto(u("/traces"));
  const w = await pageWindow(page);
  const data = await listApi(request, w, { limit: "200" });
  const by = (service: string) => {
    const t = data.items.find((x: any) => x.service_name === service || x.service_name.endsWith(service));
    expect(t, `seeded trace ${service} of this run (global setup)`).toBeTruthy();
    return t;
  };
  return { w, items: data.items, by };
}

test.beforeAll(() => {
  expect(TOKEN.length, "frontend/.env.local must hold the server-side token").toBeGreaterThan(31);
});

test("T45: the trace list shows the seeded database traces exactly as the API returns them", async ({ page, request }, info) => {
  await page.goto(u("/traces"));
  await expect(page.getByRole("heading", { level: 1, name: "Traces" })).toBeVisible();
  const w = await pageWindow(page);
  const data = await listApi(request, w, { limit: "25" });
  expect(data.items.length).toBeGreaterThan(0);
  expect(await uiTraceIds(page)).toEqual(data.items.map((t: any) => t.trace_id));

  const rows = page.getByTestId("trace-table").locator("tbody tr");
  for (const [i, t] of data.items.entries()) {
    const row = rows.nth(i);
    await expect(row.getByTestId("trace-link")).toContainText(t.service_name);
    await expect(row.getByTestId("trace-link")).toContainText(t.trace_id);
    await expect(row.locator("time")).toHaveText(formatIsoNs(t.started_at, 3));
    await expect(row).toContainText(formatDurationNs(BigInt(t.duration_ns)));
    await expect(row.locator("[data-trace-status]")).toHaveAttribute("data-trace-status", t.status);
    await expect(row.locator(`[data-provenance="${t.provenance}"]`)).toBeVisible();
    const cost = row.locator("[data-cost-state]");
    if (t.usage.inference_calls > 0) {
      if (t.usage.known_cost_usd === null) {
        await expect(cost).toHaveAttribute("data-cost-state", "unknown");
        await expect(cost).not.toContainText("$");
      } else {
        await expect(cost).toContainText(formatUsd(t.usage.known_cost_usd)!.text);
      }
    }
  }
  await shot(page, "traces-list-desktop", info);
});

test("cursor pagination walks every trace once, newest first, with a frozen window", async ({ page, request }) => {
  await page.goto(u("/traces?limit=10"));
  const w = await pageWindow(page);
  const seen: string[] = [];
  let cursor: string | null = null;
  for (let pageNo = 1; pageNo <= 20; pageNo += 1) {
    const expected = await listApi(request, w, cursor ? { limit: "10", cursor } : { limit: "10" });
    expect(await uiTraceIds(page)).toEqual(expected.items.map((t: any) => t.trace_id));
    await expect(page.getByTestId("pagination")).toContainText(`Page ${pageNo}`);
    seen.push(...expected.items.map((t: any) => t.trace_id));
    cursor = expected.next_cursor;
    if (cursor === null) {
      await expect(page.getByTestId("next-page")).toHaveCount(0);
      break;
    }
    await page.getByTestId("next-page").click();
    await expect(page).toHaveURL(new RegExp(`cursor=${cursor}&page=${pageNo + 1}$`));
    expect(await pageWindow(page)).toEqual(w);
  }
  expect(seen.length).toBeGreaterThan(10);
  expect(new Set(seen).size).toBe(seen.length);
  const all = await listApi(request, w, { limit: "200" });
  expect(seen).toEqual(all.items.map((t: any) => t.trace_id));
  await page.getByTestId("first-page").click();
  await expect(page.getByTestId("pagination")).toContainText("Page 1");
});

test("filters re-query the backend and live in the URL", async ({ page, request }) => {
  await page.goto(u("/traces"));
  const w = await pageWindow(page);
  await page.getByRole("link", { name: "Status Error" }).click();
  await expect(page).toHaveURL(new RegExp(`/traces\\?project=${PROJECT}&status=error$`));
  const errors = await listApi(request, w, { limit: "25", status: "error" });
  expect(await uiTraceIds(page)).toEqual(errors.items.map((t: any) => t.trace_id));
  for (const t of errors.items) expect(t.status).toBe("error");

  await page.getByRole("link", { name: "Mixed only" }).click();
  await expect(page).toHaveURL(/status=error&provenance=mixed$|provenance=mixed/);
  await page.goto(u("/traces?provenance=mixed"));
  const mixed = await listApi(request, w, { limit: "25", provenance: "mixed" });
  expect(await uiTraceIds(page)).toEqual(mixed.items.map((t: any) => t.trace_id));
  expect(mixed.items.some((t: any) => t.service_name === "phase3b.mixed")).toBe(true);

  await page.goto(u("/traces"));
  await page.getByRole("textbox", { name: /Model \(exact\)/ }).fill("gpt-5.4-mini");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page).toHaveURL(/model=gpt-5\.4-mini/);
  const byModel = await listApi(request, w, { limit: "25", model: "gpt-5.4-mini" });
  expect(byModel.items.length).toBeGreaterThan(0);
  expect(await uiTraceIds(page)).toEqual(byModel.items.map((t: any) => t.trace_id));

  await page.getByRole("link", { name: "Last 7 days" }).click();
  await expect(page).toHaveURL(/range=7d.*model=gpt-5\.4-mini/);
  const w7 = await pageWindow(page);
  expect(Date.parse(w7.to) - Date.parse(w7.from)).toBe(7 * 24 * 3_600_000);
});

test("unsupported URL values are ignored with a notice and never executed", async ({ page }) => {
  const dialogs: string[] = [];
  page.on("dialog", (d) => {
    dialogs.push(d.message());
    void d.dismiss();
  });
  await page.goto(u(`/traces?model=${encodeURIComponent("<script>alert(1)</script>")}&status=%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E&range=999d&cursor=${encodeURIComponent('"><svg onload=alert(1)>')}`));
  await expect(page.getByTestId("ignored-notice")).toContainText("model");
  await expect(page.getByTestId("ignored-notice")).toContainText("status");
  await expect(page.getByRole("link", { name: "Last 24 hours" })).toHaveAttribute("aria-current", "true");
  await page.goto(u(`/traces/${encodeURIComponent("<script>alert(1)</script>")}`));
  await expect(page.getByText("Not a valid trace ID")).toBeVisible();
  await page.goto(u(`/traces/${"a".repeat(32)}?span=${encodeURIComponent("<img src=x onerror=alert(1)>")}&list=${encodeURIComponent("javascript:alert(1)")}`));
  await expect(page.getByTestId("back-to-traces")).toHaveAttribute("href", `/traces?project=${PROJECT}`);
  expect(dialogs).toEqual([]);
});

test("T46: list → detail shows the root → child span tree, and back restores the list", async ({ page, request }) => {
  const { by } = await fixtures(page, request);
  const nested = by("phase3b.nested");
  await page.goto(u("/traces?provenance=synthetic&limit=50")); // canonical order, as the back link rebuilds it
  const listUrl = page.url();
  await page.getByTestId("trace-table").locator(`[data-trace-id="${nested.trace_id}"]`).click();
  await expect(page).toHaveURL(new RegExp(`/traces/${nested.trace_id}\\?project=${PROJECT}&list=`));
  await expect(page.getByRole("heading", { level: 1, name: "Trace" })).toBeVisible();

  const detail = await api(request, `/api/v1/traces/${nested.trace_id}`);
  const tree = page.getByRole("tree", { name: "Span hierarchy and timeline" });
  const items = tree.getByRole("treeitem");
  await expect(items).toHaveCount(detail.spans.length);
  const root = detail.spans.find((s: any) => s.role === "root");
  await expect(items.first()).toHaveAttribute("data-span-id", root.span_id);
  await expect(items.first()).toHaveAttribute("aria-level", "1");
  await expect(items.first()).toHaveAttribute("aria-expanded", "true");
  // Every child sits directly below its parent's subtree at parent level + 1.
  const ui = await items.evaluateAll((els) => els.map((e) => ({ id: e.getAttribute("data-span-id"), level: Number(e.getAttribute("aria-level")) })));
  const level = new Map(ui.map((r) => [r.id, r.level]));
  for (const s of detail.spans) if (s.parent_span_id) expect(level.get(s.span_id), s.name).toBe((level.get(s.parent_span_id) ?? 0) + 1);
  expect(Math.max(...ui.map((r) => r.level))).toBe(4);
  await expect(page.getByTestId("span-details")).toHaveAttribute("data-span-id", root.span_id);

  await page.getByTestId("back-to-traces").click();
  await expect(page).toHaveURL(listUrl);
  await expect(page.getByTestId("trace-table").locator(`[data-trace-id="${nested.trace_id}"]`)).toBeVisible();
});

test("nanosecond timestamps, exact offsets, Decimal cost and keyboard navigation", async ({ page, request }) => {
  const { by } = await fixtures(page, request);
  const nested = by("phase3b.nested");
  const detail = await api(request, `/api/v1/traces/${nested.trace_id}`);
  await page.goto(u(`/traces/${nested.trace_id}`));
  await expect(page.getByTestId("fact-started")).toHaveText(formatIsoNs(detail.trace.started_at));
  expect(detail.trace.start_time_unix_nano).toMatch(/789$/); // seeded with sub-microsecond digits
  await expect(page.getByTestId("fact-started").locator("[data-ns]")).toHaveAttribute("data-ns", detail.trace.start_time_unix_nano);
  await expect(page.getByTestId("fact-cost")).toContainText(formatUsd(detail.trace.usage.known_cost_usd)!.text);
  await expect(page.getByTestId("fact-cost")).toContainText("1 of 2 unpriced");

  const items = page.getByRole("treeitem");
  await items.first().focus();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByTestId("span-details-name")).toHaveText("agent.plan");
  await expect(page.getByTestId("span-offset")).toHaveText("+1.000001 ms");
  await expect(page).toHaveURL(/[?&]span=[0-9a-f]{16}$/);
  await expect(items.nth(1)).toBeFocused();
  await page.keyboard.press("ArrowLeft"); // collapse agent.plan (3 descendants)
  await expect(items).toHaveCount(2);
  await page.keyboard.press("ArrowRight");
  await expect(items).toHaveCount(5);
  await page.keyboard.press("End");
  await expect(page.getByTestId("span-details-name")).toHaveText("chat gpt-5.4-mini");
  const priced = detail.usage.find((u: any) => u.cost_status === "computed");
  await expect(page.getByTestId("span-cost")).toHaveText(formatUsd(priced.cost_usd)!.exact);
  await expect(page.getByTestId("span-inference")).toContainText("Cached input (subset)200");
  await expect(page.getByTestId("span-inference")).toContainText("Reasoning output (subset)64");
  // gen_ai.request.seed = 2^62 + 1 survives JSON transport exactly (lossless parse).
  await expect(page.getByTestId("span-attributes")).toContainText("4611686018427387905");
  await page.keyboard.press("Home");
  await expect(page.getByTestId("span-details-name")).toHaveText("POST /v1/agent");
  await page.reload();
  await expect(page.getByTestId("span-details-name")).toHaveText("POST /v1/agent", { timeout: 10_000 });
});

test("retries are separate physical attempts; trace error, span failure and recovery are distinguished", async ({ page, request }) => {
  const { items } = await fixtures(page, request);
  let retry: any = null;
  for (const t of items.filter((x: any) => x.status === "error" && x.usage.inference_calls === 2 && x.service_name === "phase3c.retry")) {
    const d = await api(request, `/api/v1/traces/${t.trace_id}`);
    const ids = new Set(d.usage.map((u: any) => u.call_id));
    if (ids.size === 1 && d.usage.some((u: any) => u.attempt_id === 2)) {
      retry = d;
      break;
    }
  }
  expect(retry, "the seeded phase3c.retry trace").toBeTruthy();
  await page.goto(u(`/traces/${retry.trace.trace_id}`));
  await expect(page.getByTestId("fact-status")).toContainText("Error");
  await expect(page.getByTestId("status-spans")).toContainText("1 span with status ERROR (1 inference attempt, 0 application spans)");
  await expect(page.getByTestId("status-calls")).toContainText("1 recovered after a failed attempt");
  await expect(page.getByRole("treeitem").filter({ hasText: "attempt 1" })).toHaveCount(1);
  await expect(page.getByRole("treeitem").filter({ hasText: "attempt 2" })).toHaveCount(1);
  const calls = page.getByTestId("table-calls");
  await expect(calls.locator('[data-outcome="recovered"]')).toHaveText(/Recovered after retry/);
  const failed = retry.spans.find((s: any) => s.status === "ERROR");
  await expect(calls).toContainText(`error.type ${failed.error_type}`);

  const appErr = (await fixtures(page, request)).by("phase3b.app-error");
  await page.goto(u(`/traces/${appErr.trace_id}`));
  await expect(page.getByTestId("status-spans")).toContainText("(0 inference attempts, 1 application span)");
  await expect(page.getByTestId("status-calls")).toContainText("0 recovered after a failed attempt, 0 ended with a failed attempt");
  await shot(page, "trace-detail-app-error");
});

test("orphans, parent cycles and equal start times render deterministically", async ({ page, request }) => {
  const { by } = await fixtures(page, request);
  await page.goto(u(`/traces/${by("phase3b.orphan").trace_id}`));
  await expect(page.locator('[role="treeitem"][data-placement="orphan"]')).toHaveCount(1);
  await expect(page.locator('[role="treeitem"][data-placement="orphan"]')).toContainText("orphan");

  await page.goto(u(`/traces/${by("phase3b.cycle").trace_id}`));
  await expect(page.getByRole("treeitem")).toHaveCount(3);
  await expect(page.locator('[role="treeitem"][data-placement="unreachable"]')).toHaveCount(1);
  await expect(page.getByTestId("trace-provenance-notice")).toHaveAttribute("data-provenance", "unknown");

  const eq = by("phase3b.equal-start");
  const detail = await api(request, `/api/v1/traces/${eq.trace_id}`);
  const kids = detail.spans.filter((s: any) => s.parent_span_id !== null);
  expect(kids[0].start_time_unix_nano).toBe(kids[1].start_time_unix_nano);
  await page.goto(u(`/traces/${eq.trace_id}`));
  const ids = await page.getByRole("treeitem").evaluateAll((els) => els.map((e) => e.getAttribute("data-span-id")));
  expect(ids.slice(1)).toEqual(kids.map((s: any) => s.span_id).sort());
  await page.reload();
  expect(await page.getByRole("treeitem").evaluateAll((els) => els.map((e) => e.getAttribute("data-span-id")))).toEqual(ids);
});

test("large traces render a bounded number of rows", async ({ page, request }) => {
  const { by } = await fixtures(page, request);
  const large = by("phase3b.large");
  await page.goto(u(`/traces/${large.trace_id}`));
  await expect(page.getByRole("treeitem")).toHaveCount(200);
  await expect(page.getByTestId("rows-hidden")).toHaveText(`Showing 200 of ${large.span_count} rows.`);
  await page.getByTestId("show-more-rows").click();
  await expect(page.getByRole("treeitem")).toHaveCount(400);
  await expect(page.getByTestId("table-spans").locator("tbody tr")).toHaveCount(500);
});

test("T48: synthetic, mixed and unknown provenance are labeled and never shown as real", async ({ page, request }) => {
  const { by, items } = await fixtures(page, request);
  await page.goto(u("/traces?limit=100"));
  for (const p of ["synthetic", "mixed", "unknown"]) {
    const t = items.find((x: any) => x.provenance === p);
    expect(t, `a ${p} trace`).toBeTruthy();
    await expect(page.getByTestId("trace-table").locator("tr", { has: page.locator(`[data-trace-id="${t.trace_id}"]`) }).locator(`[data-provenance="${p}"]`)).toBeVisible();
  }
  await expect(page.getByTestId("trace-table").locator('[data-provenance="real"]')).toHaveCount(items.filter((x: any) => x.provenance === "real").length);

  const mixed = by("phase3b.mixed");
  const detail = await api(request, `/api/v1/traces/${mixed.trace_id}`);
  await page.goto(u(`/traces/${mixed.trace_id}`));
  await expect(page.getByTestId("trace-provenance-notice")).toHaveAttribute("data-provenance", "mixed");
  await expect(page.getByTestId("trace-provenance-notice")).toContainText("never counted as real");
  const attempts = page.getByTestId("table-attempts");
  for (const u of detail.usage) await expect(attempts.locator("tr", { hasText: u.span_id }).locator(`[data-provenance="${u.provenance}"]`)).toBeVisible();
  expect(new Set(detail.usage.map((u: any) => u.provenance))).toEqual(new Set(["synthetic", "unknown"]));

  const nested = by("phase3b.nested");
  await page.goto(u(`/traces/${nested.trace_id}`));
  await expect(page.getByTestId("trace-provenance-notice")).toHaveAttribute("data-provenance", "synthetic");
  await expect(page.getByTestId("trace-provenance-notice")).toContainText("Synthetic trace");
  await expect(page.getByRole("treeitem").filter({ hasText: "synthetic" })).toHaveCount(2);
});

test("T41: hostile labels ingested through /v1/traces render as text in list and detail", async ({ page, request }) => {
  const dialogs: string[] = [];
  page.on("dialog", (d) => {
    dialogs.push(d.message());
    void d.dismiss();
  });
  const { by } = await fixtures(page, request);
  const xss = by("phase3b.xss");
  expect(xss.service_name).toContain("<img src=x onerror=");
  await page.goto(u("/traces?limit=100"));
  const link = page.getByTestId("trace-table").locator(`[data-trace-id="${xss.trace_id}"]`);
  await expect(link).toContainText(xss.service_name);
  await link.click();
  await expect(page).toHaveURL(new RegExp(xss.trace_id));
  await expect(page.getByTestId("fact-service")).toHaveText(xss.service_name);
  const detail = await api(request, `/api/v1/traces/${xss.trace_id}`);
  const root = detail.spans.find((s: any) => s.role === "root");
  await expect(page.getByTestId("span-details-name")).toHaveText(root.name);
  expect(root.name).toContain("<script>");
  await expect(page.getByTestId("span-resource")).toContainText(root.resource_attributes["deployment.environment.name"]);
  await expect(page.getByTestId("span-attributes")).toContainText(root.attributes["http.route"]);
  // The hostile model and provider never passed ingest validation: shown as not reported.
  expect(detail.usage[0].requested_model).toBeNull();
  expect(detail.usage[0].provider).toBe("unknown");
  await expect(page.getByTestId("table-attempts")).toContainText("unknown");
  await page.getByRole("treeitem").nth(1).click();
  await expect(page.getByTestId("span-inference")).toContainText("Requested modelunknown");

  expect(await page.evaluate(() => (window as unknown as { __llmiXss?: number }).__llmiXss)).toBeUndefined();
  expect(await page.locator("main script, main img, main svg[onload], main b, main i").count()).toBe(0);
  const markup = await page.content();
  expect(markup).not.toContain(`<script>${XSS_MARK}</script>`);
  expect(markup).not.toContain('<img src=x onerror="window');
  expect(dialogs).toEqual([]);
  await shot(page, "trace-detail-xss-escaped");
});

test("project isolation: another project's trace is not found and never listed", async ({ page, request }) => {
  expect(FOREIGN_PROJECT).not.toBe(PROJECT);
  const foreign = await api(request, "/api/v1/traces?limit=10", FOREIGN_PROJECT);
  const t = foreign.items.find((x: any) => x.service_name === "phase3b.isolation");
  expect(t, `phase3b.isolation trace in project ${FOREIGN_PROJECT}`).toBeTruthy();
  await page.goto(u(`/traces/${t.trace_id}`));
  await expect(page.getByTestId("trace-not-found")).toBeVisible();
  await expect(page.getByRole("treeitem")).toHaveCount(0);
  await expect(page.getByText("phase3b.isolation")).toHaveCount(0);
  await page.goto(u("/traces?range=31d&limit=100"));
  await expect(page.getByTestId("trace-table").locator(`[data-trace-id="${t.trace_id}"]`)).toHaveCount(0);
});

test("backend unavailable and unauthorized states for the trace pages", async ({ page }) => {
  await page.goto("http://127.0.0.1:3002/traces");
  const down = page.locator('[data-state="error"]');
  await expect(down).toHaveAttribute("data-error-kind", "unreachable");
  await expect(down).toContainText("Backend unreachable");
  await expect(page.getByTestId("trace-table")).toHaveCount(0);
  await page.goto(`http://127.0.0.1:3002/traces/${"a".repeat(32)}`);
  await expect(page.locator('[data-state="error"]')).toHaveAttribute("data-error-kind", "unreachable");
  await shot(page, "trace-detail-backend-unreachable");

  await page.goto("http://127.0.0.1:3003/traces");
  const denied = page.locator('[data-state="error"]');
  await expect(denied).toHaveAttribute("data-error-kind", "unauthorized");
  await expect(denied).toContainText("never shown");
  await expect(page.getByTestId("trace-table")).toHaveCount(0);
  expect(await page.content()).not.toContain("e2e-deliberately-wrong-token");
});

test("no credential reaches the browser on the trace pages (T42 regression)", async ({ page, request }) => {
  const bodies: string[] = [];
  page.on("response", async (res) => {
    try {
      bodies.push(await res.text());
    } catch {
      /* redirects and aborted responses have no body */
    }
  });
  const { by } = await fixtures(page, request);
  await page.getByTestId("trace-table").locator(`[data-trace-id="${by("phase3b.nested").trace_id}"]`).click();
  await expect(page.getByRole("tree")).toBeVisible();
  await page.getByRole("treeitem").nth(2).click();
  await page.waitForLoadState("networkidle");
  expect(bodies.length).toBeGreaterThan(3);
  const leaks = bodies.filter((b) => b.includes(TOKEN) || /LLMI_API_TOKEN|LLMI_ADMIN_TOKEN|Bearer\s+[A-Za-z0-9]/.test(b));
  expect(leaks.length).toBe(0);
  const storage = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }) + document.cookie);
  expect(storage.includes(TOKEN)).toBe(false);
});

test("trace pages load without console errors, CSP violations or hydration warnings", async ({ page, request }) => {
  const problems: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error" || m.type() === "warning") problems.push(`${m.type()}: ${m.text().slice(0, 200)}`);
  });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message.slice(0, 200)}`));
  const { by } = await fixtures(page, request);
  await page.getByRole("link", { name: "Status Error" }).click();
  await expect(page).toHaveURL(/status=error/);
  await page.goto(u(`/traces/${by("phase3b.nested").trace_id}`));
  await page.getByRole("treeitem").first().focus();
  await page.keyboard.press("ArrowDown");
  await page.getByTestId("span-table-details").locator("summary").click();
  await page.getByRole("button", { name: /Refresh/ }).click();
  await page.waitForLoadState("networkidle");
  expect(problems).toEqual([]);
});

test("accessible structure: one h1, labelled tree and regions, captioned tables, skip link", async ({ page, request }) => {
  const { by } = await fixtures(page, request);
  await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Traces" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("group", { name: "Status" })).toBeVisible();
  await expect(page.getByRole("search", { name: "Filter by model" })).toBeVisible();
  await page.goto(u(`/traces/${by("phase3b.nested").trace_id}`));
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  await expect(page.locator("h1")).toHaveCount(1);
  await expect(page.getByRole("tree", { name: "Span hierarchy and timeline" })).toBeVisible();
  await expect(page.getByRole("complementary", { name: "Selected span" })).toBeVisible();
  await expect(page.getByRole("treeitem").locator('[tabindex="0"]')).toHaveCount(0);
  expect(await page.locator('[role="treeitem"][tabindex="0"]').count()).toBe(1);
  await page.getByTestId("span-table-details").locator("summary").click();
  const tables = page.locator("table:not([hidden])");
  const unlabeled = await tables.evaluateAll((ts) => ts.filter((t) => !t.querySelector("caption")?.textContent?.trim()).length);
  expect(unlabeled).toBe(0);
});

for (const [name, viewport] of [
  ["desktop", { width: 1440, height: 900 }],
  ["mobile", { width: 390, height: 844 }],
] as const) {
  test.describe(`trace pages at ${name} width`, () => {
    test.use({ viewport });
    test(`no horizontal page scroll on list and detail: ${name}`, async ({ page, request }) => {
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
      const overflow = () => page.evaluate(() => (window as unknown as { __maxOverflow: number }).__maxOverflow);
      const { by } = await fixtures(page, request);
      await page.waitForLoadState("networkidle");
      expect(await overflow(), "list").toBeLessThanOrEqual(0);
      if (name === "mobile") await expect(page.getByTestId("trace-cards")).toBeVisible();
      else await expect(page.getByTestId("trace-table")).toBeVisible();
      await shot(page, `traces-list-${name}`);
      for (const service of ["phase3b.nested", "phase3b.xss"]) {
        await page.goto(u(`/traces/${by(service).trace_id}`));
        await expect(page.getByRole("tree")).toBeVisible();
        await page.waitForLoadState("networkidle");
        expect(await overflow(), service).toBeLessThanOrEqual(0);
      }
      await page.goto(u(`/traces/${by("phase3b.nested").trace_id}`));
      await page.waitForLoadState("networkidle");
      await shot(page, `trace-detail-${name}`);
    });
  });
}
