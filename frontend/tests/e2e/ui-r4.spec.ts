/**
 * UI-R4 (evaluation workspace and the whole-frontend pass), live, SYNTHETIC data only: the run's
 * main project already holds the datasets, cases and mock experiments the Phase 4B/4D specs
 * created (they sort before this file). Nothing here creates, runs or deletes anything.
 * - Datasets: facts, breadcrumb and workflow strip, case filters on the loaded page, case
 *   navigation, trace → review → case lineage.
 * - Experiments: "Synthetic / Offline Mock" on every mock run, imported reports labelled
 *   read-only, breadcrumbs and the dataset link of a run.
 * - Whole frontend: every page at 1280 px (sidebar open and collapsed) and 390 px, dark and light:
 *   one h1, no horizontal scroll, the skip link first, no secret in the page.
 * Screenshots go to LLMI_SCREENSHOT_DIR when set.
 */
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { SHOTS, TOKEN, u } from "./e2e-env";

test.describe.configure({ mode: "serial" });

async function noSecrets(page: Page, name: string): Promise<void> {
  const html = await page.content();
  expect(TOKEN.length).toBeGreaterThan(31);
  expect(html, name).not.toContain(TOKEN);
  expect(html, name).not.toMatch(/Bearer\s+[A-Za-z0-9._-]{16,}|postgres(?:ql)?(?:\+\w+)?:\/\/[^\s"<]*:[^\s"<@]+@/i);
}

async function settle(page: Page): Promise<void> {
  await expect(page.locator('[role="status"][aria-label^="Loading"]')).toHaveCount(0);
  await page.waitForLoadState("networkidle");
}

async function shot(page: Page, name: string, fullPage = true): Promise<void> {
  await settle(page);
  await noSecrets(page, name);
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage });
}

async function overflow(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
}

/** The dataset of the main project with the most cases (the Phase 4D golden fixture). */
async function biggestDataset(page: Page): Promise<string> {
  await page.goto(u("/datasets"));
  const rows = page.getByTestId("dataset-row");
  const n = await rows.count();
  expect(n, "datasets created by the Phase 4B/4D specs").toBeGreaterThan(0);
  let best = { href: "", cases: -1 };
  for (let i = 0; i < n; i += 1) {
    const cases = Number((await rows.nth(i).locator("td").first().textContent()) ?? "0");
    const href = (await rows.nth(i).locator("a").first().getAttribute("href")) ?? "";
    if (cases > best.cases) best = { href, cases };
  }
  return best.href;
}

