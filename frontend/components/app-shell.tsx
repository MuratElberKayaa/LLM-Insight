import { Suspense, type ReactNode } from "react";
import { BrandMark, LockIcon } from "./icons";
import { ShellFrame, SidebarToggle } from "./shell-frame";
import { SideNav, SideNavFallback } from "./side-nav";
import { ThemeToggle } from "./theme-toggle";

/**
 * Application shell (UI-R1, UI-R3): skip link, a sidebar (brand, grouped navigation, and a footer
 * panel with the backend status, the local-only notice and the theme toggle; a top strip below
 * `lg`) and the main landmark, centred and bounded for wide screens. UI-R3: from `lg` the sidebar
 * collapses to an icon rail (ShellFrame keeps the state across pages; labels stay in the
 * accessibility tree as visually hidden text, `.sb-*` rules in app/globals.css). Each page renders
 * its own header and context bar. The status slot is passed in.
 */
export function AppShell({ status, children }: { status: ReactNode; children: ReactNode }) {
  return (
    <ShellFrame
      sidebar={
        <aside id="app-sidebar" className="flex flex-col border-b border-line bg-surface lg:sticky lg:top-0 lg:h-dvh lg:border-b-0 lg:border-r" data-testid="app-sidebar">
          <div className="sb-head flex items-center gap-3 px-5 pt-4 lg:pt-6">
            <BrandMark />
            <div className="sb-hide flex min-w-0 flex-1 flex-col leading-tight">
              <span className="text-[15px] font-semibold tracking-tight text-ink">LLM Insight</span>
              <span className="text-[11px] text-ink-3">LLM observability · metadata only</span>
            </div>
            <SidebarToggle />
          </div>
          <Suspense fallback={<SideNavFallback />}>
            <SideNav />
          </Suspense>
          <div className="px-3 pb-3 lg:mt-auto lg:pb-4">
            <div className="sb-footer flex flex-wrap items-center justify-between gap-x-4 gap-y-3 rounded-xl border border-line bg-raised/40 px-3 py-3 lg:flex-col lg:items-stretch">
              {status}
              <p className="flex items-center gap-2 text-xs text-ink-3" title="Served on loopback; no login, not for public deployment." data-testid="local-only-notice">
                <LockIcon />
                <span className="sb-label">
                  <span className="font-medium text-ink-2">Local only</span>
                  <span className="hidden sm:inline"> · loopback, no login</span>
                  <span className="sr-only">. Not for public deployment.</span>
                </span>
              </p>
              <ThemeToggle />
            </div>
          </div>
        </aside>
      }
    >
      {children}
    </ShellFrame>
  );
}
