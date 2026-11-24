import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { AutoRefresh } from "../components/auto-refresh";
import { ContextBar } from "../components/context-bar";
import { FilterBar } from "../components/filter-bar";
import { LiveRegion } from "../components/live-region";
import { NavigationTransition, PendingRegion, RefreshButton } from "../components/navigation-transition";
import { CostDetails, ErrorTypes, ProvenanceTable } from "../components/overview/breakdowns";
import { AlertsCard, firingCount } from "../components/overview/alert-strip";
import { DataNoticeBanner } from "../components/overview/data-notice";
import { ErrorPatternsSection } from "../components/overview/error-patterns";
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
import { overviewTraceListState, scopeEntries } from "../lib/context-links";
import { formatUtcMinute, formatUtcTime } from "../lib/format";
import { DEFAULT_GROUP_LIMIT, modelHref } from "../lib/model-query";
import { hiddenFields, projectLinkValue } from "../lib/project";
import { hasScope } from "../lib/scope-filters";
import { traceListHref } from "../lib/trace-query";
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

  // UI-R2: the same scope as a trace-list state (project, window, provenance, provider, model).
  const traceList = overviewTraceListState(state);
  const kpiLinks = { traces: traceListHref(traceList), errors: traceListHref(overviewTraceListState(state, "error")) };
  const context = scopeEntries({
    projectId: ctx.projectId,
    projects: ctx.projects,
    window: state.window,
    shown: data?.window ?? null,
    provenance: state.provenance,
    provider: state.provider,
    model: state.model,
  }).map((e) => ({ ...e, title: e.value }));

  return (
    <NavigationTransition outcome={outcome}>
      <ContextBar items={context} />
      <PageHeader
        eyebrow="Observe"
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
      {/* UI-R1: every scope control (project, window, provenance, provider/model, custom range) in one panel. */}
      <section aria-label="Scope and filters" className="mb-6 flex flex-col gap-4 rounded-xl border border-line bg-surface p-4 *:mb-0 sm:p-5" data-testid="scope-panel">
      {/* A project switch keeps the window and provenance; provider and model are values of one project, so they are reset. */}
      <ProjectSelector context={ctx} action="/" hidden={hiddenFields(search, ["project", "provider", "model"])} />
      <FilterBar state={state} />
      <div>
        <ScopeFilterForm
          action="/"
          hidden={hiddenFields(overviewSearch(unscoped), ["provider", "model"])}
          scope={{ provider: query.provider, model: query.model }}
          options={ctx.options}
          clearHref={overviewHref(unscoped)}
          idPrefix="overview"
        />
      </div>
      <div className="border-t border-line pt-3">
        <RangeForm action="/" hidden={hiddenFields(search, ["range", "from", "to"])} window={state.window} shown={data?.window ?? null} />
      </div>
      </section>
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
                <div className="space-y-8">
                  {firingCount(ctx.alerts) > 0 && (
                    <p className="flex flex-wrap items-center gap-2 rounded-lg border border-warning/50 bg-surface px-4 py-2.5 text-sm text-warning-ink" data-testid="alerts-pointer">
                      <span aria-hidden="true">▲</span>
                      {firingCount(ctx.alerts)} alert rule(s) firing in this window.
                      <a href="#alerts-heading" className="font-medium underline underline-offset-2">
                        See alerts
                      </a>
                    </p>
                  )}
                  <KpiGrid data={data} links={kpiLinks} />
                  <TrendSection data={data} />
                  <div className="space-y-2">
                    <ModelGroups result={compare} />
                    <p className="text-sm">
                      <Link
                        href={modelHref({ project: state.project, window: state.window, provenance: state.provenance, status: "all", limit: DEFAULT_GROUP_LIMIT, provider: query.provider, model: query.model })}
                        className="font-medium text-accent underline-offset-2 hover:underline"
                        data-testid="to-models"
                      >
                        Open the model comparison for this window →
                      </Link>
                    </p>
                  </div>
                  <div className="grid grid-cols-1 gap-6 2xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
                    <ErrorPatternsSection result={ctx.errorPatterns} project={state.project} filtered={filtered} />
                    <AlertsCard result={ctx.alerts} />
                  </div>
                  <LatestTrace result={ctx.latest} fetchedAt={fetchedAt} project={state.project} filtered={filtered} listState={traceList} />
                  <div className="grid grid-cols-1 gap-6 2xl:grid-cols-2">
                    <ProvenanceTable data={data} />
                    <ErrorTypes data={data} />
                  </div>
                  <CostDetails data={data} />
                </div>
              )}
            </>
          ) : null}
        </LiveRegion>
      </PendingRegion>
    </NavigationTransition>
  );
}
