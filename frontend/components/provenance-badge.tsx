import type { TraceProvenance } from "../lib/api/types";
import { PROVENANCE_DESCRIPTION, PROVENANCE_LABEL } from "../lib/provenance";

export const PROVENANCE_SWATCH: Record<TraceProvenance, string> = {
  real: "bg-real",
  synthetic: "bg-synthetic",
  mixed: "bg-mixed",
  unknown: "bg-unknown",
};

/** Provenance pill: a colored swatch beside a text label (never color alone, T48). */
export function ProvenanceBadge({ provenance, count, emphasis = false }: { provenance: TraceProvenance; count?: string; emphasis?: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${
        emphasis ? "border-synthetic/60 bg-synthetic/10 text-ink" : "border-line bg-raised text-ink-2"
      }`}
      title={PROVENANCE_DESCRIPTION[provenance]}
      data-provenance={provenance}
    >
      <span aria-hidden="true" className={`swatch inline-block h-2 w-2 rounded-sm ${PROVENANCE_SWATCH[provenance]}`} />
      <span className={emphasis ? "uppercase tracking-wide" : undefined}>{PROVENANCE_LABEL[provenance]}</span>
      {count !== undefined && <span className="tabular text-ink">{count}</span>}
    </span>
  );
}
