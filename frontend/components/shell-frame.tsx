"use client";

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

/**
 * UI-R3: layout state shared across pages: the collapsed app sidebar and the hidden trace rail.
 * It lives in the root layout, so it survives client-side navigation between pages; nothing is
 * stored (the dashboard uses no browser storage or cookies, tests/unit/security-static.test.ts),
 * so a full reload returns to the expanded defaults.
 */
interface ShellState {
  collapsed: boolean;
  toggleSidebar: () => void;
  railHidden: boolean;
  setRailHidden: (hidden: boolean) => void;
}

const ShellContext = createContext<ShellState>({ collapsed: false, toggleSidebar: () => {}, railHidden: false, setRailHidden: () => {} });

export function useShell(): ShellState {
  return useContext(ShellContext);
}

const chevron = (left: boolean) => (
  <svg aria-hidden="true" viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
    <rect x="1.75" y="2.25" width="12.5" height="11.5" rx="2" />
    <path d="M6 2.5v11" />
    <path d={left ? "M10.5 6.5 9 8l1.5 1.5" : "M9 6.5 10.5 8 9 9.5"} />
  </svg>
);

/** The collapse button (sidebar header, `lg` and up only). */
export function SidebarToggle() {
  const { collapsed, toggleSidebar } = useShell();
  const label = collapsed ? "Expand sidebar" : "Collapse sidebar";
  return (
    <button
      type="button"
      onClick={toggleSidebar}
      aria-expanded={!collapsed}
      aria-controls="app-sidebar"
      aria-label={label}
      title={label}
      className="hidden h-8 w-8 shrink-0 items-center justify-center rounded-md text-ink-3 hover:bg-raised hover:text-ink lg:inline-flex"
      data-testid="sidebar-toggle"
    >
      {chevron(!collapsed)}
    </button>
  );
}

export function ShellFrame({ sidebar, children }: { sidebar: ReactNode; children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const [railHidden, setRailHidden] = useState(false);
  const value = useMemo(() => ({ collapsed, toggleSidebar: () => setCollapsed((c) => !c), railHidden, setRailHidden }), [collapsed, railHidden]);
  return (
    <ShellContext.Provider value={value}>
      <div
        className={`min-h-dvh lg:grid ${collapsed ? "lg:grid-cols-[4.5rem_minmax(0,1fr)]" : "lg:grid-cols-[16rem_minmax(0,1fr)]"}`}
        data-sidebar={collapsed ? "collapsed" : "expanded"}
        data-testid="app-shell"
      >
        <a
          href="#main"
          className="sr-only z-50 rounded-md bg-raised px-3 py-2 text-sm text-ink focus:not-sr-only focus:fixed focus:left-3 focus:top-3"
        >
          Skip to content
        </a>
        {sidebar}
        <main id="main" className="min-w-0 px-4 py-6 sm:px-6 lg:px-10 lg:py-8">
          <div className="mx-auto w-full max-w-[1600px]">{children}</div>
        </main>
      </div>
    </ShellContext.Provider>
  );
}
