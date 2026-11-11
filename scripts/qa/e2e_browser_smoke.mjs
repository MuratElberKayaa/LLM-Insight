// R133 (CI browser): fail-closed launch check of the exact Chromium the E2E suite uses in CI.
//
// Run from frontend/ by scripts/qa/e2e_gate.sh after `npx playwright install chromium`:
//   node ../scripts/qa/e2e_browser_smoke.mjs
//
// It resolves the frontend's own pinned @playwright/test (1.64.0) and launches its bundled
// Chromium exactly as tests/setup/e2e-browser.ts selects it for LLMI_E2E_CI=1 (browserName
// "chromium", no channel, no executablePath) and headless as in playwright.config.ts. Playwright
// validates the host's shared libraries before that launch. The check then serves one page on an
// ephemeral loopback port, loads it, verifies a per-run token, and closes the browser.
//
// Any failure prints one `::error title=e2e::<KIND> <detail>` line and exits 1. When Playwright
// reports missing system dependencies, their library or package names are listed. Nothing is
// skipped or retried, and host validation cannot be switched off.
import { randomBytes } from "node:crypto";
import http from "node:http";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const EXPECTED_PLAYWRIGHT = "1.64.0";
const LAUNCH_OPTIONS = Object.freeze({ headless: true, timeout: 30_000 });
const NAVIGATION_TIMEOUT_MS = 30_000;
const TITLE = "llmi-browser-smoke";
const MAX_DETAIL = 300;
const NOT_A_NAME = new Set(["sudo", "apt-get", "install", "npx", "playwright", "install-deps"]);

function sanitize(text) {
  return String(text).replace(/[^A-Za-z0-9 ._:/()+,=@-]+/g, " ").replace(/\s+/g, " ").trim().slice(0, MAX_DETAIL);
}

class SmokeFailure extends Error {
  constructor(kind, detail) {
    super(detail);
    this.kind = kind;
  }
}

/** Library or package names from Playwright's host-validation error, or from the loader. */
export function missingDependencies(message) {
  const text = String(message);
  const names = new Set();
  for (const m of text.matchAll(/([A-Za-z0-9_.+-]+\.so(?:\.[0-9]+)*): cannot open shared object file/g)) names.add(m[1]);
  if (/Host system is missing dependencies/.test(text)) {
    let listing = false;
    for (const raw of text.split("\n")) {
      const line = raw.replace(/[║╔╗╚╝═|]/g, " ").replace(/\\/g, " ").trim();
      if (/^Missing libraries:|apt-get install/.test(line)) listing = true;
      else if (line === "" || /^<3|^Please|^Alternatively/.test(line)) listing = false;
      if (!listing) continue;
      for (const token of line.replace(/^.*?(Missing libraries:|apt-get install)/, "").split(/\s+/)) {
        if (/^[A-Za-z0-9][A-Za-z0-9_.+-]*$/.test(token) && !NOT_A_NAME.has(token)) names.add(token);
      }
    }
    if (names.size === 0) names.add("(unnamed)");
  }
  return [...names].sort();
}

function frontendRequire() {
  return createRequire(path.join(process.cwd(), "package.json"));
}

function serve(token) {
  const body = `<!doctype html><html><head><meta charset="utf-8"><title>${TITLE}</title></head><body><p id="smoke">${token}</p></body></html>`;
  const server = http.createServer((req, res) => {
    if (req.method !== "GET" || req.url !== "/") {
      res.writeHead(404).end();
      return;
    }
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" }).end(body);
  });
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve(server));
  });
}

async function launch(chromium) {
  try {
    return await chromium.launch({ ...LAUNCH_OPTIONS });
  } catch (error) {
    const missing = missingDependencies(error?.message ?? error);
    if (missing.length) throw new SmokeFailure("BROWSER_DEPENDENCIES_MISSING", `${missing.length} missing: ${missing.join(" ")}`);
    throw new SmokeFailure("BROWSER_LAUNCH", String(error?.message ?? error).split("\n")[0]);
  }
}

export async function smoke() {
  if (process.env.PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS) {
    throw new SmokeFailure("BROWSER_VALIDATION_DISABLED", "PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS must not be set");
  }
  const require = frontendRequire();
  let version;
  try {
    version = require("@playwright/test/package.json").version;
  } catch {
    throw new SmokeFailure("BROWSER_SMOKE_SETUP", "@playwright/test cannot be resolved from the frontend");
  }
  if (version !== EXPECTED_PLAYWRIGHT) throw new SmokeFailure("BROWSER_SMOKE_SETUP", `@playwright/test ${version}, expected ${EXPECTED_PLAYWRIGHT}`);
  const { chromium } = require("@playwright/test");
  const token = randomBytes(16).toString("hex");
  const server = await serve(token);
  let browser;
  try {
    browser = await launch(chromium);
    const page = await browser.newPage();
    const url = `http://127.0.0.1:${server.address().port}/`;
    const response = await page.goto(url, { timeout: NAVIGATION_TIMEOUT_MS, waitUntil: "load" });
    if (!response || response.status() !== 200) throw new SmokeFailure("BROWSER_SMOKE", `the local page answered ${response ? response.status() : "nothing"}`);
    if ((await page.title()) !== TITLE) throw new SmokeFailure("BROWSER_SMOKE", "the local page title differs");
    if ((await page.textContent("#smoke")) !== token) throw new SmokeFailure("BROWSER_SMOKE", "the local page content differs");
    const browserVersion = browser.version();
    await page.close();
    const closing = browser;
    browser = undefined;
    try {
      await closing.close();
    } catch (error) {
      throw new SmokeFailure("BROWSER_CLOSE", String(error?.message ?? error).split("\n")[0]);
    }
    return { playwright: version, browser: browserVersion };
  } catch (error) {
    if (error instanceof SmokeFailure) throw error;
    throw new SmokeFailure("BROWSER_SMOKE", String(error?.message ?? error).split("\n")[0]);
  } finally {
    if (browser) await browser.close().catch(() => undefined);
    await new Promise((resolve) => server.close(resolve));
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  smoke().then(
    (result) => {
      console.log(`browser smoke: PASS, Playwright ${result.playwright}, Chromium ${sanitize(result.browser)}, local page loaded and closed`);
    },
    (error) => {
      const kind = error instanceof SmokeFailure ? error.kind : "BROWSER_SMOKE";
      console.log(`::error title=e2e::${kind} ${sanitize(error?.message ?? error)}`);
      process.exitCode = 1;
    },
  );
}
