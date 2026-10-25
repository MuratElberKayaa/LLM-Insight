/**
 * Playwright global setup (Phase 3C, R41): create this run's projects and SYNTHETIC traces
 * through the real backend (scripts/dev/e2e_fixtures.py), then expose their ids to the tests.
 * The teardown deletes exactly these projects. No key or token passes through here.
 */
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const REPO = path.resolve(__dirname, "..", "..", "..");

export function backendPython(): string {
  if (process.env.LLMI_E2E_PYTHON) return process.env.LLMI_E2E_PYTHON;
  const win = path.join(REPO, "backend", ".venv", "Scripts", "python.exe");
  return existsSync(win) ? win : path.join(REPO, "backend", ".venv", "bin", "python");
}

export default function globalSetup(): void {
  const state = path.join(os.tmpdir(), `llminsight-e2e-${process.pid}.json`);
  const out = execFileSync(backendPython(), [path.join(REPO, "scripts", "dev", "e2e_fixtures.py"), "setup", "--state", state], {
    cwd: REPO,
    encoding: "utf8",
    timeout: 180_000,
  });
  const result = JSON.parse(out.trim().split(/\r?\n/).pop() ?? "{}") as { run: string; projects: Record<string, number> };
  process.env.LLMI_E2E_STATE = state;
  process.env.LLMI_E2E_RUN = result.run;
  process.env.LLMI_E2E_PROJECT = String(result.projects.main);
  process.env.LLMI_E2E_FOREIGN_PROJECT = String(result.projects.foreign);
  process.env.LLMI_E2E_HOSTILE_PROJECT = String(result.projects.hostile);
  console.log(`[e2e] run ${result.run}: projects main=${result.projects.main} foreign=${result.projects.foreign} hostile=${result.projects.hostile} (SYNTHETIC)`);
}
