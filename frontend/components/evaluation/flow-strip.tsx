import Link from "next/link";

export type FlowStep = "trace" | "review" | "case" | "dataset" | "experiment";

const STEPS: { key: FlowStep; label: string; how: string }[] = [
  { key: "trace", label: "Trace", how: "observed metadata, no content" },
  { key: "review", label: "Human review", how: "recorded on the trace page" },
  { key: "case", label: "Test case", how: "converted after an explicit approval" },
  { key: "dataset", label: "Dataset", how: "named set of approved cases" },
  { key: "experiment", label: "Mock experiment", how: "started explicitly, synthetic" },
];

/**
 * UI-R4: the evaluation loop as the product implements it, left to right. Only steps with a
 * page are links (`hrefs`); the conversions between steps happen on those pages and only after
 * a person approves them. Nothing here runs or creates anything.
 */
export function EvaluationFlow({ current, hrefs = {} }: { current: FlowStep; hrefs?: Partial<Record<FlowStep, string>> }) {
  return (
    <nav aria-label="Evaluation workflow" className="mb-6 min-w-0 rounded-xl border border-line bg-surface px-4 py-3" data-testid="evaluation-flow">
      <ol className="flex min-w-0 flex-wrap items-stretch gap-x-2 gap-y-2">
        {STEPS.map((s, i) => {
          const isCurrent = s.key === current;
          const href = hrefs[s.key];
          const body = (
            <>
              <span className="flex items-center gap-1.5 text-xs font-semibold text-ink">
                <span aria-hidden="true" className={`inline-flex h-4 w-4 items-center justify-center rounded-full text-[9px] ${isCurrent ? "bg-accent text-canvas" : "bg-raised text-ink-2"}`}>
                  {i + 1}
                </span>
                {s.label}
              </span>
              <span className="mt-0.5 block text-[11px] text-ink-3">{s.how}</span>
            </>
          );
          return (
            <li key={s.key} className="flex min-w-0 items-center gap-2" data-step={s.key} data-current={isCurrent ? "true" : undefined}>
              {href && !isCurrent ? (
                <Link href={href} className="block rounded-lg px-2.5 py-1.5 hover:bg-raised/70">
                  {body}
                </Link>
              ) : (
                <span className={`block rounded-lg px-2.5 py-1.5 ${isCurrent ? "bg-accent/12 ring-1 ring-accent/40" : ""}`} aria-current={isCurrent ? "step" : undefined}>
                  {body}
                </span>
              )}
              {i < STEPS.length - 1 && (
                <span aria-hidden="true" className="text-ink-3">
                  →
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
