/**
 * UI-R3 (model analytics, session explorer, collapsible sidebar), live: browser → Next.js →
 * FastAPI → PostgreSQL, SYNTHETIC fixtures only.
 * - Sessions: three pseudonymous sessions (`--set ui-r3-sessions`, sent during the test to the
 *   run's providers project); the inspected session's traces in time order; deep links; the raw
 *   conversation ids never reach a page.
 * - Models: side-by-side pair from the table, its differences, no ranking; group → traces.
 * - Sidebar: collapses to icons with accessible names, survives client navigation; the trace
 *   explorer measured at 1280 px with the sidebar open and collapsed, and with the list hidden.
 * Screenshots go to LLMI_SCREENSHOT_DIR when set; every captured page is checked for secrets.
 */
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { PROVIDERS_PROJECT, SHOTS, TOKEN, comparisonProject, u } from "./e2e-env";
import { ensureSessions } from "./ui-r3-fixtures";

const RAW_CONVERSATION = /uir3-conv/;

test.describe.configure({ mode: "serial" });

async function noSecrets(page: Page, name: string): Promise<void> {
  const html = await page.content();
  expect(TOKEN.length).toBeGreaterThan(31);
  expect(html, name).not.toContain(TOKEN);
  expect(html, name).not.toMatch(/Bearer\s+[A-Za-z0-9._-]{16,}|postgres(?:ql)?(?:\+\w+)?:\/\/[^\s"<]*:[^\s"<@]+@/i);
  expect(html, name).not.toMatch(RAW_CONVERSATION);
}

async function shot(page: Page, name: string, fullPage = true): Promise<void> {
  await expect(page.locator('[role="status"][aria-label^="Loading"]')).toHaveCount(0);
  await page.waitForLoadState("networkidle");
  await noSecrets(page, name);
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage });
}

async function overflow(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
}

async function width(page: Page, testId: string): Promise<number> {
  const box = await page.getByTestId(testId).boundingBox();
  return box ? Math.round(box.width) : 0;
}

test.describe("sessions at desktop width", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("three pseudonymous sessions, a time-ordered session, deep links, no raw id", async ({ page }) => {
    await ensureSessions(page);
    await page.goto(u("/sessions", PROVIDERS_PROJECT));
    const table = page.getByTestId("session-table");
    await expect(table.getByTestId("session-link")).toHaveCount(3);
    await expect(page.getByTestId("sessions-notice")).toContainText("Pseudonymous, not anonymous");
    await noSecrets(page, "sessions list");
    await shot(page, "ui-r3-sessions-dark-1440");

    // Inspect the largest session (3 traces, one failed): the URL carries the pseudonym.
    const first = table.getByTestId("session-link").first();
    const pseudonym = (await first.textContent())?.trim() ?? "";
    expect(pseudonym).toMatch(/^s1_[0-9a-f]{32}$/);
    await table.getByTestId("session-inspect").first().click();
    await expect(page).toHaveURL(new RegExp(`/sessions\\?project=\\d+.*session=${pseudonym}`));
    const detail = page.getByTestId("session-detail");
    await expect(detail).toHaveAttribute("data-session", pseudonym);
    await expect(detail.getByTestId("session-facts")).toContainText("3");
    const rows = detail.getByTestId("session-timeline-row");
    await expect(rows).toHaveCount(3);
    // Oldest first: offsets grow down the list, the first is +0.
    await expect(rows.first()).toContainText("+0 ns");
    await expect(rows.nth(1).locator('[data-trace-status="error"]')).toHaveCount(1);
    await expect(table.locator('[aria-current="true"]')).toHaveText("Selected");
    await shot(page, "ui-r3-session-detail-dark-1440");

    // A deep link reloads to the same selection.
    await page.reload();
    await expect(page.getByTestId("session-detail")).toHaveAttribute("data-session", pseudonym);

    // A timeline row opens the explorer with this session as the listing.
    const traceId = (await rows.first().getAttribute("data-trace-id")) ?? "";
    await rows.first().getByRole("link").first().click();
    await expect(page).toHaveURL(new RegExp(`/traces/${traceId}\\?project=\\d+&list=[^&]*session%3D${pseudonym}`));
    const rail = page.getByTestId("trace-rail");
    if (await rail.isVisible()) await expect(rail).toContainText("one session");
    await expect(page.getByTestId("context-bar")).toContainText("one session");
    await noSecrets(page, "session trace");

    // An invalid session value is ignored, never sent: the plain list shows.
    await page.goto(u("/sessions?session=not-a-hash", PROVIDERS_PROJECT));
    await expect(page.getByTestId("session-detail")).toHaveCount(0);
    await expect(page.getByTestId("session-table")).toBeVisible();
  });
});

