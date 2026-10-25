/**
 * Project context of every dashboard page (Phase 3C). The project comes from `?project=<id>`
 * in the URL, so it is shareable and survives reloads; without the parameter the configured
 * default (LLMI_DASHBOARD_PROJECT_ID) applies. The id is opaque apart from its format (the
 * API's `project` parameter: a positive BIGINT). Anything else is refused before any backend
 * request and never echoed; the dashboard never falls back to another project silently.
 */

const PROJECT_RE = /^[1-9][0-9]{0,18}$/;
const BIGINT_MAX = 2n ** 63n - 1n;

export function isProjectId(value: string): boolean {
  return PROJECT_RE.test(value) && BigInt(value) <= BIGINT_MAX;
}

export type ProjectParam = { kind: "default" } | { kind: "explicit"; id: string } | { kind: "invalid" };

/** `?project=`: absent → default; one well-formed id → explicit; anything else → invalid. */
export function parseProjectParam(value: string | string[] | undefined): ProjectParam {
  if (value === undefined) return { kind: "default" };
  if (typeof value !== "string" || !isProjectId(value)) return { kind: "invalid" };
  return { kind: "explicit", id: value };
}

/** The `project` value that links keep: the explicit id, or none (the default applies). */
export function projectLinkValue(param: ProjectParam): string | null {
  return param.kind === "explicit" ? param.id : null;
}

/** `path` plus `?project=<id>` when explicit (used by the navigation). */
export function projectHref(path: string, project: string | null): string {
  return project === null ? path : `${path}?project=${project}`;
}

/** Hidden form fields that carry a canonical query string, minus the names a form sets itself. */
export function hiddenFields(search: string, drop: readonly string[]): [string, string][] {
  return [...new URLSearchParams(search)].filter(([k]) => !drop.includes(k));
}
