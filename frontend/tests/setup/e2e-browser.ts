/**
 * Browser of the E2E suite. Local default: the installed Microsoft Edge. LLMI_E2E_CI=1 (only set
 * by scripts/qa/e2e_gate.sh on the GitHub runner): the Chromium that Playwright bundles for its
 * pinned version, so the browser version cannot float. There is no fallback: a browser that is
 * not installed fails the run. Chromium coverage is not identical to local Edge coverage.
 */
export type E2eBrowser = { browserName: "chromium" } | { channel: "msedge" };

export function e2eBrowser(env: Record<string, string | undefined>): E2eBrowser {
  return env.LLMI_E2E_CI === "1" ? { browserName: "chromium" } : { channel: "msedge" };
}
