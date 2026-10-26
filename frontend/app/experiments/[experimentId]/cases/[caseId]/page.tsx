import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { jsonText } from "../../../../../components/datasets/case-list";
import { codeLabel, Count, Ms, OutcomeLabel, SyntheticBanner, Usd } from "../../../../../components/experiments/values";
import { NavigationTransition, PendingRegion } from "../../../../../components/navigation-transition";
import { PageHeader, SectionHeading } from "../../../../../components/page-header";
import { ErrorState, Notice } from "../../../../../components/states";
import { Ident } from "../../../../../components/traces/trace-values";
import { COST_REASON_LABELS, type ResultRow } from "../../../../../lib/api/experiment-types";
import { RULE_LABELS } from "../../../../../lib/api/review-types";
import { formatIsoNs } from "../../../../../lib/nanos";
import { parseProjectParam, projectHref, projectLinkValue } from "../../../../../lib/project";
import { loadExperimentCase } from "../../../../../lib/server/experiments";

export const metadata: Metadata = { title: "Test case result" };

const ID_RE = /^[1-9][0-9]{0,15}$/;

type PageProps = {
  params: Promise<{ experimentId: string; caseId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

function ResultCard({ row, version, slot }: { row: ResultRow | undefined; version: string; slot: "a" | "b" }) {
  return (
    <section className="min-w-0 rounded-lg border border-line bg-surface px-4 py-4" aria-label={`Result under version ${slot.toUpperCase()}`} data-testid={`case-result-${slot}`}>
      <h3 className="text-sm font-semibold text-ink">
        Version {slot.toUpperCase()} <code className="font-mono text-xs text-ink-2">{version}</code>
      </h3>
      {row === undefined ? (
        <p className="mt-2 text-sm text-ink-3">No result was recorded for this case under this version.</p>
      ) : (
        <dl className="mt-2 grid gap-2 text-sm sm:grid-cols-2">
          <div className="sm:col-span-2">
            <dt className="text-xs text-ink-3">Recorded outcome</dt>
            <dd className="mt-0.5">
              <OutcomeLabel outcome={row.outcome} code={row.detail_code} /> <span className="text-xs text-ink-3">{codeLabel(row.detail_code)}</span>
            </dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="text-xs text-ink-3">Evaluation detail (fixed text; never contains output or expected values)</dt>
            <dd className="mt-0.5 whitespace-pre-wrap break-words font-mono text-xs text-ink-2" data-testid={`case-detail-${slot}`}>
              {row.evaluation_detail ?? <span className="font-sans text-ink-3">None: the rule passed.</span>}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-ink-3">Duration (synthetic)</dt>
            <dd>
              <Ms value={row.duration_ms} />
            </dd>
          </div>
          <div>
            <dt className="text-xs text-ink-3">Tokens in / out (synthetic)</dt>
            <dd>
              <Count value={row.input_tokens} /> / <Count value={row.output_tokens} />
            </dd>
          </div>
          <div>
            <dt className="text-xs text-ink-3">Estimated cost</dt>
            <dd>
              <Usd value={row.cost_usd} />
              {row.cost_unknown_reason !== null && <span className="ml-1 text-xs text-ink-3">({COST_REASON_LABELS[row.cost_unknown_reason]})</span>}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-ink-3">Provenance</dt>
            <dd className="uppercase tracking-wide text-ink" data-testid={`case-provenance-${slot}`}>
              {row.provenance}
            </dd>
          </div>
        </dl>
      )}
    </section>
  );
}

/**
 * Phase 4D: one test case of one experiment. The outcomes come from the report; the case's
 * content and rule from its dataset, as stored (human-typed, redacted by the server). The
 * model's output is never stored, so it is not shown and not reconstructed.
 */
export default async function ExperimentCasePage({ params, searchParams }: PageProps) {
  await connection();
  const { experimentId, caseId } = await params;
  const sp = await searchParams;
  const project = parseProjectParam(sp.project);
  const valid = ID_RE.test(experimentId) && ID_RE.test(caseId);
  const ctx = valid ? await loadExperimentCase(project, experimentId, caseId) : null;
  const back = projectHref(`/experiments/${valid ? experimentId : ""}`, projectLinkValue(project));
  const report = ctx?.report.ok ? ctx.report.data : null;
  const rows = report === null ? [] : report.results.filter((r) => r.test_case_id === Number(caseId));

  return (
    <NavigationTransition>
      <Link href={back} className="mb-4 inline-flex items-center gap-1 text-sm text-ink-2 hover:text-ink">
        <span aria-hidden="true">←</span> Back to the experiment
      </Link>
      <PageHeader title={valid ? `Test case #${caseId} in experiment #${experimentId}` : "Test case result"} description="Recorded outcomes under both prompt versions, with the case's stored metadata." />
      <PendingRegion>
        {ctx === null ? (
          <ErrorState error={{ kind: "invalid_request", fields: ["experiment_id", "test_case_id"] }} resource="this result" />
        ) : !ctx.report.ok ? (
          <ErrorState error={ctx.report.error} resource="this experiment" />
        ) : report === null || rows.length === 0 ? (
          <div data-testid="case-not-in-experiment">
            <ErrorState error={{ kind: "not_found" }} resource="this test case in the experiment" />
          </div>
        ) : (
          <div className="space-y-6">
            <SyntheticBanner mode={report.experiment.mode} />
            <dl className="grid gap-4 rounded-lg border border-line bg-surface px-4 py-4 text-sm sm:grid-cols-3" data-testid="case-identity">
              <div>
                <dt className="text-xs text-ink-3">Test case</dt>
                <dd className="text-ink">#{caseId}</dd>
              </div>
              <div>
                <dt className="text-xs text-ink-3">Dataset</dt>
                <dd className="text-ink">
                  <Link href={`/datasets/${report.experiment.dataset_id}?project=${ctx.projectId}`} className="underline underline-offset-2">
                    {report.experiment.dataset_name}
                  </Link>{" "}
                  <span className="text-ink-3">(#{report.experiment.dataset_id})</span>
                </dd>
              </div>
              <div>
                <dt className="text-xs text-ink-3">Experiment</dt>
                <dd className="text-ink">
                  #{report.experiment.id} · model <code className="font-mono text-xs">{report.experiment.model}</code>
                </dd>
              </div>
            </dl>
            <div className="grid gap-4 lg:grid-cols-2">
              <ResultCard row={rows.find((r) => r.slot === "a")} version={report.experiment.prompt_version_a} slot="a" />
              <ResultCard row={rows.find((r) => r.slot === "b")} version={report.experiment.prompt_version_b} slot="b" />
            </div>
            <Notice tone="info" title="The model output is not stored" testId="no-output-notice">
              Experiments record the outcome and a fixed detail code only; the (synthetic) output itself is never stored, so it cannot be shown or reconstructed here.
            </Notice>
            <section aria-labelledby="case-metadata-heading">
              <SectionHeading id="case-metadata-heading" title="Test case as stored in its dataset" description="Human-entered content after server redaction. The case may have been approved after a trace review." />
              {ctx.testCase === null ? (
                <p className="text-sm text-ink-3">Case metadata was not loaded.</p>
              ) : !ctx.testCase.ok ? (
                <ErrorState error={ctx.testCase.error} resource="the test case" compact />
              ) : ctx.testCase.data === null ? (
                <Notice tone="caution" title="Case metadata unavailable" testId="case-metadata-missing">
                  The case is no longer in the dataset; its recorded results above remain.
                </Notice>
              ) : (
                <dl className="grid gap-3 rounded-lg border border-line bg-surface px-4 py-4 text-sm lg:grid-cols-2" data-testid="case-metadata">
                  <div>
                    <dt className="text-xs text-ink-3">Rule</dt>
                    <dd className="text-ink" data-testid="case-rule">
                      {RULE_LABELS[ctx.testCase.data.rule.type]} (<code className="font-mono text-xs">{ctx.testCase.data.rule.type}</code>, version{" "}
                      {typeof ctx.testCase.data.rule.version === "number" ? ctx.testCase.data.rule.version : <span className="text-ink-3">unversioned (not evaluated)</span>})
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-ink-3">Approval (self-declared)</dt>
                    <dd className="text-ink">
                      {ctx.testCase.data.approved_by}, {formatIsoNs(ctx.testCase.data.approved_at, 0)}
                    </dd>
                  </div>
                  <div className="lg:col-span-2">
                    <dt className="text-xs text-ink-3">Origin</dt>
                    <dd className="text-ink-2" data-testid="case-origin">
                      {ctx.testCase.data.origin_trace_id === null ? (
                        "Standalone case (no source trace)."
                      ) : (
                        <>
                          Trace{" "}
                          <Link href={`/traces/${ctx.testCase.data.origin_trace_id}?project=${ctx.projectId}`} className="underline underline-offset-2">
                            <Ident value={ctx.testCase.data.origin_trace_id} />
                          </Link>
                          {ctx.testCase.data.origin_review_id === null ? " (its review was removed)" : `, review #${ctx.testCase.data.origin_review_id}`}
                        </>
                      )}
                    </dd>
                  </div>
                  <div className="lg:col-span-2">
                    <dt className="text-xs text-ink-3">Input (as stored after redaction)</dt>
                    <dd className="mt-0.5 whitespace-pre-wrap break-words text-ink" data-testid="case-input">
                      {ctx.testCase.data.sanitized_input}
                    </dd>
                  </div>
                  <div className="min-w-0">
                    <dt className="text-xs text-ink-3">Approved expected output</dt>
                    <dd className="mt-0.5" data-testid="case-expected-output">
                      {ctx.testCase.data.expected_output === null ? (
                        <span className="text-xs text-ink-3">None: this case is never evaluated (not evaluated under both versions).</span>
                      ) : (
                        <pre className="max-h-64 overflow-auto rounded-md bg-canvas p-2 font-mono text-xs text-ink-2">{jsonText(ctx.testCase.data.expected_output)}</pre>
                      )}
                    </dd>
                  </div>
                  <div className="min-w-0">
                    <dt className="text-xs text-ink-3">Rule parameters</dt>
                    <dd className="mt-0.5">
                      <pre className="max-h-64 overflow-auto rounded-md bg-canvas p-2 font-mono text-xs text-ink-2">{jsonText(ctx.testCase.data.rule)}</pre>
                    </dd>
                  </div>
                </dl>
              )}
            </section>
          </div>
        )}
      </PendingRegion>
    </NavigationTransition>
  );
}
