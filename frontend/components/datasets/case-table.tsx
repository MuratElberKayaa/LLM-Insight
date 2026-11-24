import { RULE_LABELS, type TestCase } from "../../lib/api/review-types";
import { hasExpected } from "../../lib/case-filters";
import { formatIsoNs } from "../../lib/nanos";
import { DataTable, type Column } from "../data-table";
import { caseAnchor } from "./case-list";

/**
 * UI-R4: an index of the shown cases (one row each) that jumps to its card below. The cards stay
 * the record (and keep their test ids); this table repeats no content, only what tells cases apart.
 */
export function CaseIndex({ cases }: { cases: TestCase[] }) {
  const columns: Column<TestCase>[] = [
    {
      key: "id",
      header: "Case",
      rowHeader: true,
      render: (c) => (
        <a href={`#${caseAnchor(c.id)}`} className="font-medium text-ink underline-offset-2 hover:underline" data-testid="case-index-link">
          #{c.id}
        </a>
      ),
    },
    { key: "rule", header: "Rule", render: (c) => <span className="text-xs">{RULE_LABELS[c.rule.type]}</span> },
    {
      key: "origin",
      header: "Source",
      render: (c) =>
        c.origin_trace_id === null ? (
          <span className="text-xs text-ink-3">standalone</span>
        ) : (
          <span className="text-xs">
            trace <code className="font-mono">{c.origin_trace_id.slice(0, 8)}…</code>
            {c.origin_review_id !== null ? ` · review #${c.origin_review_id}` : " · review removed"}
          </span>
        ),
    },
    {
      key: "expected",
      header: "Expected output",
      render: (c) =>
        hasExpected(c) ? (
          <span className="text-xs">set</span>
        ) : (
          <span className="text-xs text-warning-ink">
            <span aria-hidden="true">▲ </span>none: never evaluated
          </span>
        ),
    },
    { key: "approved", header: "Approved by", render: (c) => <span className="text-xs">{c.approved_by}</span> },
    { key: "at", header: "Approved (UTC)", render: (c) => <span className="tabular text-xs">{formatIsoNs(c.approved_at, 0)}</span> },
  ];
  return <DataTable caption="Test cases on this page: rule, source, expected output and approval" captionHidden columns={columns} rows={cases} rowKey={(c) => String(c.id)} testId="case-index" />;
}
