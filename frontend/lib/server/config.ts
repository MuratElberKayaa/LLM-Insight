import "server-only";

/**
 * Server-side dashboard configuration (CR-06, SECURITY.md §2).
 *
 * Read from the Next.js server environment (`frontend/.env.local`, git-ignored). None of
 * these names starts with NEXT_PUBLIC_, so Next.js never inlines them into a browser
 * bundle, and `server-only` makes any client import of this module a build error.
 *
 * - LLMI_API_BASE_URL: backend origin. Loopback only (http/https on 127.0.0.1, localhost
 *   or [::1]), no credentials, path, query or fragment, so the token can only ever be sent
 *   to the local backend.
 * - LLMI_API_TOKEN: the backend's LLMI_ADMIN_TOKEN. Never logged, never rendered.
 * - LLMI_DASHBOARD_PROJECT_ID: the project shown (the API requires one; no listing exists).
 * - LLMI_API_TIMEOUT_MS: per-request deadline, 100–30000 (default 5000).
 */

export interface DashboardConfig {
  baseUrl: URL;
  token: string;
  projectId: string;
  timeoutMs: number;
}

export type ConfigResult = { ok: true; config: DashboardConfig } | { ok: false; problem: string };

const LOOPBACK_HOSTNAMES = new Set(["127.0.0.1", "localhost", "[::1]"]);
const PLACEHOLDERS = new Set(["CHANGE_ME", "changeme", "placeholder"]);
const TOKEN_RE = /^[\x21-\x7e]{32,512}$/; // visible ASCII, no whitespace: safe as a header value
const PROJECT_RE = /^[1-9][0-9]{0,18}$/;
const BIGINT_MAX = 2n ** 63n - 1n;
const DEFAULT_TIMEOUT_MS = 5000;

type Env = Readonly<Record<string, string | undefined>>;

export function readConfig(env: Env = process.env): ConfigResult {
  const rawUrl = env.LLMI_API_BASE_URL?.trim();
  if (!rawUrl) return { ok: false, problem: "LLMI_API_BASE_URL is not set" };
  let baseUrl: URL;
  try {
    baseUrl = new URL(rawUrl);
  } catch {
    return { ok: false, problem: "LLMI_API_BASE_URL is not a valid URL" };
  }
  if (baseUrl.protocol !== "http:" && baseUrl.protocol !== "https:") {
    return { ok: false, problem: "LLMI_API_BASE_URL must use http or https" };
  }
  if (!LOOPBACK_HOSTNAMES.has(baseUrl.hostname)) {
    return { ok: false, problem: "LLMI_API_BASE_URL must point to a loopback host (local-only dashboard)" };
  }
  if (baseUrl.username || baseUrl.password || baseUrl.search || baseUrl.hash || (baseUrl.pathname !== "/" && baseUrl.pathname !== "")) {
    return { ok: false, problem: "LLMI_API_BASE_URL must be a bare origin (no credentials, path, query or fragment)" };
  }

  const token = env.LLMI_API_TOKEN;
  if (!token) return { ok: false, problem: "LLMI_API_TOKEN is not set" };
  if (PLACEHOLDERS.has(token)) return { ok: false, problem: "LLMI_API_TOKEN is still a placeholder" };
  if (!TOKEN_RE.test(token)) return { ok: false, problem: "LLMI_API_TOKEN has an invalid format" };

  const projectId = env.LLMI_DASHBOARD_PROJECT_ID?.trim();
  if (!projectId) return { ok: false, problem: "LLMI_DASHBOARD_PROJECT_ID is not set" };
  if (!PROJECT_RE.test(projectId) || BigInt(projectId) > BIGINT_MAX) {
    return { ok: false, problem: "LLMI_DASHBOARD_PROJECT_ID must be a positive integer" };
  }

  let timeoutMs = DEFAULT_TIMEOUT_MS;
  const rawTimeout = env.LLMI_API_TIMEOUT_MS?.trim();
  if (rawTimeout) {
    const parsed = Number(rawTimeout);
    if (!Number.isInteger(parsed) || parsed < 100 || parsed > 30000) {
      return { ok: false, problem: "LLMI_API_TIMEOUT_MS must be an integer between 100 and 30000" };
    }
    timeoutMs = parsed;
  }

  return { ok: true, config: { baseUrl, token, projectId, timeoutMs } };
}
