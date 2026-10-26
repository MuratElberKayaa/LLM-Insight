import Link from "next/link";
import type { ReactNode } from "react";
import type { ExperimentReport, ResultRow, Variant } from "../../lib/api/experiment-types";
import type { TraceProvenance } from "../../lib/api/types";
import { formatInt } from "../../lib/format";
import { formatIsoNs } from "../../lib/nanos";
import { ChartCard, LegendItem } from "../chart-card";
import { OutcomeChart, type OutcomePoint } from "../charts/outcome-chart";
import { NullValue } from "../data-table";
import { SectionHeading } from "../page-header";
import { ProvenanceBadge } from "../provenance-badge";
import { Notice } from "../states";
import { codeLabel, Count, Ms, OutcomeLabel, RateText, Usd, VariantCost } from "./values";

/**
 * Phase 4D: one stored experiment report (`llminsight.experiment_report.v1`), as served. The
 * page names a better version only when the backend did (complete evidence on paired cases),
 * shows a cost difference only when both versions are fully priced, and shows latency per
 * version only. Everything here is SYNTHETIC for a mock experiment and labelled so.
 */

const PROVENANCE: Record<ExperimentReport["provenance"]["label"], TraceProvenance> = { none: "unknown", synthetic: "synthetic", real: "real", mixed: "mixed" };

/** The backend's reason (fixed text, lower case, no period) as a sentence. */
function sentence(reason: string | null): string {
  return reason === null ? "" : `${reason.charAt(0).toUpperCase()}${reason.slice(1)}.`;
}

function versionName(report: ExperimentReport, slot: "a" | "b"): string {
  return slot === "a" ? report.experiment.prompt_version_a : report.experiment.prompt_version_b;
}

function Item({ label, children, testId }: { label: string; children: ReactNode; testId?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-ink-3">{label}</dt>
      <dd className="mt-0.5 break-words text-sm text-ink" data-testid={testId}>
        {children}
      </dd>
    </div>
  );
}

