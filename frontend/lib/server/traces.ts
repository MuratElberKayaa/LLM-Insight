import "server-only";

import type { ApiResult } from "../api/result";
import { traceDetailDecoder, traceListDecoder } from "../api/trace-schema";
import type { TraceDetail, TraceList } from "../api/trace-types";
import type { ProjectParam } from "../project";
import type { FilterOptions } from "../scope-filters";
import { traceListParams, type TraceListQuery } from "../trace-query";
import { getJson, MAX_DETAIL_RESPONSE_BYTES, traceDetailPath, type BackendDeps } from "./backend";
import { failedContext, loadProjects, pageScope, type ProjectContext } from "./context";
import { loadFilterOptions, requireTraceScope } from "./filter-options";

export interface TraceListPageData extends ProjectContext {
  fetchedAt: Date;
  /** Server clock after every read returned (Retry-After counts from here). */
  settledAt: number;
  list: ApiResult<TraceList>;
  /** Phase 6C-C: providers and models observed in the window (filter suggestions). */
  options: ApiResult<FilterOptions> | null;
}

export interface TraceDetailPageData extends ProjectContext {
  fetchedAt: Date;
  detail: ApiResult<TraceDetail>;
}

/** One page of `GET /api/v1/traces` for one project and a validated query, plus the listing. */
export async function loadTraceList(query: TraceListQuery, now: Date = new Date(), deps: BackendDeps = {}): Promise<TraceListPageData> {
  const scope = pageScope(query.project);
  if (!scope.ok) return { ...failedContext(), fetchedAt: now, settledAt: now.getTime(), list: { ok: false, error: scope.error }, options: null };
  const { config, projectId, explicit } = scope;
  const params = traceListParams(query, now);
  const [list, options, projects] = await Promise.all([
    getJson(config, "/api/v1/traces", { project: projectId }, params, traceListDecoder, deps),
    loadFilterOptions(config, projectId, { from: params.from ?? "", to: params.to ?? "" }, deps),
    loadProjects(config, deps),
  ]);
  return { projectId, explicit, projects, fetchedAt: now, settledAt: Date.now(), list: requireTraceScope(list, query), options };
}

/**
 * `GET /api/v1/traces/{trace_id}` for one project. The caller validates the id first; an id
 * that is not 32 lowercase hex never reaches the backend.
 */
export async function loadTraceDetail(traceId: string, project: ProjectParam, now: Date = new Date(), deps: BackendDeps = {}): Promise<TraceDetailPageData> {
  const scope = pageScope(project);
  if (!scope.ok) return { ...failedContext(), fetchedAt: now, detail: { ok: false, error: scope.error } };
  const { config, projectId, explicit } = scope;
  const path = traceDetailPath(traceId);
  if (path === null) return { projectId, explicit, projects: null, fetchedAt: now, detail: { ok: false, error: { kind: "invalid_request", fields: ["trace_id"] } } };
  const [detail, projects] = await Promise.all([
    getJson(config, path, { project: projectId }, {}, traceDetailDecoder, deps, { maxBytes: MAX_DETAIL_RESPONSE_BYTES }),
    loadProjects(config, deps),
  ]);
  return { projectId, explicit, projects, fetchedAt: now, detail };
}
