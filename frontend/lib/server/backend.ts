import "server-only";

import { BUSY_CODES, CONFLICT_CODES, type ApiFailure, type ApiResult, type BusyCode, type ConflictCode } from "../api/result";
import type { BackendHealth } from "../api/types";
import { parseJsonLossless } from "../json";
import { isProjectId } from "../project";
import { decode, type Decoder } from "../validate";
import type { DashboardConfig } from "./config";

/**
 * The only way the dashboard talks to the backend. Server-side only:
 * - fixed endpoint paths (a closed union, never caller-provided), validated query values;
 * - an explicit project scope per call (Phase 3C): project endpoints need one validated id,
 *   the project listing takes none, and no other parameter may set `project`;
 * - the bearer token goes in the Authorization header of a loopback request and nowhere
 *   else (not logged, not returned, not in error objects);
 * - `cache: "no-store"` (the backend also sends `Cache-Control: no-store`), redirects
 *   refused so the token cannot follow one, a hard deadline, a response size cap;
 * - every body is decoded against the documented schema before use.
 */

export type BackendPath =
  | "/api/v1/overview"
  | "/api/v1/models/compare"
  | "/api/v1/traces"
  | `/api/v1/traces/${string}`
  | "/api/v1/projects"
  | "/api/v1/datasets"
  | `/api/v1/datasets/${string}/cases`
  | "/api/v1/experiments"
  | `/api/v1/experiments/${string}`
  | "/api/v1/experiment-options";

/**
 * Runtime allowlist behind the type: a trace id path segment must be 32 lowercase hex, a
 * dataset or experiment id a positive integer. Phase 4B adds the diagnostics read and the
 * dataset reads; Phase 4D the experiment list, report and run options.
 */
const PATH_RE =
  /^\/api\/v1\/(?:overview|models\/compare|traces|traces\/[0-9a-f]{32}|traces\/[0-9a-f]{32}\/diagnostics|projects|datasets|datasets\/[1-9][0-9]{0,18}\/cases|experiments|experiments\/[1-9][0-9]{0,18}|experiment-options)$/;
/** Endpoints without a `project` parameter. */
const GLOBAL_PATHS: ReadonlySet<string> = new Set(["/api/v1/projects"]);

/**
 * Write allowlist: each path accepts exactly one method (ARCHITECTURE.md §5.7, §5.10). The
 * dashboard writes only through these five endpoints, from explicit form submissions: the four
 * Phase 4B writes (ADR-22) and the Phase 4D mock experiment run (ADR-26).
 */
export type WritePath = "/api/v1/failure-reviews" | `/api/v1/failure-reviews/${string}` | "/api/v1/datasets" | `/api/v1/datasets/${string}/cases` | "/api/v1/experiments";
export type WriteMethod = "POST" | "PATCH";
const WRITE_ROUTES: readonly { method: WriteMethod; re: RegExp }[] = [
  { method: "POST", re: /^\/api\/v1\/failure-reviews$/ },
  { method: "PATCH", re: /^\/api\/v1\/failure-reviews\/[1-9][0-9]{0,18}$/ },
  { method: "POST", re: /^\/api\/v1\/datasets$/ },
  { method: "POST", re: /^\/api\/v1\/datasets\/[1-9][0-9]{0,18}\/cases$/ },
  { method: "POST", re: /^\/api\/v1\/experiments$/ },
];

/** Which project a request reads: a validated id, or `null` for the project listing only. */
export interface Scope {
  project: string | null;
}
const TRACE_ID_RE = /^[0-9a-f]{32}$/;
const RECORD_ID_RE = /^[1-9][0-9]{0,18}$/;

/** `/api/v1/traces/{id}` for a validated trace id, or null (never builds a path from other input). */
export function traceDetailPath(traceId: string): BackendPath | null {
  return TRACE_ID_RE.test(traceId) ? `/api/v1/traces/${traceId}` : null;
}

/** `/api/v1/traces/{id}/diagnostics` for a validated trace id, or null. */
export function diagnosticsPath(traceId: string): BackendPath | null {
  return TRACE_ID_RE.test(traceId) ? `/api/v1/traces/${traceId}/diagnostics` : null;
}

/** `/api/v1/datasets/{id}/cases` for a validated dataset id, or null. */
export function datasetCasesPath(datasetId: string): `/api/v1/datasets/${string}/cases` | null {
  return RECORD_ID_RE.test(datasetId) && BigInt(datasetId) <= 2n ** 63n - 1n ? `/api/v1/datasets/${datasetId}/cases` : null;
}

/** `/api/v1/experiments/{id}` for a validated experiment id, or null. */
export function experimentPath(experimentId: string): `/api/v1/experiments/${string}` | null {
  return RECORD_ID_RE.test(experimentId) && BigInt(experimentId) <= 2n ** 63n - 1n ? `/api/v1/experiments/${experimentId}` : null;
}

