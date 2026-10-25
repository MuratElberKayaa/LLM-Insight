/** Server-side preparation of the backend trend for the charts (no new metric is derived). */

import type { Overview } from "./api/types";
import { decimalToChartNumber, formatUsd } from "./decimal";
import { formatBucketRange, formatBucketTick } from "./format";

export interface TrendPoint {
  start: string;
  tick: string;
  label: string;
  attempts: number;
  failedAttempts: number;
  /** attempts − failed: the backend's own `succeeded` definition (spans not in ERROR). */
  succeededAttempts: number;
  traces: number;
  errorTraces: number;
  okTraces: number;
  /** Chart geometry only; the exact value is `knownCostText`. `null` = no computed cost. */
  knownCost: number | null;
  knownCostText: string | null;
  unknownCostCalls: number;
}

export function toTrendPoints(trend: Overview["trend"]): TrendPoint[] {
  return trend.items.map((item) => ({
    start: item.start,
    tick: formatBucketTick(item.start, trend.bucket),
    label: formatBucketRange(item.start, trend.bucket),
    attempts: item.inference_attempts,
    failedAttempts: item.failed_attempts,
    succeededAttempts: Math.max(0, item.inference_attempts - item.failed_attempts),
    traces: item.traces,
    errorTraces: item.error_traces,
    okTraces: Math.max(0, item.traces - item.error_traces),
    knownCost: decimalToChartNumber(item.known_cost_usd),
    knownCostText: item.known_cost_usd === null ? null : (formatUsd(item.known_cost_usd, 10)?.exact ?? null),
    unknownCostCalls: item.unknown_cost_calls,
  }));
}
