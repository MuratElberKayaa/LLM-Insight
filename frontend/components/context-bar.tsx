import type { ReactNode } from "react";

export interface ContextItem {
  label: string;
  value: ReactNode;
  /** Long values (a model, a trace id) may be cut on narrow screens; the title keeps them. */
  title?: string;
  key: string;
}

/**
 * UI-R2: a thin bar that stays at the top while the page scrolls. It restates what the data on
 * screen is scoped to (project, window, provenance, filters) and keeps the local-only,
 * metadata-only notice in view. Text only; it changes nothing (every control stays on the page).
 */
export function ContextBar({ items, testId = "context-bar" }: { items: ContextItem[]; testId?: string }) {
  return (
    <div
      className="sticky top-0 z-30 -mx-4 mb-5 border-b border-line bg-canvas/95 px-4 py-2 backdrop-blur-sm sm:-mx-6 sm:px-6 lg:-mx-10 lg:px-10"
      data-testid={testId}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        <dl className="flex min-w-0 flex-1 flex-wrap items-center gap-x-4 gap-y-1">
          {items.map((it) => (
            <div key={it.key} className="flex min-w-0 max-w-full items-baseline gap-1.5" data-context={it.key}>
              <dt className="shrink-0 text-[10px] font-semibold uppercase tracking-[0.1em] text-ink-3">{it.label}</dt>
              <dd className="min-w-0 truncate text-ink-2" title={it.title}>
                {it.value}
              </dd>
            </div>
          ))}
        </dl>
        <p className="hidden shrink-0 text-[11px] text-ink-3 md:block">Local only · metadata only · not live</p>
      </div>
    </div>
  );
}
