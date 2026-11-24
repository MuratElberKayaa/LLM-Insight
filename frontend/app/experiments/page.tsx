import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { Breadcrumbs } from "../../components/breadcrumbs";
import { ContextBar } from "../../components/context-bar";
import { DataTable } from "../../components/data-table";
import { ImportedBadge, ModeBadge, StateBadge } from "../../components/evaluation/badges";
import { EvaluationFlow } from "../../components/evaluation/flow-strip";
import { ImportedList } from "../../components/experiments/imported-report";
import { MetricCard } from "../../components/metric-card";
import { NavigationTransition, PendingRegion, RefreshButton } from "../../components/navigation-transition";
import { PageHeader } from "../../components/page-header";
import { ProjectSelector } from "../../components/project-selector";
import { EmptyState, ErrorState } from "../../components/states";
import type { ExperimentSummary } from "../../lib/api/experiment-types";
import { projectContext } from "../../lib/context-links";
import { formatInt, formatUtcTime } from "../../lib/format";
import { formatIsoNs } from "../../lib/nanos";
import { parseProjectParam, projectHref, projectLinkValue } from "../../lib/project";
import { loadExperimentsPage } from "../../lib/server/experiments";
import { loadImportedExperiments } from "../../lib/server/imported-experiments";

export const metadata: Metadata = { title: "Experiments" };

type PageProps = { searchParams: Promise<Record<string, string | string[] | undefined>> };

/**
 * Phase 4D / UI-R4: the project's stored mock experiments, newest first, and (separately) the
 * imported live-comparison reports. Opening this page runs nothing.
 */
