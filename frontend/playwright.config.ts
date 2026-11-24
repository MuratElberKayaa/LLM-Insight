import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { defineConfig } from "@playwright/test";
import { e2eBrowser } from "./tests/setup/e2e-browser";
import { backendPython } from "./tests/setup/e2e-global-setup";
import { decideVisual, parseManifest, type VisualDecision } from "./tests/setup/visual-policy";

/**
 * Browser-to-frontend-to-backend tests against the REAL local stack (no mocked responses):
 * - the backend (`python -m app` on 127.0.0.1:8000) and PostgreSQL must already be running;
 * - the dashboard on 127.0.0.1:3001, started here from the current build (port must be free);
 * - a second dashboard instance on 127.0.0.1:3002 whose backend URL points to a closed
 *   loopback port, for the backend-outage state.
 * - a third instance on 127.0.0.1:3003 with a wrong (well-formed) server-side token, for the
 *   unauthorized state (Phase 3B). Process env overrides frontend/.env.local in Next.js.
 * - Phase 5A (T43): a second backend on 127.0.0.1:8001 with a tiny /api/v1 bucket and a fourth
 *   dashboard on 127.0.0.1:3004 reading through it, for the live rate-limited state.
 *   Process env overrides the backend's .env too (pydantic-settings).
 * - Phase 6C-C: a third backend on 127.0.0.1:8002 (6-token /api/v1 bucket, one more every 1000 s)
 *   and a fifth dashboard on 127.0.0.1:3005 reading through it, for the refresh-after-429 test
 *   (a first load succeeds, then the test drains the bucket and refreshes).
 * Uses the installed Microsoft Edge (`channel: "msedge"`), so no browser download is needed;
 * CI (R104-5, `LLMI_E2E_CI=1`) uses the Chromium bundled with Playwright (tests/setup/e2e-browser.ts).
 * One worker: the backend's /api/v1 rate limit is per client address.
 * Test data (Phase 3C, R41): the global setup creates this run's own projects and seeds them
 * with SYNTHETIC traces at current UTC times (scripts/dev/e2e_fixtures.py); the teardown deletes
 * exactly those projects, even when tests fail. Nothing else in the dev database is touched.
 */
/**
 * Visual-regression discovery (tests/setup/visual-policy.ts, manifest tests/e2e/visual-baselines.json).
 * Locally (msedge on Windows) every visual spec runs. Under LLMI_E2E_CI=1 an accepted spec must run
 * and a pending one is listed as NOT RUN; a broken acceptance stops the run here, before any test.
 * LLMI_VISUAL_LINUX=1 is the capture run of .github/workflows/visual-baselines.yml only.
 */
function visualSelection(): VisualDecision {
  const dir = path.join(__dirname, "tests", "e2e");
  const specs = readdirSync(dir)
    .filter((f) => f.endsWith("-visual.spec.ts"))
    .map((spec) => {
      const snaps = path.join(dir, `${spec}-snapshots`);
      return { spec, snapshots: existsSync(snaps) ? readdirSync(snaps) : [] };
    });
  const manifest = parseManifest(readFileSync(path.join(dir, "visual-baselines.json"), "utf8"));
  return decideVisual({ ci: process.env.LLMI_E2E_CI === "1", capture: process.env.LLMI_VISUAL_LINUX === "1" }, specs, manifest);
}
const VISUAL = visualSelection();
if (VISUAL.errors.length > 0) {
  throw new Error(`[visual-regression] refused: ${VISUAL.errors.join("; ")}`);
}
for (const { spec, reason } of VISUAL.notRun) {
  console.warn(`[visual-regression] NOT RUN on this platform: ${spec}: ${reason}. See docs/reports/frontend-final-stabilization.md.`);
}
const VISUAL_IGNORE = VISUAL.notRun.map((n) => `**/${n.spec}`);

export default defineConfig({
  testDir: "./tests/e2e",
  // Visual specs: see visualSelection() above. A pending spec is not discovered in CI (the gate
  // counts a skipped test as a failure) and the run says so in its log.
  testIgnore: VISUAL_IGNORE,
  // CI never writes a missing baseline (a written baseline would "pass" without a comparison).
  updateSnapshots: process.env.LLMI_E2E_CI === "1" && process.env.LLMI_VISUAL_LINUX !== "1" ? "none" : "missing",
  expect: { toHaveScreenshot: { maxDiffPixelRatio: 0.015, threshold: 0.2, animations: "disabled", caret: "hide", scale: "css" } },
  globalSetup: "./tests/setup/e2e-global-setup.ts",
  globalTeardown: "./tests/setup/e2e-global-teardown.ts",
  outputDir: "./test-results",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:3001",
    ...e2eBrowser(process.env),
    headless: true,
    colorScheme: "dark",
    timezoneId: "Europe/Istanbul", // a non-UTC zone: the UI must still show UTC
    trace: "off",
  },
  webServer: [
    {
      command: "node scripts/next.mjs start --hostname 127.0.0.1 --port 3001",
      url: "http://127.0.0.1:3001/",
      reuseExistingServer: false, // always the current build; fails if 3001 is busy
      timeout: 60_000,
    },
    {
      command: "node scripts/next.mjs start --hostname 127.0.0.1 --port 3002",
      url: "http://127.0.0.1:3002/",
      reuseExistingServer: false,
      timeout: 60_000,
      env: { LLMI_API_BASE_URL: "http://127.0.0.1:59999" },
    },
    {
      command: "node scripts/next.mjs start --hostname 127.0.0.1 --port 3003",
      url: "http://127.0.0.1:3003/icon.svg", // static: readiness polls never reach the backend
      reuseExistingServer: false,
      timeout: 60_000,
      env: { LLMI_API_TOKEN: "e2e-deliberately-wrong-token-0000000000000000" },
    },
    // Phase 5A (T43, dashboard half): a second backend with a tiny /api/v1 bucket (5 tokens, one
    // more every 1000 s) on the same database, and a dashboard on :3004 that reads through it.
    {
      command: `"${backendPython()}" -m app`,
      cwd: path.join(__dirname, "..", "backend"),
      url: "http://127.0.0.1:8001/health/live",
      reuseExistingServer: false,
      timeout: 60_000,
      env: { LLMI_PORT: "8001", LLMI_RATE_LIMIT_API_RPS: "0.001", LLMI_RATE_LIMIT_API_BURST: "5" },
    },
    {
      command: "node scripts/next.mjs start --hostname 127.0.0.1 --port 3004",
      url: "http://127.0.0.1:3004/icon.svg",
      reuseExistingServer: false,
      timeout: 60_000,
      env: { LLMI_API_BASE_URL: "http://127.0.0.1:8001" },
    },
    // Phase 6C-C: own bucket, so the 429-after-success test does not depend on the Phase 5A drain.
    {
      command: `"${backendPython()}" -m app`,
      cwd: path.join(__dirname, "..", "backend"),
      url: "http://127.0.0.1:8002/health/live",
      reuseExistingServer: false,
      timeout: 60_000,
      env: { LLMI_PORT: "8002", LLMI_RATE_LIMIT_API_RPS: "0.001", LLMI_RATE_LIMIT_API_BURST: "6" },
    },
    {
      command: "node scripts/next.mjs start --hostname 127.0.0.1 --port 3005",
      url: "http://127.0.0.1:3005/icon.svg",
      reuseExistingServer: false,
      timeout: 60_000,
      env: { LLMI_API_BASE_URL: "http://127.0.0.1:8002" },
    },
  ],
});
