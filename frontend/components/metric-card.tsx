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
  metricId,
  detail,
  footer,
  flag,
}: {
  id: string;
  label: string;
  value?: ReactNode;
  state?: ValueState;
  unknownText?: string;
  /** Catalog id(s), e.g. "M02". */
  metricId: string;
  detail?: ReactNode;
  footer?: ReactNode;
  flag?: ReactNode;
}) {
  return (
    <section aria-labelledby={`${id}-label`} className="flex min-w-0 flex-col rounded-lg border border-line bg-surface p-4" data-metric={id}>
      <div className="flex items-start justify-between gap-2">
        <h3 id={`${id}-label`} className="text-sm font-medium text-ink-2">
          {label}
        </h3>
        <span className="shrink-0 text-[11px] font-medium text-ink-3" title="Metric id in docs/METRICS_CATALOG.md">
          {metricId}
        </span>
      </div>
      <p className="mt-2 text-2xl font-semibold tracking-tight text-ink" data-testid={`${id}-value`} data-state={state}>
        {state === "known" ? value : <span className="text-lg font-medium text-ink-3">{state === "unknown" ? unknownText : "No data"}</span>}
      </p>
      {flag && <div className="mt-2 flex flex-wrap gap-1.5">{flag}</div>}
      {detail && <div className="mt-2 space-y-0.5 text-xs text-ink-3">{detail}</div>}
      {footer && <div className="mt-auto border-t border-line pt-2 text-xs text-ink-3 [&:not(:first-child)]:mt-3">{footer}</div>}
    </section>
  );
}

/** A small inline flag (e.g. "Low sample", "Incomplete"), always text + glyph. */
export function Flag({ tone, children, title }: { tone: "warning" | "critical" | "neutral"; children: ReactNode; title?: string }) {
  const cls =
    tone === "warning"
      ? "border-warning/50 text-warning"
      : tone === "critical"
        ? "border-critical/60 text-critical-ink"
        : "border-line text-ink-3";
  return (
    <span className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium ${cls}`} title={title}>
      <span aria-hidden="true">{tone === "neutral" ? "○" : "▲"}</span>
      {children}
    </span>
  );
}
