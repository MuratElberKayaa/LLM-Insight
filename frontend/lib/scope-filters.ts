/**
 * Provider and model filters (Phase 6C-C), shared by Overview, Models and Traces. Both are exact
 * backend filters (ARCHITECTURE.md §5.11): `provider` matches `usage_records.provider` and
 * `model` the requested or response model of a usage row; together they select one row
 * matching both. Values are restricted to the stored identifier formats (TELEMETRY_CONTRACT.md
 * §3.1), so nothing else is ever forwarded, and they are never case-folded or guessed.
 *
 * The suggested values come from the backend's own groups for the same project and window
 * (`GET /api/v1/models/compare`, top 200 by attempts): no provider or model id is invented here.
 */

import type { ModelsCompare } from "./api/types";

/** `gen_ai.provider.name`; a span without one is stored as `unknown`. */
export const PROVIDER_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
/** Stored model names match this format; nothing else can match. */
export const MODEL_RE = /^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$/;
/** The provider value the backend stores when a span named none. */
export const UNKNOWN_PROVIDER = "unknown";

export interface ScopeFilters {
  provider: string | null;
  model: string | null;
}

type Getter = (name: string) => string | undefined;

/** `?provider=&model=`: valid values are kept, anything else is ignored and named. */
export function parseScopeFilters(get: Getter, ignored: string[]): ScopeFilters {
  const pick = (name: string, re: RegExp): string | null => {
    const raw = get(name)?.trim();
    if (raw === undefined || raw === "") return null;
    if (re.test(raw)) return raw;
    ignored.push(name);
    return null;
  };
  return { provider: pick("provider", PROVIDER_RE), model: pick("model", MODEL_RE) };
}

/** Backend parameters of the filters (validated values only). */
export function scopeParams(f: ScopeFilters): Record<string, string> {
  const params: Record<string, string> = {};
  if (f.provider !== null) params.provider = f.provider;
  if (f.model !== null) params.model = f.model;
  return params;
}

export function writeScope(f: ScopeFilters, sp: URLSearchParams): void {
  if (f.provider !== null) sp.set("provider", f.provider);
  if (f.model !== null) sp.set("model", f.model);
}

export function hasScope(f: ScopeFilters): boolean {
  return f.provider !== null || f.model !== null;
}

// ---------------------------------------------------------------- suggested values
export interface ObservedValue {
  value: string;
  attempts: number;
}

export interface FilterOptions {
  providers: ObservedValue[];
  models: ObservedValue[];
  /** Attempts whose requested and response model are both unreported: no model filter selects them. */
  attemptsWithoutModel: number;
  /** The backend listed only the largest groups: some values may be missing. */
  truncated: boolean;
}

function ranked(counts: Map<string, number>): ObservedValue[] {
  return [...counts]
    .map(([value, attempts]) => ({ value, attempts }))
    .sort((a, b) => b.attempts - a.attempts || (a.value < b.value ? -1 : a.value > b.value ? 1 : 0));
}

/** Distinct providers and models of the groups, with their attempt counts (largest first). */
export function filterOptions(compare: ModelsCompare): FilterOptions {
  const providers = new Map<string, number>();
  const models = new Map<string, number>();
  let withoutModel = 0;
  for (const g of compare.groups) {
    providers.set(g.provider, (providers.get(g.provider) ?? 0) + g.attempts);
    const names = new Set([g.requested_model, g.response_model].filter((m): m is string => m !== null && MODEL_RE.test(m)));
    if (names.size === 0) withoutModel += g.attempts;
    for (const m of names) models.set(m, (models.get(m) ?? 0) + g.attempts);
  }
  return { providers: ranked(providers), models: ranked(models), attemptsWithoutModel: withoutModel, truncated: compare.groups_truncated };
}

/**
 * Defence in depth: the backend echoes the filters it applied. A response that does not echo
 * the requested provider and model (for example an older backend that ignores `provider`) is
 * refused instead of being shown as filtered data.
 */
export function echoMatches(echo: { model: string | null; provider?: string | null }, f: ScopeFilters): boolean {
  return (echo.provider ?? null) === f.provider && echo.model === f.model;
}
