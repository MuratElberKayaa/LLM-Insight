import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { ExperimentForm } from "../../../components/experiments/experiment-form";
import { NavigationTransition, PendingRegion } from "../../../components/navigation-transition";
import { PageHeader } from "../../../components/page-header";
import { ProjectSelector } from "../../../components/project-selector";
import { EmptyState, ErrorState, Notice } from "../../../components/states";
import { parseProjectParam, projectHref, projectLinkValue } from "../../../lib/project";
import { loadExperimentSetup } from "../../../lib/server/experiments";

export const metadata: Metadata = { title: "New experiment" };

const ID_RE = /^[1-9][0-9]{0,15}$/;

type PageProps = { searchParams: Promise<Record<string, string | string[] | undefined>> };

/**
 * Phase 4D experiment setup. Opening this page reads datasets and run options and writes
 * nothing; the run starts only from the confirmed form submission (ADR-26). `?dataset=<id>`
 * only preselects a dataset of this project's listing (validated, never sent anywhere).
 */
export default async function NewExperimentPage({ searchParams }: PageProps) {
  await connection();
  const sp = await searchParams;
  const project = parseProjectParam(sp.project);
  const initialDataset = typeof sp.dataset === "string" && ID_RE.test(sp.dataset) ? sp.dataset : null;
  const ctx = await loadExperimentSetup(project);
  const { projectId, datasets, options } = ctx;
  return (
    <NavigationTransition>
      <Link href={projectHref("/experiments", projectLinkValue(project))} className="mb-4 inline-flex items-center gap-1 text-sm text-ink-2 hover:text-ink">
        <span aria-hidden="true">←</span> All experiments
      </Link>
      <PageHeader
        title="New mock experiment"
        description="Run every test case of one dataset under prompt versions A and B and evaluate each output with its deterministic rule. Opening this page runs nothing."
      />
      <ProjectSelector context={ctx} action="/experiments/new" hidden={[]} />
      <PendingRegion>
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
          <div className="min-w-0 space-y-4">
            {!options.ok ? (
              <ErrorState error={options.error} resource="the experiment options" />
            ) : !datasets.ok ? (
              <ErrorState error={datasets.error} resource="the datasets" />
            ) : datasets.data.items.length === 0 ? (
              <EmptyState title="No dataset yet">
                Create a dataset and approve test cases first (<Link href={projectHref("/datasets", projectLinkValue(project))} className="underline underline-offset-2">Datasets</Link>, or convert a reviewed trace).
              </EmptyState>
            ) : options.data.prompt_versions.length < 2 ? (
              <Notice tone="caution" title="The mock provider answers fewer than two prompt versions" testId="experiment-no-versions">
                An A/B experiment needs two prompt versions with scripted answers; nothing can be run.
              </Notice>
            ) : options.data.models.length === 0 ? (
              <Notice tone="caution" title="No model has a stored price" testId="experiment-no-models">
                Experiments price their synthetic token counts with the stored catalogue. Load the verified prices with <code className="font-mono text-xs">python -m app.cli seed-prices</code> in
                backend/, then reload.
              </Notice>
            ) : (
              projectId !== null && (
                <section aria-labelledby="setup-heading" className="rounded-lg border border-line bg-surface px-4 py-4">
                  <h2 id="setup-heading" className="mb-3 text-base font-semibold text-ink">
                    Configure the run
                  </h2>
                  <ExperimentForm project={projectId} datasets={datasets.data.items} options={options.data} initialDataset={initialDataset} />
                </section>
              )
            )}
            {datasets.ok && datasets.data.truncated && <p className="text-xs text-ink-3">Only the first {datasets.data.limit} datasets are offered.</p>}
          </div>
          <aside className="space-y-4" aria-label="Execution mode">
            <section className="rounded-lg border border-synthetic/60 bg-synthetic/10 px-4 py-4 text-sm" aria-labelledby="mode-heading" data-testid="execution-mode">
              <h2 id="mode-heading" className="mb-2 text-base font-semibold text-ink">
                Execution mode: mock only
              </h2>
              <ul className="list-disc space-y-1 pl-5 text-ink-2">
                <li>A deterministic mock answers from a fixed script of SYNTHETIC outputs; every result is labelled synthetic.</li>
                <li>No request goes to OpenAI or any other provider, and nothing is spent.</li>
                <li>An input the script does not know is recorded as not evaluated; no answer is invented.</li>
                <li>
                  Real provider: <strong className="text-ink">not available</strong> in this build
                  {options.ok ? (
                    <>
                      {" "}
                      (<code className="font-mono text-xs">{options.data.real_mode.code}</code>)
                    </>
                  ) : null}
                  . It is not offered here, and the backend refuses it.
                </li>
              </ul>
            </section>
          </aside>
        </div>
      </PendingRegion>
    </NavigationTransition>
  );
}
