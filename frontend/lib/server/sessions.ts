import "server-only";

import type { ApiResult } from "../api/result";
import { sessionListDecoder, type SessionList } from "../api/sessions";
import { compareParams, type ModelQuery } from "../model-query";
import { getJson, type BackendDeps } from "./backend";
import { failedContext, loadProjects, pageScope, requireProject, type ProjectContext } from "./context";

export interface SessionsPageData extends ProjectContext {
  fetchedAt: Date;
  settledAt: number;
  sessions: ApiResult<SessionList>;
}

/** Phase 10C: `GET /api/v1/sessions` for one project and window (only the window is forwarded). */
export async function loadSessions(query: ModelQuery, now: Date = new Date(), deps: BackendDeps = {}): Promise<SessionsPageData> {
  const scope = pageScope(query.project);
  if (!scope.ok) return { ...failedContext(), fetchedAt: now, settledAt: now.getTime(), sessions: { ok: false, error: scope.error } };
  const { config, projectId, explicit } = scope;
  const { from, to } = compareParams(query, now);
  const params: Record<string, string> = { limit: String(query.limit) };
  if (from !== undefined) params.from = from;
  if (to !== undefined) params.to = to;
  const [sessions, projects] = await Promise.all([
    getJson(config, "/api/v1/sessions", { project: projectId }, params, sessionListDecoder, deps),
    loadProjects(config, deps),
  ]);
  return { projectId, explicit, projects, fetchedAt: now, settledAt: Date.now(), sessions: requireProject(sessions, projectId) };
}
