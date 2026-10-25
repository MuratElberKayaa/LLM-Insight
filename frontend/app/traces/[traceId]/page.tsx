import type { Metadata } from "next";
import type { ReactNode } from "react";
import Link from "next/link";
import { connection } from "next/server";
import { NavigationTransition, PendingRegion, RefreshButton } from "../../../components/navigation-transition";
import { PageHeader, SectionHeading } from "../../../components/page-header";
import { ProjectSelector } from "../../../components/project-selector";
import { ErrorState } from "../../../components/states";
import {
  AttemptsTable,
  LogicalCalls,
  ProvenanceNotice,
  SpanTable,
  StatusBreakdown,
  TraceFacts,
  TruncationNotice,
} from "../../../components/traces/trace-detail-sections";
import { Ident } from "../../../components/traces/trace-values";
import { Waterfall } from "../../../components/traces/waterfall";
import { formatUtcTime } from "../../../lib/format";
import { parseProjectParam, projectLinkValue } from "../../../lib/project";
import { loadTraceDetail } from "../../../lib/server/traces";
import { buildTraceView } from "../../../lib/span-tree";
import { listStateFromBackParam, restartFields, traceListHref } from "../../../lib/trace-query";

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

  const ctx = await loadTraceDetail(traceId, project);
  const { projectId, fetchedAt, detail } = ctx;
  const rawSpan = typeof sp.span === "string" && SPAN_ID_RE.test(sp.span) ? sp.span : null;

  return (
    <NavigationTransition>
      <BackLink href={back} />
      <PageHeader
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
            return (
              <>
                <ProvenanceNotice provenance={data.trace.provenance} />
                <TruncationNotice detail={data} />
                <TraceFacts trace={data.trace} returnedSpans={data.spans.length} spansTruncated={data.spans_truncated} />
                <StatusBreakdown trace={data.trace} view={view} />
                <section aria-labelledby="waterfall-heading" className="min-w-0">
                  <SectionHeading
                    id="waterfall-heading"
                    title="Span tree and timeline"
                    description="Parent-child hierarchy in start order with a relative-time waterfall. Each inference row is one physical attempt; retries are separate rows."
                  />
                  <Waterfall view={view} initialSelected={rawSpan} />
                </section>
                <LogicalCalls view={view} />
                <AttemptsTable view={view} usageTruncated={data.usage_truncated} />
                <SpanTable view={view} />
              </>
            );
          })()
        )}
      </PendingRegion>
    </NavigationTransition>
  );
}
