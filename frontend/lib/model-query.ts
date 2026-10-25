/**
 * URL state of the model comparison page (`/models`, Phase 3C) and its translation into
 * `GET /api/v1/models/compare` parameters. Only filters the backend implements: a UTC window
 * of at most 31 days, `provenance`, the attempt `status`, and the group `limit` (top-N by
 * attempts; the backend caps it at 200).
 */

import { parseProjectParam, projectLinkValue, type ProjectParam } from "./project";
import {
  PROVENANCE_FILTERS,
  parseWindow,
  resolveWindowSpec,
  singleGetter,
  writeWindow,
  type ProvenanceFilter,
  type WindowProblem,
  type WindowSpec,
} from "./time-range";

export const GROUP_LIMITS = [10, 25, 50, 100, 200] as const;
export type GroupLimit = (typeof GROUP_LIMITS)[number];
export const DEFAULT_GROUP_LIMIT: GroupLimit = 50;
export type AttemptStatusFilter = "all" | "ok" | "error";
export const ATTEMPT_STATUS_FILTERS: readonly AttemptStatusFilter[] = ["all", "ok", "error"];

export interface ModelQuery {
  project: ProjectParam;
  window: WindowSpec;
  provenance: ProvenanceFilter;
  status: AttemptStatusFilter;
  limit: GroupLimit;
  ignored: string[];
  windowProblem: WindowProblem | null;
}

export type ModelState = Omit<ModelQuery, "project" | "ignored" | "windowProblem"> & { project: string | null };

type SearchParams = Readonly<Record<string, string | string[] | undefined>>;

export function parseModelQuery(params: SearchParams): ModelQuery {
  const ignored: string[] = [];
  const get = singleGetter(params, ignored);
  const project = parseProjectParam(params.project);
  const { window, problem } = parseWindow(get, ignored);

  const rawProv = get("provenance");
  const provenance = PROVENANCE_FILTERS.find((p) => p === rawProv) ?? "all";
  if (rawProv !== undefined && provenance !== rawProv) ignored.push("provenance");

  const rawStatus = get("status");
  const status = ATTEMPT_STATUS_FILTERS.find((s) => s === rawStatus) ?? "all";
  if (rawStatus !== undefined && status !== rawStatus) ignored.push("status");

  const rawLimit = get("limit");
  const limit = GROUP_LIMITS.find((n) => String(n) === rawLimit) ?? DEFAULT_GROUP_LIMIT;
  if (rawLimit !== undefined && String(limit) !== rawLimit) ignored.push("limit");

  return { project, window, provenance, status, limit, ignored, windowProblem: problem };
}

export function modelStateOf(query: ModelQuery): ModelState {
  const { project, window, provenance, status, limit } = query;
  return { project: projectLinkValue(project), window, provenance, status, limit };
}

/** Backend parameters for `GET /api/v1/models/compare` (validated values only). */
export function compareParams(query: Pick<ModelQuery, "window" | "provenance" | "status" | "limit">, now: Date): Record<string, string> {
  const params: Record<string, string> = { ...resolveWindowSpec(query.window, now), limit: String(query.limit) };
  if (query.provenance !== "all") params.provenance = query.provenance;
  if (query.status !== "all") params.status = query.status;
  return params;
}

export function modelSearch(state: ModelState): string {
  const sp = new URLSearchParams();
  if (state.project !== null) sp.set("project", state.project);
  writeWindow(state.window, sp);
  if (state.provenance !== "all") sp.set("provenance", state.provenance);
  if (state.status !== "all") sp.set("status", state.status);
  if (state.limit !== DEFAULT_GROUP_LIMIT) sp.set("limit", String(state.limit));
  return sp.toString();
}

export function modelHref(state: ModelState): string {
  const qs = modelSearch(state);
  return qs ? `/models?${qs}` : "/models";
}

export function withModelFilter(state: ModelState, change: Partial<Omit<ModelState, "project">>): ModelState {
  return { ...state, ...change };
}
