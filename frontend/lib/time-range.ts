/**
 * Time windows and URL filters shared by the dashboard pages (backend `[from, to)` on event
 * time, UTC, at most 31 days; ARCHITECTURE.md §5.1–5.2).
 *
 * Presets: the backend buckets the trend hourly for windows up to 2 days and daily beyond,
 * aligned to UTC from the epoch. Each preset therefore ends at the next bucket boundary after
 * "now" (the current bucket is still filling) and starts a whole number of buckets earlier,
 * so every bucket in the chart covers its full width and none is silently truncated.
 *
 * Custom windows (Phase 3C): `from`/`to` as ISO-8601 UTC with `Z` (what links carry), or the
 * custom-range form's `from_utc`/`to_utc` (`YYYY-MM-DDTHH:MM[:SS]`, explicitly UTC by name).
 * A naive `from`/`to` is refused, exactly like the backend. Invalid windows fall back to the
 * default preset with a stated reason; nothing invalid is forwarded.
 */

import { TRACE_PROVENANCES, type TraceProvenance } from "./api/types";
import { parseProjectParam, type ProjectParam } from "./project";

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

// ---------------------------------------------------------------- custom windows
export type WindowSpec = { kind: "preset"; range: RangeKey } | { kind: "custom"; from: string; to: string };

/** UTC with `Z` and at most microseconds (what the backend parses and echoes). */
const ISO_Z_RE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(\.\d{1,6})?Z$/;
/** An `<input type="datetime-local">` value; the form names it `*_utc`, so it is UTC. */
const FORM_RE = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})(:\d{2})?$/;

/** A real calendar instant in [1970, 9999]: `2026-02-30T…` is refused, not rolled over. */
export function isUtcInstant(s: string): boolean {
  const m = ISO_Z_RE.exec(s);
  if (m === null) return false;
  const ms = Date.parse(s);
  if (Number.isNaN(ms) || ms < 0) return false;
  return new Date(ms).toISOString().slice(0, 19) === s.slice(0, 19);
}

/** Microseconds since the epoch of a valid instant (the backend's precision; `Date` keeps ms only). */
export function utcMicros(s: string): bigint {
  const m = ISO_Z_RE.exec(s);
  const fraction = (m?.[7] ?? ".").slice(1).padEnd(6, "0");
  return BigInt(Date.parse(`${s.slice(0, 19)}Z`)) * 1000n + BigInt(fraction);
}

const MAX_WINDOW_US = BigInt(MAX_WINDOW_MS) * 1000n;

/** A custom-range form value as an ISO `Z` instant, or null. */
export function utcFromFormValue(s: string): string | null {
  const m = FORM_RE.exec(s);
  if (m === null) return null;
  const iso = `${m[1]}${m[2] ?? ":00"}Z`;
  return isUtcInstant(iso) ? iso : null;
}

/** `YYYY-MM-DDTHH:MM` of an ISO `Z` instant, for a datetime-local field's value. */
export function formValueFromUtc(iso: string): string {
  return iso.slice(0, 16);
}

export type WindowProblem = "incomplete" | "format" | "order" | "too_long";

export const WINDOW_PROBLEM_TEXT: Record<WindowProblem, string> = {
  incomplete: "a custom range needs both a start and an end",
  format: "the start or end is not a UTC date and time",
  order: "the start must be before the end",
  too_long: "the range is longer than the backend's 31-day maximum",
};

type Getter = (name: string) => string | undefined;

/**
 * The window of a URL: a valid custom range (form fields win over link fields), else a
 * preset. Names of ignored parameters go to `ignored`; a refused custom range is explained.
 */
export function parseWindow(get: Getter, ignored: string[]): { window: WindowSpec; problem: WindowProblem | null } {
  let window: WindowSpec = { kind: "preset", range: DEFAULT_RANGE };
  let problem: WindowProblem | null = null;

  const formFrom = get("from_utc");
  const formTo = get("to_utc");
  const linkFrom = get("from");
  const linkTo = get("to");
  const fromForm = formFrom !== undefined || formTo !== undefined;
  const rawFrom = fromForm ? formFrom : linkFrom;
  const rawTo = fromForm ? formTo : linkTo;
  if (rawFrom !== undefined || rawTo !== undefined) {
    const convert = (raw: string) => (fromForm ? utcFromFormValue(raw.trim()) : isUtcInstant(raw) ? raw : null);
    const from = rawFrom === undefined || rawFrom.trim() === "" ? undefined : convert(rawFrom);
    const to = rawTo === undefined || rawTo.trim() === "" ? undefined : convert(rawTo);
    if (from === undefined || to === undefined) problem = "incomplete";
    else if (from === null || to === null) problem = "format";
    else if (utcMicros(from) >= utcMicros(to)) problem = "order";
    else if (utcMicros(to) - utcMicros(from) > MAX_WINDOW_US) problem = "too_long";
    else window = { kind: "custom", from, to };
    if (problem !== null) ignored.push("from/to");
  }

  const rawRange = get("range");
  if (rawRange !== undefined) {
    const range = RANGE_KEYS.find((k) => k === rawRange);
    if (range === undefined) ignored.push("range");
    else if (window.kind === "preset") window = { kind: "preset", range };
  }
  return { window, problem };
}

