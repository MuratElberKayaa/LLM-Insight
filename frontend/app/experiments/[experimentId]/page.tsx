import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { CaseResultsTable, ComparisonTable, ComparisonVerdict, Limitations, OutcomeSection, ReportSummary, ReportWarnings } from "../../../components/experiments/experiment-report";
import { SyntheticBanner } from "../../../components/experiments/values";
import { NavigationTransition, PendingRegion, RefreshButton } from "../../../components/navigation-transition";
import { PageHeader, SectionHeading } from "../../../components/page-header";
import { ProjectSelector } from "../../../components/project-selector";
import { ErrorState } from "../../../components/states";
import { formatUtcTime } from "../../../lib/format";
import { parseProjectParam, projectHref, projectLinkValue } from "../../../lib/project";
import { loadExperimentReport } from "../../../lib/server/experiments";

export const metadata: Metadata = { title: "Experiment results" };

const ID_RE = /^[1-9][0-9]{0,18}$/;

type PageProps = {
  params: Promise<{ experimentId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

/** Phase 4D: the persisted results of one experiment (read-only). */
export default async function ExperimentPage({ params, searchParams }: PageProps) {
  await connection();
  const { experimentId } = await params;
  const sp = await searchParams;
  const project = parseProjectParam(sp.project);
  const back = projectHref("/experiments", projectLinkValue(project));
  // Ids are checked by format before any request; an invalid one is neither sent nor echoed.
  const ctx = ID_RE.test(experimentId) ? await loadExperimentReport(project, experimentId) : null;

  return (
    <NavigationTransition>
      <Link href={back} className="mb-4 inline-flex items-center gap-1 text-sm text-ink-2 hover:text-ink">
        <span aria-hidden="true">←</span> All experiments
      </Link>
      {ctx === null ? (
        <>
          <PageHeader title="Experiment results" />
          <ErrorState error={{ kind: "invalid_request", fields: ["experiment_id"] }} resource="this experiment" />
        </>
      ) : (
        <>
          <PageHeader
            title={ctx.report.ok ? `Experiment #${ctx.report.data.experiment.id}` : "Experiment results"}
            description="Stored results of a prompt A/B experiment. Numbers are the backend's; nothing is recomputed, and unknown stays unknown."
            meta={<span>Fetched {formatUtcTime(ctx.fetchedAt)}</span>}
            actions={<RefreshButton />}
          />
          <ProjectSelector context={ctx} action="/experiments" hidden={[]} />
          <PendingRegion>
            {!ctx.report.ok ? (
              <div data-testid="experiment-not-found">
                <ErrorState error={ctx.report.error} resource="this experiment" />
              </div>
            ) : (
              ctx.projectId !== null && (
                <div className="space-y-6">
                  <SyntheticBanner mode={ctx.report.data.experiment.mode} />
                  <ReportSummary report={ctx.report.data} project={ctx.projectId} />
                  <ReportWarnings report={ctx.report.data} />
                  <ComparisonVerdict report={ctx.report.data} />
                  <section aria-labelledby="comparison-heading">
                    <SectionHeading id="comparison-heading" title="Comparison table" description="Pass rates exclude not-evaluated results and have their own denominators; compare them only together with the counts." />
                    <ComparisonTable report={ctx.report.data} />
                  </section>
                  <OutcomeSection report={ctx.report.data} />
                  <section aria-labelledby="cases-heading">
                    <SectionHeading id="cases-heading" title="Results per test case" />
                    <CaseResultsTable report={ctx.report.data} project={ctx.projectId} />
                  </section>
                  <Limitations report={ctx.report.data} />
                </div>
              )
            )}
          </PendingRegion>
        </>
      )}
    </NavigationTransition>
  );
}
