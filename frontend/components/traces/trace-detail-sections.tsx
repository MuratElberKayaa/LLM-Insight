import type { ReactNode } from "react";
import type { TraceDetail, TraceSummary } from "../../lib/api/trace-types";
import type { TraceProvenance } from "../../lib/api/types";
import { formatInt } from "../../lib/format";
import { formatDurationNs, formatExactMs, formatIsoNs } from "../../lib/nanos";
import { PROVENANCE_DESCRIPTION } from "../../lib/provenance";
import { CALL_OUTCOME_LABEL, COST_REASON_LABEL, PLACEMENT_LABEL, type CallView, type SpanView, type TraceView } from "../../lib/span-tree";
import { DataTable, NullValue, type Column } from "../data-table";
import { SectionHeading } from "../page-header";
import { ProvenanceBadge } from "../provenance-badge";
import { Notice } from "../states";
import { Ident, SpanStatusLabel, TokenSummary, Tokens, TraceCost, TraceStatusLabel } from "./trace-values";

/** Rows of the accessible span table rendered at once; the waterfall pages through the rest. */
export const SPAN_TABLE_LIMIT = 500;

const PROVENANCE_NOTICE: Record<TraceProvenance, { title: string; body: string }> = {
  synthetic: { title: "Synthetic trace", body: "Every inference span is labeled synthetic (mock provider or offline fixtures). Nothing here describes real provider traffic." },
  mixed: { title: "Mixed provenance", body: "The inference spans disagree (synthetic, real or unlabeled). This trace is never counted as real." },
  unknown: { title: "Provenance unknown", body: "No inference span carries a provenance claim the server accepted, or the trace has no inference spans. Never counted as real." },
  real: { title: "Labeled real by the instrumentation", body: "The instrumentation labeled every inference span real. That label is asserted, not verified by the server." },
};

export function ProvenanceNotice({ provenance }: { provenance: TraceProvenance }) {
  const n = PROVENANCE_NOTICE[provenance];
  return (
    <div
      role="note"
      data-testid="trace-provenance-notice"
      data-provenance={provenance}
      className={`mb-5 rounded-lg border px-4 py-3 ${provenance === "real" ? "border-line bg-surface" : "border-synthetic/60 bg-synthetic/10"}`}
    >
      <p className="flex flex-wrap items-center gap-2 text-sm font-semibold text-ink">
        {provenance === "synthetic" && <span className="rounded bg-synthetic px-1.5 py-0.5 text-[11px] font-bold uppercase tracking-wide text-white">Synthetic</span>}
        <ProvenanceBadge provenance={provenance} />
        {n.title}
      </p>
      <p className="mt-1 text-sm text-ink-2">{n.body}</p>
      <p className="mt-1 text-xs text-ink-3">{PROVENANCE_DESCRIPTION[provenance]} Provenance is asserted by the instrumentation and not verified by the server.</p>
    </div>
  );
}

function Fact({ label, children, testId }: { label: string; children: ReactNode; testId?: string }) {
  return (
    <div className="min-w-0 rounded-lg border border-line bg-surface px-4 py-3">
      <dt className="text-xs text-ink-3">{label}</dt>
      <dd className="mt-1 break-words text-sm text-ink" data-testid={testId}>
        {children}
      </dd>
    </div>
  );
}

