import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { Breadcrumbs } from "../../../components/breadcrumbs";
import { ContextBar } from "../../../components/context-bar";
import { CaseFocus } from "../../../components/datasets/case-focus";
import { CaseList } from "../../../components/datasets/case-list";
import { CaseIndex } from "../../../components/datasets/case-table";
import { EvaluationFlow } from "../../../components/evaluation/flow-strip";
import { MetricCard } from "../../../components/metric-card";
import { NavigationTransition, PendingRegion, RefreshButton } from "../../../components/navigation-transition";
import { PageHeader } from "../../../components/page-header";
import { ProjectSelector } from "../../../components/project-selector";
import { EmptyState, ErrorState } from "../../../components/states";
import { RULE_LABELS, RULE_TYPES } from "../../../lib/api/review-types";
import { NO_CASE_FILTER, applyCaseFilters, caseFacts, datasetHref, isFiltered, parseCaseFilters, type CaseFilters } from "../../../lib/case-filters";
import { projectContext } from "../../../lib/context-links";
import { formatInt, formatUtcTime } from "../../../lib/format";
import { parseProjectParam, projectHref, projectLinkValue } from "../../../lib/project";
import { loadDatasetCasesPage } from "../../../lib/server/datasets";

export const metadata: Metadata = { title: "Dataset" };

const ID_RE = /^[1-9][0-9]{0,18}$/;

