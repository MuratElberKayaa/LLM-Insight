import type { Metadata } from "next";
import type { ReactNode } from "react";
import Link from "next/link";
import { connection } from "next/server";
import { ContextBar } from "../../../components/context-bar";
import { NavigationTransition, PendingRegion, RefreshButton } from "../../../components/navigation-transition";
import { PageHeader, SectionHeading } from "../../../components/page-header";
import { ProjectSelector } from "../../../components/project-selector";
import { DiagnosticsPanel } from "../../../components/reviews/diagnostics-panel";
import { ReviewSection } from "../../../components/reviews/review-section";
import { ErrorState } from "../../../components/states";
import {
  AgentSteps,
  AttemptsTable,
  LogicalCalls,
  ProvenanceNotice,
  SpanTable,
  StatusBreakdown,
  TraceFacts,
  TruncationNotice,
} from "../../../components/traces/trace-detail-sections";
import { TraceRail } from "../../../components/traces/trace-rail";
import { Ident } from "../../../components/traces/trace-values";
import { Waterfall } from "../../../components/traces/waterfall";
import { analyseAgent, findingsBySpan } from "../../../lib/agent-analysis";
import { projectContext, windowContext } from "../../../lib/context-links";
import { formatUtcTime } from "../../../lib/format";
import { parseProjectParam, projectLinkValue } from "../../../lib/project";
import type { FailureReview } from "../../../lib/api/review-types";
import { loadTraceDiagnostics } from "../../../lib/server/review-page";
import { loadTraceDetail, loadTraceRail } from "../../../lib/server/traces";
import { buildTraceView } from "../../../lib/span-tree";
import { listStateFromBackParam, restartFields, traceDetailHref, traceListHref } from "../../../lib/trace-query";

export const metadata: Metadata = { title: "Trace" };

const TRACE_ID_RE = /^[0-9a-f]{32}$/;
const SPAN_ID_RE = /^[0-9a-f]{16}$/;

