import Link from "next/link";
import { describeFailure, type ApiResult } from "../../lib/api/result";
import type { TraceList, TraceListItem } from "../../lib/api/trace-types";
import { formatUtcTime } from "../../lib/format";
import { formatDurationNs, formatIsoNs } from "../../lib/nanos";
import { traceDetailHref, traceListHref, listStateOf, parseTraceListQuery, type ListState } from "../../lib/trace-query";
import { DataTable, type Column } from "../data-table";
import { SectionHeading } from "../page-header";
import { ProvenanceBadge } from "../provenance-badge";
import { TraceModelsCell } from "../traces/trace-list";
import { TraceStatusLabel } from "../traces/trace-values";

/** UI-R1: how many recent traces the overview reads (`limit` of GET /api/v1/traces). */
export const RECENT_TRACES_LIMIT = 5;

/**
 * Phase 6C-C / UI-R1: the newest stored traces of the current window and filters. The newest one
 * is summarised with the three times a reader could confuse: when the dashboard read the backend,
 * when the instrumented work started (the trace's event time, as reported by the instrumentation;
 * not when it was stored), and how long it ran. The others follow in a short table, each with its
 * own provenance badge, so real and synthetic traces are never blended. Nothing here is pushed or
 * streamed: it is as fresh as the last refresh.
 */
export function LatestTrace({
  result,
  fetchedAt,
  project,
  filtered,
  listState,
}: {
  result: ApiResult<TraceList> | null;
  fetchedAt: Date;
  project: string | null;
  filtered: boolean;
  /** UI-R2: the overview's scope as a trace-list state, so the links open the same traces (lib/context-links.ts). */
  listState?: ListState;
}) {
  if (result === null) return null;
  const items = result.ok ? result.data.items.slice(0, RECENT_TRACES_LIMIT) : [];
  const item = items[0] ?? null;
  const state = listState ?? { ...listStateOf(parseTraceListQuery({})), project };
  return (
    <section aria-labelledby="latest-heading" className="min-w-0 rounded-xl border border-line bg-surface p-5" data-testid="latest-trace" data-trace-id={item?.trace_id}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <SectionHeading
          id="latest-heading"
          title="Recent traces"
          description={`Newest first, in this window${filtered ? " and with these filters" : ""}. Read from the backend at ${formatUtcTime(fetchedAt)}; it changes only when the page refreshes (not live).`}
        />
        <Link href={traceListHref(state)} prefetch={false} className="shrink-0 rounded-md text-sm font-medium text-accent underline-offset-2 hover:underline">
          All traces →
        </Link>
      </div>
      {!result.ok ? (
        <p className="text-sm text-ink-2" data-latest="unavailable">
          Unavailable: {describeFailure(result.error, "the latest trace").title.toLowerCase()}. No trace is assumed.
        </p>
      ) : item === null ? (
        <p className="text-sm text-ink-2" data-latest="none">
          No trace started in this window{filtered ? " with these filters" : ""}.
        </p>
      ) : (
        <>
          <dl className="grid grid-cols-1 gap-x-6 gap-y-3 rounded-lg border border-line bg-raised/40 p-4 text-sm sm:grid-cols-2" data-latest="found">
            <div className="sm:col-span-2">
              <dt className="text-[11px] font-semibold uppercase tracking-[0.1em] text-ink-3">Latest stored trace</dt>
              <dd className="mt-1 min-w-0 break-all">
                <Link href={traceDetailHref(item.trace_id, state)} prefetch={false} className="text-sm font-medium text-ink underline underline-offset-2 hover:text-accent">
                  Open {item.service_name} ({item.trace_id})
                </Link>
              </dd>
            </div>
            <div>
              <dt className="text-xs text-ink-3">Started (event time reported by the instrumentation)</dt>
              <dd className="tabular text-ink-2">
                <time dateTime={item.started_at} title={formatIsoNs(item.started_at)} data-testid="latest-started">
                  {formatIsoNs(item.started_at, 3)}
                </time>
              </dd>
            </div>
            <div>
              <dt className="text-xs text-ink-3">Duration · status</dt>
              <dd className="text-ink-2">
                <span className="tabular" title={`${item.duration_ns} ns`}>
                  {formatDurationNs(BigInt(item.duration_ns))}
                </span>{" "}
                · <TraceStatusLabel status={item.status} />
              </dd>
            </div>
            <div>
              <dt className="text-xs text-ink-3">Provenance</dt>
              <dd>
                <ProvenanceBadge provenance={item.provenance} emphasis={item.provenance === "synthetic"} />
              </dd>
            </div>
            <div className="min-w-0">
              <dt className="text-xs text-ink-3">Providers · models (calls)</dt>
              <dd className="min-w-0 overflow-x-auto">
                <TraceModelsCell models={item.usage.models} calls={item.usage.inference_calls} />
              </dd>
            </div>
          </dl>
          {items.length > 1 && (
            <div className="mt-4">
              <DataTable
                caption={`The ${items.length} newest traces in this window (GET /api/v1/traces, limit ${RECENT_TRACES_LIMIT})`}
                captionHidden
                columns={recentColumns(state)}
                rows={items}
                rowKey={(t) => t.trace_id}
                testId="recent-traces"
              />
            </div>
          )}
        </>
      )}
    </section>
  );
}

function recentColumns(state: ReturnType<typeof listStateOf>): Column<TraceListItem>[] {
  return [
    {
      key: "service",
      header: "Trace",
      rowHeader: true,
      render: (t) => (
        <Link href={traceDetailHref(t.trace_id, state)} prefetch={false} className="underline-offset-2 hover:text-accent hover:underline" title={t.trace_id}>
          {t.service_name} <span className="font-normal text-ink-3">{t.trace_id.slice(0, 8)}…</span>
        </Link>
      ),
    },
    {
      key: "started",
      header: "Started (UTC)",
      render: (t) => (
        <time className="tabular" dateTime={t.started_at} title={formatIsoNs(t.started_at)}>
          {formatIsoNs(t.started_at, 0)}
        </time>
      ),
    },
    { key: "status", header: "Status", render: (t) => <TraceStatusLabel status={t.status} /> },
    { key: "duration", header: "Duration", align: "right", render: (t) => formatDurationNs(BigInt(t.duration_ns)) },
    { key: "provenance", header: "Provenance", render: (t) => <ProvenanceBadge provenance={t.provenance} emphasis={t.provenance === "synthetic"} /> },
  ];
}
