"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, type TooltipContentProps } from "recharts";
import type { NameType, ValueType } from "recharts/types/component/DefaultTooltipContent";
import { formatInt } from "../../lib/format";
import { CHART } from "./trend-charts";

/**
 * Phase 4D: outcome counts per prompt version, one horizontal stacked bar each. Shown only
 * when both versions ran on the identical case ids, so both bars have the same total and the
 * segments compare like with like. Counts, not rates: per-version pass rates have different
 * denominators and are never set side by side as bars.
 *
 * Encoding (validated with the dataviz palette script on #15171c): series-1 = passed,
 * critical = failed (the Phase 3 pair, deutan ΔE 25.7), neutral gray = not evaluated (ΔE 8.5
 * to red; gray on purpose: an absent result, like the "unknown" provenance). Every segment is
 * also named in the legend, the tooltip and the table.
 */
export interface OutcomePoint {
  key: string;
  label: string;
  passed: number;
  failed: number;
  not_evaluated: number;
}

const GAP = { stroke: CHART.surface, strokeWidth: 2 } as const;
const UNKNOWN = "#7d7b75";

function Body({ active, payload }: { active?: boolean | undefined; payload?: readonly { payload?: unknown }[] | undefined }) {
  const p = payload?.[0]?.payload as OutcomePoint | undefined;
  if (!active || p === undefined) return null;
  const row = (value: number, label: string, key: string) => (
    <div className="flex items-center gap-2">
      <span aria-hidden="true" className="inline-block h-2 w-2 rounded-sm" style={{ background: key }} />
      <span className="tabular font-semibold text-ink">{formatInt(value)}</span>
      <span className="text-ink-3">{label}</span>
    </div>
  );
  return (
    <div className="max-w-80 rounded-md border border-line bg-raised px-3 py-2 text-xs shadow-lg">
      <p className="mb-1 break-words text-ink-2">{p.label}</p>
      {row(p.passed, "passed", CHART.series1)}
      {row(p.failed, "failed", CHART.critical)}
      {row(p.not_evaluated, "not evaluated (excluded from the pass rate)", UNKNOWN)}
    </div>
  );
}

export function OutcomeChart({ points }: { points: OutcomePoint[] }) {
  const height = points.length * 44 + 40;
  return (
    <div style={{ height }} className="w-full min-w-0 overflow-hidden" data-testid="outcome-chart">
      <ResponsiveContainer width="100%" height={height} initialDimension={{ width: 300, height }}>
        <BarChart data={points} layout="vertical" margin={{ top: 4, right: 12, bottom: 0, left: 0 }} barCategoryGap="30%">
          <CartesianGrid horizontal={false} stroke={CHART.grid} strokeWidth={1} />
          <XAxis type="number" tickLine={false} axisLine={{ stroke: CHART.axis }} tick={{ fill: CHART.tick, fontSize: 11 }} allowDecimals={false} />
          <YAxis
            type="category"
            dataKey="key"
            width={140}
            tickLine={false}
            axisLine={false}
            interval={0}
            tick={{ fill: CHART.tick, fontSize: 11 }}
            tickFormatter={(key: string) => {
              const p = points.find((x) => x.key === key);
              return p === undefined ? "" : p.label.length > 30 ? `${p.label.slice(0, 29)}…` : p.label;
            }}
          />
          <Tooltip cursor={{ fill: CHART.cursor }} content={(props: TooltipContentProps<ValueType, NameType>) => <Body active={props.active} payload={props.payload} />} isAnimationActive={false} />
          <Bar dataKey="passed" name="Passed" stackId="o" fill={CHART.series1} maxBarSize={18} isAnimationActive={false} {...GAP} />
          <Bar dataKey="failed" name="Failed" stackId="o" fill={CHART.critical} maxBarSize={18} isAnimationActive={false} {...GAP} />
          <Bar dataKey="not_evaluated" name="Not evaluated" stackId="o" fill={UNKNOWN} maxBarSize={18} radius={[0, 4, 4, 0]} isAnimationActive={false} {...GAP} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
