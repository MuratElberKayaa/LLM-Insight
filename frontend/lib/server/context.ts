import "server-only";

import type { ApiFailure, ApiResult } from "../api/result";
import { projectListDecoder } from "../api/schema";
import type { ProjectList } from "../api/types";
import type { ProjectParam } from "../project";
import { getJson, type BackendDeps } from "./backend";
import { readConfig, type DashboardConfig } from "./config";

/**
 * The project a page reads (Phase 3C). Exactly one project per render: the URL's explicit id,
 * or the configured default when the URL has none. An invalid id is refused here, before
 * any backend request, and never replaced by another project.
 */
export type PageScope = { ok: true; config: DashboardConfig; projectId: string; explicit: boolean } | { ok: false; error: ApiFailure };

export function pageScope(param: ProjectParam): PageScope {
  const cfg = readConfig();
  if (!cfg.ok) return { ok: false, error: { kind: "config", detail: cfg.problem } };
  if (param.kind === "invalid") return { ok: false, error: { kind: "invalid_project" } };
  const explicit = param.kind === "explicit";
  return { ok: true, config: cfg.config, projectId: explicit ? param.id : cfg.config.defaultProjectId, explicit };
}

export const PROJECT_LIST_LIMIT = "200";

/** `GET /api/v1/projects` for the selector (the listing has no project scope). */
export function loadProjects(config: DashboardConfig, deps: BackendDeps = {}): Promise<ApiResult<ProjectList>> {
  return getJson(config, "/api/v1/projects", { project: null }, { limit: PROJECT_LIST_LIMIT }, projectListDecoder, deps);
}

/** Rejects a response that echoes a project other than the one requested (defence in depth). */
export function requireProject<T extends { project: number }>(result: ApiResult<T>, projectId: string): ApiResult<T> {
  if (result.ok && String(result.data.project) !== projectId) return { ok: false, error: { kind: "malformed", detail: "response for another project" } };
  return result;
}

/** What every project-scoped page shows about the current project. */
export interface ProjectContext {
  /** The project the page's data belongs to; null when no project could be resolved. */
  projectId: string | null;
  /** True when the project came from the URL (links then keep `?project=`). */
  explicit: boolean;
  /** The selector's listing; null when no request was made (configuration problem). */
  projects: ApiResult<ProjectList> | null;
}

export function failedContext(): ProjectContext {
  return { projectId: null, explicit: false, projects: null };
}
