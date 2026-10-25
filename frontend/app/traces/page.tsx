import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { NavigationTransition, PendingRegion, RefreshButton } from "../../components/navigation-transition";
import { PageHeader } from "../../components/page-header";
import { EmptyState, ErrorState, Notice } from "../../components/states";
import { TraceFilters } from "../../components/traces/trace-filters";
import { TraceListView } from "../../components/traces/trace-list";
import { formatUtcMinute, formatUtcTime } from "../../lib/format";
import { loadTraceList } from "../../lib/server/traces";
import { RANGE_PRESETS } from "../../lib/time-range";
import { firstPageState, parseTraceListQuery, traceListHref } from "../../lib/trace-query";

export const metadata: Metadata = { title: "Traces" };

type PageProps = { searchParams: Promise<Record<string, string | string[] | undefined>> };

export default async function TracesPage({ searchParams }: PageProps) {
  await connection();
  const { ignored, ...state } = parseTraceListQuery(await searchParams);
  const { projectId, fetchedAt, list } = await loadTraceList({ ...state, ignored });
  const data = list.ok ? list.data : null;
  const windowLabel = state.window.kind === "preset" ? RANGE_PRESETS[state.window.range].long.toLowerCase() : "fixed window";
  const filtered = state.status !== "all" || state.provenance !== "all" || state.model !== null;

  return (
    <NavigationTransition>
      <PageHeader
        title="Traces"
        description="Stored traces of one project, newest first. Metadata only: prompts and completions are never captured or stored."
        meta={
          <>
            {projectId !== null && <span>Project {projectId}</span>}
            {data ? (
              <span data-testid="window" data-from={data.window.from} data-to={data.window.to}>
                Window {formatUtcMinute(data.window.from)} → {formatUtcMinute(data.window.to)} ({windowLabel}, trace start time)
              </span>
            ) : null}
            <span>Fetched {formatUtcTime(fetchedAt)} · not live; refresh to update</span>
          </>
        }
        actions={<RefreshButton />}
      />
      <TraceFilters state={state} />
      {ignored.length > 0 && (
        <div className="mb-5">
          <Notice tone="info" title="Unsupported filter value ignored" testId="ignored-notice">
            The URL contained unsupported values for: {ignored.join(", ")}. Defaults were used instead.
          </Notice>
        </div>
      )}
      <PendingRegion>
        {!list.ok ? (
          <>
            <ErrorState error={list.error} resource="the trace list" />
            {list.error.kind === "invalid_request" && list.error.fields?.includes("cursor") && (
              <p className="mt-3 text-sm">
                <Link href={traceListHref(firstPageState(state))} className="text-ink-2 underline underline-offset-2 hover:text-ink">
                  Start again from the first page
                </Link>
              </p>
            )}
          </>
        ) : data !== null && data.items.length === 0 ? (
          <EmptyState title={state.cursor === null ? "No traces in this window" : "No more traces on this page"}>
            <p>
              The backend returned no traces for project {projectId} that started between {formatUtcMinute(data.window.from)} and {formatUtcMinute(data.window.to)}
              {filtered ? " with the selected filters" : ""}.
            </p>
            <p className="mt-2 text-ink-3">
              To generate synthetic traces locally, run the mock demo: <code className="font-mono text-xs">sdk/.venv/Scripts/python -m demo_app</code> (see docs/PHASE_STATUS.md).
            </p>
          </EmptyState>
        ) : data !== null ? (
          <TraceListView data={data} state={state} />
        ) : null}
      </PendingRegion>
    </NavigationTransition>
  );
}
