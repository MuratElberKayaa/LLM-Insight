import type { ReactNode } from "react";

/**
 * Chart container: title, description, an optional legend, the chart and its table-view
 * twin behind a disclosure (every plotted value stays readable without hovering, and the
 * table shows the exact backend values).
 */
export function ChartCard({
  id,
  title,
  description,
  legend,
  children,
  table,
}: {
  id: string;
  title: string;
  description?: ReactNode;
  legend?: ReactNode;
  children: ReactNode;
  table: ReactNode;
}) {
  return (
    <section aria-labelledby={`${id}-title`} className="min-w-0 rounded-lg border border-line bg-surface p-4" data-chart={id}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 id={`${id}-title`} className="text-sm font-semibold text-ink">
            {title}
          </h3>
          {description && <p className="mt-0.5 text-xs text-ink-3">{description}</p>}
        </div>
        {legend}
      </div>
      <div className="mt-3">{children}</div>
      <details className="group mt-3">
        <summary className="cursor-pointer select-none text-xs font-medium text-ink-2 hover:text-ink">Show data table</summary>
        <div className="mt-2">{table}</div>
      </details>
    </section>
  );
}

/** Legend entry: a swatch mirroring the mark (rect for bars) beside a text label. */
export function LegendItem({ swatchClass, label }: { swatchClass: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-ink-2">
      <span aria-hidden="true" className={`swatch inline-block h-2.5 w-2.5 rounded-[2px] ${swatchClass}`} />
      {label}
    </span>
  );
}
