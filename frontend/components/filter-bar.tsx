import type { ReactNode } from "react";
import { PROVENANCE_LABEL } from "../lib/provenance";
import { PROVENANCE_FILTERS, RANGE_KEYS, RANGE_PRESETS, overviewHref, type OverviewState, type ProvenanceFilter, type WindowSpec } from "../lib/time-range";
import { FilterLink } from "./navigation-transition";

/** One labelled group of filter links. */
export function FilterGroup({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return (
    <div role="group" aria-labelledby={id} className="flex flex-wrap items-center gap-2">
      <span id={id} className="text-xs font-medium uppercase tracking-wide text-ink-3">
        {label}
      </span>
      <div className="flex flex-wrap gap-1 rounded-lg border border-line bg-surface p-1">{children}</div>
    </div>
  );
}

/** Preset links; none is current while a custom range is shown. */
export function RangeLinks({ id, window, hrefFor }: { id: string; window: WindowSpec; hrefFor: (w: WindowSpec) => string }) {
  return (
    <FilterGroup id={id} label="Time range">
      {RANGE_KEYS.map((key) => (
        <FilterLink key={key} href={hrefFor({ kind: "preset", range: key })} current={window.kind === "preset" && window.range === key} label={RANGE_PRESETS[key].long}>
          {RANGE_PRESETS[key].label}
        </FilterLink>
      ))}
    </FilterGroup>
  );
}

export function ProvenanceLinks({ id, current, hrefFor }: { id: string; current: ProvenanceFilter; hrefFor: (p: ProvenanceFilter) => string }) {
  return (
    <FilterGroup id={id} label="Provenance">
      {PROVENANCE_FILTERS.map((p) => (
        <FilterLink key={p} href={hrefFor(p)} current={current === p} label={p === "all" ? "All provenance" : `${PROVENANCE_LABEL[p]} only`}>
          {p === "all" ? "All" : PROVENANCE_LABEL[p]}
        </FilterLink>
      ))}
    </FilterGroup>
  );
}

/**
 * The overview's filter row, above everything it scopes. Each option is a link to the same
 * page with a different query, so selecting it re-runs the backend queries server-side.
 */
export function FilterBar({ state }: { state: OverviewState }) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-x-6 gap-y-3" data-testid="filter-bar">
      <RangeLinks id="range-label" window={state.window} hrefFor={(window) => overviewHref({ ...state, window })} />
      <ProvenanceLinks id="prov-label" current={state.provenance} hrefFor={(provenance) => overviewHref({ ...state, provenance })} />
    </div>
  );
}