/** `[from, to)` the backend will be asked for: a preset resolved at `now`, or the custom window. */
export function resolveWindowSpec(spec: WindowSpec, now: Date): { from: string; to: string } {
  if (spec.kind === "custom") return { from: spec.from, to: spec.to };
  const { from, to } = resolveWindow(spec.range, now);
  return { from: toQueryTimestamp(from), to: toQueryTimestamp(to) };
}

/** Canonical URL parameters of a window (default preset omitted). */
export function writeWindow(spec: WindowSpec, sp: URLSearchParams): void {
  if (spec.kind === "preset") {
    if (spec.range !== DEFAULT_RANGE) sp.set("range", spec.range);
  } else {
    sp.set("from", spec.from);
    sp.set("to", spec.to);
  }
}

export function windowLabel(spec: WindowSpec): string {
  return spec.kind === "preset" ? RANGE_PRESETS[spec.range].long.toLowerCase() : "custom range";
}

export function sameWindow(a: WindowSpec, b: WindowSpec): boolean {
  return a.kind === "preset" ? b.kind === "preset" && a.range === b.range : b.kind === "custom" && a.from === b.from && a.to === b.to;
}

// ---------------------------------------------------------------- overview query
export type ProvenanceFilter = TraceProvenance | "all";
export const PROVENANCE_FILTERS: readonly ProvenanceFilter[] = ["all", ...TRACE_PROVENANCES];

type SearchParams = Readonly<Record<string, string | string[] | undefined>>;

/** A single-valued parameter; a repeated one is recorded as ignored and treated as absent. */
export function singleGetter(params: SearchParams, ignored: string[]): Getter {
  return (name) => {
    const v = params[name];
    if (Array.isArray(v)) {
      ignored.push(name);
      return undefined;
    }
    return v;
  };
}

export interface OverviewQuery {
  project: ProjectParam;
  window: WindowSpec;
  provenance: ProvenanceFilter;
  /** Names of parameters that were present but unsupported (their values were ignored). */
  ignored: string[];
  windowProblem: WindowProblem | null;
}

/** Parse `?project=&range=|from=&to=&provenance=` strictly; anything else falls back to the defaults. */
export function parseOverviewQuery(params: SearchParams): OverviewQuery {
  const ignored: string[] = [];
  const get = singleGetter(params, ignored);
  const project = parseProjectParam(params.project);
  const { window, problem } = parseWindow(get, ignored);
  const rawProv = get("provenance");
  const provenance = PROVENANCE_FILTERS.find((p) => p === rawProv) ?? "all";
  if (rawProv !== undefined && provenance !== rawProv) ignored.push("provenance");
  return { project, window, provenance, ignored, windowProblem: problem };
}

/** Backend query parameters for a parsed overview query (never raw user input). */
export function backendParams(query: Pick<OverviewQuery, "window" | "provenance">, now: Date): Record<string, string> {
  const params: Record<string, string> = resolveWindowSpec(query.window, now);
  if (query.provenance !== "all") params.provenance = query.provenance;
  return params;
}

export type OverviewState = Pick<OverviewQuery, "window" | "provenance"> & { project: string | null };

/** Canonical query string of an overview state (defaults omitted, project first). */
export function overviewSearch(state: OverviewState): string {
  const sp = new URLSearchParams();
  if (state.project !== null) sp.set("project", state.project);
  writeWindow(state.window, sp);
  if (state.provenance !== "all") sp.set("provenance", state.provenance);
  return sp.toString();
}

/** Relative URL of the overview with the given state (defaults omitted). */
export function overviewHref(state: OverviewState): string {
  const qs = overviewSearch(state);
  return qs ? `/?${qs}` : "/";
}
