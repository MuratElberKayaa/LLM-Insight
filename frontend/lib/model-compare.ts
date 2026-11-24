/**
 * UI-R3: side-by-side reading of two model groups the backend already returned
 * (`GET /api/v1/models/compare`, M15). Nothing is recomputed: every value is the group's own.
 *
 * The groups are observational, not a controlled experiment: they can differ in provider,
 * prompt version, provenance, sample size, usage source and cost completeness. `comparability`
 * lists those differences so the page can state them; no test of significance is made.
 */
import type { ModelGroup, ModelsCompare } from "./api/types";
import { groupKey } from "./model-groups";
import { MODEL_RE, PROVIDER_RE } from "./scope-filters";
import type { ModelState } from "./model-query";
import { DEFAULT_PAGE_SIZE, traceListHref, type ListState } from "./trace-query";

/** A p95 below this many samples is flagged by the backend (`low_sample.p95`). */
export const MIN_P95_SAMPLES = 20;
const MAX_PART = 256;

/** The URL value of a group: its key as compact JSON (provider, requested, response, prompt). */
export function groupParam(g: ModelGroup): string {
  return groupKey(g);
}

/** A group key from the URL, validated: four strings or nulls, each at most 256 characters. */
export function parseGroupParam(raw: string | string[] | undefined): string | null {
  if (typeof raw !== "string" || raw.length > 4 * (MAX_PART + 8)) return null;
  let v: unknown;
  try {
    v = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!Array.isArray(v) || v.length !== 4) return null;
  if (typeof v[0] !== "string" || v[0].length === 0 || v[0].length > 64) return null;
  for (const part of v.slice(1)) if (part !== null && (typeof part !== "string" || part.length > MAX_PART)) return null;
  return JSON.stringify(v);
}

export interface PairSelection {
  a: ModelGroup | null;
  b: ModelGroup | null;
  /** A selection in the URL that is not among the returned groups (filters, window or top-N changed). */
  missing: ("a" | "b")[];
  aKey: string | null;
  bKey: string | null;
}

export function selectPair(data: ModelsCompare | null, aKey: string | null, bKey: string | null): PairSelection {
  const find = (k: string | null) => (k === null || data === null ? null : (data.groups.find((g) => groupKey(g) === k) ?? null));
  const a = find(aKey);
  const b = find(bKey);
  const missing: ("a" | "b")[] = [];
  if (aKey !== null && a === null) missing.push("a");
  if (bKey !== null && b === null) missing.push("b");
  return { a, b, missing, aKey, bKey };
}

/** Hidden fields of the comparison form: the page's filters, so choosing groups keeps them. */
export function pairHref(base: string, aKey: string | null, bKey: string | null): string {
  const [path, qs = ""] = base.split("?", 2);
  const sp = new URLSearchParams(qs);
  sp.delete("a");
  sp.delete("b");
  if (aKey !== null) sp.set("a", aKey);
  if (bKey !== null) sp.set("b", bKey);
  const s = sp.toString();
  return s ? `${path}?${s}` : (path ?? "/models");
}

export type Aspect = "provider" | "requested_model" | "response_model" | "prompt_version" | "provenance" | "sample" | "cost" | "usage_source" | "streaming";

export interface Difference {
  aspect: Aspect;
  /** True when the two groups are alike in this respect. */
  same: boolean;
  text: string;
}

const provenanceMix = (g: ModelGroup) =>
  (["real", "synthetic", "unknown"] as const)
    .filter((p) => g.by_provenance[p] > 0)
    .join("+");

