/**
 * UI-R4: the evaluation workspace's status vocabulary, text first (a glyph and a word; color is
 * never the only carrier). The mock label is the brief's wording: these experiments run a
 * scripted, offline mock and say nothing about a real model.
 */

/** `compact`: may wrap to two lines (inside tables). */
export function ModeBadge({ mode, compact = false }: { mode: "mock" | "real"; compact?: boolean }) {
  return mode === "mock" ? (
    <span className={`inline-flex items-center gap-1 rounded-md border border-synthetic/60 bg-synthetic/10 px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-ink ${compact ? "max-w-[7rem] whitespace-normal leading-tight" : ""}`} data-testid="mode-badge" data-mode="mock">
      <span aria-hidden="true">◇</span>Synthetic / Offline Mock
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-md border border-line px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-ink-2" data-testid="mode-badge" data-mode="real">
      <span aria-hidden="true">○</span>Real mode (recorded; this build cannot run it)
    </span>
  );
}

/** `compact`: the short word only (tables); the full wording stays in the title and for screen readers. */
export function StateBadge({ state, compact = false }: { state: "finished" | "running"; compact?: boolean }) {
  return state === "finished" ? (
    <span className="inline-flex items-center gap-1 text-xs text-ink-2" data-state="finished">
      <span aria-hidden="true" className="text-good">
        ●
      </span>
      Finished
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 text-xs text-ink-2" data-state="running">
      <span aria-hidden="true" className="text-warning-ink">
        ◐
      </span>
      {compact ? (
        <span title="Not finished (running or interrupted)">
          Not finished<span className="sr-only"> (running or interrupted)</span>
        </span>
      ) : (
        "Not finished (running or interrupted)"
      )}
    </span>
  );
}

/** Imported reports: produced elsewhere, read-only here, not part of the mock Experiments API. */
export function ImportedBadge() {
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-series-1/60 bg-series-1/10 px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-ink" data-testid="imported-badge">
      <span aria-hidden="true">⇩</span>Imported report · read-only
    </span>
  );
}
