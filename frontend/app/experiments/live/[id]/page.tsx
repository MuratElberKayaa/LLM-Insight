import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { Breadcrumbs } from "../../../../components/breadcrumbs";
import { ImportedBadge } from "../../../../components/evaluation/badges";
import { ImportedReportView, modelLabel } from "../../../../components/experiments/imported-report";
import { PageHeader } from "../../../../components/page-header";
import { ErrorState } from "../../../../components/states";
import { parseProjectParam } from "../../../../lib/project";
import { loadImportedReport } from "../../../../lib/server/imported-experiments";

export const metadata: Metadata = { title: "Live prompt comparison" };
export default async function LiveExperimentPage({ params, searchParams }: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  await connection();
  const { id } = await params;
  const sp = await searchParams;
  const result = await loadImportedReport(parseProjectParam(sp.project), id);
  const crumbs = (label: string, project: string | null) => [{ label: "Evaluate" }, { label: "Experiments", href: project === null ? "/experiments" : `/experiments?project=${project}` }, { label }];
  if (!result.ok)
    return (
      <>
        <Breadcrumbs items={crumbs("Imported report", null)} />
        <ErrorState error={result.error} resource="the live experiment" />
      </>
    );
  const report = result.data;
  return <>
    <Breadcrumbs items={crumbs(modelLabel(report.model), String(report.project))} />
    <PageHeader
      eyebrow="Evaluate · Imported live comparison"
      title={modelLabel(report.model)}
      description="Completed live prompt comparison · V1 vs V2. Produced outside this dashboard and imported read-only; it is not a mock experiment and cannot be re-run here."
      meta={<ImportedBadge />}
      actions={<Link className="underline" href={`/experiments?project=${report.project}`}>All experiments</Link>}
    />
    <ImportedReportView report={report} />
  </>;
}
