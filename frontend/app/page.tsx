import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { AutoRefresh } from "../components/auto-refresh";
import { FilterBar } from "../components/filter-bar";
import { LiveRegion } from "../components/live-region";
import { NavigationTransition, PendingRegion, RefreshButton } from "../components/navigation-transition";
import { CostDetails, ErrorTypes, ProvenanceTable } from "../components/overview/breakdowns";
import { DataNoticeBanner } from "../components/overview/data-notice";
import { KpiGrid } from "../components/overview/kpi-grid";
import { LatestTrace } from "../components/overview/latest-trace";
import { ModelGroups } from "../components/overview/model-groups";
import { TrendSection } from "../components/overview/trend-section";
import { PageHeader } from "../components/page-header";
import { ProjectSelector } from "../components/project-selector";
import { RangeForm } from "../components/range-form";
import { ScopeFilterForm } from "../components/scope-filter-form";
import { EmptyState, ErrorState, Notice } from "../components/states";
import { renderOutcome } from "../lib/auto-refresh";
import { formatUtcMinute, formatUtcTime } from "../lib/format";
import { DEFAULT_GROUP_LIMIT, modelHref } from "../lib/model-query";
import { hiddenFields, projectLinkValue } from "../lib/project";
import { hasScope } from "../lib/scope-filters";
import { loadOverview } from "../lib/server/overview";
import { WINDOW_PROBLEM_TEXT, overviewHref, overviewSearch, parseOverviewQuery, windowLabel, type OverviewState } from "../lib/time-range";

export const metadata: Metadata = { title: "Overview" };

type PageProps = { searchParams: Promise<Record<string, string | string[] | undefined>> };

export default async function OverviewPage({ searchParams }: PageProps) {
  await connection();
  const query = parseOverviewQuery(await searchParams);
  const state: OverviewState = { project: projectLinkValue(query.project), window: query.window, provenance: query.provenance, provider: query.provider, model: query.model };
  const ctx = await loadOverview(query);
  const { fetchedAt, overview, compare } = ctx;
  const data = overview.ok ? overview.data : null;
  const isEmpty = data !== null && data.traces.count.total === 0 && data.inference.attempts.total === 0;
  const search = overviewSearch(state);
  const unscoped = { ...state, provider: null, model: null };
  const scoped = hasScope(query);
  const filtered = scoped || query.provenance !== "all";
  const outcome = renderOutcome(fetchedAt, ctx.settledAt, [overview, compare, ctx.latest, ctx.options, ctx.projects]);

  return (
    <NavigationTransition outcome={outcome}>
      <PageHeader
        title="Overview"
        description="Inference attempts, traces, latency, tokens and estimated cost for one project, computed by the backend (M01–M14)."
        meta={
          <>
            {data ? (
              <span data-testid="window" data-from={data.window.from} data-to={data.window.to}>
                Window {formatUtcMinute(data.window.from)} → {formatUtcMinute(data.window.to)} ({windowLabel(state.window)}, event time)
              </span>
            ) : null}
            <span data-testid="fetched-at" data-fetched-at={fetchedAt.toISOString()}>
              Fetched {formatUtcTime(fetchedAt)} · not live; refresh to update
            </span>
          </>
        }
        actions={
          <>
            <AutoRefresh />
            <RefreshButton />
          </>
        }
      />
      {/* A project switch keeps the window and provenance; provider and model are values of one project, so they are reset. */}
      <ProjectSelector context={ctx} action="/" hidden={hiddenFields(search, ["project", "provider", "model"])} />
      <FilterBar state={state} />
      <div className="mb-3">
        <ScopeFilterForm
          action="/"
          hidden={hiddenFields(overviewSearch(unscoped), ["provider", "model"])}
          scope={{ provider: query.provider, model: query.model }}
          options={ctx.options}
          clearHref={overviewHref(unscoped)}
          idPrefix="overview"
        />
      </div>
      <div className="mb-5">
        <RangeForm action="/" hidden={hiddenFields(search, ["range", "from", "to"])} window={state.window} shown={data?.window ?? null} />
      </div>
      {query.ignored.length > 0 && (
        <div className="mb-5">
          <Notice tone="info" title="Unsupported filter value ignored" testId="ignored-notice">
            The URL contained unsupported values for: {query.ignored.join(", ")}
            {query.windowProblem ? ` (${WINDOW_PROBLEM_TEXT[query.windowProblem]})` : ""}. Defaults were used instead.
          </Notice>
        </div>
      )}
      {state.window.kind === "custom" && (
        <div className="mb-5">
          <Notice tone="info" title="Custom range" testId="custom-range-notice">
            Trend buckets stay aligned to UTC hours or days, so the first and last bucket may cover only part of their width.
          </Notice>
        </div>
      )}
      {query.provenance === "mixed" && (
        <div className="mb-5">
          <Notice tone="info" title="Mixed is a trace-level value" testId="mixed-notice">
            Only traces can be mixed; no single inference attempt is. With this filter every attempt-level metric (attempts, tokens, cost, model groups) is empty by definition.
          </Notice>
        </div>
      )}
      <PendingRegion>
        <LiveRegion viewKey={`${ctx.projectId ?? "-"}?${search}`} fetchedAt={fetchedAt.toISOString()} failure={overview.ok ? null : overview.error}>
          {data === null && !overview.ok ? (
            <ErrorState error={overview.error} resource="the overview metrics" />
          ) : data !== null ? (
            <>
              <DataNoticeBanner data={data} />
              {isEmpty ? (
                <EmptyState title="No telemetry in this window">
                  <p>
                    The backend has no traces or inference attempts for project {data.project} between {formatUtcMinute(data.window.from)} and {formatUtcMinute(data.window.to)}
                    {query.provenance !== "all" ? ` with provenance “${query.provenance}”` : ""}
                    {query.provider !== null ? ` from provider “${query.provider}”` : ""}
                    {query.model !== null ? ` for model “${query.model}”` : ""}.
                  </p>
                  <p className="mt-2 text-ink-3">
                    To generate synthetic telemetry locally, run the mock demo: <code className="font-mono text-xs">sdk/.venv/Scripts/python -m demo_app</code> (see docs/PHASE_STATUS.md).
                  </p>
                </EmptyState>
              ) : (
                <>
                  <LatestTrace result={ctx.latest} fetchedAt={fetchedAt} project={state.project} filtered={filtered} />
                  <KpiGrid data={data} />
                  <TrendSection data={data} />
                  <div className="mt-8 grid grid-cols-1 gap-8 2xl:grid-cols-2">
                    <ProvenanceTable data={data} />
                    <ErrorTypes data={data} />
                  </div>
                  <ModelGroups result={compare} />
                  <p className="mt-2 text-sm">
                    <Link
                      href={modelHref({ project: state.project, window: state.window, provenance: state.provenance, status: "all", limit: DEFAULT_GROUP_LIMIT, provider: query.provider, model: query.model })}
                      className="text-ink-2 underline underline-offset-2 hover:text-ink"
                      data-testid="to-models"
                    >
                      Open the model comparison for this window
                    </Link>
                  </p>
                  <div className="mt-8">
                    <CostDetails data={data} />
                  </div>
                </>
              )}
            </>
          ) : null}
        </LiveRegion>
      </PendingRegion>
    </NavigationTransition>
  );
}
