import "server-only";

import type { ApiResult } from "../api/result";
import { modelsCompareDecoder } from "../api/schema";
import type { ModelsCompare } from "../api/types";
import { compareParams, type ModelQuery } from "../model-query";
import type { FilterOptions } from "../scope-filters";
import { getJson, type BackendDeps } from "./backend";
import { failedContext, loadProjects, pageScope, requireProject, type ProjectContext } from "./context";
import { loadFilterOptions, requireScope } from "./filter-options";

export interface ModelsPageData extends ProjectContext {
  fetchedAt: Date;
  /** Server clock after every read returned (Retry-After counts from here). */
  settledAt: number;
  compare: ApiResult<ModelsCompare>;
  /** Phase 6C-C: providers and models observed in the window (filter suggestions). */
  options: ApiResult<FilterOptions> | null;
}

/** `GET /api/v1/models/compare` (M15) for one project and window, plus the project listing. */
export async function loadModels(query: ModelQuery, now: Date = new Date(), deps: BackendDeps = {}): Promise<ModelsPageData> {
  const scope = pageScope(query.project);
  if (!scope.ok) return { ...failedContext(), fetchedAt: now, settledAt: now.getTime(), compare: { ok: false, error: scope.error }, options: null };
  const { config, projectId, explicit } = scope;
  const params = compareParams(query, now);
  const [compare, options, projects] = await Promise.all([
    getJson(config, "/api/v1/models/compare", { project: projectId }, params, modelsCompareDecoder, deps),
    loadFilterOptions(config, projectId, { from: params.from ?? "", to: params.to ?? "" }, deps),
    loadProjects(config, deps),
  ]);
  return { projectId, explicit, projects, fetchedAt: now, settledAt: Date.now(), compare: requireScope(requireProject(compare, projectId), query), options };
}
