import { PROVENANCE_LABEL } from "../lib/provenance";
import { PROVENANCE_FILTERS, RANGE_KEYS, RANGE_PRESETS, overviewHref, type OverviewQuery } from "../lib/time-range";
import { FilterLink } from "./navigation-transition";

/**
 * One filter row above everything it scopes. Each option is a link to the same page with a
 * different query, so selecting it re-runs the backend queries server-side.
 */
export function FilterBar({ query }: { query: OverviewQuery }) {
  return (
    <div className="mb-5 flex flex-wrap items-center gap-x-6 gap-y-3" data-testid="filter-bar">
      <div role="group" aria-labelledby="range-label" className="flex flex-wrap items-center gap-2">
        <span id="range-label" className="text-xs font-medium uppercase tracking-wide text-ink-3">
          Time range
        </span>
        <div className="flex flex-wrap gap-1 rounded-lg border border-line bg-surface p-1">
          {RANGE_KEYS.map((key) => (
            <FilterLink key={key} href={overviewHref({ range: key, provenance: query.provenance })} current={query.range === key} label={RANGE_PRESETS[key].long}>
              {RANGE_PRESETS[key].label}
            </FilterLink>
          ))}
        </div>
      </div>
      <div role="group" aria-labelledby="prov-label" className="flex flex-wrap items-center gap-2">
        <span id="prov-label" className="text-xs font-medium uppercase tracking-wide text-ink-3">
          Provenance
        </span>
        <div className="flex flex-wrap gap-1 rounded-lg border border-line bg-surface p-1">
          {PROVENANCE_FILTERS.map((p) => (
            <FilterLink
              key={p}
              href={overviewHref({ range: query.range, provenance: p })}
              current={query.provenance === p}
              label={p === "all" ? "All provenance" : `${PROVENANCE_LABEL[p]} only`}
            >
              {p === "all" ? "All" : PROVENANCE_LABEL[p]}
            </FilterLink>
          ))}
        </div>
      </div>
    </div>
  );
}
