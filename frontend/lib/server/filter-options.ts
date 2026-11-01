import "server-only";

import type { ApiResult } from "../api/result";
import { modelsCompareDecoder } from "../api/schema";
import type { TraceList } from "../api/trace-types";
import type { Filters } from "../api/types";
import { echoMatches, filterOptions, type FilterOptions, type ScopeFilters } from "../scope-filters";
import { getJson, type BackendDeps } from "./backend";
import type { DashboardConfig } from "./config";
import { requireProject } from "./context";

/** Groups read for the suggested values: the backend's maximum. */
export const OPTIONS_GROUP_LIMIT = "200";

/**
 * Phase 6C-C: the providers and models the backend observed for one project and window
 * (`GET /api/v1/models/compare`, no other filter, top 200 groups), for the filter controls.
 */
export async function loadFilterOptions(config: DashboardConfig, projectId: string, window: { from: string; to: string }, deps: BackendDeps = {}): Promise<ApiResult<FilterOptions>> {
  const r = requireProject(await getJson(config, "/api/v1/models/compare", { project: projectId }, { from: window.from, to: window.to, limit: OPTIONS_GROUP_LIMIT }, modelsCompareDecoder, deps), projectId);
  return r.ok ? { ok: true, data: filterOptions(r.data) } : r;
}

/** Refuses an aggregate response whose filter echo does not match the requested provider/model. */
export function requireScope<T extends { filters: Filters }>(result: ApiResult<T>, scope: ScopeFilters): ApiResult<T> {
  if (result.ok && !echoMatches(result.data.filters, scope)) return { ok: false, error: { kind: "malformed", detail: "the backend did not apply the provider/model filter" } };
  return result;
}

/**
 * The trace list has no filter echo: each item's `usage.models` must contain a combination
 * matching the requested provider and model (one row, like the backend's rule), unless that
 * item's list is truncated. A backend that ignored `provider` is caught here.
 */
export function requireTraceScope(result: ApiResult<TraceList>, scope: ScopeFilters): ApiResult<TraceList> {
  if (!result.ok || scope.provider === null) return result;
  const ok = result.data.items.every(
    (t) =>
      t.usage.models.truncated ||
      t.usage.models.items.some((m) => m.provider === scope.provider && (scope.model === null || m.requested_model === scope.model || m.response_model === scope.model)),
  );
  return ok ? result : { ok: false, error: { kind: "malformed", detail: "the backend did not apply the provider filter" } };
}
