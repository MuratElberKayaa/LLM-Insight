import "server-only";

import type { ApiResult } from "../api/result";
import { modelsCompareDecoder, overviewDecoder } from "../api/schema";
import type { BackendHealth, ModelsCompare, Overview } from "../api/types";
import { backendParams, type OverviewQuery } from "../time-range";
import { getHealth, getJson, type BackendDeps } from "./backend";
import { readConfig } from "./config";
import { failedContext, loadProjects, pageScope, requireProject, type ProjectContext } from "./context";

export const COMPARE_LIMIT = "10";

export interface OverviewPageData extends ProjectContext {
  fetchedAt: Date;
  overview: ApiResult<Overview>;
  compare: ApiResult<ModelsCompare>;
}

/** Both overview reads plus the project listing, in parallel, for one project and window. */
export async function loadOverview(query: OverviewQuery, now: Date = new Date(), deps: BackendDeps = {}): Promise<OverviewPageData> {
  const scope = pageScope(query.project);
  if (!scope.ok) {
    const error = { ok: false as const, error: scope.error };
    return { ...failedContext(), fetchedAt: now, overview: error, compare: error };
  }
  const { config, projectId, explicit } = scope;
  const params = backendParams(query, now);
  const s = { project: projectId };
  const [overview, compare, projects] = await Promise.all([
    getJson(config, "/api/v1/overview", s, params, overviewDecoder, deps),
    getJson(config, "/api/v1/models/compare", s, { ...params, limit: COMPARE_LIMIT }, modelsCompareDecoder, deps),
    loadProjects(config, deps),
  ]);
  return {
    projectId,
    explicit,
    projects,
    fetchedAt: now,
    overview: requireProject(overview, projectId),
    compare: requireProject(compare, projectId),
  };
}

export async function loadHealth(deps: BackendDeps = {}): Promise<BackendHealth | "unconfigured"> {
  const cfg = readConfig();
  if (!cfg.ok) return "unconfigured";
  return getHealth(cfg.config, deps);
}
