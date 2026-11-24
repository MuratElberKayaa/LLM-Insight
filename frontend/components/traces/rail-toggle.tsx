"use client";

import { useState, type ReactNode } from "react";
import { useShell } from "../shell-frame";

/**
 * UI-R2/UI-R3: the trace rail. When the explorer container is at least 64rem wide (a container
 * query, so the collapsed app sidebar counts) it is a fixed left panel that can be hidden; when
 * hidden, or on narrower containers, it is a collapsed section above the spans (one disclosure
 * button, `aria-expanded`). The hidden choice is shell state: it survives moving between traces,
 * not a reload (nothing is stored). The list itself is server-rendered.
 */
export function RailToggle({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const { railHidden, setRailHidden } = useShell();
  return (
    <div className="min-w-0" data-rail={railHidden ? "hidden" : "shown"} data-testid="trace-rail-frame">
      <div className={`flex items-stretch gap-2 ${railHidden ? "" : "@[64rem]/explorer:hidden"}`}>
        <button
          type="button"
          aria-expanded={open}
          aria-controls="trace-rail-panel"
          onClick={() => setOpen((o) => !o)}
          className="flex min-w-0 flex-1 items-center justify-between gap-3 rounded-xl border border-line bg-surface px-4 py-3 text-left text-sm font-medium text-ink hover:border-axis"
          data-testid="trace-rail-toggle"
        >
          <span className="truncate">{label}</span>
          <span aria-hidden="true" className="text-ink-3">
            {open ? "▴" : "▾"}
          </span>
        </button>
        {railHidden && (
          <button
            type="button"
            onClick={() => setRailHidden(false)}
            className="hidden shrink-0 items-center rounded-xl border border-line bg-surface px-3 text-sm text-ink-2 hover:border-axis hover:text-ink @[64rem]/explorer:inline-flex"
            data-testid="trace-rail-show"
          >
            Show list panel
          </button>
        )}
      </div>
      <div id="trace-rail-panel" className={`${open ? "mt-2" : "hidden"} ${railHidden ? "" : "@[64rem]/explorer:sticky @[64rem]/explorer:top-16 @[64rem]/explorer:mt-0 @[64rem]/explorer:block"}`}>
        {!railHidden && (
          <div className="mb-2 hidden justify-end @[64rem]/explorer:flex">
            <button
              type="button"
              onClick={() => setRailHidden(true)}
              className="rounded-md px-2 py-1 text-xs text-ink-3 hover:bg-raised hover:text-ink"
              data-testid="trace-rail-hide"
              title="Give the spans the full width; the list stays one click away"
            >
              ◂ Hide list panel
            </button>
          </div>
        )}
        {children}
      </div>
    </div>
  );
}
