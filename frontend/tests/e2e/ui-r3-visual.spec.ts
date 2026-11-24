/**
 * UI-R3 visual regression (local only; not discovered under LLMI_E2E_CI=1, see playwright.config.ts).
 *
 * Deterministic inputs: SYNTHETIC fixtures (the comparison project's fixed set and the session set
 * the functional spec sends), fixed viewports and color schemes, no animation, the caret hidden.
 * Everything that changes between runs is masked: times and windows, the context bar, the project
 * selector (run-specific names), pseudonyms and ids. Comparison tolerates 1.5 % of pixels with a
 * per-pixel color threshold of 0.2 (playwright.config.ts `expect.toHaveScreenshot`), so a font
 * hinting or sub-pixel change does not fail the run, while a layout or color change does.
 * Baselines: tests/e2e/ui-r3-visual.spec.ts-snapshots/ (msedge on Windows).
 */
import { expect, test, type Page } from "@playwright/test";
import { PROVIDERS_PROJECT, comparisonProject, u } from "./e2e-env";
import { ensureSessions } from "./ui-r3-fixtures";

test.describe.configure({ mode: "serial" });

function masks(page: Page) {
  return [
    page.getByTestId("fetched-at"),
    page.getByTestId("window"),
    page.getByTestId("context-bar"),
    page.getByTestId("project-bar"),
    page.getByTestId("auto-refresh"),
    page.getByTestId("rail-window"),
    page.getByTestId("session-link"),
    page.locator("#session-detail-heading"),
    page.getByTestId("session-facts"),
    page.locator("time"),
    page.locator('[data-testid="session-cards"] a'),
    page.getByTestId("trace-facts"),
    page.getByTestId("latest-started"),
    page.locator("main h1 + p"),
    page.getByTestId("span-details"),
  ];
}

async function ready(page: Page): Promise<void> {
  await expect(page.locator('[role="status"][aria-label^="Loading"]')).toHaveCount(0);
  await page.waitForLoadState("networkidle");
  // Charts measure their container after the first paint.
  await page.waitForTimeout(300);
}

for (const scheme of ["dark", "light"] as const) {
  test.describe(`models and sessions, desktop ${scheme}`, () => {
    test.use({ viewport: { width: 1440, height: 900 }, colorScheme: scheme });

    test(`models ${scheme}`, async ({ page }) => {
      await page.goto(u("/models", comparisonProject()));
      await page.getByTestId("compare-table").getByTestId("group-pair-a").nth(0).click();
      await expect(page).toHaveURL(/[?&]a=/); // the A link navigates; B must come from the new page
      await page.getByTestId("compare-table").getByTestId("group-pair-b").nth(1).click();
      await expect(page).toHaveURL(/[?&]b=/);
      await expect(page.getByTestId("pair-table")).toBeVisible();
      await page.getByTestId("model-pair").scrollIntoViewIfNeeded();
      await ready(page);
      await expect(page.getByTestId("model-pair")).toHaveScreenshot(`models-pair-${scheme}.png`, { mask: masks(page) });
      await expect(page.locator('[data-chart="group-latency"]')).toHaveScreenshot(`models-latency-${scheme}.png`, { mask: masks(page) });
    });

    test(`sessions ${scheme}`, async ({ page }) => {
      await ensureSessions(page);
      await page.goto(u("/sessions", PROVIDERS_PROJECT));
      await page.getByTestId("session-table").getByTestId("session-inspect").first().click();
      await expect(page.getByTestId("session-timeline-row")).toHaveCount(3);
      await ready(page);
      await expect(page.getByTestId("session-detail")).toHaveScreenshot(`sessions-detail-${scheme}.png`, { mask: masks(page) });
    });
  });
}

test.describe("phone width", () => {
  test.use({ viewport: { width: 390, height: 844 }, colorScheme: "dark" });

  test("models and sessions at 390 px", async ({ page }) => {
    await page.goto(u("/models", comparisonProject()));
    await ready(page);
    await expect(page.getByTestId("group-cards").locator("li").first()).toHaveScreenshot("models-card-390.png", { mask: masks(page) });
    await ensureSessions(page);
    await page.goto(u("/sessions", PROVIDERS_PROJECT));
    await page.getByTestId("session-cards").getByTestId("session-inspect").first().click();
    await expect(page.getByTestId("session-timeline-row")).toHaveCount(3);
    await ready(page);
    await expect(page.getByTestId("session-timeline")).toHaveScreenshot("sessions-timeline-390.png", { mask: masks(page) });
  });
});

test.describe("trace explorer at 1280 px", () => {
  test.use({ viewport: { width: 1280, height: 800 }, colorScheme: "dark" });

  test("sidebar open and collapsed", async ({ page }) => {
    await page.goto(u("/traces", comparisonProject()));
    // One fixed trace of the comparison set (the same span tree in every run).
    await page.getByTestId("trace-table").getByTestId("trace-link").filter({ hasText: "cmp8a6.openai" }).first().click();
    await expect(page.getByRole("tree")).toBeVisible();
    await ready(page);
    await expect(page.getByTestId("app-sidebar")).toHaveScreenshot("sidebar-expanded.png", { mask: masks(page) });
    await expect(page.getByTestId("explorer-spans-panel")).toHaveScreenshot("explorer-spans-1280-open.png", { mask: masks(page) });
    await page.getByTestId("sidebar-toggle").click();
    await expect(page.getByTestId("trace-rail")).toBeVisible();
    await ready(page);
    await expect(page.getByTestId("app-sidebar")).toHaveScreenshot("sidebar-collapsed.png", { mask: masks(page) });
    await expect(page.getByTestId("explorer-spans-panel")).toHaveScreenshot("explorer-spans-1280-collapsed.png", { mask: masks(page) });
  });
});
