import { STEP_FAMILY_GLYPH, STEP_FAMILY_LABEL, type StepFamily } from "../../lib/agent-analysis";

/** UI-R2: badge tint per step family (the letter stays in ink, so the text contrast never depends on the hue). */
const FAMILY_BADGE: Record<StepFamily, string> = {
  llm: "border-series-1/60 bg-series-1/15",
  tool: "border-series-3/60 bg-series-3/15",
  retrieval: "border-series-3/60 bg-series-3/15",
  mcp: "border-series-3/60 bg-series-3/15",
  agent: "border-accent-violet/60 bg-accent-violet/15",
  app: "border-line bg-transparent",
};

/** One letter in a tinted box, plus a screen-reader label: the step kind is never color alone. */
export function FamilyBadge({ family }: { family: StepFamily }) {
  return (
    <span
      className={`inline-flex h-4 w-4 shrink-0 items-center justify-center rounded border text-[9px] font-bold text-ink-2 ${FAMILY_BADGE[family]}`}
      title={STEP_FAMILY_LABEL[family]}
      data-family={family}
    >
      <span aria-hidden="true">{STEP_FAMILY_GLYPH[family]}</span>
      <span className="sr-only">{STEP_FAMILY_LABEL[family]} step</span>
    </span>
  );
}