test.describe("sessions in the light theme and at phone width", () => {
  test("light theme", async ({ browser }) => {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, colorScheme: "light" });
    await page.goto(u("/sessions", PROVIDERS_PROJECT));
    await page.getByTestId("session-table").getByTestId("session-inspect").first().click();
    await expect(page.getByTestId("session-detail")).toBeVisible();
    await shot(page, "ui-r3-session-detail-light-1440");
    await page.close();
  });

  test("cards and the timeline at 390 px without horizontal scroll", async ({ browser }) => {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, colorScheme: "dark" });
    await page.goto(u("/sessions", PROVIDERS_PROJECT));
    await expect(page.getByTestId("session-cards")).toBeVisible();
    await expect(page.getByTestId("session-table")).toBeHidden();
    await page.getByTestId("session-cards").getByTestId("session-inspect").first().click();
    await expect(page.getByTestId("session-timeline-row")).toHaveCount(3);
    await page.waitForLoadState("networkidle");
    expect(await overflow(page)).toBeLessThanOrEqual(0);
    await shot(page, "ui-r3-sessions-mobile-390");
    await page.close();
  });
});

test.describe("models at desktop width", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("a side-by-side pair from the table, its differences, no ranking; group → traces", async ({ page }) => {
    const project = comparisonProject();
    await page.goto(u("/models", project));
    await expect(page.getByTestId("model-pair")).toBeVisible();
    await expect(page.getByTestId("pair-empty")).toBeVisible();
    const table = page.getByTestId("compare-table");
    await table.getByTestId("group-pair-a").nth(0).click();
    await expect(page).toHaveURL(/[?&]a=/);
    await page.getByTestId("compare-table").getByTestId("group-pair-b").nth(1).click();
    await expect(page).toHaveURL(/[?&]a=/);
    await expect(page).toHaveURL(/[?&]b=/);
    const pair = page.getByTestId("model-pair");
    await expect(pair.getByTestId("pair-table")).toBeVisible();
    await expect(pair.getByTestId("pair-latency-a")).toBeVisible();
    await expect(pair.getByTestId("pair-latency-b")).toBeVisible();
    const diffs = pair.getByTestId("pair-differences");
    await expect(diffs).toContainText("not a controlled experiment");
    await expect(diffs.locator("li")).not.toHaveCount(0);
    // Reasoning is shown as part of the output, never added to it; unknown cost is never $0.
    const pairTable = pair.getByTestId("pair-table");
    await expect(pairTable).toContainText("Reasoning output");
    // The fixture groups report no reasoning subset: it says so rather than showing 0 or a sum.
    await expect(pairTable).toContainText(/part of output, not added|not reported/);
    await expect(pairTable).not.toContainText("not reported (not reported)");
    for (const side of ["a", "b"]) {
      const cost = pair.getByTestId(`pair-cost-${side}`);
      if ((await cost.getAttribute("data-cost-state")) === "unknown") await expect(cost).not.toContainText("$0");
    }
    await expect(pair).not.toContainText(/statistically significant|winner|best model/i);
    // The chosen pair survives a reload (deep link) and the form shows it.
    await page.reload();
    await expect(page.getByTestId("pair-table")).toBeVisible();
    await shot(page, "ui-r3-models-dark-1440");

    // A group → its traces (provider and model of one attempt, same window).
    const link = page.getByTestId("compare-table").getByTestId("group-traces-link").first();
    const href = (await link.getAttribute("href")) ?? "";
    expect(href).toMatch(/^\/traces\?project=\d+&.*provider=/);
    await link.click();
    await expect(page.getByRole("heading", { level: 1, name: "Traces" })).toBeVisible();
    await expect(page.getByTestId("ignored-notice")).toHaveCount(0);

    // A pair link for groups that are not returned says so.
    await page.goto(u(`/models?a=${encodeURIComponent(JSON.stringify(["nope", null, null, null]))}`, project));
    await expect(page.getByTestId("pair-missing")).toBeVisible();
  });

  test("light theme and phone width", async ({ browser }) => {
    const project = comparisonProject();
    const light = await browser.newPage({ viewport: { width: 1440, height: 900 }, colorScheme: "light" });
    await light.goto(u("/models", project));
    await light.getByTestId("compare-table").getByTestId("group-pair-a").nth(0).click();
    await expect(light).toHaveURL(/[?&]a=/); // the A link navigates; B must come from the new page
    await light.getByTestId("compare-table").getByTestId("group-pair-b").nth(1).click();
    await expect(light).toHaveURL(/[?&]b=/);
    await expect(light.getByTestId("pair-table")).toBeVisible();
    await shot(light, "ui-r3-models-light-1440");
    await light.close();
    const phone = await browser.newPage({ viewport: { width: 390, height: 844 }, colorScheme: "dark" });
    await phone.goto(u("/models", project));
    await expect(phone.getByTestId("group-cards")).toBeVisible();
    await phone.waitForLoadState("networkidle");
    expect(await overflow(phone)).toBeLessThanOrEqual(0);
    await shot(phone, "ui-r3-models-mobile-390");
    await phone.close();
  });
});

