import type { ReactNode } from "react";
import type { ApiResult } from "../../lib/api/result";
import { formatUtcMinute } from "../../lib/format";
import { hiddenFields } from "../../lib/project";
import { PROVENANCE_LABEL } from "../../lib/provenance";
import type { FilterOptions } from "../../lib/scope-filters";
import { PROVENANCE_FILTERS, RANGE_KEYS, RANGE_PRESETS } from "../../lib/time-range";
import { PAGE_SIZES, STATUS_FILTERS, traceListHref, traceListSearch, withFilter, type ListState } from "../../lib/trace-query";
import { FilterLink } from "../navigation-transition";
import { ScopeFilterForm } from "../scope-filter-form";

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
 * status, provenance, exact provider and model (Phase 6C-C), page size). Every option is a link, so the backend query
 * is re-run server-side and the URL is shareable.
 */
export function TraceFilters({ state, options = null }: { state: ListState; options?: ApiResult<FilterOptions> | null }) {
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
        <ScopeFilterForm
          action="/traces"
          hidden={hiddenFields(traceListSearch(withFilter(state, { model: null, provider: null })), ["model", "provider"])}
          scope={{ provider: state.provider, model: state.model }}
          options={options}
          clearHref={traceListHref(withFilter(state, { model: null, provider: null }))}
          idPrefix="trace"
        />
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
