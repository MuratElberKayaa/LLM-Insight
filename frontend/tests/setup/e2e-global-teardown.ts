/**
 * Playwright global teardown (Phase 3C, R41): delete exactly the projects this run created
 * (scripts/dev/e2e_fixtures.py checks id and name against the state file). Runs even when tests
 * fail. `LLMI_E2E_KEEP=1` keeps the data, for screenshots or debugging.
 */
import { execFileSync } from "node:child_process";
import path from "node:path";
import { backendPython } from "./e2e-global-setup";

const REPO = path.resolve(__dirname, "..", "..", "..");

export default function globalTeardown(): void {
  const state = process.env.LLMI_E2E_STATE;
  if (!state) return;
  const out = execFileSync(backendPython(), [path.join(REPO, "scripts", "dev", "e2e_fixtures.py"), "teardown", "--state", state], {
    cwd: REPO,
    encoding: "utf8",
    timeout: 120_000,
  });
  console.log(`[e2e] teardown: ${out.trim()}`);
}
