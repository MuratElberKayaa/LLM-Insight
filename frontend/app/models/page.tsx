import type { Metadata } from "next";
import { connection } from "next/server";
import { AutoRefresh } from "../../components/auto-refresh";
import { FilterGroup, ProvenanceLinks, RangeLinks } from "../../components/filter-bar";
import { ComparisonCharts, ComparisonNotices, ComparisonTable } from "../../components/models/model-comparison";
import { NavigationTransition, PendingRegion, FilterLink, RefreshButton } from "../../components/navigation-transition";
import { PageHeader } from "../../components/page-header";
import { ProjectSelector } from "../../components/project-selector";
import { RangeForm } from "../../components/range-form";
import { EmptyState, ErrorState, Notice } from "../../components/states";
import { formatUtcMinute, formatUtcTime } from "../../lib/format";
import { ATTEMPT_STATUS_FILTERS, GROUP_LIMITS, modelHref, modelSearch, modelStateOf, parseModelQuery, withModelFilter } from "../../lib/model-query";
import { hiddenFields } from "../../lib/project";
import { loadModels } from "../../lib/server/models";
import { WINDOW_PROBLEM_TEXT, windowLabel } from "../../lib/time-range";

export const metadata: Metadata = { title: "Model comparison" };

type PageProps = { searchParams: Promise<Record<string, string | string[] | undefined>> };

const STATUS_LABEL = { all: "All", ok: "Not failed", error: "Failed" } as const;

export default async function ModelsPage({ searchParams }: PageProps) {
  await connection();
  const query = parseModelQuery(await searchParams);
  const state = modelStateOf(query);
  const ctx = await loadModels(query);
  const { compare, fetchedAt } = ctx;
  const data = compare.ok ? compare.data : null;
  const linkState = state;
  const search = modelSearch(linkState);

  return (
    <NavigationTransition>
      <PageHeader
        title="Model comparison"
        description="Inference attempts, errors, latency, tokens and estimated cost per provider, requested model, response model and prompt version (M15), computed by the backend."
        meta={
          <>
            {data ? (
              <span data-testid="window" data-from={data.window.from} data-to={data.window.to}>
                Window {formatUtcMinute(data.window.from)} → {formatUtcMinute(data.window.to)} ({windowLabel(state.window)}, attempt start time)
              </span>
            ) : null}
            <span>Fetched {formatUtcTime(fetchedAt)}</span>
          </>
        }
        actions={
          <>
            <AutoRefresh />
            <RefreshButton />
          </>
        }
      />
      <ProjectSelector context={ctx} action="/models" hidden={hiddenFields(search, ["project"])} />
      <div className="mb-3 flex flex-wrap items-center gap-x-6 gap-y-3" data-testid="model-filters">
        <RangeLinks id="m-range-label" window={state.window} hrefFor={(window) => modelHref(withModelFilter(linkState, { window }))} />
        <ProvenanceLinks id="m-prov-label" current={state.provenance} hrefFor={(provenance) => modelHref(withModelFilter(linkState, { provenance }))} />
        <FilterGroup id="m-status-label" label="Attempt status">
          {ATTEMPT_STATUS_FILTERS.map((s) => (
            <FilterLink key={s} href={modelHref(withModelFilter(linkState, { status: s }))} current={state.status === s} label={s === "all" ? "All attempts" : `${STATUS_LABEL[s]} attempts only`}>
              {STATUS_LABEL[s]}
            </FilterLink>
          ))}
        </FilterGroup>
        <FilterGroup id="m-limit-label" label="Groups">
          {GROUP_LIMITS.map((n) => (
            <FilterLink key={n} href={modelHref(withModelFilter(linkState, { limit: n }))} current={state.limit === n} label={`Top ${n} groups`}>
              {n}
            </FilterLink>
          ))}
        </FilterGroup>
      </div>
      <div className="mb-5">
        <RangeForm action="/models" hidden={hiddenFields(search, ["range", "from", "to"])} window={state.window} shown={data?.window ?? null} />
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
        {!compare.ok ? (
          <ErrorState error={compare.error} resource="the model comparison" />
        ) : data !== null && data.groups.length === 0 ? (
          <EmptyState title="No inference attempts in this window">
            <p>
              The backend has no inference attempts for project {ctx.projectId} between {formatUtcMinute(data.window.from)} and {formatUtcMinute(data.window.to)}
              {state.provenance !== "all" || state.status !== "all" ? " with the selected filters" : ""}.
            </p>
            <p className="mt-2 text-ink-3">
              To generate synthetic telemetry locally, run the mock demo: <code className="font-mono text-xs">sdk/.venv/Scripts/python -m demo_app</code> (see docs/PHASE_STATUS.md).
            </p>
          </EmptyState>
        ) : data !== null ? (
          <>
            <ComparisonNotices data={data} />
            <ComparisonCharts data={data} />
            <ComparisonTable data={data} />
          </>
        ) : null}
      </PendingRegion>
    </NavigationTransition>
  );
}
