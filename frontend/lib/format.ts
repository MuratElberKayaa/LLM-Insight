/**
 * Display formatting. Fixed `en-US` locale and UTC everywhere, so server-rendered output is
 * deterministic and identical for every viewer. Formatting never changes what a value
 * means: `null` stays "unknown"/"not available", never 0.
 */

const intFmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const compactFmt = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

export function formatInt(n: number): string {
  return intFmt.format(n);
}

/** Axis ticks: 0 / 950 / 1.2K / 3.4M. */
export function formatCompact(n: number): string {
  return Math.abs(n) < 1000 ? intFmt.format(n) : compactFmt.format(n);
}

/**
 * A rate in [0, 1] as a percentage with at most 2 decimals. Values that would round to
 * 0% or 100% without being exactly that are shown as "<0.01%" / ">99.99%".
 */
export function formatPercent(rate: number | null): string | null {
  if (rate === null || !Number.isFinite(rate)) return null;
  if (rate === 0) return "0%";
  if (rate === 1) return "100%";
  if (rate < 0.0001) return "<0.01%";
  if (rate > 0.9999) return ">99.99%";
  const pct = Math.round(rate * 10000) / 100;
  return `${pct.toFixed(2).replace(/\.?0+$/, "")}%`;
}

/** Milliseconds: "0.042 ms", "8.25 ms", "412.3 ms", "1.24 s", "2.1 min". */
export function formatMs(ms: number | null): string | null {
  if (ms === null || !Number.isFinite(ms)) return null;
  if (ms < 1) return `${ms.toFixed(3)} ms`;
  if (ms < 10) return `${ms.toFixed(2)} ms`;
  if (ms < 1000) return `${ms.toFixed(1)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)} s`;
  return `${(ms / 60_000).toFixed(1)} min`;
}

function pad(n: number): string {
  return n.toString().padStart(2, "0");
}

/** "2026-10-25 14:00 UTC" (minute precision) from an ISO-8601 UTC string or a Date. */
export function formatUtcMinute(value: string | Date): string {
  const d = typeof value === "string" ? new Date(value) : value;
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
}

/** "14:05:09 UTC". */
export function formatUtcTime(value: Date): string {
  return `${pad(value.getUTCHours())}:${pad(value.getUTCMinutes())}:${pad(value.getUTCSeconds())} UTC`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Short bucket label for chart axes: "14:00" (hourly) or "Oct 25" (daily), in UTC. */
export function formatBucketTick(iso: string, bucket: "hour" | "day"): string {
  const d = new Date(iso);
  return bucket === "hour" ? `${pad(d.getUTCHours())}:00` : `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

/** Full bucket label for tooltips and tables: "2026-10-25 14:00–15:00 UTC" / "2026-10-25 (UTC day)". */
export function formatBucketRange(iso: string, bucket: "hour" | "day"): string {
  const d = new Date(iso);
  const date = `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
  if (bucket === "day") return `${date} (UTC day)`;
  const end = (d.getUTCHours() + 1) % 24;
  return `${date} ${pad(d.getUTCHours())}:00–${pad(end)}:00 UTC`;
}
