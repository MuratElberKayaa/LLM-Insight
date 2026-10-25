"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, type TooltipContentProps } from "recharts";
import type { NameType, ValueType } from "recharts/types/component/DefaultTooltipContent";
import { formatCompact, formatInt } from "../../lib/format";
import type { TrendPoint } from "../../lib/trend";

// Hex mirrors of the CSS tokens in app/globals.css (SVG attributes cannot read CSS variables
// in every browser). Validated on the chart surface #15171c.
export const CHART = {
  surface: "#15171c",
  grid: "#24272f",
  axis: "#3a3e48",
  tick: "#8f8d86",
  series1: "#3987e5",
  critical: "#d03b3b",
  cursor: "rgba(255,255,255,0.05)",
} as const;

const HEIGHT = 220;
const BAR_MAX = 24;

type Kind = "attempts" | "traces" | "cost";

interface TooltipBodyProps {
  kind: Kind;
  active?: boolean | undefined;
  payload?: readonly { payload?: unknown }[] | undefined;
}

function TooltipBody({ kind, active, payload }: TooltipBodyProps) {
  const point = payload?.[0]?.payload as TrendPoint | undefined;
  if (!active || point === undefined) return null;
  const row = (value: string, label: string, key?: string) => (
    <div className="flex items-center gap-2">
      {key && <span aria-hidden="true" className="inline-block h-0.5 w-3" style={{ background: key }} />}
      <span className="tabular font-semibold text-ink">{value}</span>
      <span className="text-ink-3">{label}</span>
    </div>
  );
  return (
    <div className="rounded-md border border-line bg-raised px-3 py-2 text-xs shadow-lg">
      <p className="mb-1 text-ink-2">{point.label}</p>
      {kind === "attempts" && (
        <>
          {row(formatInt(point.attempts), "attempts")}
          {row(formatInt(point.succeededAttempts), "not failed", CHART.series1)}
          {row(formatInt(point.failedAttempts), "failed", CHART.critical)}
        </>
      )}
      {kind === "traces" && (
        <>
          {row(formatInt(point.traces), "traces")}
          {row(formatInt(point.okTraces), "not error", CHART.series1)}
          {row(formatInt(point.errorTraces), "error", CHART.critical)}
        </>
      )}
      {kind === "cost" && (
        <>
          {row(point.knownCostText ?? "none computed", "known cost", CHART.series1)}
          {row(formatInt(point.unknownCostCalls), "calls with unknown cost")}
        </>
      )}
    </div>
  );
}

function Frame({ data, kind, children, money = false }: { data: TrendPoint[]; kind: Kind; children: React.ReactNode; money?: boolean }) {
  return (
    // Clipped, with a phone-sized first render: before ResizeObserver measures the card, a
    // wider initial chart would push narrow layouts into horizontal scroll.
    <div style={{ height: HEIGHT }} className="w-full min-w-0 overflow-hidden">
      <ResponsiveContainer width="100%" height={HEIGHT} initialDimension={{ width: 300, height: HEIGHT }}>
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} barCategoryGap="20%">
          <CartesianGrid vertical={false} stroke={CHART.grid} strokeWidth={1} />
          <XAxis dataKey="tick" tickLine={false} axisLine={{ stroke: CHART.axis }} tick={{ fill: CHART.tick, fontSize: 11 }} minTickGap={12} interval="preserveStartEnd" />
          <YAxis
            width={money ? 64 : 40}
            tickLine={false}
            axisLine={false}
            allowDecimals={money}
            tick={{ fill: CHART.tick, fontSize: 11 }}
            tickFormatter={(v: number) => (money ? `$${v.toPrecision(2).replace(/\.?0+(e|$)/, "$1")}` : formatCompact(v))}
          />
          <Tooltip
            cursor={{ fill: CHART.cursor }}
            content={(props: TooltipContentProps<ValueType, NameType>) => <TooltipBody kind={kind} active={props.active} payload={props.payload} />}
            isAnimationActive={false}
          />
          {children}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

const GAP = { stroke: CHART.surface, strokeWidth: 2 } as const;

export function AttemptsChart({ data }: { data: TrendPoint[] }) {
  return (
    <Frame data={data} kind="attempts">
      <Bar dataKey="succeededAttempts" name="Not failed" stackId="a" fill={CHART.series1} maxBarSize={BAR_MAX} isAnimationActive={false} {...GAP} />
      <Bar dataKey="failedAttempts" name="Failed" stackId="a" fill={CHART.critical} maxBarSize={BAR_MAX} radius={[4, 4, 0, 0]} isAnimationActive={false} {...GAP} />
    </Frame>
  );
}

export function TracesChart({ data }: { data: TrendPoint[] }) {
  return (
    <Frame data={data} kind="traces">
      <Bar dataKey="okTraces" name="Not error" stackId="t" fill={CHART.series1} maxBarSize={BAR_MAX} isAnimationActive={false} {...GAP} />
      <Bar dataKey="errorTraces" name="Error" stackId="t" fill={CHART.critical} maxBarSize={BAR_MAX} radius={[4, 4, 0, 0]} isAnimationActive={false} {...GAP} />
    </Frame>
  );
}

export function CostChart({ data }: { data: TrendPoint[] }) {
  return (
    <Frame data={data} kind="cost" money>
      <Bar dataKey="knownCost" name="Known cost (USD)" fill={CHART.series1} maxBarSize={BAR_MAX} radius={[4, 4, 0, 0]} isAnimationActive={false} />
    </Frame>
  );
}
