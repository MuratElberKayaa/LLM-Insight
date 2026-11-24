/**
 * UI-R4: filters for the test cases of one dataset page. The cases API has no filter (only
 * `after` paging, 50 per page), so these apply to the cases already loaded, and the page says so.
 * URL parameters `rule`, `origin` and `expected` are additive and read by the dashboard only.
 */
import { RULE_TYPES, type RuleType, type TestCase } from "./api/review-types";

export type OriginFilter = "all" | "trace" | "standalone";
export type ExpectedFilter = "all" | "yes" | "no";

export interface CaseFilters {
  rule: RuleType | "all";
  origin: OriginFilter;
  expected: ExpectedFilter;
}

export const NO_CASE_FILTER: CaseFilters = { rule: "all", origin: "all", expected: "all" };

type Params = Readonly<Record<string, string | string[] | undefined>>;
const one = (v: string | string[] | undefined) => (typeof v === "string" ? v : undefined);

export function parseCaseFilters(sp: Params): CaseFilters {
  const rule = RULE_TYPES.find((r) => r === one(sp.rule)) ?? "all";
  const origin = (["trace", "standalone"] as const).find((o) => o === one(sp.origin)) ?? "all";
  const expected = (["yes", "no"] as const).find((e) => e === one(sp.expected)) ?? "all";
  return { rule, origin, expected };
}

export function isFiltered(f: CaseFilters): boolean {
  return f.rule !== "all" || f.origin !== "all" || f.expected !== "all";
}

/** A case without an expected output is never evaluated (every experiment reports it as not evaluated). */
export function hasExpected(c: TestCase): boolean {
  return c.expected_output !== null;
}

export function applyCaseFilters(cases: TestCase[], f: CaseFilters): TestCase[] {
  return cases.filter(
    (c) =>
      (f.rule === "all" || c.rule.type === f.rule) &&
      (f.origin === "all" || (f.origin === "trace" ? c.origin_trace_id !== null : c.origin_trace_id === null)) &&
      (f.expected === "all" || (f.expected === "yes" ? hasExpected(c) : !hasExpected(c))),
  );
}

export interface CaseFacts {
  shown: number;
  byRule: Record<RuleType, number>;
  fromTrace: number;
  standalone: number;
  withExpected: number;
  withoutExpected: number;
}

/** Counts over the loaded page (never the whole dataset unless it fits on one page). */
export function caseFacts(cases: TestCase[]): CaseFacts {
  const byRule = Object.fromEntries(RULE_TYPES.map((r) => [r, 0])) as Record<RuleType, number>;
  let fromTrace = 0;
  let withExpected = 0;
  for (const c of cases) {
    byRule[c.rule.type] += 1;
    if (c.origin_trace_id !== null) fromTrace += 1;
    if (hasExpected(c)) withExpected += 1;
  }
  return { shown: cases.length, byRule, fromTrace, standalone: cases.length - fromTrace, withExpected, withoutExpected: cases.length - withExpected };
}

/** The dataset page URL with filters (defaults omitted), keeping `project` and `after`. */
export function datasetHref(datasetId: string, project: string | null, after: string | null, f: CaseFilters): string {
  const sp = new URLSearchParams();
  if (project !== null) sp.set("project", project);
  if (after !== null) sp.set("after", after);
  if (f.rule !== "all") sp.set("rule", f.rule);
  if (f.origin !== "all") sp.set("origin", f.origin);
  if (f.expected !== "all") sp.set("expected", f.expected);
  const qs = sp.toString();
  return qs ? `/datasets/${datasetId}?${qs}` : `/datasets/${datasetId}`;
}
