import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { DatasetForm } from "../../components/datasets/dataset-form";
import { NavigationTransition, PendingRegion, RefreshButton } from "../../components/navigation-transition";
import { PageHeader } from "../../components/page-header";
import { ProjectSelector } from "../../components/project-selector";
import { EmptyState, ErrorState } from "../../components/states";
import { formatUtcTime } from "../../lib/format";
import { formatIsoNs } from "../../lib/nanos";
import { parseProjectParam } from "../../lib/project";
import { loadDatasetsPage } from "../../lib/server/datasets";

export const metadata: Metadata = { title: "Datasets" };

type PageProps = { searchParams: Promise<Record<string, string | string[] | undefined>> };

export default async function DatasetsPage({ searchParams }: PageProps) {
  await connection();
  const sp = await searchParams;
  const ctx = await loadDatasetsPage(parseProjectParam(sp.project));
  const { projectId, datasets, fetchedAt } = ctx;
  return (
    <NavigationTransition>
      <PageHeader
        title="Datasets"
        description="Named sets of human-approved regression test cases. Cases are created from reviewed traces on the trace page; nothing is added automatically. Cases are evaluated only by experiments, which you start explicitly."
        meta={<span>Fetched {formatUtcTime(fetchedAt)}</span>}
        actions={<RefreshButton />}
      />
      <ProjectSelector context={ctx} action="/datasets" hidden={[]} />
      <PendingRegion>
        {!datasets.ok ? (
          <ErrorState error={datasets.error} resource="the datasets" />
        ) : (
          <>
            {datasets.data.items.length === 0 ? (
              <EmptyState title="No dataset yet">Create one below, then convert a reviewed trace into a test case from its trace page.</EmptyState>
            ) : (
              <table className="mb-6 w-full text-left text-sm" data-testid="dataset-table">
                <caption className="sr-only">Datasets of this project</caption>
                <thead className="text-xs text-ink-3">
                  <tr>
                    <th scope="col" className="py-2 pr-4 font-medium">
                      Name
                    </th>
                    <th scope="col" className="py-2 pr-4 font-medium">
                      Test cases
                    </th>
                    <th scope="col" className="py-2 font-medium">
                      Created (UTC)
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {datasets.data.items.map((d) => (
                    <tr key={d.id} className="border-t border-line" data-testid="dataset-row">
                      <td className="py-2 pr-4">
                        <Link href={`/datasets/${d.id}?project=${projectId}`} className="font-medium text-ink underline underline-offset-2">
                          {d.name}
                        </Link>
                      </td>
                      <td className="tabular py-2 pr-4 text-ink-2">{d.case_count}</td>
                      <td className="tabular py-2 text-ink-2">{formatIsoNs(d.created_at, 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {datasets.data.truncated && <p className="mb-4 text-xs text-ink-3">Only the first {datasets.data.limit} datasets are listed.</p>}
            {projectId !== null && (
              <section aria-labelledby="new-dataset-heading" className="rounded-lg border border-line bg-surface px-4 py-4">
                <h2 id="new-dataset-heading" className="mb-3 text-base font-semibold text-ink">
                  Create a dataset
                </h2>
                <DatasetForm project={projectId} />
              </section>
            )}
          </>
        )}
      </PendingRegion>
    </NavigationTransition>
  );
}
