import path from "node:path";
import type { NextConfig } from "next";

// The frontend is its own package; pin the workspace root so Next.js never infers a
// lockfile from an enclosing directory (the home directory is an unrelated repository).
const root = path.resolve(__dirname);

// Static security headers for every response. The Content-Security-Policy is set per
// request with a nonce in proxy.ts, because the dashboard is always dynamically rendered.
const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()" },
];

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  turbopack: { root },
  outputFileTracingRoot: root,
  experimental: {
    serverActions: {
      // Phase 4B forms carry at most ~30 KB of text (8000-character input, two 16 KiB JSON
      // fields); the backend caps its own bodies at 64 KiB. Default is 1 MB.
      bodySizeLimit: "128kb",
      // No extra allowed origins: only the dashboard's own host may invoke actions (default).
    },
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
