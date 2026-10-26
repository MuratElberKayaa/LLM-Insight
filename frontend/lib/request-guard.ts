/** Pure helpers for proxy.ts (unit-tested without a server). */

const LOOPBACK_HOSTNAMES = new Set(["127.0.0.1", "localhost", "[::1]"]);

/** True when the Host header names a loopback host (any port). A missing header is refused. */
export function isLoopbackHost(host: string | null): boolean {
  if (host === null || host.length === 0 || host.length > 255) return false;
  const match = /^(\[[0-9a-fA-F:.]+\]|[^:[\]]+)(?::(\d{1,5}))?$/.exec(host.trim());
  if (match === null) return false;
  const hostname = (match[1] ?? "").toLowerCase();
  return LOOPBACK_HOSTNAMES.has(hostname);
}

export function isAllowedMethod(method: string): boolean {
  return method === "GET" || method === "HEAD";
}

/**
 * The only pages whose forms post Server Actions (Phase 4B, plus the Phase 4D experiment
 * setup, ADR-26). Every other path stays GET/HEAD. Ids are checked by shape here and validated
 * again by the action and the backend.
 */
const WRITE_PAGES: readonly RegExp[] = [/^\/traces\/[0-9a-f]{32}$/, /^\/traces\/[0-9a-f]{32}\/reviews\/[1-9][0-9]{0,18}\/test-case$/, /^\/datasets$/, /^\/experiments\/new$/];

export function isWritePage(pathname: string): boolean {
  return WRITE_PAGES.some((re) => re.test(pathname));
}

const ACTION_ID_RE = /^[A-Za-z0-9_-]{8,256}$/;

export interface ActionRequest {
  host: string | null;
  origin: string | null;
  secFetchSite: string | null;
  nextAction: string | null;
}

/**
 * CSRF check for a Server Action POST (ARCHITECTURE.md §5.8). The dashboard has no session or
 * cookie, so the defences are about where the request comes from:
 * - `Origin` is required and must be this loopback host (Next.js alone lets a request without
 *   an Origin through, with a warning);
 * - `Sec-Fetch-Site`, when the browser sends it, must be `same-origin`;
 * - the `Next-Action` header must be present: Next.js's client sends it, while a cross-site
 *   HTML form cannot set a header, and a cross-site script would need a CORS preflight that
 *   the dashboard never answers.
 * Returns null when the request may proceed, else a fixed reason (never request data).
 */
export function actionRequestProblem(req: ActionRequest): string | null {
  if (!isLoopbackHost(req.host)) return "host";
  if (req.origin === null) return "origin missing";
  let origin: URL;
  try {
    origin = new URL(req.origin);
  } catch {
    return "origin malformed";
  }
  if ((origin.protocol !== "http:" && origin.protocol !== "https:") || origin.host.toLowerCase() !== (req.host ?? "").trim().toLowerCase()) return "cross-origin";
  if (req.secFetchSite !== null && req.secFetchSite !== "same-origin") return "cross-site";
  if (req.nextAction === null || !ACTION_ID_RE.test(req.nextAction)) return "not a server action";
  return null;
}

/**
 * Content-Security-Policy for the dashboard.
 * - Scripts: nonce + 'strict-dynamic' (Next.js tags its own scripts with the nonce).
 *   Development adds 'unsafe-eval' (React dev tooling) and the HMR websocket.
 * - Styles: 'unsafe-inline' is needed because the chart library sets inline style
 *   attributes, which a nonce cannot authorize. No telemetry string is ever placed in a
 *   style, and scripts stay nonce-only.
 */
export function buildCsp(nonce: string, isDev: boolean): string {
  const directives = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self'",
    `connect-src 'self'${isDev ? " ws://127.0.0.1:* ws://localhost:*" : ""}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ];
  return directives.join("; ");
}
