import Link from "next/link";
import { describeFailure, type ApiResult } from "../../lib/api/result";
import type { TraceList } from "../../lib/api/trace-types";
import { formatUtcTime } from "../../lib/format";
import { formatDurationNs, formatIsoNs } from "../../lib/nanos";
import { traceDetailHref, listStateOf, parseTraceListQuery } from "../../lib/trace-query";
import { SectionHeading } from "../page-header";
import { ProvenanceBadge } from "../provenance-badge";
import { TraceModelsCell } from "../traces/trace-list";
import { TraceStatusLabel } from "../traces/trace-values";

/**
 * Phase 6C-C: the newest stored trace of the current window and filters, and the three times a
 * reader could confuse: when the dashboard read the backend, when the instrumented work started
 * (the trace's event time, as reported by the instrumentation; not when it was stored), and how
 * long it ran. Nothing here is pushed or streamed: it is as fresh as the last refresh.
 */
export function LatestTrace({ result, fetchedAt, project, filtered }: { result: ApiResult<TraceList> | null; fetchedAt: Date; project: string | null; filtered: boolean }) {
  if (result === null) return null;
  const item = result.ok ? (result.data.items[0] ?? null) : null;
  return (
    <section aria-labelledby="latest-heading" className="mb-8 rounded-lg border border-line bg-surface px-4 py-3" data-testid="latest-trace" data-trace-id={item?.trace_id}>
      <SectionHeading
        id="latest-heading"
        title="Latest stored trace"
        description={`In this window${filtered ? " and with these filters" : ""}. Read from the backend at ${formatUtcTime(fetchedAt)}; it changes only when the page refreshes (not live).`}
      />
      {!result.ok ? (
        <p className="text-sm text-ink-2" data-latest="unavailable">
          Unavailable: {describeFailure(result.error, "the latest trace").title.toLowerCase()}. No trace is assumed.
        </p>
      ) : item === null ? (
        <p className="text-sm text-ink-2" data-latest="none">
          No trace started in this window{filtered ? " with these filters" : ""}.
        </p>
      ) : (
        <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2 xl:grid-cols-4" data-latest="found">
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
          <div className="sm:col-span-2 xl:col-span-4">
            <Link href={traceDetailHref(item.trace_id, { ...listStateOf(parseTraceListQuery({})), project })} prefetch={false} className="text-sm text-ink-2 underline underline-offset-2 hover:text-ink">
              Open {item.service_name} ({item.trace_id})
            </Link>
          </div>
        </dl>
      )}
    </section>
  );
}
