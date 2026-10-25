import type { ReactNode } from "react";
import { formatUtcMinute } from "../../lib/format";
import { PROVENANCE_LABEL } from "../../lib/provenance";
import { PROVENANCE_FILTERS, RANGE_KEYS, RANGE_PRESETS } from "../../lib/time-range";
import { PAGE_SIZES, STATUS_FILTERS, traceListHref, withFilter, type ListState } from "../../lib/trace-query";
import { FilterLink } from "../navigation-transition";

const STATUS_LABEL = { all: "All", ok: "OK", error: "Error" } as const;

function Group({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return (
    <div role="group" aria-labelledby={id} className="flex flex-wrap items-center gap-2">
      <span id={id} className="text-xs font-medium uppercase tracking-wide text-ink-3">
        {label}
      </span>
      <div className="flex flex-wrap gap-1 rounded-lg border border-line bg-surface p-1">{children}</div>
    </div>
  );
}

/**
 * The trace explorer's filter row: only the filters `GET /api/v1/traces` implements (window,
 * status, provenance, exact model, page size). Every option is a link, so the backend query
 * is re-run server-side and the URL is shareable.
 */
export function TraceFilters({ state }: { state: ListState }) {
  const custom = state.window.kind === "custom" ? state.window : null;
  return (
    <div className="mb-5 space-y-3" data-testid="trace-filters">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <Group id="trace-range-label" label="Time range">
          {RANGE_KEYS.map((key) => (
            <FilterLink
              key={key}
              href={traceListHref(withFilter(state, { window: { kind: "preset", range: key } }))}
              current={state.window.kind === "preset" && state.window.range === key}
              label={RANGE_PRESETS[key].long}
            >
              {RANGE_PRESETS[key].label}
            </FilterLink>
          ))}
        </Group>
        <Group id="trace-status-label" label="Status">
          {STATUS_FILTERS.map((s) => (
            <FilterLink key={s} href={traceListHref(withFilter(state, { status: s }))} current={state.status === s} label={s === "all" ? "All statuses" : `Status ${STATUS_LABEL[s]}`}>
              {STATUS_LABEL[s]}
            </FilterLink>
          ))}
        </Group>
        <Group id="trace-prov-label" label="Provenance">
          {PROVENANCE_FILTERS.map((p) => (
            <FilterLink
              key={p}
              href={traceListHref(withFilter(state, { provenance: p }))}
              current={state.provenance === p}
              label={p === "all" ? "All provenance" : `${PROVENANCE_LABEL[p]} only`}
            >
              {p === "all" ? "All" : PROVENANCE_LABEL[p]}
            </FilterLink>
          ))}
        </Group>
      </div>
      <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
        {/* Plain GET form: works without JavaScript; the server validates the value. */}
        <form method="get" action="/traces" className="flex flex-wrap items-end gap-2" role="search" aria-label="Filter by model">
          {custom ? (
            <>
              <input type="hidden" name="from" value={custom.from} />
              <input type="hidden" name="to" value={custom.to} />
            </>
          ) : state.window.kind === "preset" ? (
            <input type="hidden" name="range" value={state.window.range} />
          ) : null}
          {state.status !== "all" && <input type="hidden" name="status" value={state.status} />}
          {state.provenance !== "all" && <input type="hidden" name="provenance" value={state.provenance} />}
          <input type="hidden" name="limit" value={String(state.limit)} />
          <label className="flex flex-col gap-1 text-xs font-medium uppercase tracking-wide text-ink-3">
            Model (exact)
            <input
              type="text"
              name="model"
              defaultValue={state.model ?? ""}
              maxLength={256}
              spellCheck={false}
              autoComplete="off"
              placeholder="e.g. gpt-5.4-mini"
              aria-describedby="model-help"
              className="min-h-8 w-60 max-w-full rounded-md border border-line bg-surface px-2 font-mono text-sm normal-case tracking-normal text-ink placeholder:text-ink-3"
            />
          </label>
          <button type="submit" className="min-h-8 rounded-md border border-line px-3 text-sm font-medium text-ink-2 hover:bg-raised hover:text-ink">
            Apply
          </button>
          {state.model !== null && (
            <a href={traceListHref(withFilter(state, { model: null }))} className="min-h-8 rounded-md px-2 py-1.5 text-sm text-ink-2 underline-offset-2 hover:text-ink hover:underline">
              Clear model
            </a>
          )}
          <span id="model-help" className="sr-only">
            Matches the requested or response model of any inference call in the trace, exactly.
          </span>
        </form>
        <Group id="trace-limit-label" label="Per page">
          {PAGE_SIZES.map((n) => (
            <FilterLink key={n} href={traceListHref(withFilter(state, { limit: n }))} current={state.limit === n} label={`${n} traces per page`}>
              {n}
            </FilterLink>
          ))}
        </Group>
      </div>
      {custom && (
        <p className="text-xs text-ink-3" data-testid="custom-window">
          Fixed window {formatUtcMinute(custom.from)} → {formatUtcMinute(custom.to)} (UTC, kept while paging).{" "}
          <a href={traceListHref(withFilter(state, { window: { kind: "preset", range: "24h" } }))} className="text-ink-2 underline underline-offset-2 hover:text-ink">
            Return to the last 24 hours
          </a>
        </p>
      )}
    </div>
  );
}