type PageProps = {
  params: Promise<{ traceId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

function BackLink({ href }: { href: string }) {
  return (
    <Link href={href} className="mb-4 inline-flex items-center gap-1 text-sm text-ink-2 hover:text-ink" data-testid="back-to-traces">
      <span aria-hidden="true">←</span> Back to traces
    </Link>
  );
}

/** Reviewed or not, near the top; the review itself is at the end of the page. */
function ReviewStateLine({ reviews }: { reviews: FailureReview[] }) {
  const open = reviews.filter((r) => r.review_status !== "closed").length;
  return (
    <p className="-mt-3 mb-6 text-sm text-ink-2" data-testid="review-state" data-reviews={reviews.length}>
      <span className="font-medium text-ink">Human review:</span>{" "}
      {reviews.length === 0 ? "not reviewed yet." : `${reviews.length} review${reviews.length === 1 ? "" : "s"} (${open} not closed).`}{" "}
      <a href="#reviews-heading" className="underline underline-offset-2">
        Go to the review
      </a>
    </p>
  );
}

function Problem({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div role="alert" data-state="error" className="rounded-lg border border-critical/50 bg-critical/10 px-6 py-6">
      <p className="text-sm font-semibold text-ink">{title}</p>
      <div className="mt-1 text-sm text-ink-2">{children}</div>
    </div>
  );
}

export default async function TraceDetailPage({ params, searchParams }: PageProps) {
  await connection();
  const { traceId } = await params;
  const sp = await searchParams;
  const project = parseProjectParam(sp.project);
  // The back link keeps this page's project; `list` only restores filters and paging.
  const backState = listStateFromBackParam(sp.list, projectLinkValue(project));
  const back = traceListHref(backState);
  // The trace id is opaque; it is only checked against the API's format before any request.
  const validId = TRACE_ID_RE.test(traceId);

  if (!validId) {
    return (
      <>
        <BackLink href={back} />
        <PageHeader title="Trace" />
        <Problem title="Not a valid trace ID">
          <p>Trace IDs are 32 lowercase hexadecimal characters. The value in the URL is not shown and was not sent to the backend.</p>
        </Problem>
      </>
    );
  }

  // The diagnostics read runs in parallel and degrades only its own panel when it fails.
  // UI-R2: the rail is one more read of the listing (its own panel degrades when it fails).
  const [ctx, diagnostics, rail] = await Promise.all([
    loadTraceDetail(traceId, project),
    loadTraceDiagnostics(traceId, project),
    loadTraceRail({ ...backState, project, session: backState.session ?? null, ignored: [], windowProblem: null }),
  ]);
  const { projectId, fetchedAt, detail } = ctx;
  const rawSpan = typeof sp.span === "string" && SPAN_ID_RE.test(sp.span) ? sp.span : null;
  const selfHref = traceDetailHref(traceId, backState);
  const spanHref = (spanId: string) => `${selfHref}${selfHref.includes("?") ? "&" : "?"}span=${spanId}#waterfall-heading`;

  const diag = diagnostics?.ok ? diagnostics.data : null;
  const filters = [
    backState.status !== "all" ? `status ${backState.status}` : null,
    backState.provenance !== "all" ? `provenance ${backState.provenance}` : null,
    backState.provider ? `provider ${backState.provider}` : null,
    backState.model ? `model ${backState.model}` : null,
    backState.session ? "one session" : null,
  ].filter((x): x is string => x !== null);
  const contextItems = [
    { key: "project", label: "Project", value: projectContext(projectId, ctx.projects) },
    { key: "trace", label: "Trace", value: <span className="font-mono">{traceId.slice(0, 12)}…</span>, title: traceId },
    { key: "list", label: "List", value: windowContext(backState.window, rail.ok ? rail.data.window : null) },
    ...(filters.length > 0 ? [{ key: "filters", label: "Filters", value: filters.join(" · ") }] : []),
  ];

  return (
    <NavigationTransition>
      <ContextBar items={contextItems} />
      <BackLink href={back} />
      <PageHeader
        eyebrow="Observe · Trace explorer"
        title="Trace"
        description={<Ident value={traceId} className="text-sm text-ink-2" />}
        meta={
          <>
            <span>Fetched {formatUtcTime(fetchedAt)} · not live; refresh to update</span>
            <span>Metadata only: no prompt or completion content is stored</span>
          </>
        }
        actions={<RefreshButton />}
      />
      {/* A trace belongs to one project: switching opens the chosen project's list, first page. */}
      <ProjectSelector context={ctx} action="/traces" hidden={restartFields(backState, ["project"])} />
      <PendingRegion>
        {/* UI-R3: three panels when the explorer itself is >= 64rem wide (so a collapsed sidebar counts), not at a viewport width. */}
        <div className="@container/explorer">
        <div className="grid grid-cols-1 gap-6 @[64rem]/explorer:grid-cols-[15rem_minmax(0,1fr)] @[64rem]/explorer:has-[[data-rail=hidden]]:grid-cols-1">
        <TraceRail result={rail} currentId={traceId} state={backState} />
        <div className="min-w-0">
        {!detail.ok ? (
          detail.error.kind === "not_found" ? (
            <Problem title="Trace not found">
              <p data-testid="trace-not-found">
                Project {projectId} has no trace with this ID. The backend answers the same way when the trace never existed, belongs to another project, or the project itself is
                missing, so the dashboard cannot tell these apart (by design).
              </p>
            </Problem>
          ) : (
            <ErrorState error={detail.error} resource="this trace" />
          )
        ) : (
          (() => {
            const data = detail.data;
            const view = buildTraceView(data);
            const analysis = analyseAgent(view.rows, data.agent_summary, diag);
            const reviewsBySpan: Record<string, number> = {};
            for (const r of data.reviews) if (r.span_id !== null) reviewsBySpan[r.span_id] = (reviewsBySpan[r.span_id] ?? 0) + 1;
            return (
              <>
                <ProvenanceNotice provenance={data.trace.provenance} />
                <TruncationNotice detail={data} />
                <TraceFacts trace={data.trace} returnedSpans={data.spans.length} spansTruncated={data.spans_truncated} />
                <ReviewStateLine reviews={data.reviews} />
                <StatusBreakdown trace={data.trace} view={view} />
                {analysis.hasAgentSteps && (
                  <div className="mb-8">
                    <AgentSteps summary={data.agent_summary} spansTruncated={data.spans_truncated} analysis={analysis} />
                  </div>
                )}
                <section aria-labelledby="waterfall-heading" className="min-w-0 scroll-mt-16">
                  <SectionHeading
                    id="waterfall-heading"
                    title="Span tree and timeline"
                    description="Parent-child hierarchy in start order with a relative-time waterfall. Each inference row is one physical attempt; retries are separate rows. Select a span to inspect it."
                  />
                  {/* Keyed by the selected span so an evidence link re-selects in the waterfall. */}
                  <Waterfall
                    key={rawSpan ?? "none"}
                    view={view}
                    initialSelected={rawSpan}
                    findingsBySpan={findingsBySpan(diag)}
                    reviewsBySpan={reviewsBySpan}
                    reviewsTotal={data.reviews.length}
                  />
                </section>
                <LogicalCalls view={view} />
                <AttemptsTable view={view} usageTruncated={data.usage_truncated} />
                <SpanTable view={view} />
                <DiagnosticsPanel result={diagnostics} spanHref={spanHref} />
                {projectId !== null && (
                  <ReviewSection
                    project={projectId}
                    traceId={traceId}
                    reviews={data.reviews}
                    truncated={data.reviews_truncated}
                    spans={data.spans}
                    spanHref={spanHref}
                    convertHref={(id) => `/traces/${traceId}/reviews/${id}/test-case?project=${projectId}`}
                  />
                )}
              </>
            );
          })()
        )}
        </div>
        </div>
        </div>
      </PendingRegion>
    </NavigationTransition>
  );
}
