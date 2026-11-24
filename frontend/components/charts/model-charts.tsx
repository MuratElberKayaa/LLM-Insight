"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, type TooltipContentProps } from "recharts";
import type { NameType, ValueType } from "recharts/types/component/DefaultTooltipContent";
import { formatCompact, formatInt, formatMs } from "../../lib/format";
import type { GroupPoint } from "../../lib/model-groups";
import { ChartTooltip, TooltipRow } from "./chart-tooltip";
import { CHART } from "./trend-charts";

// Horizontal bars, one row per group in backend order. Encoding as in the Phase 3A trend
// (validated on #15171c dark and #fcfcfb light): series-1 = not failed, critical = failed; failures also carry text.
const ROW = 30;
/** UI-R3: three bars per group in the latency chart (p50, p95, p99: one hue, light → dark). */
const LATENCY_ROW = 44;
export const PERCENTILE_OPACITY = { p50: 0.4, p95: 0.7, p99: 1 } as const;
const BAR_MAX = 16;
const GAP = { stroke: CHART.surface, strokeWidth: 2 } as const;

function shortLabel(label: string): string {
  return label.length > 34 ? `${label.slice(0, 33)}…` : label;
}

function Body({ kind, active, payload }: { kind: "attempts" | "latency"; active?: boolean | undefined; payload?: readonly { payload?: unknown }[] | undefined }) {
  const p = payload?.[0]?.payload as GroupPoint | undefined;
  if (!active || p === undefined) return null;
  return (
    <ChartTooltip title={p.label}>
      {kind === "attempts" ? (
        <>
          <TooltipRow value={formatInt(p.attempts)} label="attempts" />
          <TooltipRow value={formatInt(p.succeeded)} label="not failed" swatch={CHART.series1} shape="square" />
          <TooltipRow value={formatInt(p.failed)} label="failed" swatch={CHART.critical} shape="square" />
        </>
      ) : (
        <>
          <TooltipRow value={formatMs(p.p50) ?? "no sample"} label="p50" swatch={CHART.series1} shape="square" opacity={PERCENTILE_OPACITY.p50} />
          <TooltipRow value={formatMs(p.p95) ?? "no sample"} label={p.lowSample ? "p95 (fewer than 20 samples)" : "p95"} swatch={CHART.series1} shape="square" opacity={PERCENTILE_OPACITY.p95} />
          <TooltipRow value={formatMs(p.p99) ?? "no sample"} label={p.lowSampleP99 ? "p99 (fewer than 100 samples)" : "p99"} swatch={CHART.series1} shape="square" opacity={PERCENTILE_OPACITY.p99} />
          <TooltipRow value={formatInt(p.n)} label="samples" />
        </>
      )}
    </ChartTooltip>
  );
}

function Frame({ points, kind, children }: { points: GroupPoint[]; kind: "attempts" | "latency"; children: React.ReactNode }) {
  const height = Math.max(120, points.length * (kind === "latency" ? LATENCY_ROW : ROW) + 40);
  return (
    <div style={{ height }} className="w-full min-w-0 overflow-hidden">
      <ResponsiveContainer width="100%" height={height} initialDimension={{ width: 300, height }}>
        <BarChart data={points} layout="vertical" margin={{ top: 4, right: 12, bottom: 0, left: 0 }} barCategoryGap={kind === "latency" ? "18%" : "25%"} barGap={1}>
          <CartesianGrid horizontal={false} stroke={CHART.grid} strokeWidth={1} />
          <XAxis
            type="number"
            tickLine={false}
            axisLine={{ stroke: CHART.axis }}
            tick={{ fill: CHART.tick, fontSize: 11 }}
            allowDecimals={kind === "latency"}
            tickFormatter={(v: number) => (kind === "latency" ? `${formatCompact(v)} ms` : formatCompact(v))}
          />
          <YAxis
            type="category"
            dataKey="key"
            width={150}
            tickLine={false}
            axisLine={false}
            interval={0}
            tick={{ fill: CHART.tick, fontSize: 11 }}
            tickFormatter={(key: string) => {
              const p = points.find((x) => x.key === key);
              return p ? (kind === "latency" && p.lowSample ? "▲ " : "") + shortLabel(p.label) : "";
            }}
          />
          <Tooltip
            cursor={{ fill: CHART.cursor }}
            content={(props: TooltipContentProps<ValueType, NameType>) => <Body kind={kind} active={props.active} payload={props.payload} />}
            isAnimationActive={false}
          />
          {children}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function GroupAttemptsChart({ points }: { points: GroupPoint[] }) {
  return (
    <Frame points={points} kind="attempts">
      <Bar dataKey="succeeded" name="Not failed" stackId="a" fill={CHART.series1} maxBarSize={BAR_MAX} isAnimationActive={false} {...GAP} />
      <Bar dataKey="failed" name="Failed" stackId="a" fill={CHART.critical} maxBarSize={BAR_MAX} radius={[0, 4, 4, 0]} isAnimationActive={false} {...GAP} />
    </Frame>
  );
}

/** UI-R3: p50, p95 and p99 per group, one hue in three steps (light → dark); a group without a sample has no bar. */
export function GroupLatencyChart({ points }: { points: GroupPoint[] }) {
  const bar = (key: "p50" | "p95" | "p99") => (
    <Bar dataKey={key} name={`${key} latency (ms)`} fill={CHART.series1} fillOpacity={PERCENTILE_OPACITY[key]} maxBarSize={9} radius={[0, 3, 3, 0]} isAnimationActive={false} />
  );
  return (
    <Frame points={points} kind="latency">
      {bar("p50")}
      {bar("p95")}
      {bar("p99")}
    </Frame>
  );
}
