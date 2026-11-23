import type { ApiResult } from "../../lib/api/result";
import type { ErrorPattern, ErrorPatterns } from "../../lib/api/error-patterns";
import { formatInt, formatUtcMinute } from "../../lib/format";
import { DataTable, NullValue, type Column } from "../data-table";
import { SectionHeading } from "../page-header";
import { ErrorState } from "../states";

const SOURCE_LABEL = { inference: "Inference", tool: "Tool", agent: "Agent", other: "Other span" } as const;

function subject(p: ErrorPattern) {
  const parts = [p.provider, p.model ?? p.tool_name].filter((x): x is string => x !== null);
  return parts.length === 0 ? <NullValue label="no provider, model or tool recorded">not recorded</NullValue> : <code className="font-mono text-xs">{parts.join(" · ")}</code>;
}

/** Phase 10D (ADR-57): exact fingerprints of ERROR spans; a count of stored rows, not a root cause. */
export function ErrorPatternsSection({ result, project, filtered }: { result: ApiResult<ErrorPatterns> | null; project: string | null; filtered: boolean }) {
  if (result === null) return null;
  if (!result.ok) return <ErrorState error={result.error} resource="the error patterns" />;
  const d = result.data;
  if (d.patterns.length === 0) return null;
  const traceHref = (id: string) => (project === null ? `/traces/${id}` : `/traces/${id}?project=${encodeURIComponent(project)}`);
  const columns: Column<ErrorPattern>[] = [
    { key: "source", header: "Source", rowHeader: true, render: (p) => SOURCE_LABEL[p.source] },
    { key: "subject", header: "Provider · model or tool", render: subject },
    {
      key: "error",
      header: "error.type",
      render: (p) => (
        <span>
          {p.error_type === null ? <NullValue label="no error.type recorded">not recorded</NullValue> : <code className="font-mono text-xs">{p.error_type}</code>}
          <span className="block text-xs text-ink-3">{p.error_class.replaceAll("_", " ")}</span>
        </span>
      ),
    },
    { key: "spans", header: "Error spans", align: "right", render: (p) => formatInt(p.spans) },
    { key: "traces", header: "Traces", align: "right", render: (p) => formatInt(p.traces) },
    { key: "seen", header: "Last seen (UTC)", render: (p) => formatUtcMinute(p.last_seen) },
    {
      key: "latest",
      header: "Newest trace",
      render: (p) => (
        <a className="font-mono text-xs underline underline-offset-2" href={traceHref(p.latest_trace_id)} data-testid="pattern-trace">
          {p.latest_trace_id.slice(0, 12)}…
        </a>
      ),
    },
  ];
  return (
    <section aria-labelledby="patterns-heading" className="mt-8 min-w-0" data-testid="error-patterns">
      <SectionHeading
        id="patterns-heading"
        title="Error patterns"
        description={`${formatInt(d.error_spans)} error span(s) grouped by exact fingerprint (source, provider, model or tool, error.type). A pattern counts stored rows; it is not a root cause.${filtered ? " The provider, model, status and provenance filters do not apply here: all error spans of the window are grouped." : ""}`}
      />
      <DataTable caption="Error patterns, most error spans first" captionHidden columns={columns} rows={d.patterns} rowKey={(p) => [p.source, p.provider, p.model, p.tool_name, p.error_type].join("|")} testId="table-error-patterns" />
      {d.truncated ? <p className="mt-2 text-xs text-ink-3">Only the {formatInt(d.limit)} largest patterns are listed.</p> : null}
    </section>
  );
}
