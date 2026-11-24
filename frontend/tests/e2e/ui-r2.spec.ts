/**
 * UI-R2 (trace and agent explorer), live: browser → Next.js → FastAPI → PostgreSQL.
 * - the trace rail marks the open trace and keeps the listing (filters, window) in every link;
 * - span deep links (`?span=`) survive reloads and rail navigation keeps `list`;
 * - overview metric links open the trace list with the same project, window and provenance;
 * - an agent trace (SYNTHETIC `--set ui-r2-agent`, sent during the test to the run's providers
 *   project) shows every step family, a possible loop only as a hypothesis, and the recorded
 *   time to first chunk; nothing about tool arguments, results or prompts is shown;
 * - narrow screens get the rail as a disclosure and the Spans / Selected span switch.
 * Nothing is mocked; no provider is called. Screenshots go to LLMI_SCREENSHOT_DIR when set.
 */
import { execFileSync } from "node:child_process";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { backendPython } from "../setup/e2e-global-setup";
import { PROVIDERS_PROJECT, SHOTS, TOKEN, u } from "./e2e-env";

const REPO = path.resolve(__dirname, "..", "..", "..");

test.describe.configure({ mode: "serial" });

let agentTrace = "";

function addAgentTrace(): string {
  const state = process.env.LLMI_E2E_STATE;
  expect(state, "the global setup's state file").toBeTruthy();
  const out = execFileSync(backendPython(), [path.join(REPO, "scripts", "dev", "e2e_fixtures.py"), "add-trace", "--set", "ui-r2-agent", "--state", state!], {
    cwd: REPO,
    encoding: "utf8",
    timeout: 120_000,
  });
  const id = (JSON.parse(out.trim().split(/\r?\n/).pop() ?? "{}") as { trace?: string }).trace ?? "";
  expect(id).toMatch(/^[0-9a-f]{32}$/);
  return id;
}

async function shot(page: Page, name: string): Promise<void> {
  await expect(page.locator('[role="status"][aria-label^="Loading"]')).toHaveCount(0);
  await page.waitForLoadState("networkidle");
  // No secret on a page that is captured: the server-side token, a bearer header or a database URL.
  const html = await page.content();
  expect(TOKEN.length).toBeGreaterThan(31);
  expect(html, name).not.toContain(TOKEN);
  expect(html, name).not.toMatch(/Bearer\s+[A-Za-z0-9._-]{16,}|postgres(?:ql)?(?:\+\w+)?:\/\/[^\s"<]*:[^\s"<@]+@/i);
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });
}

async function maxOverflow(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
}

