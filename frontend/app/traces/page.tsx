import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { AutoRefresh } from "../../components/auto-refresh";
import { NavigationTransition, PendingRegion, RefreshButton } from "../../components/navigation-transition";
import { PageHeader } from "../../components/page-header";
import { ProjectSelector } from "../../components/project-selector";
import { RangeForm } from "../../components/range-form";
import { EmptyState, ErrorState, Notice } from "../../components/states";
import { TraceFilters } from "../../components/traces/trace-filters";
import { TraceListView } from "../../components/traces/trace-list";
import { formatUtcMinute, formatUtcTime } from "../../lib/format";
import { loadTraceList } from "../../lib/server/traces";
import { WINDOW_PROBLEM_TEXT, windowLabel } from "../../lib/time-range";
import { firstPageState, listStateOf, parseTraceListQuery, restartFields, traceListHref } from "../../lib/trace-query";

export const metadata: Metadata = { title: "Traces" };

type PageProps = { searchParams: Promise<Record<string, string | string[] | undefined>> };

export default async function TracesPage({ searchParams }: PageProps) {
  await connection();
  const query = parseTraceListQuery(await searchParams);
  const state = listStateOf(query);
  const ctx = await loadTraceList(query);
  const { fetchedAt, list } = ctx;
  const data = list.ok ? list.data : null;
  const filtered = state.status !== "all" || state.provenance !== "all" || state.model !== null;

  return (
    <NavigationTransition>
      <PageHeader
        title="Traces"
        description="Stored traces of one project, newest first. Metadata only: prompts and completions are never captured or stored."
        meta={
          <>
            {data ? (
              <span data-testid="window" data-from={data.window.from} data-to={data.window.to}>
                Window {formatUtcMinute(data.window.from)} → {formatUtcMinute(data.window.to)} ({state.cursor === null ? windowLabel(state.window) : "frozen for paging"}, trace start time)
              </span>
            ) : null}
            <span>Fetched {formatUtcTime(fetchedAt)} · not live; refresh to update</span>
          </>
        }
        actions={
          <>
            <AutoRefresh disabledReason={state.cursor !== null ? "Off on later pages: the window is frozen for paging. Go to the first page to use it." : undefined} />
            <RefreshButton />
          </>
        }
      />
      <ProjectSelector context={ctx} action="/traces" hidden={restartFields(state, ["project"])} />
      <TraceFilters state={state} />
      <div className="mb-5">
        <RangeForm action="/traces" hidden={restartFields(state, ["range", "from", "to"])} window={state.window} shown={data?.window ?? null} />
      </div>
      {query.ignored.length > 0 && (
        <div className="mb-5">
          <Notice tone="info" title="Unsupported filter value ignored" testId="ignored-notice">
            The URL contained unsupported values for: {query.ignored.join(", ")}
            {query.windowProblem ? ` (${WINDOW_PROBLEM_TEXT[query.windowProblem]})` : ""}. Defaults were used instead.
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
              The backend returned no traces for project {ctx.projectId} that started between {formatUtcMinute(data.window.from)} and {formatUtcMinute(data.window.to)}
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
