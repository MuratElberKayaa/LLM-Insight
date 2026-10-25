import { NextResponse, type NextRequest } from "next/server";
import { actionRequestProblem, buildCsp, isAllowedMethod, isLoopbackHost, isWritePage } from "./lib/request-guard";

const TEXT = { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" };

/**
 * Local-only request guard (SECURITY.md §5, ADR-08).
 *
 * - The server binds to 127.0.0.1, and this rejects any request whose Host header is not a
 *   loopback name, so a DNS-rebinding page cannot read the dashboard through a foreign
 *   hostname that resolves to 127.0.0.1.
 * - GET and HEAD everywhere. POST only for the Phase 4B Server Actions, only on the pages
 *   that host their forms, and only from this origin (`actionRequestProblem`). There are no
 *   route handlers, and each action calls one fixed backend write, so the dashboard is not a
 *   generic proxy to the backend (ARCHITECTURE.md §5.8).
 * - A fresh CSP nonce per request lets Next.js tag its own scripts (dynamic rendering).
 */
export function proxy(request: NextRequest) {
  if (!isLoopbackHost(request.headers.get("host"))) {
    return new NextResponse("Misdirected request: the dashboard is served on loopback only.", { status: 421, headers: TEXT });
  }
  const write = request.method === "POST" && isWritePage(request.nextUrl.pathname);
  if (!isAllowedMethod(request.method) && !write) {
    return new NextResponse("Method not allowed.", { status: 405, headers: { ...TEXT, Allow: "GET, HEAD" } });
  }
  if (write) {
    const problem = actionRequestProblem({
      host: request.headers.get("host"),
      origin: request.headers.get("origin"),
      secFetchSite: request.headers.get("sec-fetch-site"),
      nextAction: request.headers.get("next-action"),
    });
    if (problem !== null) {
      // Fixed text: the reason names the failed check, never a request value.
      return new NextResponse(`Forbidden: ${problem}.`, { status: 403, headers: TEXT });
    }
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
