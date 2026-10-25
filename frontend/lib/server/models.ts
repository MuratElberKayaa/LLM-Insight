import "server-only";

import type { ApiResult } from "../api/result";
import { modelsCompareDecoder } from "../api/schema";
import type { ModelsCompare } from "../api/types";
import { compareParams, type ModelQuery } from "../model-query";
import { getJson, type BackendDeps } from "./backend";
import { failedContext, loadProjects, pageScope, requireProject, type ProjectContext } from "./context";

export interface ModelsPageData extends ProjectContext {
  fetchedAt: Date;
  compare: ApiResult<ModelsCompare>;
}

/** `GET /api/v1/models/compare` (M15) for one project and window, plus the project listing. */
export async function loadModels(query: ModelQuery, now: Date = new Date(), deps: BackendDeps = {}): Promise<ModelsPageData> {
  const scope = pageScope(query.project);
  if (!scope.ok) return { ...failedContext(), fetchedAt: now, compare: { ok: false, error: scope.error } };
  const { config, projectId, explicit } = scope;
  const [compare, projects] = await Promise.all([
    getJson(config, "/api/v1/models/compare", { project: projectId }, compareParams(query, now), modelsCompareDecoder, deps),
    loadProjects(config, deps),
  ]);
  return { projectId, explicit, projects, fetchedAt: now, compare: requireProject(compare, projectId) };
}
