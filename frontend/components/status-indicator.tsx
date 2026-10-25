export type StatusTone = "good" | "warning" | "critical" | "neutral";

const TONE: Record<StatusTone, { dot: string; glyph: string }> = {
  good: { dot: "bg-good", glyph: "●" },
  warning: { dot: "bg-warning", glyph: "▲" },
  critical: { dot: "bg-critical", glyph: "✕" },
  neutral: { dot: "bg-unknown", glyph: "○" },
};

/** A status dot plus a text label: the state is always readable without color. */
export function StatusIndicator({ tone, label, detail }: { tone: StatusTone; label: string; detail?: string }) {
  const t = TONE[tone];
  return (
    <span className="inline-flex items-center gap-2 text-sm text-ink-2" title={detail}>
      <span aria-hidden="true" className={`swatch inline-block h-2 w-2 rounded-full ${t.dot}`} />
      <span>
        <span className="sr-only">{t.glyph} </span>
        {label}
      </span>
    </span>
  );
}
