/**
 * Outcome of one backend read, shared by server code and components. A failure carries a
 * fixed kind and safe metadata only (no token, no URL, no response body).
 */

export type ApiFailureKind =
  | "config" // the dashboard's own server-side configuration is missing or invalid
  | "invalid_project" // `?project=` is not a project id; nothing was sent to the backend
  | "unreachable" // connection refused / DNS / network error
  | "timeout" // no response within the configured deadline
  | "unauthorized" // 401: the server-side token was rejected
  | "not_found" // 404: unknown project (indistinguishable by design, T28)
  | "invalid_request" // 422: a parameter the backend refused
  | "rate_limited" // 429
  | "storage_unavailable" // 503: the backend's database is unavailable
  | "server_error" // 500 and other 5xx
  | "unexpected_status" // any other status
  | "malformed"; // not JSON, too large, or not matching the documented schema

export interface ApiFailure {
  kind: ApiFailureKind;
  status?: number;
  /** 422: parameter names reported by the backend (never values). */
  fields?: string[];
  /** 429/503: seconds from Retry-After, when given. */
  retryAfterSeconds?: number;
  /** malformed: JSON path of the first mismatch, or a fixed reason. */
  detail?: string;
}

export type ApiResult<T> = { ok: true; data: T } | { ok: false; error: ApiFailure };

export interface FailureCopy {
  title: string;
  description: string;
  hint?: string;
}

/** Operator-facing copy per failure kind. Fixed strings only. */
export function describeFailure(error: ApiFailure, resource: string): FailureCopy {
  switch (error.kind) {
    case "config":
      return {
        title: "Dashboard configuration incomplete",
        description: `The dashboard server cannot query ${resource}: ${error.detail ?? "a required setting is missing"}.`,
        hint: "Run `python -I scripts/dev/init_frontend_env.py --project-id <id>` from the repository root, then restart the dashboard.",
      };
    case "unreachable":
      return {
        title: "Backend unreachable",
        description: `The dashboard server could not connect to the LLM Insight backend to load ${resource}.`,
        hint: "Start it with `python -m app` in backend/ and reload.",
      };
    case "timeout":
      return {
        title: "Backend timed out",
        description: `The backend did not answer within the dashboard's deadline while loading ${resource}.`,
        hint: "Check the backend log and reload.",
      };
    case "unauthorized":
      return {
        title: "Backend rejected the dashboard's credentials",
        description: "The server-side read token was refused (401). The token itself is never shown here.",
        hint: "LLMI_API_TOKEN in frontend/.env.local must equal the backend's LLMI_ADMIN_TOKEN; restart the dashboard after changing it.",
      };
    case "invalid_project":
      return {
        title: "Not a valid project ID",
        description: "The project in the URL is not a positive integer id. It is not shown here and was not sent to the backend; no other project was substituted.",
        hint: "Choose a project in the project selector, or remove the project parameter to use the configured default.",
      };
    case "not_found":
      return {
        title: "Project not found",
        description: "The selected project does not exist in the backend (404). No data of another project is shown instead.",
        hint: "Choose a listed project in the selector, or create one with `python -m app.cli create-key --project <name>`.",
      };
    case "invalid_request":
      return {
        title: "The backend refused the query",
        description: `Rejected parameter${(error.fields?.length ?? 0) === 1 ? "" : "s"}: ${(error.fields ?? []).join(", ") || "unspecified"} (422).`,
      };
    case "rate_limited":
      return {
        title: "Backend rate limit reached",
        description: "Too many API requests in a short time (429).",
        hint: error.retryAfterSeconds === undefined ? "Retry shortly." : `Retry after ${error.retryAfterSeconds} s.`,
      };
    case "storage_unavailable":
      return {
        title: "Backend database unavailable",
        description: "The backend is running but cannot reach PostgreSQL (503).",
        hint: "Check `docker compose ps` and reload.",
      };
    case "server_error":
      return {
        title: "Backend error",
        description: `The backend failed while computing ${resource} (${error.status ?? 500}).`,
        hint: "Check the backend log.",
      };
    case "unexpected_status":
      return {
        title: "Unexpected backend response",
        description: `The backend answered with HTTP ${error.status ?? "?"}.`,
      };
    case "malformed":
      return {
        title: "Backend response did not match the contract",
        description: `The ${resource} response was rejected instead of being partially shown (${error.detail ?? "invalid"}).`,
        hint: "The frontend and backend versions may disagree; see ARCHITECTURE.md §5.2.",
      };
  }
}
