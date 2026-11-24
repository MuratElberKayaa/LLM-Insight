import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { Breadcrumbs } from "../../components/breadcrumbs";
import { ContextBar } from "../../components/context-bar";
import { DatasetForm } from "../../components/datasets/dataset-form";
import { EvaluationFlow } from "../../components/evaluation/flow-strip";
import { MetricCard } from "../../components/metric-card";
import { NavigationTransition, PendingRegion, RefreshButton } from "../../components/navigation-transition";
import { PageHeader } from "../../components/page-header";
import { ProjectSelector } from "../../components/project-selector";
import { EmptyState, ErrorState } from "../../components/states";
import { projectContext } from "../../lib/context-links";
import { formatInt, formatUtcTime } from "../../lib/format";
import { formatIsoNs } from "../../lib/nanos";
import { parseProjectParam, projectHref, projectLinkValue } from "../../lib/project";
import { loadDatasetsPage } from "../../lib/server/datasets";

export const metadata: Metadata = { title: "Datasets" };

type PageProps = { searchParams: Promise<Record<string, string | string[] | undefined>> };

export default async function DatasetsPage({ searchParams }: PageProps) {
  await connection();
  const sp = await searchParams;
  const ctx = await loadDatasetsPage(parseProjectParam(sp.project));
  const { projectId, datasets, fetchedAt } = ctx;
  const link = projectLinkValue(parseProjectParam(sp.project));
  const items = datasets.ok ? datasets.data.items : [];
  const totalCases = items.reduce((n, d) => n + d.case_count, 0);
  const emptySets = items.filter((d) => d.case_count === 0).length;
  const newest = items.reduce<string | null>((m, d) => (m === null || d.created_at > m ? d.created_at : m), null);
  return (
    <NavigationTransition>
      <ContextBar items={[{ key: "project", label: "Project", value: projectContext(projectId, ctx.projects) }]} />
      <Breadcrumbs items={[{ label: "Evaluate" }, { label: "Datasets" }]} />
      <PageHeader
        eyebrow="Evaluate"
        title="Datasets"
        description="Named sets of human-approved regression test cases. Cases are created from reviewed traces on the trace page; nothing is added automatically. Cases are evaluated only by experiments, which you start explicitly."
        meta={<span>Fetched {formatUtcTime(fetchedAt)}</span>}
        actions={<RefreshButton />}
      />
      <EvaluationFlow current="dataset" hrefs={{ trace: projectHref("/traces", link), experiment: projectHref("/experiments", link) }} />
      <ProjectSelector context={ctx} action="/datasets" hidden={[]} />
      <PendingRegion>
        {!datasets.ok ? (
          <ErrorState error={datasets.error} resource="the datasets" />
        ) : (
          <div className="space-y-6">
            {items.length > 0 && (
              <section aria-label="Datasets in numbers" className="grid grid-cols-2 gap-4 lg:grid-cols-4" data-testid="datasets-facts">
                <MetricCard id="dss-count" label="Datasets" metricId="listed" value={formatInt(items.length)} accent="violet" detail={datasets.data.truncated ? <p>first {datasets.data.limit} listed</p> : undefined} />
                <MetricCard id="dss-cases" label="Approved test cases" metricId="listed" value={formatInt(totalCases)} accent="violet" detail={<p>sum over the listed datasets</p>} />
                <MetricCard id="dss-empty" label="Datasets without a case" metricId="listed" value={formatInt(emptySets)} accent="violet" detail={<p>nothing to evaluate there yet</p>} />
                <MetricCard id="dss-newest" label="Newest dataset" metricId="created" value={<span className="text-base">{newest === null ? "—" : formatIsoNs(newest, 0)}</span>} accent="violet" />
              </section>
            )}
            {items.length === 0 ? (
              <EmptyState title="No dataset yet">Create one below, then convert a reviewed trace into a test case from its trace page.</EmptyState>
            ) : (
              <section aria-labelledby="datasets-heading" className="min-w-0 rounded-xl border border-line bg-surface p-5">
                <h2 id="datasets-heading" className="mb-3 text-[15px] font-semibold text-ink">
                  Datasets of this project
                </h2>
                <div className="relative hidden overflow-x-auto rounded-lg border border-line md:block" role="region" aria-label="Datasets of this project" tabIndex={0}>
                  <table className="w-full text-left text-sm" data-testid="dataset-table">
                    <caption className="sr-only">Datasets of this project</caption>
                    <thead className="bg-raised text-xs text-ink-2">
                      <tr>
                        <th scope="col" className="px-3 py-2 font-medium">
                          Name
                        </th>
                        <th scope="col" className="px-3 py-2 text-right font-medium">
                          Test cases
                        </th>
                        <th scope="col" className="px-3 py-2 font-medium">
                          Created (UTC)
                        </th>
                        <th scope="col" className="px-3 py-2 font-medium">
                          Next step
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((d) => (
                        <tr key={d.id} className="border-t border-line hover:bg-raised/40" data-testid="dataset-row">
                          <th scope="row" className="px-3 py-2.5 text-left font-normal">
                            <Link href={`/datasets/${d.id}?project=${projectId}`} className="font-medium text-ink underline underline-offset-2">
                              {d.name}
                            </Link>
                          </th>
                          <td className="tabular px-3 py-2.5 text-right text-ink-2">{d.case_count}</td>
                          <td className="tabular px-3 py-2.5 text-ink-2">{formatIsoNs(d.created_at, 0)}</td>
                          <td className="px-3 py-2.5 text-xs">
                            {d.case_count > 0 ? (
                              <Link href={`/experiments/new?project=${projectId}&dataset=${d.id}`} className="text-accent underline-offset-2 hover:underline">
                                Set up a mock experiment →
                              </Link>
                            ) : (
                              <span className="text-ink-3">Add a case from a reviewed trace</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <ul className="space-y-2 md:hidden" aria-label="Datasets of this project" data-testid="dataset-cards">
                  {items.map((d) => (
                    <li key={d.id} className="rounded-lg border border-line px-4 py-3">
                      <Link href={`/datasets/${d.id}?project=${projectId}`} className="font-medium text-ink underline underline-offset-2">
                        {d.name}
                      </Link>
                      <p className="tabular mt-1 text-xs text-ink-2">
                        {formatInt(d.case_count)} test case{d.case_count === 1 ? "" : "s"} · created {formatIsoNs(d.created_at, 0)}
                      </p>
                    </li>
                  ))}
                </ul>
                {datasets.data.truncated && <p className="mt-3 text-xs text-ink-3">Only the first {datasets.data.limit} datasets are listed.</p>}
              </section>
            )}
            {projectId !== null && (
              <section aria-labelledby="new-dataset-heading" className="rounded-xl border border-line bg-surface p-5">
                <h2 id="new-dataset-heading" className="mb-1 text-[15px] font-semibold text-ink">
                  Create a dataset
                </h2>
                <p className="mb-3 text-xs text-ink-3">An empty, named set. Test cases are added later from reviewed traces, one approval at a time.</p>
                <DatasetForm project={projectId} />
              </section>
            )}
          </div>
        )}
      </PendingRegion>
    </NavigationTransition>
  );
}
