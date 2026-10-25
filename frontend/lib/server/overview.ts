import "server-only";

import type { ApiResult } from "../api/result";
import { modelsCompareDecoder, overviewDecoder } from "../api/schema";
import type { BackendHealth, ModelsCompare, Overview } from "../api/types";
import { backendParams, type OverviewQuery } from "../time-range";
import { getHealth, getJson, type BackendDeps } from "./backend";
import { readConfig } from "./config";

export const COMPARE_LIMIT = "10";

export interface OverviewPageData {
  projectId: string | null;
  fetchedAt: Date;
  overview: ApiResult<Overview>;
  compare: ApiResult<ModelsCompare>;
}

/** Both overview reads, in parallel, with the same validated window and filters. */
export async function loadOverview(query: OverviewQuery, now: Date = new Date(), deps: BackendDeps = {}): Promise<OverviewPageData> {
  const cfg = readConfig();
  if (!cfg.ok) {
    const error = { ok: false as const, error: { kind: "config" as const, detail: cfg.problem } };
    return { projectId: null, fetchedAt: now, overview: error, compare: error };
  }
  const params = backendParams(query, now);
  const [overview, compare] = await Promise.all([
    getJson(cfg.config, "/api/v1/overview", params, overviewDecoder, deps),
    getJson(cfg.config, "/api/v1/models/compare", { ...params, limit: COMPARE_LIMIT }, modelsCompareDecoder, deps),
  ]);
  return { projectId: cfg.config.projectId, fetchedAt: now, overview, compare };
}

export async function loadHealth(deps: BackendDeps = {}): Promise<BackendHealth | "unconfigured"> {
  const cfg = readConfig();
  if (!cfg.ok) return "unconfigured";
  return getHealth(cfg.config, deps);
}