test.describe("evaluation workspace at desktop width", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test("datasets: facts, trail, workflow, filters on the loaded page, case navigation and lineage", async ({ page }) => {
    await page.goto(u("/datasets"));
    await expect(page.getByTestId("breadcrumbs").locator('[aria-current="page"]')).toHaveText("Datasets");
    const flow = page.getByTestId("evaluation-flow");
    await expect(flow.locator('[data-step="dataset"]')).toHaveAttribute("data-current", "true");
    await expect(flow.getByRole("link")).toHaveCount(2); // traces and experiments: the pages that exist
    await expect(page.getByTestId("datasets-facts")).toBeVisible();
    await shot(page, "ui-r4-datasets-dark-1280");

    await page.goto(await biggestDataset(page));
    const cards = page.getByTestId("test-case");
    const total = await cards.count();
    expect(total).toBeGreaterThan(1);
    await expect(page.getByTestId("case-index").getByTestId("case-index-link")).toHaveCount(total);
    await expect(page.getByTestId("dataset-facts")).toBeVisible();
    await expect(page.getByTestId("case-input-text").first()).toBeVisible();
    await expect(page.locator("main")).toContainText("not a captured user conversation");
    await shot(page, "ui-r4-dataset-detail-dark-1280");

    // A rule filter narrows the loaded cases and keeps the dataset URL; "Clear" restores them.
    const ruleGroup = page.getByRole("group", { name: "Rule" });
    const options = ruleGroup.getByRole("link");
    let filtered = false;
    for (let i = 1; i < (await options.count()); i += 1) {
      await options.nth(i).click();
      await expect(page).toHaveURL(/[?&]rule=/);
      const shown = await page.getByTestId("test-case").count();
      if (shown > 0 && shown < total) {
        await expect(page.getByTestId("case-filter-result")).toContainText(`Showing ${shown} of ${total}`);
        filtered = true;
        break;
      }
    }
    if (filtered) {
      await page.getByTestId("case-filter-result").getByRole("link", { name: "Clear filters" }).click();
      await expect(page.getByTestId("test-case")).toHaveCount(total);
    }

    // Case navigation: next moves to the following card's anchor, and focus follows.
    const firstNext = page.getByTestId("case-next").first();
    const target = (await firstNext.getAttribute("href")) ?? "";
    await firstNext.click();
    await expect(page).toHaveURL(new RegExp(`${target}$`));
    await expect(page.locator(target)).toBeFocused();
    // From the index, by mouse and by keyboard (Enter on the link): the chosen card has focus.
    const indexLinks = page.getByTestId("case-index").getByTestId("case-index-link");
    const last = indexLinks.last();
    const lastTarget = (await last.getAttribute("href")) ?? "";
    await last.click();
    await expect(page.locator(lastTarget)).toBeFocused();
    const firstLink = indexLinks.first();
    const firstTarget = (await firstLink.getAttribute("href")) ?? "";
    await firstLink.focus();
    await page.keyboard.press("Enter");
    await expect(page.locator(firstTarget)).toBeFocused();
    // A deep link to a case focuses it on load.
    await page.goto(`${page.url().split("#")[0]}${lastTarget}`);
    await expect(page.locator(lastTarget)).toBeFocused();

    // Lineage of a converted case: trace, then its review section.
    const review = page.getByTestId("case-origin-review").first();
    if ((await review.count()) > 0) await expect(review).toHaveAttribute("href", /^\/traces\/[0-9a-f]{32}\?project=\d+#reviews-heading$/);
    expect(await overflow(page)).toBeLessThanOrEqual(0);
  });

  test("experiments: mock runs labelled synthetic offline, imported reports read-only, run → dataset", async ({ page }) => {
    await page.goto(u("/experiments"));
    await expect(page.getByTestId("breadcrumbs").locator('[aria-current="page"]')).toHaveText("Experiments");
    const table = page.getByTestId("experiment-table");
    const runs = table.getByTestId("experiment-link");
    expect(await runs.count()).toBeGreaterThan(0);
    // Every mock row says so, in words.
    await expect(table.locator('[data-testid="mode-badge"][data-mode="real"]')).toHaveCount(0);
    await expect(table.getByTestId("mode-badge").first()).toContainText("Synthetic / Offline Mock");
    await expect(page.getByTestId("imported-experiments").getByTestId("imported-badge")).toContainText("Imported report · read-only");
    await expect(page.getByTestId("imported-experiments")).toContainText("cannot be started or re-run here");
    // Final stabilization: at 1280 px with the sidebar open the table fits; Started is fully in view.
    const region = page.getByRole("region", { name: "Experiments of this project, newest first" });
    expect(await region.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
    const started = await table.getByTestId("experiment-started").first().boundingBox();
    const box = await region.boundingBox();
    expect(started && box && started.x + started.width <= box.x + box.width).toBe(true);
    await shot(page, "ui-r4-experiments-dark-1280");

    await runs.first().click();
    await expect(page.getByTestId("experiment-synthetic-banner")).toContainText("SYNTHETIC · mock provider · not a real model");
    await expect(page.getByTestId("breadcrumbs").locator('[aria-current="page"]')).toHaveText(/^#\d+$/);
    await expect(page.getByTestId("mode-badge")).toContainText("Synthetic / Offline Mock");
    const datasetLink = page.getByTestId("evaluation-flow").locator('[data-step="dataset"] a');
    await expect(datasetLink).toHaveAttribute("href", /^\/datasets\/\d+\?project=\d+$/);
    await expect(page.getByTestId("version-cards")).toBeVisible();
    await expect(page.locator("main")).not.toContainText(/statistically better|winner is|best prompt/i);
    await shot(page, "ui-r4-experiment-detail-dark-1280");
    await datasetLink.click();
    await expect(page.getByTestId("test-case").first()).toBeVisible();
  });
});

test.describe("evaluation workspace in the light theme and at phone width", () => {
  test("light theme: dataset and experiment", async ({ browser }) => {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 }, colorScheme: "light" });
    await page.goto(await biggestDataset(page));
    await expect(page.getByTestId("case-index")).toBeVisible();
    await shot(page, "ui-r4-dataset-detail-light-1280");
    await page.goto(u("/experiments"));
    await page.getByTestId("experiment-link").first().click();
    await expect(page.getByTestId("version-cards")).toBeVisible();
    await shot(page, "ui-r4-experiment-detail-light-1280");
    await page.close();
  });

  test("phone width: datasets as cards, a dataset and an experiment without horizontal scroll", async ({ browser }) => {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, colorScheme: "dark" });
    await page.goto(u("/datasets"));
    await expect(page.getByTestId("dataset-cards")).toBeVisible();
    await settle(page);
    expect(await overflow(page)).toBeLessThanOrEqual(0);
    await shot(page, "ui-r4-datasets-mobile-390");
    await page.goto(await biggestDataset(page));
    await settle(page);
    expect(await overflow(page)).toBeLessThanOrEqual(0);
    await page.goto(u("/experiments"));
    await page.getByTestId("experiment-link").first().click();
    await expect(page.getByTestId("version-cards")).toBeVisible();
    await settle(page);
    expect(await overflow(page)).toBeLessThanOrEqual(0);
    await shot(page, "ui-r4-experiment-mobile-390");
    await page.close();
  });
});

