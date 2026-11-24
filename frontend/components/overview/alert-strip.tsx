import type { AlertList, AlertState } from "../../lib/api/alerts";
import type { ApiResult } from "../../lib/api/result";
import { formatInt } from "../../lib/format";
import { SectionHeading } from "../page-header";
import { Notice } from "../states";

const METRIC_LABEL: Record<AlertState["metric"], string> = {
  inference_error_rate: "Inference error rate",
  inference_latency_p95_ms: "Inference latency p95 (ms)",
  time_to_first_chunk_p95_ms: "Time to first chunk p95 (ms)",
  known_cost_usd: "Known cost (USD)",
};

function line(r: AlertState): string {
  const base = `${METRIC_LABEL[r.metric]} ${r.value ?? "—"} ≥ ${r.threshold}`;
  if (r.state === "insufficient_data") return `${METRIC_LABEL[r.metric]}: not enough data (n ${formatInt(r.n)} of ${formatInt(r.min_n)})`;
  return r.lower_bound ? `${base} (lower bound: some calls have unknown cost)` : base;
}

/** UI-R1: number of firing rules (0 when the alert read failed or is absent). */
export function firingCount(result: ApiResult<AlertList> | null): number {
  return result !== null && result.ok ? result.data.rules.filter((r) => r.state === "firing").length : 0;
}

/** UI-R1: the alert rules as a titled card (placed after the error patterns on the overview). */
export function AlertsCard({ result }: { result: ApiResult<AlertList> | null }) {
  if (result === null || !result.ok) return null;
  return (
    <section aria-labelledby="alerts-heading" className="min-w-0 rounded-xl border border-line bg-surface p-5" data-testid="alerts-card">
      <SectionHeading id="alerts-heading" title="Alerts" description="Threshold rules of this window, evaluated on request." />
      <AlertStrip result={result} />
    </section>
  );
}

/** Phase 10F (ADR-59): rule states for the window; nothing is sent anywhere. */
export function AlertStrip({ result }: { result: ApiResult<AlertList> | null }) {
  if (result === null || !result.ok) return null; // the KPI cards carry the same metrics
  const d = result.data;
  const firing = d.rules.filter((r) => r.state === "firing");
  const waiting = d.rules.filter((r) => r.state === "insufficient_data").length;
  const bound = d.rules.filter((r) => r.state === "ok" && r.lower_bound);
  if (firing.length === 0) {
    return (
      <p className="text-xs leading-relaxed text-ink-3" data-testid="alerts-quiet" data-firing="0">
        {`Alert rules: none firing (${formatInt(d.rules.length - waiting)} checked, ${formatInt(waiting)} without enough data)${bound.length > 0 ? "; a cost rule is checked on a lower bound" : ""}. Rules are evaluated on request; no notification is sent.`}
      </p>
    );
  }
  return (
    <div data-testid="alerts-firing" data-firing={String(firing.length)}>
      <Notice tone="caution" title={`${formatInt(firing.length)} alert rule(s) firing in this window`} testId="alerts-notice">
        <ul className="list-disc pl-5">
          {firing.map((r) => (
            <li key={r.id}>
              <code className="font-mono text-xs">{r.id}</code>: {line(r)}
            </li>
          ))}
        </ul>
        <span className="mt-1 block text-xs text-ink-3">Thresholds come from backend/alerts/rules.json. Rules are evaluated on request; no notification is sent.</span>
      </Notice>
    </div>
  );
}
