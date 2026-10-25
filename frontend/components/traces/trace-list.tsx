import Link from "next/link";
import type { ReviewSummary } from "../../lib/api/review-types";
import type { TraceList as TraceListData, TraceListItem, TraceModels, TraceSummary } from "../../lib/api/trace-types";
import { formatInt } from "../../lib/format";
import { formatDurationNs, formatIsoNs } from "../../lib/nanos";
import { firstPageState, nextPageState, traceDetailHref, traceListHref, type ListState } from "../../lib/trace-query";
import { DataTable, NullValue, type Column } from "../data-table";
import { ProvenanceBadge } from "../provenance-badge";
import { Ident, TokenSummary, TraceCost, TraceStatusLabel } from "./trace-values";

function started(t: TraceSummary) {
  // Millisecond precision in the list; the full nanosecond value is in the title and the detail page.
  return (
    <time dateTime={t.started_at} title={formatIsoNs(t.started_at)} className="tabular">
      {formatIsoNs(t.started_at, 3)}
    </time>
  );
}

function duration(t: TraceSummary) {
  return <span title={`${t.duration_ns} ns`}>{formatDurationNs(BigInt(t.duration_ns))}</span>;
}

function TraceLink({ t, state }: { t: TraceSummary; state: ListState }) {
  return (
    <Link
      href={traceDetailHref(t.trace_id, state)}
      prefetch={false}
      className="group block rounded-sm text-left"
      data-testid="trace-link"
      data-trace-id={t.trace_id}
    >
      <span className="block font-medium text-ink group-hover:underline">{t.service_name}</span>
      <Ident value={t.trace_id} className="text-ink-3" />
      <span className="sr-only">: open trace details</span>
    </Link>
  );
}

/**
 * Providers and models of a trace (R40). One trace can call several providers and models, so
 * every combination is listed with its call count; none is picked to stand for the trace.
 */
export function TraceModelsCell({ models, calls }: { models: TraceModels; calls: number }) {
  if (calls === 0) return <NullValue label="no inference calls">none</NullValue>;
  const shown = models.items.slice(0, 3);
  const hidden = models.distinct - shown.length;
  return (
    <ul className="space-y-0.5" data-testid="trace-models" data-distinct={models.distinct}>
      {shown.map((m) => (
        <li key={`${m.provider}|${m.requested_model ?? ""}|${m.response_model ?? ""}`} className="whitespace-nowrap text-xs" data-testid="trace-model">
          <code className="font-mono text-ink-2">{m.provider}</code>
          <span className="text-ink-3"> · </span>
          {m.requested_model === null ? <NullValue label="requested model not reported">unknown</NullValue> : <code className="font-mono text-ink-2">{m.requested_model}</code>}
          {m.response_model !== m.requested_model && (
            <>
              <span aria-hidden="true" className="text-ink-3">
                {" "}
                →{" "}
              </span>
              <span className="sr-only"> response model </span>
              {m.response_model === null ? <NullValue label="response model not reported">unknown</NullValue> : <code className="font-mono text-ink-2">{m.response_model}</code>}
            </>
          )}
          <span className="text-ink-3"> ×{formatInt(m.calls)}</span>
        </li>
      ))}
      {hidden > 0 && (
        <li className="text-xs text-ink-3" data-testid="trace-models-more">
          +{formatInt(hidden)} more combination{hidden === 1 ? "" : "s"}
        </li>
      )}
    </ul>
  );
}

/** Phase 4B: reviewed vs not reviewed, from the backend's per-trace review counts. */
export function ReviewCell({ reviews }: { reviews: ReviewSummary }) {
  if (reviews.total === 0) return <span className="text-ink-3" data-testid="review-cell" data-reviews="0">Not reviewed</span>;
  return (
    <span className="text-ink-2" data-testid="review-cell" data-reviews={reviews.total}>
      {reviews.total} review{reviews.total === 1 ? "" : "s"}
      {reviews.not_closed > 0 ? ` · ${reviews.not_closed} not closed` : " · all closed"}
    </span>
  );
}

