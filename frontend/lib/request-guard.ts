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
