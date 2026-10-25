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
import { hiddenFields, parseProjectParam, projectLinkValue, type ProjectParam } from "./project";
import { parseWindow, resolveWindowSpec, singleGetter, writeWindow, type ProvenanceFilter, type WindowProblem, type WindowSpec } from "./time-range";

export type { WindowSpec } from "./time-range";

export const PAGE_SIZES = [10, 25, 50, 100] as const;
export type PageSize = (typeof PAGE_SIZES)[number];
export const DEFAULT_PAGE_SIZE: PageSize = 25;
export type StatusFilter = "ok" | "error" | "all";
export const STATUS_FILTERS: readonly StatusFilter[] = ["all", "ok", "error"];

/** Stored model names match this format (TELEMETRY_CONTRACT.md §3.1); nothing else can match. */
const MODEL_RE = /^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$/;
const CURSOR_RE = /^[A-Za-z0-9_-]{1,128}$/;
const PAGE_RE = /^[1-9]\d{0,4}$/;

export interface TraceListQuery {
  /** `?project=` (Phase 3C); `default` = the configured project. */
  project: ProjectParam;
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
  /** Why a custom range was refused, if one was. */
  windowProblem: WindowProblem | null;
}

type SearchParams = Readonly<Record<string, string | string[] | undefined>>;

export function parseTraceListQuery(params: SearchParams): TraceListQuery {
  const ignored: string[] = [];
  const get = singleGetter(params, ignored);
  const project = parseProjectParam(params.project);
  const { window, problem } = parseWindow(get, ignored);

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

  return { project, window, status, provenance, model, limit, cursor, page, ignored, windowProblem: problem };
}

/** `[from, to)` the backend will be asked for: a preset resolved at `now`, or the custom window. */
export const resolveListWindow = resolveWindowSpec;

/** Backend query parameters for `GET /api/v1/traces` (validated values only, never raw input). */
export function traceListParams(query: TraceListQuery, now: Date): Record<string, string> {
  const params: Record<string, string> = { ...resolveListWindow(query.window, now), limit: String(query.limit) };
  if (query.status !== "all") params.status = query.status;
  if (query.provenance !== "all") params.provenance = query.provenance;
  if (query.model !== null) params.model = query.model;
  if (query.cursor !== null) params.cursor = query.cursor;
  return params;
}

export type ListState = Omit<TraceListQuery, "ignored" | "windowProblem" | "project"> & {
  /** The explicit `?project=` id, or null (the configured default applies). */
  project: string | null;
};

/** A parsed query as link state (an invalid project never becomes a link). */
export function listStateOf(query: TraceListQuery): ListState {
  const { project, window, status, provenance, model, limit, cursor, page } = query;
  return { project: projectLinkValue(project), window, status, provenance, model, limit, cursor, page };
}

/** Canonical query string of a list state (defaults omitted, project first). */
export function traceListSearch(state: ListState, includeProject = true): string {
  const sp = new URLSearchParams();
  if (includeProject && state.project !== null) sp.set("project", state.project);
  writeWindow(state.window, sp);
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

/**
 * Hidden fields for forms that leave the current listing (project switch, custom range): the
 * filters only. A cursor and page number belong to one project's listing and are never kept.
 */
export function restartFields(state: ListState, drop: readonly string[]): [string, string][] {
  return hiddenFields(traceListSearch(withFilter(state, {})), drop);
}

/** The next page: the backend-echoed window (frozen) plus its cursor. */
export function nextPageState(state: ListState, window: { from: string; to: string }, cursor: string): ListState {
  return { ...state, window: { kind: "custom", from: window.from, to: window.to }, cursor, page: state.page + 1 };
}

/** The first page of the same frozen window. */
export function firstPageState(state: ListState): ListState {
  return { ...state, cursor: null, page: 1 };
}

/**
 * Detail link that remembers the listing, so "Back to traces" restores filters and page. The
 * project travels as the detail page's own `project` parameter, not inside `list`.
 */
export function traceDetailHref(traceId: string, state: ListState): string {
  const list = traceListSearch(state, false);
  const parts: string[] = [];
  if (state.project !== null) parts.push(`project=${state.project}`);
  if (list) parts.push(`list=${encodeURIComponent(list)}`);
  return parts.length > 0 ? `/traces/${traceId}?${parts.join("&")}` : `/traces/${traceId}`;
}

/** Link state of the listing a detail page came from; the detail page's project wins. */
export function listStateFromBackParam(list: string | string[] | undefined, project: string | null): ListState {
  if (typeof list !== "string" || list.length > 2048) return { ...listStateOf(parseTraceListQuery({})), project };
  const sp = new URLSearchParams(list);
  const params: Record<string, string | string[]> = {};
  for (const key of new Set(sp.keys())) {
    if (key === "project") continue;
    const all = sp.getAll(key);
    params[key] = all.length === 1 ? (all[0] ?? "") : all;
  }
  return { ...listStateOf(parseTraceListQuery(params)), project };
}

/**
 * The listing a detail page came from. The `list` value is parsed with the same strict
 * parser and re-serialized, so only canonical trace-list parameters survive (no open
 * redirect, no arbitrary path or parameter).
 */
export function backToListHref(list: string | string[] | undefined, project: string | null = null): string {
  return traceListHref(listStateFromBackParam(list, project));
}