export function TraceFacts({ trace, returnedSpans, spansTruncated }: { trace: TraceSummary; returnedSpans: number; spansTruncated: boolean }) {
  return (
    <dl className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4" data-testid="trace-facts">
      <Fact label="Status (stored)" testId="fact-status">
        <TraceStatusLabel status={trace.status} />
      </Fact>
      <Fact label="Service" testId="fact-service">
        {trace.service_name}
      </Fact>
      <Fact label="Duration" testId="fact-duration">
        <span title={`${trace.duration_ns} ns · ${formatExactMs(BigInt(trace.duration_ns))}`}>{formatDurationNs(BigInt(trace.duration_ns))}</span>
      </Fact>
      <Fact label="Spans" testId="fact-spans">
        {formatInt(trace.span_count)}
        {spansTruncated && <span className="block text-xs text-ink-3">first {formatInt(returnedSpans)} returned</span>}
      </Fact>
      <Fact label="Started (UTC)" testId="fact-started">
        <span className="tabular" data-ns={trace.start_time_unix_nano}>
          {formatIsoNs(trace.started_at)}
        </span>
      </Fact>
      <Fact label="Ended (UTC)" testId="fact-ended">
        <span className="tabular">{formatIsoNs(trace.ended_at)}</span>
      </Fact>
      <Fact label="Inference calls · tokens in / out" testId="fact-tokens">
        <span className="block">{formatInt(trace.usage.inference_calls)} attempts</span>
        <TokenSummary usage={trace.usage} />
      </Fact>
      <Fact label="Known cost" testId="fact-cost">
        <TraceCost usage={trace.usage} />
      </Fact>
      <Fact label="Prompt version" testId="fact-prompt">
        {trace.prompt_version === null ? <NullValue label="none, or the inference spans disagree" /> : <code className="font-mono text-xs">{trace.prompt_version}</code>}
      </Fact>
      <Fact label="Provenance" testId="fact-provenance">
        <ProvenanceBadge provenance={trace.provenance} emphasis={trace.provenance === "synthetic"} />
      </Fact>
    </dl>
  );
}

/**
 * Trace-level status vs individual span failures vs recovery, from stored data only. The
 * trace status is the backend's (`error` if any span is ERROR) and is not reinterpreted.
 */
export function StatusBreakdown({ trace, view }: { trace: TraceSummary; view: TraceView }) {
  const c = view.counts;
  const recovered = view.calls.filter((x) => x.outcome === "recovered").length;
  const allFailed = view.calls.filter((x) => x.outcome === "all_failed" || x.outcome === "last_failed").length;
  return (
    <section aria-labelledby="status-heading" className="mb-6 rounded-lg border border-line bg-surface px-4 py-4" data-testid="status-breakdown">
      <h2 id="status-heading" className="text-base font-semibold text-ink">
        Status breakdown
      </h2>
      <ul className="mt-2 space-y-1.5 text-sm text-ink-2">
        <li data-testid="status-trace">
          <span className="font-medium text-ink">Trace level:</span> {trace.status === "error" ? "Error" : "OK"}. The backend marks a trace as error when any of its spans has status ERROR.
        </li>
        <li data-testid="status-spans">
          <span className="font-medium text-ink">Span failures:</span>{" "}
          {c.errorSpans === 0
            ? "no span has status ERROR."
            : `${formatInt(c.errorSpans)} span${c.errorSpans === 1 ? "" : "s"} with status ERROR (${formatInt(c.failedInference)} inference attempt${c.failedInference === 1 ? "" : "s"}, ${formatInt(c.failedApplication)} application span${c.failedApplication === 1 ? "" : "s"}).`}
        </li>
        <li data-testid="status-calls">
          <span className="font-medium text-ink">Logical calls:</span>{" "}
          {view.calls.length === 0
            ? "no inference attempt in this trace."
            : `${formatInt(view.calls.length)} (${formatInt(c.inferenceSpans)} physical attempt${c.inferenceSpans === 1 ? "" : "s"}); ${formatInt(recovered)} recovered after a failed attempt, ${formatInt(allFailed)} ended with a failed attempt.`}
        </li>
      </ul>
      <p className="mt-2 text-xs text-ink-3">
        Recorded <span className="font-mono">error.type</span> values are shown as reported by the instrumentation. A failed span is a fact; its cause is not inferred here.
      </p>
    </section>
  );
}

const outcomeTone = (o: CallView["outcome"]) => (o === "recovered" ? "text-ink" : o === "all_failed" || o === "last_failed" ? "text-critical-ink" : "text-ink-2");