test.describe("trace explorer at desktop width", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("the rail marks the open trace and keeps the listing; span deep links survive", async ({ page }) => {
    await page.goto(u("/traces?status=error"));
    const first = page.getByTestId("trace-table").getByTestId("trace-link").first();
    const id = (await first.getAttribute("data-trace-id")) ?? "";
    expect(id).toMatch(/^[0-9a-f]{32}$/);
    await first.click();
    await expect(page).toHaveURL(new RegExp(`/traces/${id}\\?project=\\d+&list=status%3Derror`));
    const rail = page.getByRole("navigation", { name: "Traces in this list" });
    await expect(rail).toBeVisible();
    await expect(rail.locator('[aria-current="page"]')).toHaveAttribute("data-trace-id", id);
    await expect(rail).toContainText("status error");
    // Every row of the rail is an error trace (the listing's filter applies), each with its own provenance text.
    await expect(rail.getByTestId("rail-trace").first()).toContainText("Error:");
    await expect(page.getByTestId("context-bar")).toContainText("status error");

    // Select a span: the URL keeps `list` and gains `span`; a reload restores both.
    await page.getByRole("treeitem").nth(1).click();
    const spanId = (await page.getByTestId("span-details").getAttribute("data-span-id")) ?? "";
    await expect(page).toHaveURL(new RegExp(`list=status%3Derror.*[?&]span=${spanId}$`));
    await page.reload();
    await expect(page.getByTestId("span-details")).toHaveAttribute("data-span-id", spanId);
    await expect(rail.locator('[aria-current="page"]')).toHaveAttribute("data-trace-id", id);
    await expect(page.getByTestId("back-to-traces")).toHaveAttribute("href", /status=error/);

    // Another trace from the rail: the selection moves, the listing stays.
    if ((await rail.getByTestId("rail-trace").count()) > 1) {
      const next = rail.locator('[data-testid="rail-trace"]:not([aria-current])').first();
      const nextId = (await next.getAttribute("data-trace-id")) ?? "";
      await next.click();
      await expect(page).toHaveURL(new RegExp(`/traces/${nextId}\\?project=\\d+&list=status%3Derror`));
      await expect(rail.locator('[aria-current="page"]')).toHaveAttribute("data-trace-id", nextId);
    }
    expect(await maxOverflow(page)).toBeLessThanOrEqual(0);
  });

  test("overview metric links open the same scope in the trace list", async ({ page }) => {
    await page.goto(u("/?range=7d&provenance=synthetic"));
    await expect(page.getByTestId("context-bar")).toContainText("Synthetic only");
    const link = page.getByTestId("kpi-traces-link");
    await expect(link).toHaveAttribute("href", /\/traces\?project=\d+&range=7d&provenance=synthetic$/);
    await link.click();
    await expect(page.getByRole("heading", { level: 1, name: "Traces" })).toBeVisible();
    await expect(page).toHaveURL(/range=7d/);
    await expect(page).toHaveURL(/provenance=synthetic/);
    await expect(page.getByRole("link", { name: "Last 7 days" })).toHaveAttribute("aria-current", "true");
    await expect(page.getByTestId("context-bar")).toContainText("Synthetic only");
    await page.goto(u("/?range=7d"));
    const errors = page.getByTestId("kpi-error-traces-link");
    if ((await errors.count()) > 0) {
      await expect(errors).toHaveAttribute("href", /\/traces\?project=\d+&range=7d&status=error$/);
    }
    // Recent traces open the trace page with the overview's listing as `list`.
    const recent = page.getByTestId("recent-traces").getByRole("link").first();
    if ((await recent.count()) > 0) await expect(recent).toHaveAttribute("href", /list=range%3D7d/);
  });

  test("an agent trace: step families, a possible loop as a hypothesis, the recorded TTFT", async ({ page }) => {
    agentTrace = addAgentTrace();
    await page.goto(u(`/traces/${agentTrace}`, PROVIDERS_PROJECT));
    const families = page.getByTestId("agent-families");
    for (const [f, n] of [
      ["agent", "1"],
      ["llm", "1"],
      ["tool", "7"],
      ["retrieval", "1"],
      ["mcp", "1"],
    ] as const) {
      await expect(families.locator(`li[data-family="${f}"]`)).toHaveAttribute("data-count", n);
    }
    const notice = page.getByTestId("agent-loop-notice");
    await expect(notice).toContainText("Possible loop (hypothesis, not verified)");
    await expect(notice).toContainText("lookup_order");
    await expect(notice).toContainText("may be intended");
    await expect(page.getByTestId("tool-loop-flag")).toHaveCount(1);
    // The diagnostics rule fired too; the notice links its finding.
    await expect(notice.getByRole("link")).toHaveAttribute("href", /^#finding-repeated_tool_calls/);
    await expect(page.locator('[data-testid="diagnostic-finding"][data-category="repeated_tool_calls"]')).toHaveCount(1);

    // The tree tells the families apart; the three longest spans are marked.
    await expect(page.locator('[role="treeitem"][data-family="tool"]')).toHaveCount(7);
    await expect(page.locator('[role="treeitem"][data-family="mcp"]')).toHaveCount(1);
    await expect(page.getByTestId("span-slow")).toHaveCount(3);

    // The LLM attempt: its recorded time to first chunk, never an estimate.
    await page.locator('[role="treeitem"][data-family="llm"]').click();
    await expect(page.getByTestId("span-details-family")).toHaveAttribute("data-family", "llm");
    await expect(page.getByTestId("span-ttft")).toHaveAttribute("data-ttft-state", "recorded");
    await expect(page.getByTestId("span-ttft")).toHaveText("250.0 ms");
    // A lookup_order execution names the loop finding as evidence.
    await page.locator('[role="treeitem"][data-family="tool"]').first().click();
    await expect(page.getByTestId("span-findings")).toHaveAttribute("data-count", "1");
    await expect(page.getByTestId("span-ttft")).toHaveCount(0); // not an inference span
    // Metadata only: no content field exists to show.
    await expect(page.locator("body")).not.toContainText(/gen_ai\.(input|output)\.messages|tool\.call\.arguments|tool\.call\.result/);
    // The agent section never states a cause (existing review-form disclaimers mention "root cause" to deny it).
    await expect(page.getByTestId("agent-steps")).not.toContainText(/root cause/i);
    expect(await maxOverflow(page)).toBeLessThanOrEqual(0);
    await shot(page, "ui-r2-agent-dark-1440");
  });
});