function columns(state: ListState): Column<TraceListItem>[] {
  return [
    { key: "trace", header: "Service · trace ID", rowHeader: true, render: (t) => <TraceLink t={t} state={state} /> },
    { key: "started", header: "Started (UTC)", render: started },
    { key: "duration", header: "Duration", align: "right", render: duration },
    { key: "status", header: "Status", render: (t) => <TraceStatusLabel status={t.status} /> },
    { key: "spans", header: "Spans", align: "right", render: (t) => formatInt(t.span_count) },
    { key: "calls", header: "Inference calls", align: "right", render: (t) => formatInt(t.usage.inference_calls) },
    { key: "models", header: "Providers · models (calls)", render: (t) => <TraceModelsCell models={t.usage.models} calls={t.usage.inference_calls} /> },
    { key: "tokens", header: "Tokens in / out", align: "right", render: (t) => <TokenSummary usage={t.usage} /> },
    { key: "cost", header: "Known cost", align: "right", render: (t) => <TraceCost usage={t.usage} /> },
    { key: "pv", header: "Prompt version", render: (t) => (t.prompt_version === null ? <NullValue label="none or inconsistent" /> : <code className="font-mono text-xs">{t.prompt_version}</code>) },
    { key: "prov", header: "Provenance", render: (t) => <ProvenanceBadge provenance={t.provenance} emphasis={t.provenance === "synthetic"} /> },
    { key: "review", header: "Human review", render: (t) => <ReviewCell reviews={t.reviews} /> },
  ];
}

/** Below `md`: one card per trace with the same values as the table row. */
function TraceCards({ items, state }: { items: TraceListItem[]; state: ListState }) {
  return (
    <ul className="space-y-3 md:hidden" aria-label="Traces" data-testid="trace-cards">
      {items.map((t) => (
        <li key={t.trace_id} className="rounded-lg border border-line bg-surface px-4 py-3" data-trace-id={t.trace_id}>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <TraceLink t={t} state={state} />
            </div>
            <ProvenanceBadge provenance={t.provenance} emphasis={t.provenance === "synthetic"} />
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
            <div className="col-span-2">
              <dt className="text-xs text-ink-3">Started (UTC)</dt>
              <dd className="text-ink-2">{started(t)}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-3">Status</dt>
              <dd>
                <TraceStatusLabel status={t.status} />
              </dd>
            </div>
            <div>
              <dt className="text-xs text-ink-3">Duration</dt>
              <dd className="tabular text-ink-2">{duration(t)}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-3">Spans · inference calls</dt>
              <dd className="tabular text-ink-2">
                {formatInt(t.span_count)} · {formatInt(t.usage.inference_calls)}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-ink-3">Known cost</dt>
              <dd className="text-ink-2">
                <TraceCost usage={t.usage} />
              </dd>
            </div>
            <div className="col-span-2">
              <dt className="text-xs text-ink-3">Providers · models (calls)</dt>
              <dd className="min-w-0 overflow-x-auto">
                <TraceModelsCell models={t.usage.models} calls={t.usage.inference_calls} />
              </dd>
            </div>
            <div className="col-span-2">
              <dt className="text-xs text-ink-3">Tokens in / out</dt>
              <dd className="text-ink-2">
                <TokenSummary usage={t.usage} />
              </dd>
            </div>
            <div className="col-span-2">
              <dt className="text-xs text-ink-3">Human review</dt>
              <dd>
                <ReviewCell reviews={t.reviews} />
              </dd>
            </div>
          </dl>
        </li>
      ))}
    </ul>
  );
}

function Pagination({ data, state }: { data: TraceListData; state: ListState }) {
  const isFirst = state.cursor === null;
  const next = data.next_cursor === null ? null : traceListHref(nextPageState(state, data.window, data.next_cursor));
  return (
    <nav aria-label="Trace list pages" className="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm" data-testid="pagination">
      <p className="text-ink-3">
        Page {formatInt(state.page)} · {formatInt(data.items.length)} trace{data.items.length === 1 ? "" : "s"} shown, newest first
        {data.next_cursor === null ? " · end of the window" : ""}
      </p>
      <div className="flex gap-2">
        {!isFirst && (
          <Link href={traceListHref(firstPageState(state))} className="rounded-md border border-line px-3 py-1.5 text-ink-2 hover:bg-raised hover:text-ink" data-testid="first-page">
            First page
          </Link>
        )}
        {next !== null ? (
          <Link href={next} className="rounded-md border border-line px-3 py-1.5 text-ink-2 hover:bg-raised hover:text-ink" data-testid="next-page" rel="next">
            Next page <span aria-hidden="true">→</span>
          </Link>
        ) : (
          <span className="rounded-md border border-line px-3 py-1.5 text-ink-3" aria-disabled="true">
            No more traces
          </span>
        )}
      </div>
    </nav>
  );
}

export function TraceListView({ data, state }: { data: TraceListData; state: ListState }) {
  return (
    <section aria-label="Trace list">
      <div className="hidden md:block">
        <DataTable
          caption="Traces in the window, newest first (GET /api/v1/traces)"
          captionHidden
          columns={columns(state)}
          rows={data.items}
          rowKey={(t) => t.trace_id}
          testId="trace-table"
        />
      </div>
      <TraceCards items={data.items} state={state} />
      <Pagination data={data} state={state} />
    </section>
  );
}
