import Link from "next/link";
import { DECISION_LABELS, ISSUE_TYPE_LABELS, STATUS_LABELS, type FailureReview } from "../../lib/api/review-types";
import type { SpanRecord } from "../../lib/api/trace-types";
import { formatIsoNs } from "../../lib/nanos";
import { Ident } from "../traces/trace-values";
import { DecisionForm } from "./decision-form";
import { ReviewForm, type SpanChoice } from "./review-form";

/** Native select options are plain text; the span name is a telemetry label, shown as text. */
function spanChoices(spans: SpanRecord[]): SpanChoice[] {
  return spans.map((s) => ({ spanId: s.span_id, label: `${s.name} · ${s.kind} · ${s.status} · ${s.span_id}` }));
}

function Pill({ children, tone = "neutral", testId }: { children: string; tone?: "neutral" | "open" | "closed"; testId?: string }) {
  const cls = tone === "open" ? "border-warning/60 bg-warning/10" : tone === "closed" ? "border-line bg-raised" : "border-line bg-surface";
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium text-ink ${cls}`} data-testid={testId}>
      {children}
    </span>
  );
}

function ReviewCard({ review, project, spanHref, convertHref }: { review: FailureReview; project: string; spanHref: (spanId: string) => string; convertHref: string }) {
  const decided = review.reviewer_decision !== "pending";
  return (
    <li className="rounded-lg border border-line bg-surface px-4 py-4" data-testid="review-card" data-review-id={review.id}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-ink">Review #{review.id}</span>
        <Pill testId="review-issue">{`${ISSUE_TYPE_LABELS[review.issue_type]} (${review.issue_type})`}</Pill>
        <Pill tone={review.review_status === "closed" ? "closed" : "open"} testId="review-status">
          {STATUS_LABELS[review.review_status]}
        </Pill>
        <Pill testId="review-decision">{DECISION_LABELS[review.reviewer_decision]}</Pill>
      </div>
      <dl className="mt-3 grid gap-3 text-sm">
        <div>
          <dt className="text-xs text-ink-3">Evidence (human-entered, redacted by the server)</dt>
          <dd className="mt-0.5 whitespace-pre-wrap break-words text-ink" data-testid="review-evidence-text">
            {review.evidence}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-ink-3">Hypothesis: a possible cause, not a verified root cause</dt>
          <dd className="mt-0.5 whitespace-pre-wrap break-words text-ink" data-testid="review-hypothesis-text">
            {review.hypothesis}
          </dd>
        </div>
        <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-ink-3">
          <span>
            Reviewer (self-declared): <span className="text-ink-2" data-testid="review-created-by-text">{review.created_by}</span>
          </span>
          <span>Created {formatIsoNs(review.created_at, 6)}</span>
          <span>Updated {formatIsoNs(review.updated_at, 6)}</span>
          {review.span_id !== null && (
            <span>
              Span{" "}
              <Link href={spanHref(review.span_id)} className="underline underline-offset-2" data-testid="review-span-link">
                <Ident value={review.span_id} />
              </Link>
            </span>
          )}
        </div>
      </dl>
      {review.test_cases.length > 0 && (
        <div className="mt-3 text-xs text-ink-2" data-testid="review-test-cases">
          <span className="font-medium text-ink">Test cases from this review:</span>{" "}
          {review.test_cases.map((c, i) => (
            <span key={c.id}>
              {i > 0 && ", "}
              <Link href={`/datasets/${c.dataset_id}?project=${project}`} className="underline underline-offset-2">
                #{c.id} in “{c.dataset_name}”
              </Link>
            </span>
          ))}
        </div>
      )}
      <DecisionForm project={project} traceId={review.trace_id} reviewId={review.id} status={review.review_status} decision={review.reviewer_decision} />
      <div className="mt-3 text-sm">
        {decided ? (
          <Link href={convertHref} className="font-medium text-ink underline underline-offset-2" data-testid="convert-link">
            Convert to a regression test case…
          </Link>
        ) : (
          <span className="text-ink-3" data-testid="convert-unavailable">
            Converting to a test case needs a reviewer decision first.
          </span>
        )}
      </div>
    </li>
  );
}

/**
 * Human failure reviews of one trace (CR-03): the stored records, a decision form per review,
 * and the form for a new review. Everything here is human-authored; the automatic diagnostics
 * are a separate panel and never write into this one.
 */
export function ReviewSection({
  project,
  traceId,
  reviews,
  truncated,
  spans,
  spanHref,
  convertHref,
}: {
  project: string;
  traceId: string;
  reviews: FailureReview[];
  truncated: boolean;
  spans: SpanRecord[];
  spanHref: (spanId: string) => string;
  convertHref: (reviewId: number) => string;
}) {
  return (
    <section aria-labelledby="reviews-heading" className="rounded-lg border-2 border-series-1/40 bg-canvas px-4 py-4" data-testid="human-review-section">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 id="reviews-heading" className="text-base font-semibold text-ink">
          Human failure review
        </h2>
        <span className="rounded bg-series-1/25 px-1.5 py-0.5 text-[11px] font-bold uppercase tracking-wide text-ink">Human decision</span>
      </div>
      <p className="mb-4 text-sm text-ink-2">
        Recorded by a person with the issue types of the Failure Review contract. A supported hypothesis means the evidence supports it, not that the root cause is proven.
      </p>
      {reviews.length === 0 ? (
        <p className="mb-4 text-sm text-ink-2" data-testid="reviews-empty">
          Not reviewed yet: no human review exists for this trace.
        </p>
      ) : (
        <ul className="mb-4 space-y-3" data-testid="review-list">
          {reviews.map((r) => (
            <ReviewCard key={r.id} review={r} project={project} spanHref={spanHref} convertHref={convertHref(r.id)} />
          ))}
        </ul>
      )}
      {truncated && <p className="mb-4 text-xs text-ink-3">Only the first 200 reviews are shown.</p>}
      <details className="rounded-lg border border-line bg-surface px-4 py-3" open={reviews.length === 0} data-testid="new-review">
        <summary className="cursor-pointer text-sm font-semibold text-ink">Record a new review</summary>
        <div className="mt-3">
          <ReviewForm project={project} traceId={traceId} spans={spanChoices(spans)} />
        </div>
      </details>
    </section>
  );
}
