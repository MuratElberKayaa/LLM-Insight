import { Suspense, type ReactNode } from "react";
import { BrandMark, LockIcon } from "./icons";
import { SideNav, SideNavFallback } from "./side-nav";
import { ThemeToggle } from "./theme-toggle";

/**
 * Application shell: skip link, the sidebar (brand, grouped navigation and a footer with the
 * backend status, the local-only notice and the theme toggle; a top strip below `lg`) and the
 * main landmark. There is no separate top bar: each page renders its own header. Server
 * component; the status slot is passed in.
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
      <aside className="flex flex-col border-b border-line bg-surface lg:sticky lg:top-0 lg:h-dvh lg:border-b-0 lg:border-r" data-testid="app-sidebar">
        <div className="flex items-center gap-2.5 px-5 pt-4 lg:pt-5">
          <BrandMark />
          <div className="flex min-w-0 flex-col leading-tight">
            <span className="text-sm font-semibold tracking-tight text-ink">LLM Insight</span>
            <span className="text-[11px] text-ink-3">Observability · local</span>
          </div>
        </div>
        <Suspense fallback={<SideNavFallback />}>
          <SideNav />
        </Suspense>
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3 border-t border-line px-5 py-3 lg:mt-auto lg:flex-col lg:items-stretch lg:py-4">
          {status}
          <p className="flex items-center gap-2 text-xs text-ink-3" title="Served on loopback; no login, not for public deployment." data-testid="local-only-notice">
            <LockIcon />
            <span>
              <span className="font-medium text-ink-2">Local only</span>
              <span className="hidden sm:inline"> · loopback, no login</span>
              <span className="sr-only">. Not for public deployment.</span>
            </span>
          </p>
          <ThemeToggle />
        </div>
      </aside>
      <main id="main" className="min-w-0 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
        {children}
      </main>
    </div>
  );
}
