/**
 * Result of one Phase 4B form submission, returned by a Server Action to its form. Fixed,
 * operator-facing strings and safe identifiers only: no backend body, no token, no stored
 * text echoed back (the person's own input stays in their form).
 */

import type { ApiFailure, ConflictCode } from "./api/result";
import type { MaskedCounts } from "./api/review-types";

export type ActionState =
  | { status: "idle" }
  | {
      status: "error";
      message: string;
      fields: string[];
      /** The request may have been stored (timeout or lost connection after sending). */
      outcomeUnknown: boolean;
    }
  | {
      status: "success";
      message: string;
      /** What the server masked before storing, per field and category. */
      masked: Record<string, MaskedCounts> | null;
      link: { href: string; label: string } | null;
    };

export const IDLE: ActionState = { status: "idle" };

const CONFLICT_MESSAGES: Record<ConflictCode, string> = {
  duplicate_open_review: "This trace already has a review of this issue type that is not closed. Update that review, or close it first.",
  duplicate_dataset: "This project already has a dataset with this name.",
  duplicate_test_case: "This review already has a test case in this dataset. Nothing new was stored.",
  review_not_decided: "Record a reviewer decision before converting this review into a test case.",
  review_not_eligible: "Only a review whose hypothesis was supported, or rejected with an expected output you supply, can become a test case. Nothing was stored.",
  review_decision_frozen: "A test case already cites this review, so its decision can no longer change. The status can still change. Nothing was stored.",
  review_immutable: "A review's text and references cannot change. Nothing was stored.",
};

/** Map a failed write to the message its form shows. */
export function writeFailure(error: ApiFailure, what: string): ActionState {
  const base = { status: "error" as const, fields: error.fields ?? [], outcomeUnknown: false };
  switch (error.kind) {
    case "invalid_request":
      return { ...base, message: `The backend refused ${what}: check ${(error.fields ?? []).join(", ") || "the fields"}. Nothing was stored.` };
    case "conflict":
      return { ...base, message: error.code ? CONFLICT_MESSAGES[error.code] : `The backend refused ${what} as conflicting with stored data. Nothing was stored.` };
    case "not_found":
      return { ...base, message: `The trace, review or dataset is not in this project (or no longer exists). Nothing was stored.` };
    case "payload_too_large":
      return { ...base, message: "The submitted content is too large. Nothing was stored." };
    case "timeout":
    case "unreachable":
      return {
        ...base,
        outcomeUnknown: true,
        message: `No answer from the backend. ${what[0]?.toUpperCase() ?? ""}${what.slice(1)} may or may not have been stored: refresh the page and check before submitting again.`,
      };
    case "rate_limited":
      return { ...base, message: "Too many requests in a short time. Nothing was stored; retry shortly." };
    case "storage_unavailable":
      return { ...base, message: "The backend database is unavailable. Nothing was stored; retry later." };
    case "malformed":
      return {
        ...base,
        outcomeUnknown: true,
        message: `The backend's answer did not match the contract. ${what[0]?.toUpperCase() ?? ""}${what.slice(1)} may have been stored: refresh the page and check.`,
      };
    default:
      return { ...base, message: `Could not store ${what} (${error.kind}). Nothing was stored.` };
  }
}

export function invalidForm(message: string, fields: string[]): ActionState {
  return { status: "error", message, fields, outcomeUnknown: false };
}
