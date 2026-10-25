import "server-only";

import type { ApiResult } from "../api/result";
import { traceDetailDecoder, traceListDecoder } from "../api/trace-schema";
import type { TraceDetail, TraceList } from "../api/trace-types";
import { traceListParams, type TraceListQuery } from "../trace-query";
import { getJson, MAX_DETAIL_RESPONSE_BYTES, traceDetailPath, type BackendDeps } from "./backend";
import { readConfig } from "./config";

export interface TraceListPageData {
  projectId: string | null;
  fetchedAt: Date;
  list: ApiResult<TraceList>;
}

export interface TraceDetailPageData {
  projectId: string | null;
  fetchedAt: Date;
  detail: ApiResult<TraceDetail>;
}

/** One page of `GET /api/v1/traces` for the configured project and a validated query. */
export async function loadTraceList(query: TraceListQuery, now: Date = new Date(), deps: BackendDeps = {}): Promise<TraceListPageData> {
  const cfg = readConfig();
  if (!cfg.ok) return { projectId: null, fetchedAt: now, list: { ok: false, error: { kind: "config", detail: cfg.problem } } };
  const list = await getJson(cfg.config, "/api/v1/traces", traceListParams(query, now), traceListDecoder, deps);
  return { projectId: cfg.config.projectId, fetchedAt: now, list };
}

/**
 * `GET /api/v1/traces/{trace_id}` for the configured project. The caller validates the id
 * first; an id that is not 32 lowercase hex never reaches the backend.
 */
export async function loadTraceDetail(traceId: string, now: Date = new Date(), deps: BackendDeps = {}): Promise<TraceDetailPageData> {
  const cfg = readConfig();
  if (!cfg.ok) return { projectId: null, fetchedAt: now, detail: { ok: false, error: { kind: "config", detail: cfg.problem } } };
  const path = traceDetailPath(traceId);
  if (path === null) return { projectId: cfg.config.projectId, fetchedAt: now, detail: { ok: false, error: { kind: "invalid_request", fields: ["trace_id"] } } };
  const detail = await getJson(cfg.config, path, {}, traceDetailDecoder, deps, { maxBytes: MAX_DETAIL_RESPONSE_BYTES });
  return { projectId: cfg.config.projectId, fetchedAt: now, detail };
}
