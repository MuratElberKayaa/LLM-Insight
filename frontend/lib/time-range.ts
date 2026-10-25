/**
 * Time-range presets and URL filters for the overview (backend `[from, to)` on event time,
 * UTC, at most 31 days; ARCHITECTURE.md §5.1–5.2).
 *
 * The backend buckets the trend hourly for windows up to 2 days and daily beyond, aligned
 * to UTC from the epoch. Each preset therefore ends at the next bucket boundary after
 * "now" (the current bucket is still filling) and starts a whole number of buckets earlier,
 * so every bucket in the chart covers its full width and none is silently truncated.
 */

import { TRACE_PROVENANCES, type TraceProvenance } from "./api/types";

const HOUR_MS = 3_600_000;
const DAY_MS = 24 * HOUR_MS;
export const MAX_WINDOW_MS = 31 * DAY_MS;

export const RANGE_PRESETS = {
  "6h": { label: "6 hours", long: "Last 6 hours", bucketMs: HOUR_MS, buckets: 6 },
  "24h": { label: "24 hours", long: "Last 24 hours", bucketMs: HOUR_MS, buckets: 24 },
  "7d": { label: "7 days", long: "Last 7 days", bucketMs: DAY_MS, buckets: 7 },
  "31d": { label: "31 days", long: "Last 31 days", bucketMs: DAY_MS, buckets: 31 },
} as const;

export type RangeKey = keyof typeof RANGE_PRESETS;
export const RANGE_KEYS = Object.keys(RANGE_PRESETS) as RangeKey[];
export const DEFAULT_RANGE: RangeKey = "24h";

export interface ResolvedWindow {
  from: Date;
  to: Date;
}

/** `[from, to)` for a preset at `now`: `to` is the next bucket boundary strictly after now. */
export function resolveWindow(key: RangeKey, now: Date): ResolvedWindow {
  const preset = RANGE_PRESETS[key];
  const t = now.getTime();
  if (!Number.isFinite(t)) throw new RangeError("invalid clock");
  const to = (Math.floor(t / preset.bucketMs) + 1) * preset.bucketMs;
  const from = to - preset.buckets * preset.bucketMs;
  if (to - from > MAX_WINDOW_MS) throw new RangeError("window exceeds 31 days");
  return { from: new Date(from), to: new Date(to) };
}

/** Backend query value: ISO-8601 with `Z` (the backend refuses naive or offset times). */
export function toQueryTimestamp(d: Date): string {
  return d.toISOString();
}

export type ProvenanceFilter = TraceProvenance | "all";
export const PROVENANCE_FILTERS: readonly ProvenanceFilter[] = ["all", ...TRACE_PROVENANCES];

export interface OverviewQuery {
  range: RangeKey;
  provenance: ProvenanceFilter;
  /** True when a parameter was present but not a supported value (it was ignored). */
  ignoredInvalid: boolean;
}

type SearchParams = Readonly<Record<string, string | string[] | undefined>>;

function single(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

/** Parse `?range=&provenance=` strictly; anything else falls back to the defaults. */
export function parseOverviewQuery(params: SearchParams): OverviewQuery {
  const rawRange = single(params.range);
  const rawProv = single(params.provenance);
  const range = RANGE_KEYS.find((k) => k === rawRange) ?? DEFAULT_RANGE;
  const provenance = PROVENANCE_FILTERS.find((p) => p === rawProv) ?? "all";
  const ignoredInvalid =
    (rawRange !== undefined && range !== rawRange) ||
    (rawProv !== undefined && provenance !== rawProv) ||
    Array.isArray(params.range) ||
    Array.isArray(params.provenance);
  return { range, provenance, ignoredInvalid };
}

/** Backend query parameters for a parsed overview query (never raw user input). */
export function backendParams(query: OverviewQuery, now: Date): Record<string, string> {
  const { from, to } = resolveWindow(query.range, now);
  const params: Record<string, string> = { from: toQueryTimestamp(from), to: toQueryTimestamp(to) };
  if (query.provenance !== "all") params.provenance = query.provenance;
  return params;
}

/** Relative URL of the overview with the given query (defaults omitted). */
export function overviewHref(query: Pick<OverviewQuery, "range" | "provenance">): string {
  const sp = new URLSearchParams();
  if (query.range !== DEFAULT_RANGE) sp.set("range", query.range);
  if (query.provenance !== "all") sp.set("provenance", query.provenance);
  const qs = sp.toString();
  return qs ? `/?${qs}` : "/";
}