test.describe("collapsible sidebar and the explorer at 1280 px", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test("the sidebar collapses to named icons, keeps the state across pages and keeps the skip link", async ({ page }) => {
    await page.goto(u("/"));
    const shell = page.getByTestId("app-shell");
    await expect(shell).toHaveAttribute("data-sidebar", "expanded");
    const toggle = page.getByTestId("sidebar-toggle");
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await toggle.click();
    await expect(shell).toHaveAttribute("data-sidebar", "collapsed");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(toggle).toHaveAccessibleName("Expand sidebar");
    const nav = page.getByRole("navigation", { name: "Primary" });
    // Names stay accessible; the visible label is hidden and a tooltip shows on hover and focus.
    const traces = nav.getByRole("link", { name: "Traces" });
    await expect(traces).toBeVisible();
    expect(await width(page, "app-sidebar")).toBeLessThanOrEqual(80);
    await traces.hover();
    await expect(traces.locator(".sb-tip")).toBeVisible();
    // Client navigation keeps the collapsed state.
    await traces.click();
    await expect(page.getByRole("heading", { level: 1, name: "Traces" })).toBeVisible();
    await expect(shell).toHaveAttribute("data-sidebar", "collapsed");
    await expect(nav.getByRole("link", { name: "Traces" })).toHaveAttribute("aria-current", "page");
    // A full reload returns to the default: nothing is stored. The skip link is still the first stop.
    await page.reload();
    await expect(page.getByTestId("app-shell")).toHaveAttribute("data-sidebar", "expanded");
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
    // Keyboard: the toggle is reachable and operable with Enter; the tooltip also shows on keyboard focus.
    await page.getByTestId("sidebar-toggle").focus();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("app-shell")).toHaveAttribute("data-sidebar", "collapsed");
    await page.keyboard.press("Tab");
    await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link").first()).toBeFocused();
    await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link").first().locator(".sb-tip")).toBeVisible();
  });

  test("three panels fit at 1280 px with the sidebar collapsed; the list panel can be hidden", async ({ page }, info) => {
    await page.goto(u("/traces", comparisonProject()));
    await page.getByTestId("trace-table").getByTestId("trace-link").first().click();
    await expect(page.getByRole("tree")).toBeVisible();
    // Sidebar open: the explorer is narrower than 64rem, so the list is a disclosure.
    await expect(page.getByTestId("trace-rail-toggle")).toBeVisible();
    await expect(page.getByTestId("trace-rail")).toBeHidden();
    await shot(page, "ui-r3-explorer-1280-sidebar-open", false);
    const open = { tree: await width(page, "explorer-spans-panel"), inspector: await width(page, "span-inspector") };

    // Sidebar collapsed: three panels.
    await page.getByTestId("sidebar-toggle").click();
    await expect(page.getByTestId("trace-rail")).toBeVisible();
    await expect(page.getByTestId("trace-rail-toggle")).toBeHidden();
    const collapsed = { rail: await width(page, "trace-rail"), tree: await width(page, "explorer-spans-panel"), inspector: await width(page, "span-inspector") };
    expect(collapsed.rail).toBeGreaterThanOrEqual(224);
    expect(collapsed.tree).toBeGreaterThanOrEqual(400);
    expect(collapsed.inspector).toBeGreaterThanOrEqual(320);
    expect(await overflow(page)).toBeLessThanOrEqual(0);
    await shot(page, "ui-r3-explorer-1280-sidebar-collapsed", false);

    // Hide the list panel: the spans take its width; it comes back with one click.
    await page.getByTestId("trace-rail-hide").click();
    await expect(page.getByTestId("trace-rail-show")).toBeVisible();
    await expect(page.getByTestId("trace-rail")).toBeHidden();
    const hidden = { tree: await width(page, "explorer-spans-panel") };
    expect(hidden.tree).toBeGreaterThan(collapsed.tree);
    await page.getByTestId("trace-rail-show").click();
    await expect(page.getByTestId("trace-rail")).toBeVisible();
    // Keyboard: the tree keeps one tab stop and arrow navigation.
    await page.getByRole("treeitem").first().focus();
    await page.keyboard.press("ArrowDown");
    await expect(page.getByRole("treeitem").nth(1)).toBeFocused();
    info.annotations.push({ type: "widths-1280", description: JSON.stringify({ open, collapsed, hidden }) });
    console.log(`explorer widths at 1280 px: ${JSON.stringify({ open, collapsed, hidden })}`);
  });
});