export function LogicalCalls({ view }: { view: TraceView }) {
  if (view.calls.length === 0) return null;
  const columns: Column<CallView>[] = [
    {
      key: "call",
      header: "Logical call",
      rowHeader: true,
      render: (c) => (c.callId === null ? <NullValue label="no llminsight.call_id: attempts cannot be grouped">ungrouped attempt</NullValue> : <Ident value={c.callId} />),
    },
    {
      key: "attempts",
      header: "Physical attempts (in order)",
      render: (c) => (
        <ol className="space-y-0.5">
          {c.attempts.map((a) => (
            <li key={a.spanId} className="flex flex-wrap items-center gap-2" data-attempt-span={a.spanId}>
              <span className="tabular text-ink-3">#{a.attemptId ?? "?"}</span>
              {a.status === null ? <NullValue label="span not in this response">status unavailable</NullValue> : <SpanStatusLabel status={a.status} />}
              {a.errorType !== null && <code className="font-mono text-xs text-ink-2">error.type {a.errorType}</code>}
              {a.durationLabel !== null && <span className="tabular text-ink-3">{a.durationLabel}</span>}
            </li>
          ))}
        </ol>
      ),
    },
    {
      key: "outcome",
      header: "Outcome of the attempts",
      render: (c) => (
        <span className={outcomeTone(c.outcome)} data-outcome={c.outcome}>
          {CALL_OUTCOME_LABEL[c.outcome]}
          {c.outcome === "recovered" && (
            <span className="block text-xs text-ink-3">
              last attempt without error, {formatInt(c.failed)} failed before it
            </span>
          )}
        </span>
      ),
    },
  ];
  return (
    <section aria-labelledby="calls-heading" className="mt-8 min-w-0">
      <SectionHeading
        id="calls-heading"
        title="Logical calls and retries"
        description="Attempts sharing llminsight.call_id are one logical call; each attempt is a separate physical provider request with its own span."
      />
      <DataTable caption="Logical calls with their physical attempts" captionHidden columns={columns} rows={view.calls} rowKey={(c) => `${c.callId ?? "none"}:${c.attempts[0]?.spanId ?? ""}`} testId="table-calls" />
    </section>
  );
}

export function AttemptsTable({ view, usageTruncated }: { view: TraceView; usageTruncated: boolean }) {
  const rows = view.rows.filter((r) => r.inference !== null);
  if (rows.length === 0) return null;
  const columns: Column<SpanView>[] = [
    { key: "span", header: "Span", rowHeader: true, render: (r) => <Ident value={r.spanId} /> },
    { key: "attempt", header: "Attempt", align: "right", render: (r) => r.inference?.attemptId ?? <NullValue /> },
    { key: "status", header: "Status", render: (r) => <SpanStatusLabel status={r.status} /> },
    { key: "provider", header: "Provider", render: (r) => <code className="font-mono text-xs">{r.inference!.provider}</code> },
    {
      key: "model",
      header: (
        <>
          Requested model
          <span className="block font-normal text-ink-3">→ response model</span>
        </>
      ),
      render: (r) => (
        <span className="block">
          <span className="block">{r.inference!.requestedModel === null ? <NullValue /> : <code className="font-mono text-xs">{r.inference!.requestedModel}</code>}</span>
          <span className="block text-ink-3">
            <span aria-hidden="true">→ </span>
            <span className="sr-only">response model: </span>
            {r.inference!.responseModel === null ? <NullValue /> : <code className="font-mono text-xs">{r.inference!.responseModel}</code>}
          </span>
        </span>
      ),
    },
    {
      key: "tokens",
      header: "Tokens in / out",
      align: "right",
      render: (r) => (
        <>
          <Tokens value={r.inference!.inputTokens} /> <span className="text-ink-3">/</span> <Tokens value={r.inference!.outputTokens} />
        </>
      ),
    },
    {
      key: "subsets",
      header: (
        <>
          Cached / reasoning
          <span className="block font-normal text-ink-3">subsets, not added</span>
        </>
      ),
      align: "right",
      render: (r) => (
        <>
          <Tokens value={r.inference!.cachedInputTokens} /> <span className="text-ink-3">/</span> <Tokens value={r.inference!.reasoningOutputTokens} />
        </>
      ),
    },
    {
      key: "cost",
      header: "Cost (USD)",
      align: "right",
      render: (r) => {
        const cost = r.inference!.cost;
        return cost.kind === "computed" ? (
          <span data-cost-state="computed" title={`Exact: ${cost.exact}`}>
            {cost.exact}
          </span>
        ) : (
          <span data-cost-state="unknown" className="text-ink-3">
            unknown <span className="block text-xs">{COST_REASON_LABEL[cost.reason]}</span>
          </span>
        );
      },
    },
    { key: "pv", header: "Price version", render: (r) => (r.inference!.priceVersion === null ? <NullValue label="no price applied">—</NullValue> : <code className="font-mono text-xs">{r.inference!.priceVersion}</code>) },
    { key: "prov", header: "Provenance", render: (r) => <ProvenanceBadge provenance={r.inference!.provenance} emphasis={r.inference!.provenance === "synthetic"} /> },
  ];
  return (
    <section aria-labelledby="attempts-heading" className="mt-8 min-w-0">
      <SectionHeading id="attempts-heading" title="Inference attempts" description="One usage-ledger row per physical attempt. Unknown values stay unknown; unknown cost is never shown as $0." />
      <DataTable caption="Inference attempts of this trace (usage ledger)" captionHidden columns={columns} rows={rows} rowKey={(r) => r.spanId} testId="table-attempts" />
      {usageTruncated && <p className="mt-2 text-xs text-ink-3">The backend returned the first 2,000 usage rows only.</p>}
      {view.counts.usageWithoutSpan > 0 && (
        <p className="mt-2 text-xs text-ink-3">
          {formatInt(view.counts.usageWithoutSpan)} usage row{view.counts.usageWithoutSpan === 1 ? "" : "s"} belong to spans beyond the response limit and are not listed.
        </p>
      )}
    </section>
  );
}

