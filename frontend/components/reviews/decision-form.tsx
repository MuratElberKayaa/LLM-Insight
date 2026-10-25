"use client";

import { submitDecision } from "../../lib/actions/reviews";
import { DECISION_LABELS, REVIEW_STATUSES, REVIEWER_DECISIONS, STATUS_LABELS, type ReviewerDecision, type ReviewStatus } from "../../lib/api/review-types";
import { ActionMessage, inputClass, SubmitButton, useWriteForm } from "./form-parts";

/**
 * Change a review's status and decision (PATCH). The stored values are preselected. Once a test case cites
 * the review, the decision is frozen (ADR-24): it is shown read-only and sent unchanged, and only the status
 * can still change (the backend refuses any other change with `review_decision_frozen`).
 */
export function DecisionForm({
  project,
  traceId,
  reviewId,
  status,
  decision,
  frozen = false,
}: {
  project: string;
  traceId: string;
  reviewId: number;
  status: ReviewStatus;
  decision: ReviewerDecision;
  frozen?: boolean;
}) {
  const { state, pending, formRef, onSubmit, invalid } = useWriteForm(submitDecision);
  const statusId = `review-${reviewId}-status`;
  const decisionId = `review-${reviewId}-decision`;
  return (
    <form
      ref={formRef}
      onSubmit={onSubmit}
      noValidate
      className="mt-3 flex flex-wrap items-end gap-3 border-t border-line pt-3"
      data-testid={`decision-form-${reviewId}`}
      // Remount after a stored change so the selects show the stored values again.
      key={`${status}:${decision}`}
    >
      <input type="hidden" name="project" value={project} />
      <input type="hidden" name="trace_id" value={traceId} />
      <input type="hidden" name="review_id" value={String(reviewId)} />
      <div className="min-w-40">
        <label htmlFor={statusId} className="mb-1 block text-xs font-medium text-ink-2">
          Status
        </label>
        <select id={statusId} name="review_status" defaultValue={status} aria-invalid={invalid("review_status")} className={inputClass} data-testid="decision-status">
          {REVIEW_STATUSES.map((s) => (
            <option key={s} value={s}>
              {STATUS_LABELS[s]}
            </option>
          ))}
        </select>
      </div>
      <div className="min-w-64 flex-1">
        <label htmlFor={decisionId} className="mb-1 block text-xs font-medium text-ink-2">
          Reviewer decision
        </label>
        {frozen ? (
          <>
            <input type="hidden" name="reviewer_decision" value={decision} />
            <p id={decisionId} className="rounded-md border border-line bg-raised px-3 py-2 text-sm text-ink" data-testid="decision-frozen">
              {DECISION_LABELS[decision]} (frozen: a test case cites this review)
            </p>
          </>
        ) : (
          <select id={decisionId} name="reviewer_decision" defaultValue={decision} aria-invalid={invalid("reviewer_decision")} className={inputClass} data-testid="decision-decision">
            {REVIEWER_DECISIONS.map((d) => (
              <option key={d} value={d}>
                {DECISION_LABELS[d]}
              </option>
            ))}
          </select>
        )}
      </div>
      <SubmitButton pending={pending} testId="decision-submit">
        {frozen ? "Save status" : "Save decision"}
      </SubmitButton>
      <div className="basis-full">
        <ActionMessage state={state} testId={`decision-result-${reviewId}`} />
      </div>
    </form>
  );
}
