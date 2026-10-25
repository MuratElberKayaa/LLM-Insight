import { defineConfig } from "@playwright/test";

/**
 * Browser-to-frontend-to-backend tests against the REAL local stack (no mocked responses):
 * - the backend (`python -m app` on 127.0.0.1:8000) and PostgreSQL must already be running;
 * - the dashboard on 127.0.0.1:3001, started here from the current build (port must be free);
 * - a second dashboard instance on 127.0.0.1:3002 whose backend URL points to a closed
 *   loopback port, for the backend-outage state.
 * - a third instance on 127.0.0.1:3003 with a wrong (well-formed) server-side token, for the
 *   unauthorized state (Phase 3B). Process env overrides frontend/.env.local in Next.js.
 * Uses the installed Microsoft Edge (`channel: "msedge"`), so no browser download is needed.
 * One worker: the backend's /api/v1 rate limit is per client address.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  outputDir: "./test-results",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:3001",
    channel: "msedge",
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
  ],
});
