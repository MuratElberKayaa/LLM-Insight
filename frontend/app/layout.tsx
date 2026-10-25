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

export const viewport: Viewport = {
  colorScheme: "dark",
  themeColor: "#0c0d10",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AppShell
          current="/"
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
