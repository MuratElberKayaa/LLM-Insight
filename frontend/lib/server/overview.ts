import "server-only";

import type { ApiResult } from "../api/result";
import { modelsCompareDecoder, overviewDecoder } from "../api/schema";
import { traceListDecoder } from "../api/trace-schema";
import type { TraceList } from "../api/trace-types";
import type { BackendHealth, ModelsCompare, Overview } from "../api/types";
import type { FilterOptions } from "../scope-filters";
import { backendParams, type OverviewQuery } from "../time-range";
import { getHealth, getJson, type BackendDeps } from "./backend";
import { readConfig } from "./config";
import { failedContext, loadProjects, pageScope, requireProject, type ProjectContext } from "./context";
import { loadFilterOptions, requireScope, requireTraceScope } from "./filter-options";

export const COMPARE_LIMIT = "10";

export interface OverviewPageData extends ProjectContext {
  fetchedAt: Date;
  /** Server clock after every read returned (Retry-After counts from here). */
  settledAt: number;
  overview: ApiResult<Overview>;
  compare: ApiResult<ModelsCompare>;
  /** Phase 6C-C: the newest stored trace of the same window and filters (`limit=1`). */
  latest: ApiResult<TraceList> | null;
  /** Phase 6C-C: providers and models observed in the window (filter suggestions). */
  options: ApiResult<FilterOptions> | null;
}

/** The overview reads plus the project listing, in parallel, for one project and window. */
export async function loadOverview(query: OverviewQuery, now: Date = new Date(), deps: BackendDeps = {}): Promise<OverviewPageData> {
  const scope = pageScope(query.project);
  if (!scope.ok) {
    const error = { ok: false as const, error: scope.error };
    return { ...failedContext(), fetchedAt: now, settledAt: now.getTime(), overview: error, compare: error, latest: null, options: null };
  }
  const { config, projectId, explicit } = scope;
  const params = backendParams(query, now);
  const filters = { provider: query.provider, model: query.model };
  const s = { project: projectId };
  const [overview, compare, latest, options, projects] = await Promise.all([
    getJson(config, "/api/v1/overview", s, params, overviewDecoder, deps),
    getJson(config, "/api/v1/models/compare", s, { ...params, limit: COMPARE_LIMIT }, modelsCompareDecoder, deps),
    getJson(config, "/api/v1/traces", s, { ...params, limit: "1" }, traceListDecoder, deps),
    loadFilterOptions(config, projectId, { from: params.from ?? "", to: params.to ?? "" }, deps),
    loadProjects(config, deps),
  ]);
  return {
    projectId,
    explicit,
    projects,
    fetchedAt: now,
    settledAt: Date.now(),
    overview: requireScope(requireProject(overview, projectId), filters),
    compare: requireScope(requireProject(compare, projectId), filters),
    latest: requireTraceScope(latest, filters),
    options,
  };
}

export async function loadHealth(deps: BackendDeps = {}): Promise<BackendHealth | "unconfigured"> {
  const cfg = readConfig();
  if (!cfg.ok) return "unconfigured";
  return getHealth(cfg.config, deps);
}