type PageProps = {
  params: Promise<{ datasetId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

/** UI-R4: one filter row (links; the current value is marked with aria-current). */
function FilterRow<V extends string>({ label, values, current, hrefFor, labels }: { label: string; values: readonly V[]; current: V; hrefFor: (v: V) => string; labels: Record<V, string> }) {
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label={label}>
      <span className="text-[11px] font-semibold uppercase tracking-[0.1em] text-ink-3">{label}</span>
      {values.map((v) => (
        <Link
          key={v}
          href={hrefFor(v)}
          aria-current={current === v ? "true" : undefined}
          className="rounded-md border border-line px-2.5 py-1 text-xs text-ink-2 hover:bg-raised hover:text-ink aria-[current=true]:border-accent/60 aria-[current=true]:bg-accent/12 aria-[current=true]:text-ink"
        >
          {labels[v]}
        </Link>
      ))}
    </div>
  );
}

export default async function DatasetPage({ params, searchParams }: PageProps) {
  await connection();
  const { datasetId } = await params;
  const sp = await searchParams;
  const project = parseProjectParam(sp.project);
  const projectLink = projectLinkValue(project);
  const back = projectHref("/datasets", projectLink);
  // Ids are checked by format before any request; an invalid one is neither sent nor echoed.
  const validId = ID_RE.test(datasetId);
  const after = typeof sp.after === "string" && ID_RE.test(sp.after) ? sp.after : null;
  const filters = parseCaseFilters(sp);
  const ctx = validId ? await loadDatasetCasesPage(project, datasetId, after) : null;
  const name = ctx?.cases.ok ? ctx.cases.data.dataset.name : null;
  const crumbs = [{ label: "Evaluate" }, { label: "Datasets", href: back }, { label: name ?? "Dataset" }];
  const href = (f: Partial<CaseFilters>) => datasetHref(datasetId, projectLink, after, { ...filters, ...f });

  return (
    <NavigationTransition>
      {ctx !== null && (
        <ContextBar
          items={[
            { key: "project", label: "Project", value: projectContext(ctx.projectId, ctx.projects) },
            ...(ctx.cases.ok
              ? [
                  { key: "dataset", label: "Dataset", value: ctx.cases.data.dataset.name, title: ctx.cases.data.dataset.name },
                  { key: "cases", label: "Test cases", value: formatInt(ctx.cases.data.dataset.case_count) },
                ]
              : []),
          ]}
        />
      )}
      <Breadcrumbs items={crumbs} />
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
            eyebrow="Evaluate · Dataset"
            title={name !== null ? `Dataset: ${name}` : "Dataset"}
            description="Human-approved test cases, each with its source trace and review, as stored. They are evaluated only by experiments, which you start explicitly."
            meta={<span>Fetched {formatUtcTime(ctx.fetchedAt)}</span>}
            actions={
              <>
                {ctx.cases.ok && ctx.projectId !== null && ctx.cases.data.dataset.case_count > 0 && (
                  // Phase 4D: only opens the setup page with this dataset preselected; nothing runs.
                  <Link
                    href={`/experiments/new?project=${ctx.projectId}&dataset=${ctx.cases.data.dataset.id}`}
                    className="inline-flex items-center rounded-md border border-line bg-raised px-3 py-2 text-sm font-medium text-ink hover:bg-surface"
                    data-testid="dataset-new-experiment"
                  >
                    Set up a mock experiment
                  </Link>
                )}
                <RefreshButton />
              </>
            }
          />
          <EvaluationFlow current="dataset" hrefs={{ trace: projectHref("/traces", projectLink), experiment: projectHref("/experiments", projectLink) }} />
          <ProjectSelector context={ctx} action="/datasets" hidden={[]} />
          <PendingRegion>
            {!ctx.cases.ok ? (
              <ErrorState error={ctx.cases.error} resource="this dataset" />
            ) : ctx.cases.data.items.length === 0 ? (
              <EmptyState title="No test case in this dataset">
                Convert a reviewed trace from its trace page: open the trace, record a review and a decision, then choose “Convert to a regression test case”. Nothing is added
                automatically.
              </EmptyState>
            ) : (
              (() => {
                const all = ctx.cases.data.items;
                const shown = applyCaseFilters(all, filters);
                const facts = caseFacts(all);
                const total = ctx.cases.data.dataset.case_count;
                const paged = ctx.cases.data.next_after !== null || after !== null;
                return (
                  <div className="space-y-6">
                    <p className="text-sm text-ink-2" data-testid="case-count">
                      {total} test case{total === 1 ? "" : "s"}
                      {after !== null ? `, after #${after}` : ""}.
                    </p>
                    <section aria-label="This page in numbers" className="grid grid-cols-2 gap-4 lg:grid-cols-4" data-testid="dataset-facts">
                      <MetricCard id="ds-total" label="Test cases in the dataset" metricId="dataset" value={formatInt(total)} accent="violet" detail={paged ? <p>{formatInt(facts.shown)} on this page</p> : undefined} />
                      <MetricCard
                        id="ds-expected"
                        label="With an expected output"
                        metricId={paged ? "this page" : "dataset"}
                        value={`${formatInt(facts.withExpected)} / ${formatInt(facts.shown)}`}
                        accent="violet"
                        detail={facts.withoutExpected > 0 ? <p className="text-warning-ink">{formatInt(facts.withoutExpected)} never evaluated (no expected output)</p> : <p>Every case can be evaluated</p>}
                      />
                      <MetricCard id="ds-origin" label="From a reviewed trace" metricId={paged ? "this page" : "dataset"} value={`${formatInt(facts.fromTrace)} / ${formatInt(facts.shown)}`} accent="violet" detail={<p>{formatInt(facts.standalone)} standalone</p>} />
                      <MetricCard
                        id="ds-rules"
                        label="Rules"
                        metricId={paged ? "this page" : "dataset"}
                        value={formatInt(RULE_TYPES.filter((r) => facts.byRule[r] > 0).length)}
                        accent="violet"
                        detail={
                          <ul>
                            {RULE_TYPES.filter((r) => facts.byRule[r] > 0).map((r) => (
                              <li key={r}>
                                {RULE_LABELS[r]}: {formatInt(facts.byRule[r])}
                              </li>
                            ))}
                          </ul>
                        }
                      />
                    </section>
                    <section aria-labelledby="case-filter-heading" className="space-y-3 rounded-xl border border-line bg-surface p-4 sm:p-5" data-testid="case-filters">
                      <div className="flex flex-wrap items-baseline justify-between gap-2">
                        <h2 id="case-filter-heading" className="text-[15px] font-semibold text-ink">
                          Filter the cases
                        </h2>
                        <p className="text-xs text-ink-3">{paged ? "Applies to the cases on this page (the cases API has no filter; 50 per page)." : "Applies to every case of the dataset (one page)."}</p>
                      </div>
                      <FilterRow
                        label="Rule"
                        values={["all", ...RULE_TYPES] as const}
                        current={filters.rule}
                        hrefFor={(rule) => href({ rule })}
                        labels={{ all: "All", json_schema: "JSON Schema", field_match: "Field match", exact: "Exact", regex: "Regex" }}
                      />
                      <FilterRow label="Source" values={["all", "trace", "standalone"] as const} current={filters.origin} hrefFor={(origin) => href({ origin })} labels={{ all: "All", trace: "From a reviewed trace", standalone: "Standalone" }} />
                      <FilterRow label="Expected output" values={["all", "yes", "no"] as const} current={filters.expected} hrefFor={(expected) => href({ expected })} labels={{ all: "All", yes: "Set", no: "None (never evaluated)" }} />
                      {isFiltered(filters) && (
                        <p className="text-xs text-ink-2" data-testid="case-filter-result">
                          Showing {formatInt(shown.length)} of {formatInt(all.length)} cases on this page.{" "}
                          <Link href={datasetHref(datasetId, projectLink, after, NO_CASE_FILTER)} className="underline underline-offset-2">
                            Clear filters
                          </Link>
                        </p>
                      )}
                    </section>
                    {shown.length === 0 ? (
                      <EmptyState title="No case matches these filters">Clear a filter to see the other cases of this page.</EmptyState>
                    ) : (
                      <>
                        <section aria-labelledby="case-index-heading" className="min-w-0">
                          <h2 id="case-index-heading" className="mb-2 text-[15px] font-semibold text-ink">
                            Case index
                          </h2>
                          <CaseIndex cases={shown} />
                        </section>
                        <section aria-labelledby="case-cards-heading" className="min-w-0">
                          <h2 id="case-cards-heading" className="mb-2 text-[15px] font-semibold text-ink">
                            Test cases
                          </h2>
                          {ctx.projectId !== null && <CaseList cases={shown} project={ctx.projectId} />}
                          <CaseFocus />
                        </section>
                      </>
                    )}
                    {ctx.cases.data.next_after !== null && (
                      <Link href={datasetHref(datasetId, ctx.projectId, String(ctx.cases.data.next_after), filters)} className="inline-block text-sm underline underline-offset-2" data-testid="cases-next">
                        Next cases →
                      </Link>
                    )}
                  </div>
                );
              })()
            )}
          </PendingRegion>
        </>
      )}
    </NavigationTransition>
  );
}
