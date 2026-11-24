/**
 * UI-R4 visual regression: the evaluation workspace, dark and light, 1280 px and 390 px.
 *
 * Deterministic inputs: the SYNTHETIC golden fixture the Phase 4D spec stores (12 fixed cases) and
 * its mock experiments (a scripted provider: same outputs in every run), fixed viewports and
 * schemes, a fixed browser clock, no animation, the caret hidden. Masked: anything that differs
 * per run (times, project and dataset names with the run id, database ids, the context bar).
 * Tolerances come from playwright.config.ts. Baselines are per platform (`-win32` here; `-linux`
 * only when captured on a CI-equivalent runner, see docs/reports/phase-ui-r4.md).
 */
import { expect, test, type Page } from "@playwright/test";
import { u } from "./e2e-env";

test.describe.configure({ mode: "serial" });

const FIXED = new Date("2026-11-20T12:00:00Z");

function masks(page: Page) {
  return [
    page.getByTestId("context-bar"),
    page.getByTestId("breadcrumbs"),
    page.getByTestId("project-bar"),
    page.locator("main h1"),
    page.locator("time"),
    page.getByTestId("case-index").locator("tbody th"),
    page.getByTestId("case-index").locator("tbody td:nth-child(3)"),
    page.getByTestId("case-index").locator("tbody td:nth-child(6)"),
    page.getByTestId("experiment-summary"),
    page.getByTestId("dataset-table").locator("tbody"),
  ];
}

async function ready(page: Page): Promise<void> {
  await expect(page.locator('[role="status"][aria-label^="Loading"]')).toHaveCount(0);
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(300);
}

/** The golden fixture dataset: the one with the most cases (12, created by the Phase 4D spec). */
async function golden(page: Page): Promise<string> {
  await page.goto(u("/datasets"));
  const rows = page.getByTestId("dataset-row");
  let best = { href: "", cases: -1 };
  for (let i = 0; i < (await rows.count()); i += 1) {
    const cases = Number((await rows.nth(i).locator("td").first().textContent()) ?? "0");
    if (cases > best.cases) best = { href: (await rows.nth(i).locator("a").first().getAttribute("href")) ?? "", cases };
  }
  return best.href;
}

for (const scheme of ["dark", "light"] as const) {
  test.describe(`evaluation workspace ${scheme}`, () => {
    test.use({ viewport: { width: 1280, height: 800 }, colorScheme: scheme });

    test(`dataset facts, filters and the first case (${scheme})`, async ({ page }) => {
      await page.clock.setFixedTime(FIXED);
      await page.goto(await golden(page));
      await ready(page);
      await expect(page.getByTestId("dataset-facts")).toHaveScreenshot(`dataset-facts-${scheme}.png`, { mask: masks(page) });
      await expect(page.getByTestId("case-filters")).toHaveScreenshot(`dataset-filters-${scheme}.png`, { mask: masks(page) });
      await expect(page.getByTestId("test-case").first()).toHaveScreenshot(`dataset-case-${scheme}.png`, { mask: [...masks(page), page.getByTestId("case-lineage"), page.getByTestId("test-case").first().locator(":scope > p")] });
    });

    test(`experiment version cards (${scheme})`, async ({ page }) => {
      await page.clock.setFixedTime(FIXED);
      await page.goto(u("/experiments"));
      // The oldest mock run is the Phase 4D golden A/B (fixed scripted outputs).
      await page.getByTestId("experiment-link").last().click();
      await expect(page.getByTestId("version-cards")).toBeVisible();
      await ready(page);
      await expect(page.getByTestId("version-cards")).toHaveScreenshot(`experiment-versions-${scheme}.png`, { mask: masks(page) });
    });
  });
}

test.describe("evaluation workspace at 390 px", () => {
  test.use({ viewport: { width: 390, height: 844 }, colorScheme: "dark" });

  test("dataset facts at phone width", async ({ page }) => {
    await page.clock.setFixedTime(FIXED);
    await page.goto(await golden(page));
    await ready(page);
    await expect(page.getByTestId("dataset-facts")).toHaveScreenshot("dataset-facts-390.png", { mask: masks(page) });
  });
});
