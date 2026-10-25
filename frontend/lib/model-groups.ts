/**
 * Presentation of M15 groups (Phase 3C). Nothing is recomputed: every value is the backend's
 * per group. The only derivation is `attempts − failed` for a stacked bar, which is the
 * backend's own `succeeded`. Groups keep the backend order (most attempts first); the page
 * never ranks groups by a "better" metric, because groups differ in sample size, prompt
 * version, provenance and cost completeness.
 */

import type { ModelGroup, ModelsCompare, UsageProvenance } from "./api/types";

export const CHART_GROUP_LIMIT = 15;

export function groupKey(g: ModelGroup): string {
  return JSON.stringify([g.provider, g.requested_model, g.response_model, g.prompt_version]);
}

export function groupLabel(g: ModelGroup): string {
  const req = g.requested_model ?? "model not reported";
  const resp = g.response_model ?? "not reported";
  const pv = g.prompt_version === null ? "" : ` · prompt ${g.prompt_version}`;
  return `${g.provider} · ${req} → ${resp}${pv}`;
}

export interface GroupPoint {
  key: string;
  label: string;
  attempts: number;
  succeeded: number;
  failed: number;
  p50: number | null;
  p95: number | null;
  n: number;
  lowSample: boolean;
}

/** Chart rows for the first groups in backend order (the table lists every returned group). */
export function toGroupPoints(groups: ModelGroup[]): GroupPoint[] {
  return groups.slice(0, CHART_GROUP_LIMIT).map((g) => ({
    key: groupKey(g),
    label: groupLabel(g),
    attempts: g.attempts,
    succeeded: g.errors.succeeded,
    failed: g.errors.failed,
    p50: g.latency.p50_ms,
    p95: g.latency.p95_ms,
    n: g.latency.n,
    lowSample: g.latency.low_sample.p95,
  }));
}

export interface CompareNotice {
  /** Provenances present in the returned groups (attempt counts > 0). */
  present: UsageProvenance[];
  syntheticOnly: boolean;
  anyUnknownCost: boolean;
  anyLowSample: boolean;
  anyEstimated: boolean;
}

/** Which caveats apply to the groups shown ("any" logic, exact even when truncated). */
export function compareNotice(data: ModelsCompare): CompareNotice {
  const present = (["real", "synthetic", "unknown"] as const).filter((p) => data.groups.some((g) => g.by_provenance[p] > 0));
  return {
    present: [...present],
    syntheticOnly: present.length === 1 && present[0] === "synthetic",
    anyUnknownCost: data.groups.some((g) => g.cost.unknown_cost_calls > 0),
    anyLowSample: data.groups.some((g) => g.latency.low_sample.p95),
    anyEstimated: data.groups.some((g) => g.usage_source.estimated > 0),
  };
}
