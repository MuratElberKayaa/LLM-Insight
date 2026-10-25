/**
 * URL state of the trace explorer (`/traces`) and its translation into backend parameters.
 * Only filters the backend implements are accepted (ARCHITECTURE.md §5.1): a UTC window of
 * at most 31 days, `status`, `provenance`, an exact `model`, `limit` and the opaque `cursor`.
 * There is no provider or service filter because the API has none.
 *
 * Paging freezes the window: the "next page" link carries the backend-echoed `from`/`to`
 * plus the cursor, so every page of one listing is cut from the same window even while
 * the clock moves (keyset pagination on `started_at DESC, trace_id DESC`).
 */

import { TRACE_PROVENANCES } from "./api/types";
import { DEFAULT_RANGE, MAX_WINDOW_MS, RANGE_KEYS, resolveWindow, toQueryTimestamp, type ProvenanceFilter, type RangeKey } from "./time-range";

export const PAGE_SIZES = [10, 25, 50, 100] as const;
export type PageSize = (typeof PAGE_SIZES)[number];
export const DEFAULT_PAGE_SIZE: PageSize = 25;
export type StatusFilter = "ok" | "error" | "all";
export const STATUS_FILTERS: readonly StatusFilter[] = ["all", "ok", "error"];

/** Stored model names match this format (TELEMETRY_CONTRACT.md §3.1); nothing else can match. */
const MODEL_RE = /^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$/;
const CURSOR_RE = /^[A-Za-z0-9_-]{1,128}$/;
/** UTC with `Z` and at most microseconds (what the backend parses and echoes). */
const ISO_Z_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$/;
const PAGE_RE = /^[1-9]\d{0,4}$/;

export type WindowSpec = { kind: "preset"; range: RangeKey } | { kind: "custom"; from: string; to: string };

export interface TraceListQuery {
  window: WindowSpec;
  status: StatusFilter;
  provenance: ProvenanceFilter;
  model: string | null;
  limit: PageSize;
  cursor: string | null;
  /** 1-based page number for display only (the cursor decides the content). */
  page: number;
  /** Names of parameters that were present but unsupported (their values were ignored). */
  ignored: string[];
}

type SearchParams = Readonly<Record<string, string | string[] | undefined>>;

function single(params: SearchParams, name: string, ignored: string[]): string | undefined {
  const v = params[name];
  if (Array.isArray(v)) {
    ignored.push(name);
    return undefined;
  }
  return v;
}

function isUtcInstant(s: string): boolean {
  return ISO_Z_RE.test(s) && !Number.isNaN(Date.parse(s));
}

export function parseTraceListQuery(params: SearchParams): TraceListQuery {
  const ignored: string[] = [];
  const get = (name: string) => single(params, name, ignored);

  const rawRange = get("range");
  const rawFrom = get("from");
  const rawTo = get("to");
  let window: WindowSpec = { kind: "preset", range: DEFAULT_RANGE };
  if (rawFrom !== undefined || rawTo !== undefined) {
    const valid =
      rawFrom !== undefined &&
      rawTo !== undefined &&
      isUtcInstant(rawFrom) &&
      isUtcInstant(rawTo) &&
      Date.parse(rawFrom) < Date.parse(rawTo) &&
      Date.parse(rawTo) - Date.parse(rawFrom) <= MAX_WINDOW_MS;
    if (valid) window = { kind: "custom", from: rawFrom, to: rawTo };
    else ignored.push("from/to");
  }
  if (rawRange !== undefined) {
    const range = RANGE_KEYS.find((k) => k === rawRange);
    if (range === undefined) ignored.push("range");
    else if (window.kind === "preset") window = { kind: "preset", range };
  }

  const rawStatus = get("status");
  const status = STATUS_FILTERS.find((s) => s === rawStatus) ?? "all";
  if (rawStatus !== undefined && status !== rawStatus) ignored.push("status");

  const rawProv = get("provenance");
  const provenance: ProvenanceFilter = (["all", ...TRACE_PROVENANCES] as const).find((p) => p === rawProv) ?? "all";
  if (rawProv !== undefined && provenance !== rawProv) ignored.push("provenance");

  const rawModel = get("model")?.trim();
  let model: string | null = null;
  if (rawModel !== undefined && rawModel !== "") {
    if (MODEL_RE.test(rawModel)) model = rawModel;
    else ignored.push("model");
  }

  const rawLimit = get("limit");
  const limit = PAGE_SIZES.find((n) => String(n) === rawLimit) ?? DEFAULT_PAGE_SIZE;
  if (rawLimit !== undefined && String(limit) !== rawLimit) ignored.push("limit");

  const rawCursor = get("cursor");
  let cursor: string | null = null;
  if (rawCursor !== undefined) {
    // A cursor is only meaningful inside a frozen window; without one it is dropped.
    if (CURSOR_RE.test(rawCursor) && window.kind === "custom") cursor = rawCursor;
    else ignored.push("cursor");
  }

  const rawPage = get("page");
  const page = cursor !== null && rawPage !== undefined && PAGE_RE.test(rawPage) ? Number(rawPage) : 1;

  return { window, status, provenance, model, limit, cursor, page, ignored };
}