/** `/api/v1/failure-reviews/{id}` for a validated review id, or null. */
export function reviewPath(reviewId: string): WritePath | null {
  return RECORD_ID_RE.test(reviewId) && BigInt(reviewId) <= 2n ** 63n - 1n ? `/api/v1/failure-reviews/${reviewId}` : null;
}

export const MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
/** A trace detail holds up to 2000 spans and 2000 usage rows with their attributes. */
export const MAX_DETAIL_RESPONSE_BYTES = 8 * 1024 * 1024;
const HEALTH_TIMEOUT_MS = 1500;
const FIELD_NAME_RE = /^[a-z_]{1,64}$/;

export interface BackendDeps {
  fetch?: typeof fetch;
}

export interface GetOptions {
  maxBytes?: number;
}

export interface SendOptions {
  /** Phase 4D: a longer deadline for the one write that runs work (an experiment, whose backend
   * deadline is shorter), so a completed run is not reported as "outcome unknown". */
  timeoutMs?: number;
}

function retryAfter(headers: Headers): number | undefined {
  const raw = headers.get("retry-after");
  if (raw === null || !/^\d{1,6}$/.test(raw.trim())) return undefined;
  return Number(raw.trim());
}

async function readCapped(response: Response, maxBytes: number): Promise<string | null> {
  const declared = response.headers.get("content-length");
  if (declared !== null && Number(declared) > maxBytes) {
    await response.body?.cancel();
    return null;
  }
  if (response.body === null) return "";
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > maxBytes) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
}

function errorFields(body: string | null): string[] | undefined {
  if (body === null) return undefined;
  try {
    const parsed: unknown = JSON.parse(body);
    const fields = (parsed as { error?: { fields?: unknown } })?.error?.fields;
    if (!Array.isArray(fields)) return undefined;
    const names = fields.filter((f): f is string => typeof f === "string" && FIELD_NAME_RE.test(f));
    return names.length > 0 ? names.slice(0, 10) : undefined;
  } catch {
    return undefined;
  }
}

/** An error body's `error.code`, only when it is one of `known` (never free text). */
function documentedCode<C extends string>(body: string | null, known: readonly C[]): C | undefined {
  if (body === null) return undefined;
  try {
    const code = (JSON.parse(body) as { error?: { code?: unknown } })?.error?.code;
    return (known as readonly unknown[]).includes(code) ? (code as C) : undefined;
  } catch {
    return undefined;
  }
}

function failureForStatus(status: number, headers: Headers, body: string | null): ApiFailure {
  const ra = retryAfter(headers);
  const withRetry = (f: ApiFailure): ApiFailure => (ra === undefined ? f : { ...f, retryAfterSeconds: ra });
  if (status === 401) return { kind: "unauthorized", status };
  if (status === 404) return { kind: "not_found", status };
  if (status === 409) {
    const code = documentedCode<ConflictCode>(body, CONFLICT_CODES);
    return code === undefined ? { kind: "conflict", status } : { kind: "conflict", status, code };
  }
  if (status === 413) return { kind: "payload_too_large", status };
  if (status === 422) {
    const fields = errorFields(body);
    return fields === undefined ? { kind: "invalid_request", status } : { kind: "invalid_request", status, fields };
  }
  if (status === 429) {
    const code = documentedCode<BusyCode>(body, BUSY_CODES);
    return withRetry(code === undefined ? { kind: "rate_limited", status } : { kind: "rate_limited", status, code });
  }
  if (status === 503) return withRetry({ kind: "storage_unavailable", status });
  if (status >= 500) return { kind: "server_error", status };
  return { kind: "unexpected_status", status };
}

function networkFailure(err: unknown): ApiFailure {
  if (err instanceof Error && (err.name === "TimeoutError" || err.name === "AbortError")) return { kind: "timeout" };
  return { kind: "unreachable" };
}

function log(path: string, failure: ApiFailure): void {
  // Kind, status and schema path only: never the token, the query or a response body.
  // Trace, review and dataset ids are logged as their endpoint shape only.
  const endpoint = path
    .replace(/^\/api\/v1\/traces\/[0-9a-f]{32}/, "/api/v1/traces/{trace_id}")
    .replace(/^\/api\/v1\/failure-reviews\/\d+$/, "/api/v1/failure-reviews/{id}")
    .replace(/^\/api\/v1\/datasets\/\d+\/cases$/, "/api/v1/datasets/{id}/cases")
    .replace(/^\/api\/v1\/experiments\/\d+$/, "/api/v1/experiments/{id}");
  const parts = [failure.kind, failure.status, failure.detail].filter((p) => p !== undefined);
  console.warn(`[llm-insight] backend ${endpoint} failed: ${parts.join(" ")}`);
}

