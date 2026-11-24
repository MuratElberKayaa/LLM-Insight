import "server-only";

import type { ApiResult } from "../api/result";
import { sessionListDecoder, type SessionList } from "../api/sessions";
import type { TraceList } from "../api/trace-types";
import { compareParams, type ModelQuery } from "../model-query";
import { getJson, type BackendDeps } from "./backend";
import { failedContext, loadProjects, pageScope, requireProject, type ProjectContext } from "./context";
import { loadTraceRail } from "./traces";

export interface SessionsPageData extends ProjectContext {
  fetchedAt: Date;
  settledAt: number;
  sessions: ApiResult<SessionList>;
  /** UI-R3: the selected session's traces in the same window (`GET /api/v1/traces?session=`), or null. */
  traces: ApiResult<TraceList> | null;
}

/** Phase 10C: `GET /api/v1/sessions` for one project and window (only the window is forwarded). */
export async function loadSessions(query: ModelQuery, now: Date = new Date(), deps: BackendDeps = {}, session: string | null = null): Promise<SessionsPageData> {
  const scope = pageScope(query.project);
  if (!scope.ok) return { ...failedContext(), fetchedAt: now, settledAt: now.getTime(), sessions: { ok: false, error: scope.error }, traces: null };
  const { config, projectId, explicit } = scope;
  const { from, to } = compareParams(query, now);
  const params: Record<string, string> = { limit: String(query.limit) };
  if (from !== undefined) params.from = from;
  if (to !== undefined) params.to = to;
  const [sessions, projects, traces] = await Promise.all([
    getJson(config, "/api/v1/sessions", { project: projectId }, params, sessionListDecoder, deps),
    loadProjects(config, deps),
    // UI-R3: one more read only when a session is selected (same project and window).
    session === null
      ? Promise.resolve(null)
      : loadTraceRail(
          { project: query.project, window: query.window, status: "all", provenance: "all", model: null, provider: null, session, limit: 100, cursor: null, page: 1, ignored: [], windowProblem: null },
          now,
          deps,
        ),
  ]);
  return { projectId, explicit, projects, fetchedAt: now, settledAt: Date.now(), sessions: requireProject(sessions, projectId), traces };
}
