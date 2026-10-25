import { formValueFromUtc, type WindowSpec } from "../lib/time-range";
import type { HiddenFields } from "./project-selector";

/**
 * Custom UTC range (Phase 3C). A plain GET form whose fields are named `from_utc`/`to_utc`, so
 * the values are UTC by name; the server validates them (real instants, start before end, at
 * most 31 days) and falls back to the default preset with a stated reason otherwise.
 * Prefilled with the window currently shown.
 */
export function RangeForm({ action, hidden, window, shown }: { action: string; hidden: HiddenFields; window: WindowSpec; shown: { from: string; to: string } | null }) {
  const from = window.kind === "custom" ? window.from : shown?.from;
  const to = window.kind === "custom" ? window.to : shown?.to;
  return (
    <details className="group" open={window.kind === "custom"} data-testid="range-form">
      <summary className="inline-flex min-h-8 cursor-pointer select-none items-center rounded-md px-1 text-xs font-medium uppercase tracking-wide text-ink-3 hover:text-ink">
        Custom range (UTC)
      </summary>
      <form method="get" action={action} className="mt-2 flex flex-wrap items-end gap-2" aria-label="Custom time range in UTC">
        {hidden.map(([name, value]) => (
          <input key={name} type="hidden" name={name} value={value} />
        ))}
        <label className="flex flex-col gap-1 text-xs text-ink-3">
          From (UTC, inclusive)
          <input
            type="datetime-local"
            name="from_utc"
            step={60}
            required
            defaultValue={from ? formValueFromUtc(from) : undefined}
            className="min-h-8 rounded-md border border-line bg-surface px-2 text-sm text-ink [color-scheme:dark]"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-3">
          To (UTC, exclusive)
          <input
            type="datetime-local"
            name="to_utc"
            step={60}
            required
            defaultValue={to ? formValueFromUtc(to) : undefined}
            className="min-h-8 rounded-md border border-line bg-surface px-2 text-sm text-ink [color-scheme:dark]"
          />
        </label>
        <button type="submit" className="min-h-8 rounded-md border border-line px-3 text-sm font-medium text-ink-2 hover:bg-raised hover:text-ink">
          Apply range
        </button>
        <span className="w-full text-[11px] text-ink-3">At most 31 days. Times are UTC regardless of this computer&apos;s time zone.</span>
      </form>
    </details>
  );
}