export function ReportSummary({ report, project }: { report: ExperimentReport; project: string }) {
  const e = report.experiment;
  return (
    <dl className="grid gap-4 rounded-lg border border-line bg-surface px-4 py-4 sm:grid-cols-2 xl:grid-cols-4" data-testid="experiment-summary">
      <Item label="Experiment" testId="summary-experiment-id">
        #{e.id}
      </Item>
      <Item label="Project">#{project}</Item>
      <Item label="Dataset" testId="summary-dataset">
        <Link href={`/datasets/${e.dataset_id}?project=${project}`} className="underline underline-offset-2">
          {e.dataset_name}
        </Link>{" "}
        <span className="text-ink-3">(#{e.dataset_id})</span>
      </Item>
      <Item label="Model (identity and price only)" testId="summary-model">
        <code className="font-mono text-xs">{e.model}</code>
      </Item>
      <Item label="Version A" testId="summary-version-a">
        <code className="font-mono text-xs">{e.prompt_version_a}</code>
      </Item>
      <Item label="Version B" testId="summary-version-b">
        <code className="font-mono text-xs">{e.prompt_version_b}</code>
      </Item>
      <Item label="Execution mode" testId="summary-mode">
        <span className="inline-flex flex-wrap items-center gap-2">
          <span className="rounded border border-synthetic/60 px-1.5 py-0.5 text-xs font-semibold uppercase tracking-wide text-ink">{e.mode === "mock" ? "Mock" : "Real"}</span>
          {report.provenance.simulated_provider && <span className="text-xs text-ink-3">simulated provider</span>}
        </span>
      </Item>
      <Item label="Result provenance" testId="summary-provenance">
        <ProvenanceBadge provenance={PROVENANCE[report.provenance.label]} count={formatInt(report.provenance.synthetic + report.provenance.real)} emphasis={report.provenance.label !== "real"} />
        <span className="ml-2 text-xs text-ink-3">
          {formatInt(report.provenance.synthetic)} synthetic, {formatInt(report.provenance.real)} real
        </span>
      </Item>
      <Item label="Started (UTC)">{formatIsoNs(e.started_at, 6)}</Item>
      <Item label="Finished (UTC)" testId="summary-state">
        {e.finished_at === null ? <span className="text-ink-2">Not finished (running or interrupted)</span> : formatIsoNs(e.finished_at, 6)}
      </Item>
      <Item label="Budget">{e.budget_usd === null ? <span className="text-ink-3">None given (optional for mock runs)</span> : <Usd value={e.budget_usd} />}</Item>
      <Item label="Evaluation">
        Deterministic rules (<code className="font-mono text-xs">{report.evaluation.rule_contract}</code>); no language model judges anything.
      </Item>
    </dl>
  );
}

/** Everything that limits what the report can say, stated before any number. */
export function reportWarnings(report: ExperimentReport): string[] {
  const out: string[] = [];
  const { quality, cost } = report.comparison;
  if (report.experiment.state !== "finished") out.push("The experiment has not finished, so nothing is compared.");
  if (report.results.length === 0) out.push("No result is stored for this experiment.");
  if (!report.cases.ids_identical_across_versions) out.push("The two versions did not run on identical test case ids, so their quality is not compared.");
  if (quality.status === "partial" && quality.reason !== null) out.push(`Quality not compared: ${quality.reason}.`);
  if (cost.status === "incomplete" && cost.reason !== null) out.push(`Cost not compared: ${cost.reason}.`);
  for (const v of report.variants) {
    if (v.not_evaluated > 0) {
      const codes = Object.entries(v.not_evaluated_by_code)
        .map(([c, n]) => `${n} ${c}`)
        .join(", ");
      out.push(`Version ${v.slot.toUpperCase()} (${v.prompt_version}): ${v.not_evaluated} of ${v.results} results not evaluated (${codes}); they are excluded from its pass rate.`);
    }
    if (v.duration_ms.n > 0 && v.duration_ms.p95_low_sample) out.push(`Version ${v.slot.toUpperCase()}: only ${v.duration_ms.n} recorded durations; its p95 is unreliable (fewer than 20).`);
  }
  return out;
}

export function ReportWarnings({ report }: { report: ExperimentReport }) {
  const warnings = reportWarnings(report);
  if (warnings.length === 0) return null;
  return (
    <Notice tone="caution" title="Incomplete or limited evidence" testId="experiment-warnings">
      <ul className="list-disc space-y-1 pl-5">
        {warnings.map((w) => (
          <li key={w}>{w}</li>
        ))}
      </ul>
    </Notice>
  );
}

export function ComparisonVerdict({ report }: { report: ExperimentReport }) {
  const { quality, cost } = report.comparison;
  const winner = quality.higher_pass_rate;
  return (
    <section aria-labelledby="verdict-heading" className="rounded-lg border border-line bg-surface px-4 py-4">
      <SectionHeading id="verdict-heading" title="What can be compared" description="As decided by the backend from the stored results; the dashboard adds no judgement." />
      <dl className="grid gap-4 lg:grid-cols-3">
        <Item label="Quality (deterministic rules)" testId="quality-verdict">
          <span data-status={quality.status} data-higher={winner ?? "none"}>
            {quality.status === "complete" ? (
              <>
                On the {formatInt(quality.paired_cases)} cases evaluated under both versions, A passed {formatInt(quality.a_passed_on_paired)} and B passed{" "}
                {formatInt(quality.b_passed_on_paired)}:{" "}
                <strong className="font-semibold">{winner === "equal" ? "both passed the same number" : `version ${winner?.toUpperCase()} (${versionName(report, winner === "a" ? "a" : "b")}) passed more`}</strong>.
                {" "}
                <span className="text-ink-3">Synthetic outputs; no significance test.</span>
              </>
            ) : (
              <>
                <strong className="font-semibold">No better version is named.</strong> <span className="text-ink-2">{sentence(quality.reason)}</span>
              </>
            )}
          </span>
          <span className="mt-1 block text-xs text-ink-3">
            {formatInt(quality.paired_cases)} paired · {formatInt(quality.excluded_by_design)} excluded by design (not evaluable under either version) · {formatInt(quality.unpaired_cases)} evaluated under one version only
          </span>
        </Item>
        <Item label="Cost (synthetic estimate)" testId="cost-verdict">
          <span data-status={cost.status}>
            {cost.status === "complete" ? (
              <>
                B − A = <Usd value={cost.difference_usd_b_minus_a} /> <span className="text-ink-3">(both versions fully priced; nothing was spent)</span>
              </>
            ) : (
              <>
                <strong className="font-semibold">Not compared.</strong> <span className="text-ink-2">{sentence(cost.reason)}</span>
              </>
            )}
          </span>
        </Item>
        <Item label="Latency" testId="latency-verdict">
          Reported per version only; no difference is claimed. <span className="text-ink-3">Durations are scripted synthetic values, not measurements.</span>
        </Item>
      </dl>
    </section>
  );
}

function notEvaluatedCodes(v: Variant): string {
  const parts = Object.entries(v.not_evaluated_by_code).map(([c, n]) => `${n} ${c}`);
  return parts.length === 0 ? "" : parts.join(", ");
}

export function ComparisonTable({ report }: { report: ExperimentReport }) {
  const [a, b] = report.variants;
  const rows: { key: string; label: ReactNode; cell: (v: Variant) => ReactNode }[] = [
    { key: "results", label: "Results stored", cell: (v) => formatInt(v.results) },
    { key: "passed", label: "Passed", cell: (v) => formatInt(v.passed) },
    { key: "failed", label: "Failed", cell: (v) => formatInt(v.failed) },
    {
      key: "not_evaluated",
      label: "Not evaluated (NULL)",
      cell: (v) => (
        <>
          {formatInt(v.not_evaluated)}
          {v.not_evaluated > 0 && <span className="block text-xs text-ink-3">{notEvaluatedCodes(v)}</span>}
        </>
      ),
    },
    { key: "pass_rate", label: "Pass rate = passed / (passed + failed)", cell: (v) => <RateText rate={v.pass_rate} /> },
    {
      key: "p50",
      label: "Duration p50 (synthetic)",
      cell: (v) => <Ms value={v.duration_ms.p50} />,
    },
    {
      key: "p95",
      label: "Duration p95 (synthetic)",
      cell: (v) => (
        <>
          <Ms value={v.duration_ms.p95} />
          <span className="block text-xs text-ink-3">
            n = {formatInt(v.duration_ms.n)}
            {v.duration_ms.n > 0 && v.duration_ms.p95_low_sample ? " · fewer than 20: unreliable" : ""}
          </span>
        </>
      ),
    },
    {
      key: "input_tokens",
      label: "Input tokens (synthetic)",
      cell: (v) => (
        <>
          <Count value={v.tokens.input.sum} /> <span className="text-xs text-ink-3">n = {formatInt(v.tokens.input.n)}</span>
        </>
      ),
    },
    {
      key: "output_tokens",
      label: "Output tokens (synthetic)",
      cell: (v) => (
        <>
          <Count value={v.tokens.output.sum} /> <span className="text-xs text-ink-3">n = {formatInt(v.tokens.output.n)}</span>
        </>
      ),
    },
    { key: "cost", label: "Estimated cost", cell: (v) => <VariantCost cost={v.cost} testId={`cost-${v.slot}`} /> },
  ];
  return (
    <div className="relative overflow-x-auto rounded-lg border border-line" role="region" aria-label="Comparison of the two prompt versions" tabIndex={0} data-testid="comparison-table">
      <table className="w-full min-w-[40rem] border-collapse text-sm">
        <caption className="px-3 py-2 text-left text-xs text-ink-3">Per-version results as stored; unknown values are shown as unknown, never as 0.</caption>
        <thead className="bg-raised">
          <tr>
            <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-ink-2">
              Measure
            </th>
            {[a, b].map((v) => (
              <th key={v.slot} scope="col" className="px-3 py-2 text-left text-xs font-medium text-ink-2">
                Version {v.slot.toUpperCase()} <code className="font-mono text-ink">{v.prompt_version}</code>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="border-t border-line" data-row={r.key}>
              <th scope="row" className="px-3 py-2 text-left align-top text-xs font-medium text-ink-2">
                {r.label}
              </th>
              <td className="px-3 py-2 align-top text-ink" data-slot="a">
                {r.cell(a)}
              </td>
              <td className="px-3 py-2 align-top text-ink" data-slot="b">
                {r.cell(b)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The chart is shown only for counts that compare like with like (see OutcomeChart). */
export function chartable(report: ExperimentReport): boolean {
  return report.experiment.state === "finished" && report.cases.ids_identical_across_versions && report.results.length > 0;
}

export function OutcomeSection({ report }: { report: ExperimentReport }) {
  if (!chartable(report)) {
    return (
      <p className="text-sm text-ink-3" data-testid="outcome-chart-omitted">
        No outcome chart: the versions did not complete runs on identical test cases, so their counts are not comparable.
      </p>
    );
  }
  const points: OutcomePoint[] = report.variants.map((v) => ({ key: v.slot, label: `${v.slot.toUpperCase()} · ${v.prompt_version}`, passed: v.passed, failed: v.failed, not_evaluated: v.not_evaluated }));
  return (
    <ChartCard
      id="outcomes"
      title="Outcomes per prompt version"
      description={`Counts on the same ${formatInt(report.cases.count)} test cases. Not evaluated results are excluded from the pass rate.`}
      legend={
        <div className="flex flex-wrap gap-3">
          <LegendItem swatchClass="bg-series-1" label="Passed" />
          <LegendItem swatchClass="bg-critical" label="Failed ✕" />
          <LegendItem swatchClass="bg-unknown" label="Not evaluated ○" />
        </div>
      }
      table={
        <table className="w-full text-left text-xs" data-testid="outcome-table">
          <caption className="sr-only">Outcome counts per prompt version</caption>
          <thead className="text-ink-3">
            <tr>
              <th scope="col" className="py-1 pr-3 font-medium">
                Version
              </th>
              <th scope="col" className="py-1 pr-3 font-medium">
                Passed
              </th>
              <th scope="col" className="py-1 pr-3 font-medium">
                Failed
              </th>
              <th scope="col" className="py-1 font-medium">
                Not evaluated
              </th>
            </tr>
          </thead>
          <tbody>
            {points.map((p) => (
              <tr key={p.key} className="border-t border-line">
                <th scope="row" className="py-1 pr-3 font-medium text-ink">
                  {p.label}
                </th>
                <td className="tabular py-1 pr-3">{p.passed}</td>
                <td className="tabular py-1 pr-3">{p.failed}</td>
                <td className="tabular py-1">{p.not_evaluated}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      <OutcomeChart points={points} />
    </ChartCard>
  );
}

/** Rows of the per-case table: one per test case id, with its result under A and under B. */
export function caseRows(report: ExperimentReport): { id: number; a: ResultRow | null; b: ResultRow | null }[] {
  const byCase = new Map<number, { a: ResultRow | null; b: ResultRow | null }>();
  for (const r of report.results) {
    const entry = byCase.get(r.test_case_id) ?? { a: null, b: null };
    entry[r.slot] = r;
    byCase.set(r.test_case_id, entry);
  }
  return [...byCase.entries()].sort(([x], [y]) => x - y).map(([id, v]) => ({ id, ...v }));
}

function ResultCell({ row }: { row: ResultRow | null }) {
  if (row === null) return <NullValue label="no result under this version">no result</NullValue>;
  return (
    <span className="inline-flex flex-col gap-0.5">
      <OutcomeLabel outcome={row.outcome} code={row.detail_code} />
      {row.outcome !== "passed" && <span className="text-xs text-ink-3">{codeLabel(row.detail_code)}</span>}
    </span>
  );
}

export function CaseResultsTable({ report, project }: { report: ExperimentReport; project: string }) {
  const rows = caseRows(report);
  return (
    <div className="relative overflow-x-auto rounded-lg border border-line" role="region" aria-label="Results per test case" tabIndex={0} data-testid="case-results-table">
      <table className="w-full min-w-[36rem] border-collapse text-sm">
        <caption className="px-3 py-2 text-left text-xs text-ink-3">
          {formatInt(rows.length)} test case ids evaluated; {report.cases.ids_identical_across_versions ? "identical under both versions" : "NOT identical under both versions"}. Open a case for its stored
          metadata and evaluation details.
        </caption>
        <thead className="bg-raised">
          <tr>
            <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-ink-2">
              Test case
            </th>
            <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-ink-2">
              Version A · {report.experiment.prompt_version_a}
            </th>
            <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-ink-2">
              Version B · {report.experiment.prompt_version_b}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-t border-line" data-testid="case-result-row" data-case-id={r.id}>
              <th scope="row" className="px-3 py-2 text-left align-top font-medium">
                <Link href={`/experiments/${report.experiment.id}/cases/${r.id}?project=${project}`} className="underline underline-offset-2" data-testid="case-result-link">
                  #{r.id}
                </Link>
              </th>
              <td className="px-3 py-2 align-top" data-slot="a">
                <ResultCell row={r.a} />
              </td>
              <td className="px-3 py-2 align-top" data-slot="b">
                <ResultCell row={r.b} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Limitations({ report }: { report: ExperimentReport }) {
  return (
    <section aria-labelledby="limitations-heading">
      <SectionHeading id="limitations-heading" title="Limitations (from the backend)" />
      <ul className="list-disc space-y-1 pl-5 text-sm text-ink-2" data-testid="experiment-limitations">
        {report.limitations.map((l) => (
          <li key={l.code}>{l.text}</li>
        ))}
      </ul>
    </section>
  );
}
