import type { ReactNode } from "react";

/**
 * UI-R3: the one tooltip surface every chart uses (raised surface, hairline border, shadow,
 * a muted title and value rows). Values wear ink tokens; a small swatch beside a value carries
 * the series identity, never the text color. Plain markup, usable from client chart modules.
 */
export function ChartTooltip({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="max-w-80 rounded-lg border border-line bg-raised px-3 py-2 text-xs shadow-lg" data-chart-tooltip="">
      <p className="mb-1 break-words text-ink-2">{title}</p>
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}

/** One value row: an optional swatch (a line for line-like marks, a square for bars), the value, its label. */
export function TooltipRow({ value, label, swatch, shape = "line", opacity }: { value: string; label: string; swatch?: string; shape?: "line" | "square"; opacity?: number }) {
  return (
    <div className="flex items-center gap-2">
      {swatch && (
        <span
          aria-hidden="true"
          className={shape === "square" ? "inline-block h-2 w-2 rounded-sm" : "inline-block h-0.5 w-3"}
          style={opacity === undefined ? { background: swatch } : { background: swatch, opacity }}
        />
      )}
      <span className="tabular font-semibold text-ink">{value}</span>
      <span className="text-ink-3">{label}</span>
    </div>
  );
}
