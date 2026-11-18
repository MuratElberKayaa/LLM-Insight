import type { TraceProvenance } from "../../lib/api/types";
import { formatInt, formatPercent } from "../../lib/format";
import { PROVENANCE_LABEL } from "../../lib/provenance";
import { PROVENANCE_SWATCH } from "../provenance-badge";

/**
 * Small visuals inside the KPI tiles (UI-3). Server components, no JavaScript, CSP-safe (inline
 * style attributes only, which the policy allows). They only re-draw values the backend already
 * returned: no new metric is derived, nothing is re-summed.
 */

export interface SparkPoint {
  /** Bucket label, e.g. "2026-11-12 (UTC day)". */
  label: string;
  value: number;
}

const SPARK_H = 36;

/**
 * Bars per trend bucket on a 0..max scale. An empty bucket is a 2px stub in the grid color, so
 * "no data" never looks like a small value. The accessible name states the bucket count and the
 * peak; the exact series is in the trend chart and its table below the tiles.
 */
export function Sparkline({ points, noun, testId }: { points: SparkPoint[]; noun: string; testId: string }) {
  if (points.length === 0) return null;
  const max = Math.max(0, ...points.map((p) => p.value));
  const peak = points.find((p) => p.value === max);
  const withData = points.filter((p) => p.value > 0).length;
  const gap = points.length <= 48 ? "gap-[2px]" : points.length <= 120 ? "gap-px" : "gap-0";
  const summary =
    max === 0
      ? `${noun} per bucket: none in ${formatInt(points.length)} buckets`
      : `${noun} per bucket: ${formatInt(withData)} of ${formatInt(points.length)} buckets with data; peak ${formatInt(max)} in ${peak?.label ?? "one bucket"}`;
  return (
    <div role="img" aria-label={summary} className={`mt-1 flex items-end ${gap}`} style={{ height: SPARK_H }} data-testid={testId} data-buckets={points.length} data-max={max}>
      {points.map((p) => {
        const h = p.value > 0 && max > 0 ? Math.max(3, Math.round((p.value / max) * SPARK_H)) : 2;
        return (
          <span
            key={p.label}
            title={`${p.label}: ${formatInt(p.value)} ${noun.toLowerCase()}`}
            className={`min-w-0 flex-1 rounded-[2px] ${p.value > 0 ? "bg-series-1" : "bg-grid"}`}
            style={{ height: h }}
          />
        );
      })}
    </div>
  );
}

export interface RateRow {
  provenance: TraceProvenance;
  n: number;
  rate: number | null;
}

/** Bar width in percent (2 decimals); a non-zero rate keeps a visible 2% minimum. */
function barWidth(rate: number | null): number {
  if (rate === null || !Number.isFinite(rate) || rate <= 0) return 0;
  return Math.min(100, Math.max(2, Math.round(rate * 10000) / 100));
}

/**
 * Error rate per provenance on an absolute 0–100% scale (never rescaled to the largest rate, so
 * bar lengths compare honestly). Rows with n = 0 are left out; a null rate shows as "n/a".
 */
export function RateSplit({ rows, unit, testId }: { rows: RateRow[]; unit: string; testId: string }) {
  const shown = rows.filter((r) => r.n > 0);
  if (shown.length < 2) return null; // one provenance: the headline already is that rate
  return (
    <ul className="mt-1 space-y-1.5" aria-label="Error rate by provenance" data-testid={testId}>
      {shown.map((r) => {
        const text = formatPercent(r.rate) ?? "n/a";
        return (
          <li key={r.provenance} className="grid grid-cols-[6.5rem_minmax(0,1fr)_3.5rem] items-center gap-2 text-xs text-ink-2" title={`${PROVENANCE_LABEL[r.provenance]}: ${text} of ${formatInt(r.n)} ${unit}`}>
            <span className="truncate">{PROVENANCE_LABEL[r.provenance]}</span>
            <span aria-hidden="true" className="h-1.5 overflow-hidden rounded-full bg-grid">
              <span className={`swatch block h-full rounded-full ${PROVENANCE_SWATCH[r.provenance]}`} style={{ width: `${barWidth(r.rate)}%` }} />
            </span>
            <span className="tabular text-right">{text}</span>
          </li>
        );
      })}
    </ul>
  );
}

/** The most frequent error types as compact chips (backend order); the full list is below. */
export function ErrorTypeChips({ items, truncated, max = 3 }: { items: { error_type: string | null; count: number }[]; truncated: boolean; max?: number }) {
  if (items.length === 0) return null;
  const rest = items.length - max;
  return (
    <ul className="mt-1 flex flex-wrap gap-1.5" aria-label="Most frequent error types" data-testid="kpi-error-types">
      {items.slice(0, max).map((e, i) => (
        <li key={`${e.error_type ?? "null"}-${i}`} className="inline-flex h-6 items-center gap-1 rounded-full border border-line bg-raised px-2 text-xs">
          {e.error_type === null ? <span className="text-ink-3">not reported</span> : <code className="max-w-[10rem] truncate font-mono text-critical-ink">{e.error_type}</code>}
          <span className="tabular text-ink-2">×{formatInt(e.count)}</span>
        </li>
      ))}
      {(rest > 0 || truncated) && (
        <li className="inline-flex h-6 items-center px-1 text-xs text-ink-3">{rest > 0 ? `+${formatInt(rest)} more` : "more"}</li>
      )}
    </ul>
  );
}
