import type { ReactNode } from "react";

export type ValueState = "known" | "unknown" | "no_data";

/**
 * KPI tile. The value slot distinguishes three states so a missing value is never shown as
 * a number: `known` (the value), `unknown` (data exists but the value is not known, e.g. an
 * unpriced call) and `no_data` (the population is empty, n = 0).
 */
export function MetricCard({
  id,
  label,
  value,
  state = "known",
  unknownText = "Unknown",
  noDataText = "No data",
  accent = "none",
  metricId,
  detail,
  footer,
  flag,
  visual,
  action,
}: {
  id: string;
  label: string;
  value?: ReactNode;
  state?: ValueState;
  unknownText?: string;
  /** UI-R1: what an empty population is called (e.g. "No streaming attempts"). */
  noDataText?: string;
  /** UI-R1: a thin top stripe grouping related cards; decoration only, never the only carrier of meaning. */
  accent?: "blue" | "teal" | "violet" | "none";
  /** Catalog id(s), e.g. "M02". */
  metricId: string;
  detail?: ReactNode;
  footer?: ReactNode;
  flag?: ReactNode;
  /** A small drawing of backend values (sparkline, split bars, chips); never the only carrier. */
  visual?: ReactNode;
  /** UI-R2: a link from the metric to the rows behind it (e.g. the trace list with the same scope). */
  action?: ReactNode;
}) {
  return (
    <section
      aria-labelledby={`${id}-label`}
      className={`relative flex min-w-0 flex-col gap-1.5 overflow-hidden rounded-xl border border-line bg-surface p-[18px] transition-colors hover:border-axis ${STRIPE[accent]}`}
      data-metric={id}
    >
      <div className="flex items-start justify-between gap-2">
        <h3 id={`${id}-label`} className="text-[13px] font-medium text-ink-2">
          {label}
        </h3>
        <span className="shrink-0 text-[11px] font-medium text-ink-3" title="Metric id in docs/METRICS_CATALOG.md">
          {metricId}
        </span>
      </div>
      <p className="tabular text-[30px] font-semibold leading-tight tracking-tight text-ink" data-testid={`${id}-value`} data-state={state}>
        {state === "known" ? value : <span className="text-lg font-medium text-ink-3">{state === "unknown" ? unknownText : noDataText}</span>}
      </p>
      {detail && <div className="space-y-0.5 text-xs leading-relaxed text-ink-3">{detail}</div>}
      {flag && <div className="flex flex-wrap gap-1.5">{flag}</div>}
      {visual && <div className="mt-1">{visual}</div>}
      {action && <div className="text-xs font-medium">{action}</div>}
      {footer && <div className="mt-auto border-t border-line pt-2.5 text-[11px] leading-relaxed text-ink-3 [&:not(:first-child)]:mt-3">{footer}</div>}
    </section>
  );
}

const STRIPE = {
  blue: "before:absolute before:inset-x-0 before:top-0 before:h-[2px] before:bg-accent/70",
  teal: "before:absolute before:inset-x-0 before:top-0 before:h-[2px] before:bg-accent-teal/70",
  violet: "before:absolute before:inset-x-0 before:top-0 before:h-[2px] before:bg-accent-violet/70",
  none: "",
} as const;

/** A small inline flag (e.g. "Low sample", "Incomplete"), always text + glyph. */
export function Flag({ tone, children, title }: { tone: "warning" | "critical" | "neutral"; children: ReactNode; title?: string }) {
  const cls =
    tone === "warning"
      ? "border-warning/50 text-warning-ink"
      : tone === "critical"
        ? "border-critical/60 text-critical-ink"
        : "border-line text-ink-3";
  return (
    <span className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium ${cls}`} title={title}>
      <span aria-hidden="true">{tone === "neutral" ? "○" : "▲"}</span>
      {children}
    </span>
  );
}
