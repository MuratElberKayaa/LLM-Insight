import { Suspense, type ReactNode } from "react";
import { SideNav, SideNavFallback } from "./side-nav";

/**
 * Application shell: skip link, left navigation (a horizontal strip below `lg`), header
 * and the main landmark. Server component; the header's status slot is passed in.
 */
export function AppShell({ status, children }: { status: ReactNode; children: ReactNode }) {
  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[15rem_minmax(0,1fr)]">
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-raised px-3 py-2 text-sm text-ink focus:not-sr-only focus:fixed focus:left-3 focus:top-3"
      >
        Skip to content
      </a>
      <aside className="border-b border-line bg-surface lg:sticky lg:top-0 lg:h-dvh lg:border-b-0 lg:border-r">
        <div className="flex items-center gap-2 px-6 pt-4 lg:pt-6">
          <span aria-hidden="true" className="inline-block h-3 w-3 rounded-sm bg-series-1" />
          <span className="text-sm font-semibold tracking-tight text-ink">LLM Insight</span>
        </div>
        <Suspense fallback={<SideNavFallback />}>
          <SideNav />
        </Suspense>
      </aside>
      <div className="min-w-0">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3 sm:px-6 lg:px-8">
          <p className="text-sm text-ink-3">
            <span className="rounded border border-line px-1.5 py-0.5 text-xs font-medium uppercase tracking-wide text-ink-2">Local only</span>
            <span className="ml-2">Served on loopback; no login, not for public deployment.</span>
          </p>
          {status}
        </header>
        <main id="main" className="px-4 py-6 sm:px-6 lg:px-8">
          {children}
        </main>
      </div>
    </div>
  );
}
