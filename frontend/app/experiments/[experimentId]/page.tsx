import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { CaseResultsTable, ComparisonTable, ComparisonVerdict, Limitations, OutcomeSection, ReportSummary, ReportWarnings } from "../../../components/experiments/experiment-report";
import { VersionCards } from "../../../components/experiments/experiment-diff";
import { Breadcrumbs } from "../../../components/breadcrumbs";
import { ContextBar } from "../../../components/context-bar";
import { ModeBadge, StateBadge } from "../../../components/evaluation/badges";
import { EvaluationFlow } from "../../../components/evaluation/flow-strip";
import { SyntheticBanner } from "../../../components/experiments/values";
import { NavigationTransition, PendingRegion, RefreshButton } from "../../../components/navigation-transition";
import { PageHeader, SectionHeading } from "../../../components/page-header";
import { ProjectSelector } from "../../../components/project-selector";
import { ErrorState } from "../../../components/states";
import { projectContext } from "../../../lib/context-links";
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

  const exp = ctx?.report.ok ? ctx.report.data.experiment : null;
  const link = projectLinkValue(project);

  return (
    <NavigationTransition>
      {ctx !== null && (
        <ContextBar
          items={[
            { key: "project", label: "Project", value: projectContext(ctx.projectId, ctx.projects) },
            ...(exp
              ? [
                  { key: "dataset", label: "Dataset", value: exp.dataset_name, title: exp.dataset_name },
                  { key: "versions", label: "A vs B", value: `${exp.prompt_version_a} vs ${exp.prompt_version_b}` },
                  { key: "mode", label: "Mode", value: exp.mode === "mock" ? "Synthetic / offline mock" : "Real (recorded)" },
                ]
              : []),
          ]}
        />
      )}
      <Breadcrumbs items={[{ label: "Evaluate" }, { label: "Experiments", href: back }, { label: exp ? `#${exp.id}` : "Experiment" }]} />
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
            eyebrow="Evaluate · Experiment"
            title={ctx.report.ok ? `Experiment #${ctx.report.data.experiment.id}` : "Experiment results"}
            description="Stored results of a prompt A/B experiment. Numbers are the backend's; nothing is recomputed, and unknown stays unknown."
            meta={
              <>
                {exp && <ModeBadge mode={exp.mode} />}
                {exp && <StateBadge state={exp.finished_at === null ? "running" : "finished"} />}
                <span>Fetched {formatUtcTime(ctx.fetchedAt)}</span>
              </>
            }
            actions={<RefreshButton />}
          />
          <EvaluationFlow
            current="experiment"
            hrefs={{ trace: projectHref("/traces", link), dataset: exp ? `/datasets/${exp.dataset_id}${link !== null ? `?project=${link}` : ""}` : projectHref("/datasets", link) }}
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
                  <VersionCards report={ctx.report.data} />
                  <ComparisonVerdict report={ctx.report.data} />
                  <section aria-labelledby="comparison-heading">
                    <SectionHeading id="comparison-heading" title="Comparison table" description="Pass rates exclude not-evaluated results and have their own denominators; compare them only together with the counts." />
                    <ComparisonTable report={ctx.report.data} />
                  </section>
                  <OutcomeSection report={ctx.report.data} />
                  <section aria-labelledby="cases-heading">
                    <SectionHeading id="cases-heading" title="Results per test case" description="Outcome change of each case from baseline A to B; filter to the cases that changed." />
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
