import { COST_REASON_LABELS, DETAIL_CODE_LABELS, type CostUnknownReason, type Outcome, type Rate, type Variant } from "../../lib/api/experiment-types";
import { formatUsd } from "../../lib/decimal";
import { formatInt, formatMs, formatPercent } from "../../lib/format";
import { NullValue } from "../data-table";
import { Notice } from "../states";
import { StatusIndicator } from "../status-indicator";

/**
 * Phase 4D value renderers. Every value is the backend's; null is "unknown" or "not
 * evaluated", never 0. Money is the exact decimal string, formatted without a float.
 */

const OUTCOME: Record<Outcome, { tone: "good" | "critical" | "neutral"; label: string }> = {
  passed: { tone: "good", label: "Passed" },
  failed: { tone: "critical", label: "Failed" },
  not_evaluated: { tone: "neutral", label: "Not evaluated" },
};

export function OutcomeLabel({ outcome, code }: { outcome: Outcome; code?: string }) {
  const o = OUTCOME[outcome];
  return (
    <span data-outcome={outcome} className="inline-flex flex-wrap items-center gap-x-2">
      <StatusIndicator tone={o.tone} label={o.label} />
      {code !== undefined && code !== "pass" && <code className="font-mono text-xs text-ink-3">{code}</code>}
    </span>
  );
}

export function codeLabel(code: string): string {
  return Object.hasOwn(DETAIL_CODE_LABELS, code) ? (DETAIL_CODE_LABELS[code] ?? code) : code;
}

/** An exact USD amount, or "unknown" (never $0). */
export function Usd({ value, unknownLabel = "cost unknown" }: { value: string | null; unknownLabel?: string }) {
  if (value === null) return <NullValue label={unknownLabel}>unknown</NullValue>;
  const d = formatUsd(value.replace(/^-/, ""));
  if (d === null) return <span className="tabular">{value}</span>;
  const sign = value.startsWith("-") ? "−" : "";
  return (
    <span className="tabular" title={`${sign}${d.exact}`}>
      {sign}
      {d.text}
    </span>
  );
}

/** `numerator / denominator` with the backend's rate; no rate without an evaluated result. */
export function RateText({ rate }: { rate: Rate }) {
  if (rate.value === null) return <NullValue label="no evaluated result">no evaluated result</NullValue>;
  return (
    <span className="tabular">
      {formatPercent(rate.value)}{" "}
      <span className="text-ink-3">
        ({formatInt(rate.numerator)} / {formatInt(rate.denominator)})
      </span>
    </span>
  );
}

export function reasonList(counts: Partial<Record<CostUnknownReason, number>>): string {
  return (Object.entries(counts) as [CostUnknownReason, number][]).map(([reason, n]) => `${n} ${COST_REASON_LABELS[reason] ?? reason}`).join(", ");
}

/**
 * A variant's cost: the exact total only when every result is priced; otherwise the known
 * part, labelled as a part, with the unknown count and reasons. Never presented as a total.
 */
export function VariantCost({ cost, testId }: { cost: Variant["cost"]; testId?: string }) {
  const total = cost.computed + cost.unknown;
  if (cost.complete) {
    return (
      <span data-testid={testId} data-cost="complete">
        <Usd value={cost.known_cost_usd} /> <span className="text-ink-3">(all {formatInt(total)} results priced)</span>
      </span>
    );
  }
  return (
    <span data-testid={testId} data-cost="incomplete" className="inline-flex flex-col">
      <span>
        <span className="font-medium text-ink">Total unknown.</span>{" "}
        {cost.known_cost_usd === null ? (
          <span className="text-ink-3">No result is priced.</span>
        ) : (
          <span className="text-ink-2">
            Known part <Usd value={cost.known_cost_usd} /> for {formatInt(cost.computed)} of {formatInt(total)} results.
          </span>
        )}
      </span>
      <span className="text-xs text-ink-3">
        {formatInt(cost.unknown)} unknown ({reasonList(cost.unknown_by_reason)}).
      </span>
    </span>
  );
}

export function Ms({ value }: { value: number | null }) {
  const text = formatMs(value);
  return text === null ? <NullValue label="no duration recorded">not recorded</NullValue> : <span className="tabular">{text}</span>;
}

export function Count({ value, unknownLabel = "not reported" }: { value: number | null; unknownLabel?: string }) {
  return value === null ? <NullValue label={unknownLabel}>unknown</NullValue> : <span className="tabular">{formatInt(value)}</span>;
}

/** The fixed SYNTHETIC banner of every experiment page (T48: never labelled real). */
export function SyntheticBanner({ mode }: { mode: "mock" | "real" }) {
  return (
    <Notice tone="caution" title={mode === "mock" ? "SYNTHETIC · mock provider · not a real model" : "Real-mode experiment"} testId="experiment-synthetic-banner">
      {mode === "mock" ? (
        <>
          Outputs, token counts and durations come from a deterministic, scripted mock. Nothing was sent to OpenAI or any provider and nothing was spent; costs are estimates for
          synthetic token counts. Results say nothing about how a real model would behave.
        </>
      ) : (
        <>This build cannot run real experiments; a stored real-mode experiment is shown as recorded.</>
      )}
    </Notice>
  );
}
