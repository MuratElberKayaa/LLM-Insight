import type { SpanStatus, TraceStatus, TraceUsageSummary } from "../../lib/api/trace-types";
import { formatUsd } from "../../lib/decimal";
import { formatInt } from "../../lib/format";
import { formatDigits } from "../../lib/span-tree";
import { NullValue } from "../data-table";
import { StatusIndicator } from "../status-indicator";

/** Stored trace status (`error` when any span is ERROR, ARCHITECTURE.md §4.4), as text + glyph. */
export function TraceStatusLabel({ status }: { status: TraceStatus }) {
  return (
    <span data-trace-status={status}>
      {status === "error" ? (
        <StatusIndicator tone="critical" label="Error" detail="At least one span in this trace has status ERROR" />
      ) : (
        <StatusIndicator tone="good" label="OK" detail="No span in this trace has status ERROR" />
      )}
    </span>
  );
}

const SPAN_STATUS: Record<SpanStatus, { tone: "critical" | "good" | "neutral"; label: string; detail: string }> = {
  ERROR: { tone: "critical", label: "Error", detail: "Span status ERROR" },
  OK: { tone: "good", label: "OK", detail: "Span status OK (set explicitly by the instrumentation)" },
  UNSET: { tone: "neutral", label: "Unset", detail: "Span status UNSET: the instrumentation recorded no error" },
};

export function SpanStatusLabel({ status }: { status: SpanStatus }) {
  const s = SPAN_STATUS[status];
  return (
    <span data-span-status={status}>
      <StatusIndicator tone={s.tone} label={s.label} detail={s.detail} />
    </span>
  );
}

/** A BIGINT token count from its digit string; `null` is unknown, never 0. */
export function Tokens({ value, unknownLabel = "not reported" }: { value: string | null; unknownLabel?: string }) {
  return value === null ? <NullValue label={unknownLabel}>unknown</NullValue> : <span className="tabular">{formatDigits(value)}</span>;
}

/** "in / out" with the count of calls whose tokens are unknown (sums never include them). */
export function TokenSummary({ usage }: { usage: TraceUsageSummary }) {
  if (usage.inference_calls === 0) return <NullValue label="no inference spans">—</NullValue>;
  return (
    <span className="block">
      <span className="block">
        <Tokens value={usage.input_tokens} /> <span className="text-ink-3">/</span> <Tokens value={usage.output_tokens} />
      </span>
      {usage.calls_with_unknown_tokens > 0 && (
        <span className="block text-xs text-ink-3">
          {formatInt(usage.calls_with_unknown_tokens)} call{usage.calls_with_unknown_tokens === 1 ? "" : "s"} unknown
        </span>
      )}
    </span>
  );
}

/**
 * Known cost of a trace: the exact backend sum of computed rows. With unpriced calls it is
 * marked incomplete; with no computed row it is "unknown", never "$0".
 */
export function TraceCost({ usage }: { usage: TraceUsageSummary }) {
  if (usage.inference_calls === 0) return <NullValue label="no inference spans">—</NullValue>;
  const unknown = usage.unknown_cost_calls;
  const note =
    unknown > 0 ? (
      <span className="block text-xs text-ink-3" data-cost-incomplete="true">
        {formatInt(unknown)} of {formatInt(usage.inference_calls)} unpriced
      </span>
    ) : null;
  if (usage.known_cost_usd === null) {
    return (
      <span className="block" data-cost-state="unknown">
        <NullValue label="no priced call">unknown</NullValue>
        {note}
      </span>
    );
  }
  const f = formatUsd(usage.known_cost_usd);
  return (
    <span className="block" data-cost-state={unknown > 0 ? "incomplete" : "known"} title={f ? `Exact: ${f.exact}` : undefined}>
      <span className="block">{f?.text ?? usage.known_cost_usd}</span>
      {note}
    </span>
  );
}

/** An opaque identifier, shown whole in monospace (never shortened into a different id). */
export function Ident({ value, className = "" }: { value: string; className?: string }) {
  return <code className={`font-mono text-xs break-all ${className}`}>{value}</code>;
}
