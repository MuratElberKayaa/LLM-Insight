import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { DataTable } from "../../components/data-table";
import { NavigationTransition, PendingRegion, RefreshButton } from "../../components/navigation-transition";
import { PageHeader } from "../../components/page-header";
import { ProjectSelector } from "../../components/project-selector";
import { EmptyState, ErrorState } from "../../components/states";
import type { ExperimentSummary } from "../../lib/api/experiment-types";
import { formatInt, formatUtcTime } from "../../lib/format";
import { formatIsoNs } from "../../lib/nanos";
import { parseProjectParam } from "../../lib/project";
import { loadExperimentsPage } from "../../lib/server/experiments";
import { loadImportedExperiments } from "../../lib/server/imported-experiments";
import { ImportedList } from "../../components/experiments/imported-report";

export const metadata: Metadata = { title: "Experiments" };

type PageProps = { searchParams: Promise<Record<string, string | string[] | undefined>> };

/** Phase 4D: the project's stored experiments, newest first. Opening this page runs nothing. */
export default async function ExperimentsPage({ searchParams }: PageProps) {
  await connection();
  const sp = await searchParams;
  const [ctx, live] = await Promise.all([
    loadExperimentsPage(parseProjectParam(sp.project)),
    loadImportedExperiments(parseProjectParam(sp.project)),
  ]);
  const { projectId, experiments, fetchedAt } = ctx;
  return (
    <NavigationTransition>
      <PageHeader
        title="Experiments"
        description="Compare prompt versions using fixed rules. Review completed live comparisons and scripted mock experiments. Opening a report runs nothing."
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
      <ProjectSelector context={ctx} action="/experiments" hidden={[]} />
      <PendingRegion>
        <section className="mb-8 space-y-3" aria-label="Completed live comparisons">
          <h2 className="text-xl font-semibold">Completed live comparisons</h2>
          <p className="text-sm text-ink-2">Actual model calls on synthetic inputs. Imported rule results with verified trace links; six cases per prompt, no general winner.</p>
          {live.ok ? <ImportedList items={live.data.items} /> : <ErrorState error={live.error} resource="the completed live comparisons" />}
          {live.ok && live.data.truncated && <p className="text-xs text-ink-3">Only the newest 200 live comparisons are shown.</p>}
        </section>
        <h2 className="mb-3 text-xl font-semibold">Mock experiments</h2>
        {!experiments.ok ? (
          <ErrorState error={experiments.error} resource="the experiments" />
        ) : experiments.data.items.length === 0 ? (
          <EmptyState title="No experiment yet">
            Choose “New mock experiment” to compare two prompt versions on a dataset. Nothing runs until you start it.
          </EmptyState>
        ) : (
          <>
            <DataTable<ExperimentSummary>
              caption="Experiments of this project, newest first"
              captionHidden
              testId="experiment-table"
              rows={experiments.data.items}
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
                { key: "dataset", header: "Dataset", render: (e) => e.dataset_name },
                {
                  key: "versions",
                  header: "A vs B",
                  render: (e) => (
                    <span>
                      <code className="font-mono text-xs">{e.prompt_version_a}</code> <span className="text-ink-3">vs</span> <code className="font-mono text-xs">{e.prompt_version_b}</code>
                    </span>
                  ),
                },
                { key: "model", header: "Model", render: (e) => <code className="font-mono text-xs">{e.model}</code> },
                {
                  key: "mode",
                  header: "Mode",
                  render: (e) => <span className="rounded border border-synthetic/60 px-1.5 py-0.5 text-xs font-semibold uppercase tracking-wide text-ink">{e.mode === "mock" ? "Mock · synthetic" : "Real"}</span>,
                },
                { key: "state", header: "State", render: (e) => (e.state === "finished" ? "Finished" : "Not finished") },
                { key: "results", header: "Results", align: "right", render: (e) => formatInt(e.results) },
                { key: "started", header: "Started (UTC)", render: (e) => formatIsoNs(e.started_at, 0) },
              ]}
            />
            {experiments.data.truncated && <p className="mt-3 text-xs text-ink-3">Only the newest {experiments.data.limit} experiments are listed.</p>}
          </>
        )}
      </PendingRegion>
    </NavigationTransition>
  );
}
