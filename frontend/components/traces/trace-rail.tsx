import Link from "next/link";
import { describeFailure, type ApiResult } from "../../lib/api/result";
import type { TraceList, TraceListItem } from "../../lib/api/trace-types";
import { formatUtcMinute } from "../../lib/format";
import { formatDurationNs } from "../../lib/nanos";
import { PROVENANCE_LABEL } from "../../lib/provenance";
import { firstPageState, nextPageState, traceDetailHref, traceListHref, type ListState } from "../../lib/trace-query";
import { PROVENANCE_SWATCH } from "../provenance-badge";
import { RailToggle } from "./rail-toggle";

/** The first model of a trace as `provider · model`, plus how many other combinations it has. */
function modelLine(t: TraceListItem): string {
  if (t.usage.inference_calls === 0) return "no inference";
  const first = t.usage.models.items[0];
  if (!first) return "models not returned";
  const model = first.response_model ?? first.requested_model ?? "model not reported";
  const more = t.usage.models.distinct - 1;
  return `${first.provider} · ${model}${more > 0 ? ` +${more}` : ""}`;
}

function RailItem({ t, current, state }: { t: TraceListItem; current: boolean; state: ListState }) {
  return (
    <li>
      <Link
        href={traceDetailHref(t.trace_id, state)}
        prefetch={false}
        aria-current={current ? "page" : undefined}
        className="group relative block rounded-lg border border-transparent px-3 py-2 hover:border-line hover:bg-raised/50 aria-[current=page]:border-accent/50 aria-[current=page]:bg-accent/10"
        data-testid="rail-trace"
        data-trace-id={t.trace_id}
        data-current={current ? "true" : undefined}
      >
        <span className="flex min-w-0 items-center gap-2">
          {t.status === "error" ? (
            <span className="shrink-0 text-xs font-bold text-critical-ink" title="Trace status error">
              ✕<span className="sr-only">Error:</span>
            </span>
          ) : (
            <span className="shrink-0 text-xs text-good" title="Trace status OK" aria-hidden="true">
              ●
            </span>
          )}
          <span className="min-w-0 truncate text-sm font-medium text-ink" title={t.service_name}>
            {t.service_name}
          </span>
          <span className="tabular ml-auto shrink-0 text-xs text-ink-2">{formatDurationNs(BigInt(t.duration_ns))}</span>
        </span>
        <span className="mt-1 flex min-w-0 items-center gap-2 text-[11px] text-ink-3">
          <span className="inline-flex shrink-0 items-center gap-1" title={PROVENANCE_LABEL[t.provenance]}>
            <span aria-hidden="true" className={`swatch inline-block h-1.5 w-1.5 rounded-sm ${PROVENANCE_SWATCH[t.provenance]}`} />
            {PROVENANCE_LABEL[t.provenance]}
          </span>
          <span aria-hidden="true">·</span>
          <span className="min-w-0 truncate font-mono" title={modelLine(t)}>
            {modelLine(t)}
          </span>
        </span>
        {current && <span className="sr-only"> (open)</span>}
      </Link>
    </li>
  );
}

/** Active filters of the listing as short text chips (the default "all" values are left out). */
function FilterChips({ state }: { state: ListState }) {
  const chips: string[] = [];
  if (state.status !== "all") chips.push(`status ${state.status}`);
  if (state.provenance !== "all") chips.push(`provenance ${state.provenance}`);
  if (state.provider !== null) chips.push(`provider ${state.provider}`);
  if (state.model !== null) chips.push(`model ${state.model}`);
  if (state.session) chips.push("one session");
  if (chips.length === 0) return <p className="text-xs text-ink-3">No filter</p>;
  return (
    <ul className="flex flex-wrap gap-1" aria-label="Active filters">
      {chips.map((c) => (
        <li key={c} className="max-w-full truncate rounded border border-line px-1.5 py-0.5 font-mono text-[10px] text-ink-2" title={c}>
          {c}
        </li>
      ))}
    </ul>
  );
}

/**
 * UI-R2: the explorer's left panel on a trace page. It lists the page of the listing the trace
 * was opened from (same filters, window and cursor as the `list` parameter), marks the open
 * trace, and pages without leaving it. It is a read of stored traces, not live.
 */
export function TraceRail({ result, currentId, state }: { result: ApiResult<TraceList> | null; currentId: string; state: ListState }) {
  if (result === null) return null;
  const items = result.ok ? result.data.items : [];
  const present = items.some((t) => t.trace_id === currentId);
  const label = result.ok ? `Trace list · ${items.length} on this page` : "Trace list";
  return (
    <RailToggle label={label}>
      <nav aria-label="Traces in this list" className="min-w-0 rounded-xl border border-line bg-surface @[64rem]/explorer:flex @[64rem]/explorer:max-h-[calc(100dvh-8rem)] @[64rem]/explorer:flex-col" data-testid="trace-rail">
        <div className="space-y-2 border-b border-line px-3 py-3">
          <div className="flex items-baseline justify-between gap-2">
            <h2 className="text-[11px] font-semibold uppercase tracking-[0.1em] text-ink-2">Trace list</h2>
            <Link href={traceListHref(state)} prefetch={false} className="text-xs font-medium text-accent underline-offset-2 hover:underline" data-testid="rail-open-list">
              Open list
            </Link>
          </div>
          {result.ok && (
            <p className="tabular text-[11px] text-ink-3" data-testid="rail-window">
              {formatUtcMinute(result.data.window.from)} → {formatUtcMinute(result.data.window.to)} UTC
              {state.cursor !== null ? ` · page ${state.page}` : ""}
            </p>
          )}
          <FilterChips state={state} />
        </div>
        {!result.ok ? (
          <p className="px-3 py-3 text-sm text-ink-2" data-testid="rail-state" data-state="unavailable">
            Unavailable: {describeFailure(result.error, "the trace list").title.toLowerCase()}. The trace itself is shown.
          </p>
        ) : items.length === 0 ? (
          <p className="px-3 py-3 text-sm text-ink-2" data-testid="rail-state" data-state="empty">
            No trace in this listing.
          </p>
        ) : (
          <ol className="min-h-0 space-y-0.5 overflow-y-auto p-1.5" data-testid="rail-list">
            {items.map((t) => (
              <RailItem key={t.trace_id} t={t} current={t.trace_id === currentId} state={state} />
            ))}
          </ol>
        )}
        {result.ok && (
          <div className="space-y-1.5 border-t border-line px-3 py-2.5 text-xs">
            {!present && (
              <p className="text-ink-3" data-testid="rail-not-listed">
                The open trace is not on this page of the listing.
              </p>
            )}
            <div className="flex flex-wrap items-center justify-between gap-2">
              {state.cursor !== null ? (
                <Link href={traceDetailHref(currentId, firstPageState(state))} prefetch={false} className="text-ink-2 underline underline-offset-2 hover:text-ink" data-testid="rail-first">
                  ← First page
                </Link>
              ) : (
                <span />
              )}
              {result.data.next_cursor !== null && (
                <Link
                  href={traceDetailHref(currentId, nextPageState(state, result.data.window, result.data.next_cursor))}
                  prefetch={false}
                  className="text-ink-2 underline underline-offset-2 hover:text-ink"
                  data-testid="rail-next"
                >
                  Next page →
                </Link>
              )}
            </div>
          </div>
        )}
      </nav>
    </RailToggle>
  );
}