/** What differs between two groups, in plain words; the page shows every line. */
export function comparability(a: ModelGroup, b: ModelGroup): Difference[] {
  const v = (x: string | null) => x ?? "not reported";
  const out: Difference[] = [
    { aspect: "provider", same: a.provider === b.provider, text: a.provider === b.provider ? `Same provider (${a.provider})` : `Different providers (${a.provider} vs ${b.provider})` },
    {
      aspect: "requested_model",
      same: a.requested_model === b.requested_model,
      text: a.requested_model === b.requested_model ? "Same requested model" : `Different requested models (${v(a.requested_model)} vs ${v(b.requested_model)})`,
    },
    {
      aspect: "response_model",
      same: a.response_model === b.response_model,
      text: a.response_model === b.response_model ? "Same response model" : `Different response models (${v(a.response_model)} vs ${v(b.response_model)})`,
    },
    {
      aspect: "prompt_version",
      same: a.prompt_version === b.prompt_version,
      text:
        a.prompt_version === b.prompt_version
          ? `Same prompt version (${v(a.prompt_version)})`
          : `Different prompt versions (${v(a.prompt_version)} vs ${v(b.prompt_version)}): the inputs were not the same, so differences may come from the prompt`,
    },
    {
      aspect: "provenance",
      same: provenanceMix(a) === provenanceMix(b),
      text: provenanceMix(a) === provenanceMix(b) ? `Same provenance mix (${provenanceMix(a) || "none"})` : `Different provenance (${provenanceMix(a)} vs ${provenanceMix(b)}): synthetic and real attempts are not comparable`,
    },
    {
      aspect: "sample",
      same: a.latency.n >= MIN_P95_SAMPLES && b.latency.n >= MIN_P95_SAMPLES,
      text:
        a.latency.n >= MIN_P95_SAMPLES && b.latency.n >= MIN_P95_SAMPLES
          ? `Both have at least ${MIN_P95_SAMPLES} latency samples (n ${a.latency.n} and ${b.latency.n})`
          : `Small sample (n ${a.latency.n} and ${b.latency.n}; fewer than ${MIN_P95_SAMPLES} makes a p95 unreliable)`,
    },
    {
      aspect: "cost",
      same: a.cost.cost_complete && b.cost.cost_complete,
      text: a.cost.cost_complete && b.cost.cost_complete ? "Cost is complete for both" : "Cost is incomplete or unknown for at least one group: known cost is a lower bound and unknown is never $0",
    },
    {
      aspect: "usage_source",
      same: a.usage_source.estimated === 0 && b.usage_source.estimated === 0,
      text: a.usage_source.estimated === 0 && b.usage_source.estimated === 0 ? "No estimated token counts" : "Some token counts are estimated, not provider-reported",
    },
  ];
  const streams = (g: ModelGroup) => g.time_to_first_chunk.streaming_attempts > 0;
  if (streams(a) || streams(b)) {
    out.push({
      aspect: "streaming",
      same: streams(a) && streams(b),
      text: streams(a) && streams(b) ? "Both have streaming attempts" : "Only one group streams: time to first chunk exists for one side only",
    });
  }
  return out;
}

export interface GroupTraceLink {
  href: string;
  /** What the trace list cannot narrow down to, stated next to the link. */
  caveat: string;
}

/**
 * The trace list for a group: provider + model (requested, else response) in the same usage row,
 * in the page's window. Prompt version, attempt status and attempt provenance are not carried:
 * the list has no prompt filter, and its status and provenance are trace-level values.
 */
export function groupTraceLink(g: ModelGroup, state: Pick<ModelState, "project" | "window">): GroupTraceLink {
  // Only values the trace list accepts are carried (its own formats); anything else is left out and said.
  const candidate = g.requested_model ?? g.response_model;
  const model = candidate !== null && MODEL_RE.test(candidate) ? candidate : null;
  const provider = PROVIDER_RE.test(g.provider) ? g.provider : null;
  const list: ListState = {
    project: state.project,
    window: state.window,
    status: "all",
    provenance: "all",
    provider,
    model,
    session: null,
    limit: DEFAULT_PAGE_SIZE,
    cursor: null,
    page: 1,
  };
  const parts = [model === null ? "traces with an attempt of this provider (model not filterable)" : "traces with an attempt of this provider and model"];
  if (provider === null) parts[0] = "traces of this window (provider not filterable)";
  if (g.prompt_version !== null) parts.push("any prompt version");
  return { href: traceListHref(list), caveat: parts.join("; ") };
}