export default async function ExperimentsPage({ searchParams }: PageProps) {
  await connection();
  const sp = await searchParams;
  const project = parseProjectParam(sp.project);
  const link = projectLinkValue(project);
  const [ctx, live] = await Promise.all([loadExperimentsPage(project), loadImportedExperiments(project)]);
  const { projectId, experiments, fetchedAt } = ctx;
  const items = experiments.ok ? experiments.data.items : [];
  const finished = items.filter((e) => e.state === "finished").length;
  const datasets = new Set(items.map((e) => e.dataset_id)).size;
  return (
    <NavigationTransition>
      <ContextBar items={[{ key: "project", label: "Project", value: projectContext(projectId, ctx.projects) }]} />
      <Breadcrumbs items={[{ label: "Evaluate" }, { label: "Experiments" }]} />
      <PageHeader
        eyebrow="Evaluate"
        title="Experiments"
        description="Compare two prompt versions on a dataset with fixed, deterministic rules. Mock experiments run a scripted offline provider; imported reports were produced elsewhere and are read-only. Opening a report runs nothing."
        meta={<span>Fetched {formatUtcTime(fetchedAt)}</span>}
        actions={
          <>
            {projectId !== null && (
              <Link
                href={`/experiments/new?project=${projectId}`}
                className="inline-flex items-center rounded-md border border-series-1/70 bg-series-1/20 px-3 py-2 text-sm font-semibold text-ink hover:bg-series-1/30"
                data-testid="new-experiment-link"
              >
                New mock experiment
              </Link>
            )}
            <RefreshButton />
          </>
        }
      />
      <EvaluationFlow current="experiment" hrefs={{ trace: projectHref("/traces", link), dataset: projectHref("/datasets", link) }} />
      <ProjectSelector context={ctx} action="/experiments" hidden={[]} />
      <PendingRegion>
        {experiments.ok && items.length > 0 && (
          <section aria-label="Mock experiments in numbers" className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4" data-testid="experiments-facts">
            <MetricCard id="exp-count" label="Mock experiments" metricId="listed" value={formatInt(items.length)} accent="violet" detail={<p>Synthetic / offline mock</p>} />
            <MetricCard id="exp-finished" label="Finished" metricId="listed" value={`${formatInt(finished)} / ${formatInt(items.length)}`} accent="violet" detail={items.length - finished > 0 ? <p>{formatInt(items.length - finished)} not finished</p> : undefined} />
            <MetricCard id="exp-datasets" label="Datasets used" metricId="listed" value={formatInt(datasets)} accent="violet" />
            <MetricCard
              id="exp-imported"
              label="Imported live reports"
              metricId="read-only"
              value={live.ok ? formatInt(live.data.items.length) : "—"}
              state={live.ok ? "known" : "unknown"}
              unknownText="Unavailable"
              accent="violet"
              detail={<p>not run from here</p>}
            />
          </section>
        )}
        <section aria-labelledby="mock-heading" className="mb-8 min-w-0 rounded-xl border border-line bg-surface p-5" data-testid="mock-experiments">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <h2 id="mock-heading" className="text-[15px] font-semibold text-ink">
              Mock experiments
            </h2>
            <ModeBadge mode="mock" />
          </div>
          <p className="mb-3 text-sm text-ink-2">
            Started explicitly from a dataset. A deterministic, scripted mock produces the outputs: no provider is called, nothing is spent, and the results say nothing about a real
            model. No winner is named.
          </p>
          {!experiments.ok ? (
            <ErrorState error={experiments.error} resource="the experiments" />
          ) : items.length === 0 ? (
            <EmptyState title="No experiment yet">
              Choose “New mock experiment” to compare two prompt versions on a dataset. Nothing runs until you start it.
            </EmptyState>
          ) : (
            <>
              <DataTable<ExperimentSummary>
                caption="Experiments of this project, newest first"
                captionHidden
                testId="experiment-table"
                // Fits 1280 px with the sidebar open on any font set (CI Linux renders wider than Windows).
                fit
                rows={items}
                rowKey={(e) => String(e.id)}
                columns={[
                  {
                    key: "id",
                    header: "Experiment",
                    rowHeader: true,
                    render: (e) => (
                      <Link href={`/experiments/${e.id}?project=${projectId}`} className="underline underline-offset-2" data-testid="experiment-link">
                        #{e.id}
                      </Link>
                    ),
                  },
                  {
                    key: "dataset",
                    header: "Dataset",
                    render: (e) => (
                      // Final stabilization: long names wrap in a bounded column, so the table fits at 1280 px.
                      <Link href={`/datasets/${e.dataset_id}?project=${projectId}`} className="inline-block max-w-[10rem] whitespace-normal break-words underline-offset-2 hover:underline" data-testid="experiment-dataset-link">
                        {e.dataset_name}
                      </Link>
                    ),
                  },
                  {
                    key: "versions",
                    header: "A vs B",
                    render: (e) => (
                      <span className="block text-xs">
                        <code className="block font-mono">A {e.prompt_version_a}</code>
                        <code className="block font-mono text-ink-3">B {e.prompt_version_b}</code>
                      </span>
                    ),
                  },
                  {
                    key: "model",
                    header: <span title="The model name sets identity and price only; the scripted mock produces the outputs">Model</span>,
                    render: (e) => <code className="font-mono text-xs">{e.model}</code>,
                  },
                  { key: "mode", header: "Mode", render: (e) => <ModeBadge mode={e.mode} compact /> },
                  { key: "state", header: "State", render: (e) => <StateBadge state={e.state} compact /> },
                  { key: "results", header: "Results", align: "right", render: (e) => formatInt(e.results) },
                  {
                    key: "started",
                    header: "Started (UTC)",
                    // Date and time on two lines: the column is as narrow as its header.
                    render: (e) => {
                      const [day, time] = formatIsoNs(e.started_at, 0).split(" ");
                      return (
                        <span className="tabular block text-xs" data-testid="experiment-started" title={formatIsoNs(e.started_at, 6)}>
                          <span className="block">{day}</span>
                          {time !== undefined && <span className="block text-ink-3">{time} UTC</span>}
                        </span>
                      );
                    },
                  },
                ]}
              />
              {experiments.data.truncated && <p className="mt-3 text-xs text-ink-3">Only the newest {experiments.data.limit} experiments are listed.</p>}
            </>
          )}
        </section>
        <section aria-labelledby="imported-heading" className="min-w-0 space-y-3 rounded-xl border border-line bg-surface p-5" data-testid="imported-experiments">
          <div className="flex flex-wrap items-center gap-2">
            <h2 id="imported-heading" className="text-[15px] font-semibold text-ink">
              Completed live comparisons
            </h2>
            <ImportedBadge />
          </div>
          <p className="text-sm text-ink-2">
            Real provider calls on synthetic test inputs, run outside this dashboard and imported with their rule results and verified trace links. They are not part of the mock
            experiment runner, cannot be started or re-run here, and name no general winner (six cases per prompt).
          </p>
          {live.ok ? <ImportedList items={live.data.items} /> : <ErrorState error={live.error} resource="the completed live comparisons" />}
          {live.ok && live.data.truncated && <p className="text-xs text-ink-3">Only the newest 200 live comparisons are shown.</p>}
        </section>
      </PendingRegion>
    </NavigationTransition>
  );
}
