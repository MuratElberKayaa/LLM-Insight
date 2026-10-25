import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { CaseList } from "../../../components/datasets/case-list";
import { NavigationTransition, PendingRegion, RefreshButton } from "../../../components/navigation-transition";
import { PageHeader } from "../../../components/page-header";
import { ProjectSelector } from "../../../components/project-selector";
import { EmptyState, ErrorState } from "../../../components/states";
import { formatUtcTime } from "../../../lib/format";
import { parseProjectParam, projectHref, projectLinkValue } from "../../../lib/project";
import { loadDatasetCasesPage } from "../../../lib/server/datasets";

export const metadata: Metadata = { title: "Dataset" };

const ID_RE = /^[1-9][0-9]{0,18}$/;

type PageProps = {
  params: Promise<{ datasetId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function DatasetPage({ params, searchParams }: PageProps) {
  await connection();
  const { datasetId } = await params;
  const sp = await searchParams;
  const project = parseProjectParam(sp.project);
  const back = projectHref("/datasets", projectLinkValue(project));
  // Ids are checked by format before any request; an invalid one is neither sent nor echoed.
  const validId = ID_RE.test(datasetId);
  const after = typeof sp.after === "string" && ID_RE.test(sp.after) ? sp.after : null;
  const ctx = validId ? await loadDatasetCasesPage(project, datasetId, after) : null;

  return (
    <NavigationTransition>
      <Link href={back} className="mb-4 inline-flex items-center gap-1 text-sm text-ink-2 hover:text-ink">
        <span aria-hidden="true">←</span> All datasets
      </Link>
      {ctx === null ? (
        <>
          <PageHeader title="Dataset" />
          <ErrorState error={{ kind: "invalid_request", fields: ["dataset_id"] }} resource="this dataset" />
        </>
      ) : (
        <>
          <PageHeader
            title={ctx.cases.ok ? `Dataset: ${ctx.cases.data.dataset.name}` : "Dataset"}
            description="Human-approved test cases, each with its source trace and review, as stored. Not evaluated yet: deterministic evaluation is Phase 4C."
            meta={<span>Fetched {formatUtcTime(ctx.fetchedAt)}</span>}
            actions={<RefreshButton />}
          />
          <ProjectSelector context={ctx} action="/datasets" hidden={[]} />
          <PendingRegion>
            {!ctx.cases.ok ? (
              <ErrorState error={ctx.cases.error} resource="this dataset" />
            ) : ctx.cases.data.items.length === 0 ? (
              <EmptyState title="No test case in this dataset">Convert a reviewed trace from its trace page: open the trace, record a review and a decision, then choose “Convert to a regression test case”.</EmptyState>
            ) : (
              <>
                <p className="mb-3 text-sm text-ink-2" data-testid="case-count">
                  {ctx.cases.data.dataset.case_count} test case{ctx.cases.data.dataset.case_count === 1 ? "" : "s"}
                  {after !== null ? `, after #${after}` : ""}.
                </p>
                {ctx.projectId !== null && <CaseList cases={ctx.cases.data.items} project={ctx.projectId} />}
                {ctx.cases.data.next_after !== null && (
                  <Link href={`/datasets/${datasetId}?project=${ctx.projectId}&after=${ctx.cases.data.next_after}`} className="mt-4 inline-block text-sm underline underline-offset-2">
                    Next cases →
                  </Link>
                )}
              </>
            )}
          </PendingRegion>
        </>
      )}
    </NavigationTransition>
  );
}
