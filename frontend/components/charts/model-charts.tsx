"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, type TooltipContentProps } from "recharts";
import type { NameType, ValueType } from "recharts/types/component/DefaultTooltipContent";
import { formatCompact, formatInt, formatMs } from "../../lib/format";
import type { GroupPoint } from "../../lib/model-groups";
import { CHART } from "./trend-charts";

// Horizontal bars, one row per group in backend order. Encoding as in the Phase 3A trend
// (validated on #15171c): series-1 = not failed, critical = failed; failures also carry text.
const ROW = 30;
const BAR_MAX = 16;
const GAP = { stroke: CHART.surface, strokeWidth: 2 } as const;

function shortLabel(label: string): string {
  return label.length > 34 ? `${label.slice(0, 33)}…` : label;
}

function Body({ kind, active, payload }: { kind: "attempts" | "latency"; active?: boolean | undefined; payload?: readonly { payload?: unknown }[] | undefined }) {
  const p = payload?.[0]?.payload as GroupPoint | undefined;
  if (!active || p === undefined) return null;
  const row = (value: string, label: string, key?: string) => (
    <div className="flex items-center gap-2">
      {key && <span aria-hidden="true" className="inline-block h-0.5 w-3" style={{ background: key }} />}
      <span className="tabular font-semibold text-ink">{value}</span>
      <span className="text-ink-3">{label}</span>
    </div>
  );
  return (
    <div className="max-w-80 rounded-md border border-line bg-raised px-3 py-2 text-xs shadow-lg">
      <p className="mb-1 break-words text-ink-2">{p.label}</p>
      {kind === "attempts" ? (
        <>
          {row(formatInt(p.attempts), "attempts")}
          {row(formatInt(p.succeeded), "not failed", CHART.series1)}
          {row(formatInt(p.failed), "failed", CHART.critical)}
        </>
      ) : (
        <>
          {row(formatMs(p.p95) ?? "no sample", "p95", CHART.series1)}
          {row(formatMs(p.p50) ?? "no sample", "p50")}
          {row(formatInt(p.n), p.lowSample ? "samples (fewer than 20: p95 unreliable)" : "samples")}
        </>
      )}
    </div>
  );
}

function Frame({ points, kind, children }: { points: GroupPoint[]; kind: "attempts" | "latency"; children: React.ReactNode }) {
  const height = Math.max(120, points.length * ROW + 40);
  return (
    <div style={{ height }} className="w-full min-w-0 overflow-hidden">
      <ResponsiveContainer width="100%" height={height} initialDimension={{ width: 300, height }}>
        <BarChart data={points} layout="vertical" margin={{ top: 4, right: 12, bottom: 0, left: 0 }} barCategoryGap="25%">
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

export function GroupLatencyChart({ points }: { points: GroupPoint[] }) {
  return (
    <Frame points={points} kind="latency">
      <Bar dataKey="p95" name="p95 latency (ms)" fill={CHART.series1} maxBarSize={BAR_MAX} radius={[0, 4, 4, 0]} isAnimationActive={false} />
    </Frame>
  );
}
