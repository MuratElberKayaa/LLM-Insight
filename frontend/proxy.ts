import { NextResponse, type NextRequest } from "next/server";
import { buildCsp, isAllowedMethod, isLoopbackHost } from "./lib/request-guard";

/**
 * Local-only request guard (SECURITY.md §5, ADR-08).
 *
 * - The server binds to 127.0.0.1, and this rejects any request whose Host header is not a
 *   loopback name, so a DNS-rebinding page cannot read the dashboard through a foreign
 *   hostname that resolves to 127.0.0.1.
 * - Only GET and HEAD are served: the dashboard has no Server Functions and no route
 *   handlers, so it cannot be used as a proxy to the backend.
 * - A fresh CSP nonce per request lets Next.js tag its own scripts (dynamic rendering).
 */
export function proxy(request: NextRequest) {
  if (!isLoopbackHost(request.headers.get("host"))) {
    return new NextResponse("Misdirected request: the dashboard is served on loopback only.", {
      status: 421,
      headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
    });
  }
  if (!isAllowedMethod(request.method)) {
    return new NextResponse("Method not allowed.", {
      status: 405,
      headers: { Allow: "GET, HEAD", "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
    });
  }

  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const csp = buildCsp(nonce, process.env.NODE_ENV === "development");
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: [
    {
      source: "/((?!_next/static|_next/image|favicon.ico).*)",
    },
  ],
};
