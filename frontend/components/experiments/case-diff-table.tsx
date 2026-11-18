"use client";

import Link from "next/link";
import { useState } from "react";
import { formatInt } from "../../lib/format";
import { NullValue } from "../data-table";
import { CHANGE_LABEL, CHANGE_ORDER, ChangeBadge, caseChange, changeCounts, type CaseChange, type CaseOutcome } from "./experiment-diff";
import { codeLabel, OutcomeLabel } from "./values";

export interface CaseDiffRow {
  id: number;
  a: CaseOutcome | null;
  b: CaseOutcome | null;
}

type Filter = "all" | CaseChange;

function ResultCell({ row }: { row: CaseOutcome | null }) {
  if (row === null) return <NullValue label="no result under this version">no result</NullValue>;
  return (
    <span className="inline-flex flex-col gap-0.5">
      <OutcomeLabel outcome={row.outcome} code={row.detail_code} />
      {row.outcome !== "passed" && <span className="text-xs text-ink-3">{codeLabel(row.detail_code)}</span>}
    </span>
  );
}

/**
 * Results per test case with the outcome change from A to B. The filter only hides rows on
 * this page; every case stays one click away under "All" (the default).
 */
export function CaseDiffTable({
  rows,
  experimentId,
  project,
  versionA,
  versionB,
  identical,
  initialFilter = "all",
}: {
  rows: CaseDiffRow[];
  experimentId: number;
  project: string;
  versionA: string;
  versionB: string;
  identical: boolean;
  initialFilter?: Filter;
}) {
  const [filter, setFilter] = useState<Filter>(initialFilter);
  const counts = changeCounts(rows);
  const shown = filter === "all" ? rows : rows.filter((r) => caseChange(r.a, r.b) === filter);
  const chip = (f: Filter, label: string, n: number) => (
    <button
      key={f}
      type="button"
      aria-pressed={filter === f}
      onClick={() => setFilter(f)}
      disabled={f !== "all" && n === 0}
      data-filter={f}
      className="inline-flex h-8 items-center gap-2 rounded-lg border border-line bg-surface px-3 text-xs font-medium text-ink-2 hover:text-ink disabled:cursor-not-allowed disabled:opacity-50 aria-pressed:border-series-1/60 aria-pressed:bg-series-1/10 aria-pressed:text-ink"
    >
      {label}
      <span className="tabular rounded-full bg-raised px-1.5 text-ink">{formatInt(n)}</span>
    </button>
  );

  return (
    <div className="space-y-3">
      <div role="group" aria-label="Filter test cases by outcome change" className="flex flex-wrap gap-2" data-testid="case-diff-filters">
        {chip("all", "All cases", rows.length)}
        {CHANGE_ORDER.map((c) => chip(c, CHANGE_LABEL[c], counts[c]))}
      </div>
      <div className="relative overflow-x-auto rounded-xl border border-line" role="region" aria-label="Results per test case" tabIndex={0} data-testid="case-results-table">
        <table className="w-full min-w-[40rem] border-collapse text-sm">
          <caption className="px-3 py-2 text-left text-xs text-ink-3">
            {formatInt(rows.length)} test case ids evaluated; {identical ? "identical under both versions" : "NOT identical under both versions"}. Change is B against baseline A under the same
            deterministic rules; it is not a significance test. {filter !== "all" && `Showing ${formatInt(shown.length)} of ${formatInt(rows.length)}.`} Open a case for its stored metadata and evaluation
            details.
          </caption>
          <thead className="bg-raised">
            <tr>
              <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-ink-2">
                Test case
              </th>
              <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-ink-2">
                Change (B vs A)
              </th>
              <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-ink-2">
                Version A · {versionA}
              </th>
              <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-ink-2">
                Version B · {versionB}
              </th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const change = caseChange(r.a, r.b);
              return (
                <tr key={r.id} className={`border-t border-line ${change === "pass_to_fail" ? "bg-critical/5" : ""}`} data-testid="case-result-row" data-case-id={r.id} data-change={change}>
                  <th scope="row" className="px-3 py-2 text-left align-top font-medium">
                    <Link href={`/experiments/${experimentId}/cases/${r.id}?project=${project}`} className="underline underline-offset-2" data-testid="case-result-link">
                      #{r.id}
                    </Link>
                  </th>
                  <td className="px-3 py-2 align-top">
                    <ChangeBadge change={change} />
                  </td>
                  <td className="px-3 py-2 align-top" data-slot="a">
                    <ResultCell row={r.a} />
                  </td>
                  <td className="px-3 py-2 align-top" data-slot="b">
                    <ResultCell row={r.b} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
