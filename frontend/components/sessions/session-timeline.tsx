import Link from "next/link";
import type { ApiResult } from "../../lib/api/result";
import { describeFailure } from "../../lib/api/result";
import type { SessionRow } from "../../lib/api/sessions";
import type { TraceList } from "../../lib/api/trace-types";
import { formatUsd } from "../../lib/decimal";
import { formatInt } from "../../lib/format";
import { formatIsoNs } from "../../lib/nanos";
import { buildSessionTimeline } from "../../lib/session-timeline";
import { traceDetailHref, traceListHref, type ListState } from "../../lib/trace-query";
import { NullValue } from "../data-table";
import { ProvenanceBadge } from "../provenance-badge";
import { TraceModelsCell } from "../traces/trace-list";
import { TraceStatusLabel } from "../traces/trace-values";

function Fact({ label, children, testId }: { label: string; children: React.ReactNode; testId?: string }) {
  return (
    <div className="min-w-0 bg-surface px-3 py-2.5" data-testid={testId}>
      <dt className="text-[11px] font-medium uppercase tracking-wide text-ink-3">{label}</dt>
      <dd className="tabular mt-0.5 text-sm text-ink">{children}</dd>
    </div>
  );
}

/**
 * UI-R3: the selected session, read-only: the backend's own aggregates for it (when it is among
 * the listed sessions) and its traces in time order on one axis, each linking into the trace
 * explorer with this session as the listing. Nothing here is a session-level metric the API does
 * not return; the axis only places the stored start and end of each trace.
 */
export function SessionTimeline({ session, row, traces, list }: { session: string; row: SessionRow | null; traces: ApiResult<TraceList>; list: ListState }) {
  const items = traces.ok ? traces.data.items : [];
  const timeline = buildSessionTimeline(items);
  const known = row?.known_cost_usd ? formatUsd(row.known_cost_usd) : null;
  return (
    <section aria-labelledby="session-detail-heading" className="min-w-0 rounded-xl border border-line bg-surface p-5" data-testid="session-detail" data-session={session}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-accent">Selected session</p>
          <h2 id="session-detail-heading" className="mt-1 break-all font-mono text-sm text-ink">
            {session}
          </h2>
          <p className="mt-1 text-xs text-ink-3">A keyed pseudonym, not an anonymous id. No conversation id, user id, prompt or reply is stored or shown.</p>
        </div>
        <Link href={traceListHref(list)} prefetch={false} className="shrink-0 text-sm font-medium text-accent underline-offset-2 hover:underline" data-testid="session-open-traces">
          Open in trace list →
        </Link>
      </div>

      {row ? (
        <dl className="mt-4 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-3 xl:grid-cols-6" data-testid="session-facts">
          <Fact label="Traces">
            {formatInt(row.traces)}
            {row.error_traces > 0 && <span className="ml-1 text-xs text-critical-ink">({formatInt(row.error_traces)} failed)</span>}
          </Fact>
          <Fact label="Attempts">{formatInt(row.attempts)}</Fact>
          <Fact label="Tokens in">{row.input_tokens === null ? <NullValue>unknown</NullValue> : formatInt(row.input_tokens)}</Fact>
          <Fact label="Tokens out">{row.output_tokens === null ? <NullValue>unknown</NullValue> : formatInt(row.output_tokens)}</Fact>
          <Fact label="Known cost" testId="session-detail-cost">
            {known === null ? <NullValue label="no priced attempt">unknown</NullValue> : known.text}
            {known !== null && !row.cost_complete && <span className="ml-1 text-xs text-warning-ink">lower bound</span>}
          </Fact>
          <Fact label="First → last trace start (UTC)">
            <span className="text-xs">
              {formatIsoNs(row.first_seen, 0)} → {formatIsoNs(row.last_seen, 0)}
            </span>
          </Fact>
        </dl>
      ) : (
        <p className="mt-4 text-sm text-ink-3" data-testid="session-not-listed">
          This session is not among the sessions listed for this window (outside it, or beyond the listed top sessions); its traces in the window are shown below if any.
        </p>
      )}

      <div className="mt-5">
        <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="text-sm font-semibold text-ink">Traces in time order</h3>
          {timeline.spanLabel !== null && <p className="text-xs text-ink-3">Axis: first trace start to last trace end ({timeline.spanLabel}), stored times.</p>}
        </div>
        {!traces.ok ? (
          <p className="text-sm text-ink-2" data-testid="session-timeline-state" data-state="unavailable">
            Unavailable: {describeFailure(traces.error, "the session's traces").title.toLowerCase()}. No trace is assumed.
          </p>
        ) : items.length === 0 ? (
          <p className="text-sm text-ink-2" data-testid="session-timeline-state" data-state="empty">
            No trace of this session started in this window.
          </p>
        ) : (
          <ol className="space-y-1" aria-label="Traces of this session, oldest first" data-testid="session-timeline">
            {timeline.rows.map((r) => (
              <li key={r.item.trace_id} className="grid grid-cols-1 gap-2 rounded-lg px-2 py-2 hover:bg-raised/50 md:grid-cols-[minmax(0,15rem)_minmax(0,1fr)_7rem] md:items-center" data-testid="session-timeline-row" data-trace-id={r.item.trace_id}>
                <div className="min-w-0">
                  <Link href={traceDetailHref(r.item.trace_id, list)} prefetch={false} className="block truncate text-sm font-medium text-ink underline-offset-2 hover:underline" title={r.item.service_name}>
                    {r.item.service_name}
                  </Link>
                  <span className="mt-0.5 flex flex-wrap items-center gap-2 text-xs">
                    <TraceStatusLabel status={r.item.status} />
                    <ProvenanceBadge provenance={r.item.provenance} emphasis={r.item.provenance === "synthetic"} />
                  </span>
                </div>
                <div className="relative h-5 rounded bg-raised/60" title={`${r.offsetLabel} · ${r.durationLabel}`}>
                  <span
                    aria-hidden="true"
                    className={`swatch absolute top-1/2 h-2.5 -translate-y-1/2 rounded-[3px] ${r.item.status === "error" ? "bg-critical" : "bg-series-1"}`}
                    style={{ left: `${r.left}%`, width: `${r.width}%`, minWidth: "3px" }}
                  />
                  <span className="sr-only">
                    starts {r.offsetLabel} after the session&apos;s first trace, lasts {r.durationLabel}
                  </span>
                </div>
                <div className="tabular text-right text-xs text-ink-2">
                  <span className="block">{r.offsetLabel}</span>
                  <span className="block text-ink-3">{r.durationLabel}</span>
                </div>
                <div className="min-w-0 md:col-span-3">
                  <TraceModelsCell models={r.item.usage.models} calls={r.item.usage.inference_calls} />
                </div>
              </li>
            ))}
          </ol>
        )}
        {traces.ok && traces.data.next_cursor !== null && (
          <p className="mt-2 text-xs text-ink-3" data-testid="session-timeline-truncated">
            Only the {formatInt(items.length)} newest traces of this session in the window are placed; the trace list has the rest.
          </p>
        )}
      </div>
    </section>
  );
}
