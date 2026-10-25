/**
 * Shared E2E environment (Phase 3C, R41). The server-side token and backend URL come from
 * frontend/.env.local (never printed); the projects come from tests/setup/e2e-global-setup.ts,
 * which creates them for this run and seeds SYNTHETIC traces with current UTC timestamps.
 * Every page and API call of the suite is scoped to these run-owned projects.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

function serverEnv(): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of readFileSync(path.join(__dirname, "..", "..", ".env.local"), "utf8").split(/\r?\n/)) {
    const i = line.indexOf("=");
    if (i > 0 && !line.startsWith("#")) out[line.slice(0, i)] = line.slice(i + 1);
  }
  return out;
}

function required(name: string): string {
  const v = process.env[name];
  if (!v || !/^[1-9]\d{0,18}$/.test(v)) throw new Error(`${name} is not set: run the suite through playwright.config.ts (global setup)`);
  return v;
}

export const ENV = serverEnv();
export const TOKEN = ENV.LLMI_API_TOKEN ?? "";
export const API = ENV.LLMI_API_BASE_URL ?? "http://127.0.0.1:8000";
/** The run's main project (edge-case + Phase 3C traces). */
export const PROJECT = required("LLMI_E2E_PROJECT");
/** The run's second project (one isolation trace). */
export const FOREIGN_PROJECT = required("LLMI_E2E_FOREIGN_PROJECT");
/** The run's project with a hostile name and no telemetry. */
export const HOSTILE_PROJECT = required("LLMI_E2E_HOSTILE_PROJECT");
export const SHOTS = process.env.LLMI_SCREENSHOT_DIR;

/** A dashboard path with `project=<id>` as the first query parameter. */
export function u(pathAndQuery: string, project: string = PROJECT): string {
  const [p, q] = pathAndQuery.split("?", 2);
  return q ? `${p}?project=${project}&${q}` : `${p}?project=${project}`;
}
