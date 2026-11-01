import { describeFailure, type ApiResult } from "../lib/api/result";
import { UNKNOWN_PROVIDER, hasScope, type FilterOptions, type ScopeFilters } from "../lib/scope-filters";

type Hidden = readonly (readonly [name: string, value: string])[];

function providerLabel(value: string): string {
  return value === UNKNOWN_PROVIDER ? "unknown (no provider name reported)" : value;
}

/**
 * Phase 6C-C: exact provider and model filters for one project and window. A plain GET form
 * (works without JavaScript; the server validates every value and the backend applies it to
 * the whole stored dataset, not to the rows on screen). The suggested values are the providers
 * and models the backend observed in this project and window; a value from the URL that was not
 * observed is kept and marked, never replaced.
 */
export function ScopeFilterForm({
  action,
  hidden,
  scope,
  options,
  clearHref,
  idPrefix,
}: {
  action: string;
  hidden: Hidden;
  scope: ScopeFilters;
  options: ApiResult<FilterOptions> | null;
  clearHref: string;
  idPrefix: string;
}) {
  const data = options?.ok ? options.data : null;
  const providers = data?.providers ?? [];
  const models = data?.models ?? [];
  const providerKnown = scope.provider === null || providers.some((p) => p.value === scope.provider);
  const listId = `${idPrefix}-model-options`;
  return (
    <div className="space-y-1" data-testid="scope-filters">
      <form method="get" action={action} className="flex flex-wrap items-end gap-2" role="search" aria-label="Filter by provider and model">
        {hidden.map(([name, value]) => (
          <input key={name} type="hidden" name={name} value={value} />
        ))}
        <label className="flex flex-col gap-1 text-xs font-medium uppercase tracking-wide text-ink-3">
          Provider
          <select
            name="provider"
            defaultValue={scope.provider ?? ""}
            data-testid="provider-select"
            className="min-h-8 w-56 max-w-full rounded-md border border-line bg-surface px-2 font-mono text-sm normal-case tracking-normal text-ink"
          >
            <option value="">All providers</option>
            {!providerKnown && scope.provider !== null && <option value={scope.provider}>{`${providerLabel(scope.provider)} (not observed in this window)`}</option>}
            {providers.map((p) => (
              <option key={p.value} value={p.value}>
                {`${providerLabel(p.value)} · ${p.attempts} attempt${p.attempts === 1 ? "" : "s"}`}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium uppercase tracking-wide text-ink-3">
          Model (exact)
          <input
            type="text"
            name="model"
            list={listId}
            defaultValue={scope.model ?? ""}
            maxLength={256}
            spellCheck={false}
            autoComplete="off"
            placeholder="All models"
            aria-describedby={`${idPrefix}-model-help`}
            data-testid="model-input"
            className="min-h-8 w-60 max-w-full rounded-md border border-line bg-surface px-2 font-mono text-sm normal-case tracking-normal text-ink placeholder:text-ink-3"
          />
          <datalist id={listId}>
            {models.map((m) => (
              <option key={m.value} value={m.value} />
            ))}
          </datalist>
        </label>
        <button type="submit" className="min-h-8 rounded-md border border-line px-3 text-sm font-medium text-ink-2 hover:bg-raised hover:text-ink" data-testid="scope-apply">
          Apply
        </button>
        {hasScope(scope) && (
          <a href={clearHref} className="min-h-8 rounded-md px-2 py-1.5 text-sm text-ink-2 underline-offset-2 hover:text-ink hover:underline" data-testid="scope-clear">
            Clear provider and model
          </a>
        )}
        <span id={`${idPrefix}-model-help`} className="sr-only">
          Matches the requested or response model of an inference call exactly. With a provider, both must match the same call.
        </span>
      </form>
      <p className="text-xs text-ink-3" data-testid="scope-note">
        Exact backend filters over all stored data of this project and window (not only the rows on this page).
        {data !== null && data.attemptsWithoutModel > 0
          ? ` ${data.attemptsWithoutModel} attempt${data.attemptsWithoutModel === 1 ? "" : "s"} reported no model; a model filter never selects ${data.attemptsWithoutModel === 1 ? "it" : "them"}.`
          : ""}
        {data?.truncated ? " Suggestions come from the 200 largest groups only; a value may be missing from the list." : ""}
        {options !== null && !options.ok ? ` Suggested values unavailable (${describeFailure(options.error, "the filter values").title.toLowerCase()}).` : ""}
      </p>
    </div>
  );
}