/** `[from, to)` the backend will be asked for: a preset resolved at `now`, or the custom window. */
export function resolveListWindow(spec: WindowSpec, now: Date): { from: string; to: string } {
  if (spec.kind === "custom") return { from: spec.from, to: spec.to };
  const { from, to } = resolveWindow(spec.range, now);
  return { from: toQueryTimestamp(from), to: toQueryTimestamp(to) };
}

/** Backend query parameters for `GET /api/v1/traces` (validated values only, never raw input). */
export function traceListParams(query: TraceListQuery, now: Date): Record<string, string> {
  const params: Record<string, string> = { ...resolveListWindow(query.window, now), limit: String(query.limit) };
  if (query.status !== "all") params.status = query.status;
  if (query.provenance !== "all") params.provenance = query.provenance;
  if (query.model !== null) params.model = query.model;
  if (query.cursor !== null) params.cursor = query.cursor;
  return params;
}

export type ListState = Omit<TraceListQuery, "ignored">;

/** Canonical query string of a list state (defaults omitted). */
export function traceListSearch(state: ListState): string {
  const sp = new URLSearchParams();
  if (state.window.kind === "preset") {
    if (state.window.range !== DEFAULT_RANGE) sp.set("range", state.window.range);
  } else {
    sp.set("from", state.window.from);
    sp.set("to", state.window.to);
  }
  if (state.status !== "all") sp.set("status", state.status);
  if (state.provenance !== "all") sp.set("provenance", state.provenance);
  if (state.model !== null) sp.set("model", state.model);
  if (state.limit !== DEFAULT_PAGE_SIZE) sp.set("limit", String(state.limit));
  if (state.cursor !== null) {
    sp.set("cursor", state.cursor);
    if (state.page > 1) sp.set("page", String(state.page));
  }
  return sp.toString();
}

export function traceListHref(state: ListState): string {
  const qs = traceListSearch(state);
  return qs ? `/traces?${qs}` : "/traces";
}

/** The same listing with a changed filter: paging restarts, a frozen custom window is kept. */
export function withFilter(state: ListState, change: Partial<Pick<ListState, "window" | "status" | "provenance" | "model" | "limit">>): ListState {
  return { ...state, ...change, cursor: null, page: 1 };
}

/** The next page: the backend-echoed window (frozen) plus its cursor. */
export function nextPageState(state: ListState, window: { from: string; to: string }, cursor: string): ListState {
  return { ...state, window: { kind: "custom", from: window.from, to: window.to }, cursor, page: state.page + 1 };
}

/** The first page of the same frozen window. */
export function firstPageState(state: ListState): ListState {
  return { ...state, cursor: null, page: 1 };
}

/** Detail link that remembers the listing, so "Back to traces" restores filters and page. */
export function traceDetailHref(traceId: string, state: ListState): string {
  const list = traceListSearch(state);
  return list ? `/traces/${traceId}?list=${encodeURIComponent(list)}` : `/traces/${traceId}`;
}

/**
 * The listing a detail page came from. The `list` value is parsed with the same strict
 * parser and re-serialized, so only canonical trace-list parameters survive (no open
 * redirect, no arbitrary path or parameter).
 */
export function backToListHref(list: string | string[] | undefined): string {
  if (typeof list !== "string" || list.length > 2048) return "/traces";
  const sp = new URLSearchParams(list);
  const params: Record<string, string | string[]> = {};
  for (const key of new Set(sp.keys())) {
    const all = sp.getAll(key);
    params[key] = all.length === 1 ? (all[0] ?? "") : all;
  }
  const parsed = parseTraceListQuery(params);
  return traceListHref(parsed);
}
