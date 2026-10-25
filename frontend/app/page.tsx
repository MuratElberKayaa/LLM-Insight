import type { Metadata } from "next";
import { connection } from "next/server";
import { FilterBar } from "../components/filter-bar";
import { NavigationTransition, PendingRegion, RefreshButton } from "../components/navigation-transition";
import { CostDetails, ErrorTypes, ProvenanceTable } from "../components/overview/breakdowns";
import { DataNoticeBanner } from "../components/overview/data-notice";
import { KpiGrid } from "../components/overview/kpi-grid";
import { ModelGroups } from "../components/overview/model-groups";
import { TrendSection } from "../components/overview/trend-section";
import { PageHeader } from "../components/page-header";
import { EmptyState, ErrorState, Notice } from "../components/states";
import { formatUtcMinute, formatUtcTime } from "../lib/format";
import { loadOverview } from "../lib/server/overview";
import { RANGE_PRESETS, parseOverviewQuery } from "../lib/time-range";

export const metadata: Metadata = { title: "Overview" };

type PageProps = { searchParams: Promise<Record<string, string | string[] | undefined>> };

export default async function OverviewPage({ searchParams }: PageProps) {
  await connection();
  const query = parseOverviewQuery(await searchParams);
  const { projectId, fetchedAt, overview, compare } = await loadOverview(query);
  const data = overview.ok ? overview.data : null;
  const isEmpty = data !== null && data.traces.count.total === 0 && data.inference.attempts.total === 0;

  return (
    <NavigationTransition>
      <PageHeader
        title="Overview"
        description="Inference attempts, traces, latency, tokens and estimated cost for one project, computed by the backend (M01–M14)."
        meta={
          <>
            {projectId !== null && <span>Project {projectId}</span>}
            {data ? (
              <span data-testid="window" data-from={data.window.from} data-to={data.window.to}>
                Window {formatUtcMinute(data.window.from)} → {formatUtcMinute(data.window.to)} ({RANGE_PRESETS[query.range].long.toLowerCase()}, event time)
              </span>
            ) : null}
            <span>Fetched {formatUtcTime(fetchedAt)} · not live; refresh to update</span>
          </>
        }
        actions={<RefreshButton />}
      />
      <FilterBar query={query} />
      {query.ignoredInvalid && (
        <div className="mb-5">
          <Notice tone="info" title="Unsupported filter value ignored">
            The URL contained a time range or provenance value the dashboard does not support; defaults were used instead.
          </Notice>
        </div>
      )}
      <PendingRegion>
        {data === null && !overview.ok ? (
          <ErrorState error={overview.error} resource="the overview metrics" />
        ) : data !== null ? (
          <>
            <DataNoticeBanner data={data} />
            {isEmpty ? (
              <EmptyState title="No telemetry in this window">
                <p>
                  The backend has no traces or inference attempts for project {data.project} between {formatUtcMinute(data.window.from)} and {formatUtcMinute(data.window.to)}
                  {query.provenance !== "all" ? ` with provenance “${query.provenance}”` : ""}.
                </p>
                <p className="mt-2 text-ink-3">
                  To generate synthetic telemetry locally, run the mock demo: <code className="font-mono text-xs">sdk/.venv/Scripts/python -m demo_app</code> (see docs/PHASE_STATUS.md).
                </p>
              </EmptyState>
            ) : (
              <>
                <KpiGrid data={data} />
                <TrendSection data={data} />
                <div className="mt-8 grid grid-cols-1 gap-8 2xl:grid-cols-2">
                  <ProvenanceTable data={data} />
                  <ErrorTypes data={data} />
                </div>
                <ModelGroups result={compare} />
                <div className="mt-8">
                  <CostDetails data={data} />
                </div>
              </>
            )}
          </>
        ) : null}
      </PendingRegion>
    </NavigationTransition>
  );
}
