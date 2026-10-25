"use client";

import { submitReview } from "../../lib/actions/reviews";
import { ISSUE_TYPE_LABELS, ISSUE_TYPES, LIMITS } from "../../lib/api/review-types";
import { ActionMessage, FieldLabel, inputClass, SubmitButton, useWriteForm } from "./form-parts";

export interface SpanChoice {
  spanId: string;
  label: string;
}

/**
 * Record a human failure review (CR-03). Every field starts empty: the issue type has no
 * default and nothing is prefilled from the automatic diagnostics, so the record is the
 * reviewer's own. The server redacts the text and stores the review as open / pending.
 */
export function ReviewForm({ project, traceId, spans }: { project: string; traceId: string; spans: SpanChoice[] }) {
  const { state, pending, formRef, onSubmit, invalid } = useWriteForm(submitReview);
  return (
    <form ref={formRef} onSubmit={onSubmit} noValidate className="space-y-4" data-testid="review-form" aria-describedby="review-form-note">
      <input type="hidden" name="project" value={project} />
      <input type="hidden" name="trace_id" value={traceId} />
      <p id="review-form-note" className="text-xs text-ink-3">
        Do not paste prompts, completions, personal data or secrets. The server masks e-mail addresses, phone and long numbers and key-like values before storage, but redaction is best
        effort.
      </p>
      <div>
        <FieldLabel htmlFor="issue_type">Issue type</FieldLabel>
        <select id="issue_type" name="issue_type" required defaultValue="" aria-invalid={invalid("issue_type")} className={inputClass} data-testid="review-issue-type">
          <option value="" disabled>
            Choose an issue type…
          </option>
          {ISSUE_TYPES.map((t) => (
            <option key={t} value={t}>
              {ISSUE_TYPE_LABELS[t]} ({t})
            </option>
          ))}
        </select>
      </div>
      <div>
        <FieldLabel htmlFor="span_id" hint="Optional: the span the evidence is about.">
          Related span
        </FieldLabel>
        <select id="span_id" name="span_id" defaultValue="" aria-invalid={invalid("span_id")} aria-describedby="span_id-hint" className={inputClass} data-testid="review-span">
          <option value="">None (the whole trace)</option>
          {spans.map((s) => (
            <option key={s.spanId} value={s.spanId}>
              {s.label}
            </option>
          ))}
        </select>
      </div>
      <div>
        <FieldLabel htmlFor="evidence" hint={`What you observed in the stored metadata (at most ${LIMITS.evidence} characters).`}>
          Evidence
        </FieldLabel>
        <textarea
          id="evidence"
          name="evidence"
          required
          rows={4}
          maxLength={LIMITS.evidence}
          aria-invalid={invalid("evidence")}
          aria-describedby="evidence-hint"
          className={inputClass}
          data-testid="review-evidence"
        />
      </div>
      <div>
        <FieldLabel htmlFor="hypothesis" hint={`A possible cause to check, not a verified root cause (at most ${LIMITS.hypothesis} characters).`}>
          Hypothesis
        </FieldLabel>
        <textarea
          id="hypothesis"
          name="hypothesis"
          required
          rows={3}
          maxLength={LIMITS.hypothesis}
          aria-invalid={invalid("hypothesis")}
          aria-describedby="hypothesis-hint"
          className={inputClass}
          data-testid="review-hypothesis"
        />
      </div>
      <div>
        <FieldLabel htmlFor="created_by" hint="Self-declared: the MVP has no user accounts. Use a name or handle, not an e-mail address.">
          Reviewer
        </FieldLabel>
        <input
          id="created_by"
          name="created_by"
          required
          maxLength={LIMITS.actor}
          autoComplete="off"
          aria-invalid={invalid("created_by")}
          aria-describedby="created_by-hint"
          className={inputClass}
          data-testid="review-created-by"
        />
      </div>
      <ActionMessage state={state} testId="review-form-result" />
      <SubmitButton pending={pending} testId="review-submit">
        Record review
      </SubmitButton>
    </form>
  );
}