/** Accessible alternative to the waterfall: every span with its exact timing as a table. */
export function SpanTable({ view }: { view: TraceView }) {
  const rows = view.rows.slice(0, SPAN_TABLE_LIMIT);
  const columns: Column<SpanView>[] = [
    { key: "name", header: "Span (tree order)", rowHeader: true, render: (r) => <span className="block max-w-[28rem] truncate" title={r.name}>{`${"· ".repeat(Math.min(r.depth, 12))}${r.name}`}</span> },
    { key: "id", header: "Span ID", render: (r) => <Ident value={r.spanId} /> },
    { key: "parent", header: "Parent", render: (r) => (r.parentSpanId === null ? <NullValue label="root">—</NullValue> : <Ident value={r.parentSpanId} />) },
    { key: "pos", header: "Position", render: (r) => PLACEMENT_LABEL[r.placement] },
    { key: "kind", header: "Kind", render: (r) => r.kind },
    { key: "status", header: "Status", render: (r) => <SpanStatusLabel status={r.status} /> },
    { key: "err", header: "error.type", render: (r) => (r.errorType === null ? <NullValue label="none">—</NullValue> : <code className="font-mono text-xs">{r.errorType}</code>) },
    { key: "offset", header: "Offset", align: "right", render: (r) => r.offsetLabel },
    { key: "duration", header: "Duration", align: "right", render: (r) => <span title={r.durationExact}>{r.durationLabel}</span> },
    { key: "start", header: "Start (UTC, ns)", render: (r) => <span className="tabular">{r.startLabel}</span> },
  ];
  return (
    <section aria-labelledby="span-table-heading" className="mt-8 min-w-0">
      <details data-testid="span-table-details">
        <summary className="cursor-pointer text-base font-semibold text-ink" id="span-table-heading">
          Span table (text alternative to the waterfall)
        </summary>
        <div className="mt-3">
          <DataTable caption="All spans in tree order with exact timing" columns={columns} rows={rows} rowKey={(r) => r.spanId} testId="table-spans" />
          {view.rows.length > rows.length && (
            <p className="mt-2 text-xs text-ink-3">
              First {formatInt(rows.length)} of {formatInt(view.rows.length)} spans listed; the waterfall above can show all of them.
            </p>
          )}
        </div>
      </details>
    </section>
  );
}

export function TruncationNotice({ detail }: { detail: TraceDetail }) {
  if (!detail.spans_truncated && !detail.usage_truncated) return null;
  return (
    <div className="mb-5">
      <Notice tone="caution" title="Large trace: response truncated by the backend" testId="truncation-notice">
        The backend returns at most 2,000 spans and 2,000 usage rows per trace. This trace has {formatInt(detail.trace.span_count)} stored spans; spans whose parent was not returned are
        shown at the top level and labeled.
      </Notice>
    </div>
  );
}
