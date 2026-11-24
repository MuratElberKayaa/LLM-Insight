/**
 * UI-R3: the SYNTHETIC session set (`--set ui-r3-sessions`) for the run's providers project, sent
 * at most once per run: the same raw conversation ids would map to the same pseudonyms, so a second
 * send would merge into the same three sessions and change their counts. Whichever spec needs it
 * first sends it (the visual spec sorts before the functional one).
 */
import { execFileSync } from "node:child_process";
import path from "node:path";
import { expect, type Page } from "@playwright/test";
import { backendPython } from "../setup/e2e-global-setup";
import { PROVIDERS_PROJECT, u } from "./e2e-env";

const REPO = path.resolve(__dirname, "..", "..", "..");

export async function ensureSessions(page: Page): Promise<void> {
  await page.goto(u("/sessions", PROVIDERS_PROJECT));
  await expect(page.getByRole("heading", { level: 1, name: "Sessions" })).toBeVisible();
  if ((await page.getByTestId("session-link").count()) > 0) return;
  const state = process.env.LLMI_E2E_STATE;
  expect(state, "the global setup's state file").toBeTruthy();
  const out = execFileSync(backendPython(), [path.join(REPO, "scripts", "dev", "e2e_fixtures.py"), "add-trace", "--set", "ui-r3-sessions", "--state", state!], {
    cwd: REPO,
    encoding: "utf8",
    timeout: 120_000,
  });
  const id = (JSON.parse(out.trim().split(/\r?\n/).pop() ?? "{}") as { trace?: string }).trace ?? "";
  expect(id).toMatch(/^[0-9a-f]{32}$/);
}
