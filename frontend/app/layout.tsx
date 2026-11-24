import type { Metadata, Viewport } from "next";
import { Suspense, type ReactNode } from "react";
import { AppShell } from "../components/app-shell";
import { BackendStatus } from "../components/backend-status";
import { StatusIndicator } from "../components/status-indicator";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "LLM Insight", template: "%s · LLM Insight" },
  description: "Local LLM observability dashboard (metadata only).",
  robots: { index: false, follow: false },
  referrer: "no-referrer",
};

// Both themes are supported; the OS preference picks one (app/globals.css). Theme colors are
// the two canvas tokens.
export const viewport: Viewport = {
  colorScheme: "light dark",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f4f6f9" },
    { media: "(prefers-color-scheme: dark)", color: "#0a0e15" },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AppShell
          status={
            <Suspense fallback={<StatusIndicator tone="neutral" label="Checking backend…" />}>
              <BackendStatus />
            </Suspense>
          }
        >
          {children}
        </AppShell>
      </body>
    </html>
  );
}
