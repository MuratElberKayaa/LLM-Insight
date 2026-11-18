import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
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
  if (!result.ok) return <ErrorState error={result.error} resource="the live experiment" />;
  const report = result.data;
  return <>
    <PageHeader title={modelLabel(report.model)} description="Completed live prompt comparison · V1 vs V2" actions={<Link className="underline" href={`/experiments?project=${report.project}`}>All experiments</Link>} />
    <ImportedReportView report={report} />
  </>;
}