/** GET a fixed `/api/v1` endpoint for one explicit project and decode the JSON body. */
export async function getJson<T>(
  config: DashboardConfig,
  path: BackendPath,
  scope: Scope,
  params: Readonly<Record<string, string>>,
  decoder: Decoder<T>,
  deps: BackendDeps = {},
  options: GetOptions = {},
): Promise<ApiResult<T>> {
  const doFetch = deps.fetch ?? fetch;
  const maxBytes = options.maxBytes ?? MAX_RESPONSE_BYTES;
  if (!PATH_RE.test(path)) {
    const failure: ApiFailure = { kind: "config", detail: "refused a backend path outside the allowlist" };
    log("(refused path)", failure);
    return { ok: false, error: failure };
  }
  const global = GLOBAL_PATHS.has(path);
  const scopeOk = global ? scope.project === null : scope.project !== null && isProjectId(scope.project);
  if (!scopeOk || Object.hasOwn(params, "project")) {
    const failure: ApiFailure = { kind: "config", detail: "refused a request without a valid project scope" };
    log(path, failure);
    return { ok: false, error: failure };
  }
  const url = new URL(path, config.baseUrl);
  if (scope.project !== null) url.searchParams.set("project", scope.project);
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value);

  const fail = (failure: ApiFailure): ApiResult<T> => {
    log(path, failure);
    return { ok: false, error: failure };
  };

  let response: Response;
  let body: string | null;
  try {
    response = await doFetch(url, {
      method: "GET",
      headers: { Authorization: `Bearer ${config.token}`, Accept: "application/json" },
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(config.timeoutMs),
    });
    body = await readCapped(response, maxBytes);
  } catch (err) {
    return fail(networkFailure(err));
  }

  if (!response.ok) return fail(failureForStatus(response.status, response.headers, body));
  if (body === null) return fail({ kind: "malformed", detail: "response too large" });
  const contentType = response.headers.get("content-type") ?? "";
  if (!/^application\/json\b/i.test(contentType)) return fail({ kind: "malformed", detail: "not JSON" });

  let parsed: unknown;
  try {
    parsed = parseJsonLossless(body);
  } catch {
    return fail({ kind: "malformed", detail: "invalid JSON" });
  }
  const decoded = decode(decoder, parsed);
  if (!decoded.ok) return fail({ kind: "malformed", detail: `schema mismatch at ${decoded.path}` });
  return { ok: true, data: decoded.value };
}

/**
 * Phase 4B: send one JSON write to an allowlisted `/api/v1` endpoint for one explicit project
 * and decode the response. Called only from Server Actions (an explicit form submission);
 * never from a render. Same transport rules as reads: bearer header to the loopback backend
 * only, no redirects, a deadline, a response cap, schema-decoded bodies, no body in logs.
 */
export async function sendJson<T>(
  config: DashboardConfig,
  method: WriteMethod,
  path: WritePath,
  project: string,
  body: unknown,
  decoder: Decoder<T>,
  deps: BackendDeps = {},
  options: SendOptions = {},
): Promise<ApiResult<T>> {
  const doFetch = deps.fetch ?? fetch;
  if (!WRITE_ROUTES.some((r) => r.method === method && r.re.test(path))) {
    const failure: ApiFailure = { kind: "config", detail: "refused a write outside the allowlist" };
    log("(refused write)", failure);
    return { ok: false, error: failure };
  }
  if (!isProjectId(project)) {
    const failure: ApiFailure = { kind: "config", detail: "refused a write without a valid project scope" };
    log(path, failure);
    return { ok: false, error: failure };
  }
  const url = new URL(path, config.baseUrl);
  url.searchParams.set("project", project);
  const fail = (failure: ApiFailure): ApiResult<T> => {
    log(path, failure);
    return { ok: false, error: failure };
  };

  let response: Response;
  let text: string | null;
  try {
    response = await doFetch(url, {
      method,
      headers: { Authorization: `Bearer ${config.token}`, Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(options.timeoutMs ?? config.timeoutMs),
    });
    text = await readCapped(response, MAX_RESPONSE_BYTES);
  } catch (err) {
    // A timeout after sending leaves the outcome unknown: the caller says so instead of
    // claiming nothing was written (the backend commits atomically, one way or the other).
    return fail(networkFailure(err));
  }
  if (!response.ok) return fail(failureForStatus(response.status, response.headers, text));
  if (text === null) return fail({ kind: "malformed", detail: "response too large" });
  if (!/^application\/json\b/i.test(response.headers.get("content-type") ?? "")) return fail({ kind: "malformed", detail: "not JSON" });
  let parsed: unknown;
  try {
    parsed = parseJsonLossless(text);
  } catch {
    return fail({ kind: "malformed", detail: "invalid JSON" });
  }
  const decoded = decode(decoder, parsed);
  if (!decoded.ok) return fail({ kind: "malformed", detail: `schema mismatch at ${decoded.path}` });
  return { ok: true, data: decoded.value };
}

/** `GET /health/ready`: unauthenticated readiness probe of the backend and its database. */
export async function getHealth(config: Pick<DashboardConfig, "baseUrl">, deps: BackendDeps = {}): Promise<BackendHealth> {
  const doFetch = deps.fetch ?? fetch;
  try {
    const response = await doFetch(new URL("/health/ready", config.baseUrl), {
      method: "GET",
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS),
    });
    await response.body?.cancel();
    return response.ok ? "ready" : "not_ready";
  } catch {
    return "unreachable";
  }
}
