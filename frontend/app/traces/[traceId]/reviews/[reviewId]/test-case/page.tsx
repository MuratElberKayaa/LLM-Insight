import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { DatasetForm } from "../../../../../../components/datasets/dataset-form";
import { TestCaseForm } from "../../../../../../components/datasets/test-case-form";
import { NavigationTransition, PendingRegion } from "../../../../../../components/navigation-transition";
import { PageHeader } from "../../../../../../components/page-header";
import { ProvenanceBadge } from "../../../../../../components/provenance-badge";
import { ErrorState, Notice } from "../../../../../../components/states";
import { Ident } from "../../../../../../components/traces/trace-values";
import { DECISION_LABELS, ISSUE_TYPE_LABELS } from "../../../../../../lib/api/review-types";
import { parseProjectParam, projectHref, projectLinkValue } from "../../../../../../lib/project";
import { loadDatasetChoices } from "../../../../../../lib/server/review-page";
import { loadTraceDetail } from "../../../../../../lib/server/traces";

export const metadata: Metadata = { title: "New test case" };

const TRACE_ID_RE = /^[0-9a-f]{32}$/;
const ID_RE = /^[1-9][0-9]{0,15}$/;

type PageProps = {
  params: Promise<{ traceId: string; reviewId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

/**
 * Trace-to-Testset for one reviewed trace (Phase 4B). Opening this page writes nothing: the
 * only write is the explicit, approved form submission. The source review must carry a
 * reviewer decision (decision D4); the backend enforces that again.
 */
export default async function TestCasePage({ params, searchParams }: PageProps) {
  await connection();
  const { traceId, reviewId } = await params;
  const sp = await searchParams;
  const project = parseProjectParam(sp.project);
  const traceHref = `${projectHref(`/traces/${traceId}`, projectLinkValue(project))}#reviews-heading`;
  if (!TRACE_ID_RE.test(traceId) || !ID_RE.test(reviewId)) {
    return (
      <>
        <PageHeader title="New test case" />
        <ErrorState error={{ kind: "invalid_request", fields: ["trace_id", "review_id"] }} resource="this review" />
      </>
    );
  }
  const [ctx, datasets] = await Promise.all([loadTraceDetail(traceId, project), loadDatasetChoices(project)]);
  const detail = ctx.detail;
  const review = detail.ok ? detail.data.reviews.find((r) => r.id === Number(reviewId)) : undefined;

  return (
    <NavigationTransition>
      <Link href={traceHref} className="mb-4 inline-flex items-center gap-1 text-sm text-ink-2 hover:text-ink">
        <span aria-hidden="true">←</span> Back to the trace and its reviews
      </Link>
      <PageHeader
        title="New regression test case"
        description="Human-gated conversion of a reviewed trace into a test case. You type the content; only the trace and review references are taken from the trace."
      />
      <PendingRegion>
        {!detail.ok ? (
          <ErrorState error={detail.error} resource="the source trace" />
        ) : review === undefined ? (
          <ErrorState error={{ kind: "not_found" }} resource="the source review" />
        ) : (
          <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
            <div className="min-w-0 space-y-4">
              <Notice tone="caution" title="Telemetry holds no prompt or completion" testId="no-content-notice">
                LLM Insight stores metadata only, so the original input and output of this trace cannot be shown or recovered. Type a synthetic or redacted input and the expected
                result yourself. Do not paste production content.
              </Notice>
              {review.reviewer_decision === "pending" ? (
                <Notice tone="caution" title="This review has no decision yet" testId="review-pending-notice">
                  Record a reviewer decision on the trace page first; the backend refuses to convert an undecided review.
                </Notice>
              ) : review.reviewer_decision === "inconclusive" ? (
                <Notice tone="caution" title="An inconclusive review cannot become a test case" testId="review-inconclusive-notice">
                  Only a review whose hypothesis was supported, or rejected with an expected output you supply, can become a golden test case.
                </Notice>
              ) : datasets === null || !datasets.ok ? (
                datasets === null ? null : <ErrorState error={datasets.error} resource="the datasets" compact />
              ) : datasets.data.items.length === 0 ? (
                <Notice tone="info" title="No dataset yet">
                  Create a dataset in the panel on the right; it then appears in this form.
                </Notice>
              ) : (
                ctx.projectId !== null && (
                  <>
                    {review.reviewer_decision === "hypothesis_rejected" && (
                      <Notice tone="info" title="Rejected hypothesis: supply the expected output" testId="review-rejected-notice">
                        The reviewer rejected this hypothesis, so the case needs an expected output that you supply independently; the backend refuses the case without one.
                      </Notice>
                    )}
                    <TestCaseForm project={ctx.projectId} traceId={traceId} reviewId={review.id} datasets={datasets.data.items} />
                  </>
                )
              )}
            </div>
            <aside className="space-y-4" aria-label="Source and datasets">
              <section className="rounded-lg border border-line bg-surface px-4 py-4 text-sm" aria-labelledby="source-heading" data-testid="case-source">
                <h2 id="source-heading" className="mb-2 text-base font-semibold text-ink">
                  Source (references only)
                </h2>
                <dl className="space-y-2">
                  <div>
                    <dt className="text-xs text-ink-3">Trace</dt>
                    <dd>
                      <Ident value={traceId} />
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-ink-3">Trace provenance</dt>
                    <dd>
                      <ProvenanceBadge provenance={detail.data.trace.provenance} />
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-ink-3">Review</dt>
                    <dd className="text-ink">
                      #{review.id}: {ISSUE_TYPE_LABELS[review.issue_type]}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-ink-3">Reviewer decision</dt>
                    <dd className="text-ink" data-testid="case-source-decision">
                      {DECISION_LABELS[review.reviewer_decision]}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-ink-3">Prompt version (metadata)</dt>
                    <dd className="text-ink">{detail.data.trace.prompt_version ?? "none recorded"}</dd>
                  </div>
                </dl>
                {review.test_cases.length > 0 && (
                  <p className="mt-3 text-xs text-ink-2" data-testid="case-source-existing">
                    Already converted into:{" "}
                    {review.test_cases.map((c) => `#${c.id} in “${c.dataset_name}”`).join(", ")}. One test case per dataset and review.
                  </p>
                )}
              </section>
              {ctx.projectId !== null && (
                <section className="rounded-lg border border-line bg-surface px-4 py-4" aria-labelledby="dataset-create-heading">
                  <h2 id="dataset-create-heading" className="mb-2 text-base font-semibold text-ink">
                    Create a dataset
                  </h2>
                  <DatasetForm project={ctx.projectId} />
                </section>
              )}
            </aside>
          </div>
        )}
      </PendingRegion>
    </NavigationTransition>
  );
}
