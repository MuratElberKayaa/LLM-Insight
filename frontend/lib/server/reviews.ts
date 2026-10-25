import "server-only";

import { diagnosticsDecoder, type Diagnostics } from "../api/diagnostics";
import type { ApiResult } from "../api/result";
import { createdReviewDecoder, updatedReviewDecoder } from "../api/review-schema";
import type { CreatedReview, UpdatedReview } from "../api/review-types";
import type { DecisionInput, ReviewInput } from "../review-forms";
import { diagnosticsPath, getJson, MAX_DETAIL_RESPONSE_BYTES, reviewPath, sendJson, type BackendDeps } from "./backend";
import type { DashboardConfig } from "./config";

/**
 * Phase 4B: human failure reviews (CR-03) and the read-only automatic diagnostics shown next
 * to them. The two never meet here: diagnostics are only read and rendered, and a review is
 * written only with what the reviewer submitted (reports/phase-4b.md §2.4).
 */

/** `GET /api/v1/traces/{id}/diagnostics`: machine observations, unverified by contract. */
export async function loadDiagnostics(config: DashboardConfig, projectId: string, traceId: string, deps: BackendDeps = {}): Promise<ApiResult<Diagnostics>> {
  const path = diagnosticsPath(traceId);
  if (path === null) return { ok: false, error: { kind: "invalid_request", fields: ["trace_id"] } };
  // 100 findings × 25 evidence spans fit well under the detail cap.
  return getJson(config, path, { project: projectId }, {}, diagnosticsDecoder, deps, { maxBytes: MAX_DETAIL_RESPONSE_BYTES });
}

/** `POST /api/v1/failure-reviews` with exactly the reviewer's validated input. */
export function createReview(config: DashboardConfig, input: ReviewInput, deps: BackendDeps = {}): Promise<ApiResult<CreatedReview>> {
  return sendJson(config, "POST", "/api/v1/failure-reviews", input.project, input.body, createdReviewDecoder, deps);
}

/** `PATCH /api/v1/failure-reviews/{id}`: status and decision only. */
export async function updateReview(config: DashboardConfig, input: DecisionInput, deps: BackendDeps = {}): Promise<ApiResult<UpdatedReview>> {
  const path = reviewPath(input.reviewId);
  if (path === null) return { ok: false, error: { kind: "invalid_request", fields: ["review_id"] } };
  return sendJson(config, "PATCH", path, input.project, input.body, updatedReviewDecoder, deps);
}
