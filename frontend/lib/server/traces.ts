import "server-only";

import type { ApiResult } from "../api/result";
import { traceDetailDecoder, traceListDecoder } from "../api/trace-schema";
import type { TraceDetail, TraceList } from "../api/trace-types";
import type { ProjectParam } from "../project";
import { traceListParams, type TraceListQuery } from "../trace-query";
import { getJson, MAX_DETAIL_RESPONSE_BYTES, traceDetailPath, type BackendDeps } from "./backend";
import { failedContext, loadProjects, pageScope, type ProjectContext } from "./context";

export interface TraceListPageData extends ProjectContext {
  fetchedAt: Date;
  list: ApiResult<TraceList>;
}

export interface TraceDetailPageData extends ProjectContext {
  fetchedAt: Date;
  detail: ApiResult<TraceDetail>;
}

/** One page of `GET /api/v1/traces` for one project and a validated query, plus the listing. */
export async function loadTraceList(query: TraceListQuery, now: Date = new Date(), deps: BackendDeps = {}): Promise<TraceListPageData> {
  const scope = pageScope(query.project);
  if (!scope.ok) return { ...failedContext(), fetchedAt: now, list: { ok: false, error: scope.error } };
  const { config, projectId, explicit } = scope;
  const [list, projects] = await Promise.all([
    getJson(config, "/api/v1/traces", { project: projectId }, traceListParams(query, now), traceListDecoder, deps),
    loadProjects(config, deps),
  ]);
  return { projectId, explicit, projects, fetchedAt: now, list };
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