const PAGES = ["/", "/traces", "/sessions", "/models", "/datasets", "/experiments"] as const;

test.describe("whole-frontend pass", () => {
  for (const [name, viewport, scheme] of [
    ["1280-dark", { width: 1280, height: 800 }, "dark"],
    ["1280-light", { width: 1280, height: 800 }, "light"],
    ["390-dark", { width: 390, height: 844 }, "dark"],
  ] as const) {
    test(`every page at ${name}: one h1, no horizontal scroll, skip link first, no secret`, async ({ browser }) => {
      const page = await browser.newPage({ viewport, colorScheme: scheme });
      const extra: string[] = [];
      await page.goto(u("/traces"));
      const trace = await page.getByTestId("trace-link").first().getAttribute("href");
      if (trace) extra.push(trace);
      await page.goto(u("/datasets"));
      extra.push(await biggestDataset(page));
      await page.goto(u("/experiments"));
      const exp = await page.getByTestId("experiment-link").first().getAttribute("href");
      if (exp) extra.push(exp);
      for (const p of [...PAGES.map((x) => u(x)), ...extra]) {
        await page.goto(p);
        await settle(page);
        await expect(page.locator("h1"), p).toHaveCount(1);
        expect(await overflow(page), p).toBeLessThanOrEqual(0);
        await noSecrets(page, p);
        await page.keyboard.press("Tab");
        await expect(page.getByRole("link", { name: "Skip to content" }), p).toBeFocused();
      }
      await page.close();
    });
  }

  test("sidebar collapsed at 1280: every page keeps named navigation and the collapsed rail across pages", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto(u("/"));
    await page.getByTestId("sidebar-toggle").click();
    const nav = page.getByRole("navigation", { name: "Primary" });
    for (const name of ["Traces", "Sessions", "Models", "Datasets", "Experiments", "Overview"]) {
      await nav.getByRole("link", { name }).click();
      await expect(page.getByTestId("app-shell")).toHaveAttribute("data-sidebar", "collapsed");
      await expect(nav.getByRole("link", { name })).toHaveAttribute("aria-current", "page");
      await settle(page);
      expect(await overflow(page), name).toBeLessThanOrEqual(0);
    }
    await page.goto(u("/datasets"));
    await page.getByTestId("sidebar-toggle").click();
    await shot(page, "ui-r4-datasets-sidebar-collapsed-1280", false);
  });
});