test.describe("trace explorer in the light theme", () => {
  test.use({ viewport: { width: 1440, height: 900 }, colorScheme: "light" });

  test("renders the agent trace and the trace list", async ({ page }) => {
    expect(agentTrace).toMatch(/^[0-9a-f]{32}$/);
    await page.goto(u(`/traces/${agentTrace}`, PROVIDERS_PROJECT));
    await expect(page.getByRole("tree")).toBeVisible();
    await shot(page, "ui-r2-agent-light-1440");
    await page.goto(u("/traces", PROVIDERS_PROJECT));
    await expect(page.getByTestId("scope-panel")).toBeVisible();
    await shot(page, "ui-r2-list-light-1440");
  });
});

test.describe("trace explorer at phone width", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("the rail is a disclosure and spans / details share one panel", async ({ page }) => {
    expect(agentTrace).toMatch(/^[0-9a-f]{32}$/);
    await page.goto(u(`/traces/${agentTrace}`, PROVIDERS_PROJECT));
    const toggle = page.getByTestId("trace-rail-toggle");
    await expect(toggle).toBeVisible();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByTestId("trace-rail")).toBeHidden();
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByTestId("trace-rail")).toBeVisible();
    await toggle.click();

    await expect(page.getByRole("tree")).toBeVisible();
    await expect(page.getByRole("complementary", { name: "Selected span" })).toBeHidden();
    await page.locator('[role="treeitem"][data-family="llm"]').click();
    await expect(page.getByRole("complementary", { name: "Selected span" })).toBeVisible();
    await expect(page.getByRole("tree")).toBeHidden();
    await expect(page.getByTestId("span-ttft")).toHaveText("250.0 ms");
    await shot(page, "ui-r2-agent-details-390");
    await page.getByTestId("explorer-panel-switch").getByRole("button", { name: "Spans" }).click();
    await expect(page.getByRole("tree")).toBeVisible();
    await page.waitForLoadState("networkidle");
    expect(await maxOverflow(page)).toBeLessThanOrEqual(0);
    await shot(page, "ui-r2-agent-spans-390");
  });
});

test.describe("trace list at desktop width (dark)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("screenshot of the list and an explorer page with the rail", async ({ page }) => {
    await page.goto(u("/traces"));
    await expect(page.getByTestId("trace-table")).toBeVisible();
    await shot(page, "ui-r2-list-dark-1440");
    await page.getByTestId("trace-table").getByTestId("trace-link").first().click();
    await expect(page.getByRole("navigation", { name: "Traces in this list" })).toBeVisible();
    await shot(page, "ui-r2-explorer-dark-1440");
  });
});
