import type { ExperimentReport, ResultRow, Variant } from "../../lib/api/experiment-types";
import { formatInt, formatPercent } from "../../lib/format";
import { Count, Ms, VariantCost } from "./values";

/**
 * UI-5: side-by-side version cards and the per-case outcome change (B against baseline A).
 *
 * The change of one test case is a plain reading of its two stored outcomes under the same
 * deterministic rules: no score, no significance, no "better version". Naming a better version
 * stays the backend's decision (ComparisonVerdict). A case is comparable only when both versions
 * stored an evaluated result (passed or failed) for it.
 */

export type CaseChange = "pass_to_fail" | "fail_to_pass" | "same" | "not_comparable";

export const CHANGE_ORDER: CaseChange[] = ["pass_to_fail", "fail_to_pass", "same", "not_comparable"];

export const CHANGE_LABEL: Record<CaseChange, string> = {
  pass_to_fail: "Pass → Fail",
  fail_to_pass: "Fail → Pass",
  same: "Same outcome",
  not_comparable: "Not comparable",
};

export const CHANGE_HELP: Record<CaseChange, string> = {
  pass_to_fail: "Passed under A, failed under B",
  fail_to_pass: "Failed under A, passed under B",
  same: "Both passed or both failed",
  not_comparable: "Missing or not evaluated under at least one version",
};

/** The two fields the change and the cells need; the client table receives nothing else. */
export type CaseOutcome = Pick<ResultRow, "outcome" | "detail_code">;

export function caseChange(a: Pick<ResultRow, "outcome"> | null, b: Pick<ResultRow, "outcome"> | null): CaseChange {
  if (a === null || b === null) return "not_comparable";
  const evaluated = (o: ResultRow["outcome"]) => o === "passed" || o === "failed";
  if (!evaluated(a.outcome) || !evaluated(b.outcome)) return "not_comparable";
  if (a.outcome === b.outcome) return "same";
  return a.outcome === "passed" ? "pass_to_fail" : "fail_to_pass";
}

export function changeCounts(rows: { a: Pick<ResultRow, "outcome"> | null; b: Pick<ResultRow, "outcome"> | null }[]): Record<CaseChange, number> {
  const out: Record<CaseChange, number> = { pass_to_fail: 0, fail_to_pass: 0, same: 0, not_comparable: 0 };
  for (const r of rows) out[caseChange(r.a, r.b)] += 1;
  return out;
}

/** Glyph + tone per change; the text label always accompanies it (never color alone). */
export function ChangeBadge({ change }: { change: CaseChange }) {
  const style: Record<CaseChange, { glyph: string; cls: string }> = {
    pass_to_fail: { glyph: "▼", cls: "border-critical/60 bg-critical/10 text-critical-ink" },
    fail_to_pass: { glyph: "▲", cls: "border-good/50 bg-good/10 text-ink" },
    same: { glyph: "=", cls: "border-line bg-raised text-ink-2" },
    not_comparable: { glyph: "○", cls: "border-line text-ink-3" },
  };
  const s = style[change];
  return (
    <span className={`inline-flex h-6 items-center gap-1.5 whitespace-nowrap rounded-full border px-2 text-xs font-medium ${s.cls}`} title={CHANGE_HELP[change]} data-change={change}>
      <span aria-hidden="true" className={change === "fail_to_pass" ? "text-good" : undefined}>
        {s.glyph}
      </span>
      {CHANGE_LABEL[change]}
    </span>
  );
}

/** Outcome counts of one version as a single stacked bar on its own result count. */
function OutcomeBar({ v }: { v: Variant }) {
  const total = v.results;
  const seg = (n: number, cls: string, label: string) =>
    n > 0 ? <span key={label} className={`swatch block h-full ${cls}`} style={{ width: `${Math.round((n / total) * 10000) / 100}%` }} title={`${formatInt(n)} ${label}`} /> : null;
  return (
    <div>
      <div aria-hidden="true" className="flex h-2 gap-[2px] overflow-hidden rounded-full bg-grid">
        {total > 0 && [seg(v.passed, "bg-series-1", "passed"), seg(v.failed, "bg-critical", "failed"), seg(v.not_evaluated, "bg-unknown", "not evaluated")]}
      </div>
      <p className="tabular mt-1.5 text-xs text-ink-3">
        {formatInt(v.passed)} passed · {formatInt(v.failed)} failed · {formatInt(v.not_evaluated)} not evaluated
      </p>
    </div>
  );
}

function VersionCard({ v, role }: { v: Variant; role: "baseline" | "compared" }) {
  const rate = formatPercent(v.pass_rate.value);
  return (
    <section aria-labelledby={`version-${v.slot}-title`} className="flex min-w-0 flex-col gap-3 rounded-xl border border-line bg-surface p-[18px]" data-testid={`version-card-${v.slot}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-md px-1.5 py-0.5 text-xs font-semibold ${role === "baseline" ? "bg-raised text-ink-2" : "bg-series-1/15 text-ink"}`}>
          {v.slot.toUpperCase()} · {role}
        </span>
        <h3 id={`version-${v.slot}-title`} className="min-w-0 truncate font-mono text-sm text-ink" title={v.prompt_version}>
          {v.prompt_version}
        </h3>
      </div>
      <div>
        <p className="text-xs text-ink-3">Pass rate (passed / evaluated)</p>
        <p className="text-[28px] font-semibold leading-tight tracking-tight text-ink" data-testid={`version-${v.slot}-pass-rate`}>
          {rate ?? <span className="text-lg font-medium text-ink-3">n/a</span>}
          <span className="tabular ml-2 text-sm font-normal text-ink-3">
            {formatInt(v.pass_rate.numerator)} / {formatInt(v.pass_rate.denominator)}
          </span>
        </p>
      </div>
      <OutcomeBar v={v} />
      <dl className="grid grid-cols-2 gap-3 border-t border-line pt-3 text-xs">
        <div className="min-w-0">
          <dt className="text-ink-3">Duration p50</dt>
          <dd className="mt-0.5 text-sm text-ink">
            <Ms value={v.duration_ms.p50} />
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-ink-3">Tokens in / out</dt>
          <dd className="tabular mt-0.5 text-sm text-ink">
            <Count value={v.tokens.input.sum} /> / <Count value={v.tokens.output.sum} />
          </dd>
        </div>
        <div className="col-span-2 min-w-0">
          <dt className="text-ink-3">Cost (estimate)</dt>
          <dd className="mt-0.5 text-sm text-ink">
            <VariantCost cost={v.cost} />
          </dd>
        </div>
      </dl>
    </section>
  );
}

/** A as the baseline, B as the compared version; per-version values exactly as stored. */
export function VersionCards({ report }: { report: ExperimentReport }) {
  const [a, b] = report.variants;
  if (!a || !b) return null;
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2" data-testid="version-cards">
      <VersionCard v={a} role="baseline" />
      <VersionCard v={b} role="compared" />
    </div>
  );
}
