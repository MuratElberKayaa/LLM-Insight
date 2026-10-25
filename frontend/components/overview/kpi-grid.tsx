import type { Latency, Overview, TraceProvenance } from "../../lib/api/types";
import { formatUsd } from "../../lib/decimal";
import { formatInt, formatMs, formatPercent } from "../../lib/format";
import { PROVENANCE_LABEL } from "../../lib/provenance";
import { Flag, MetricCard } from "../metric-card";
import { PROVENANCE_SWATCH } from "../provenance-badge";

/** Per-provenance counts under a KPI (the backend's own buckets, never re-summed). */
function ProvenanceSplit<P extends TraceProvenance>({ counts, keys }: { counts: Record<P, number>; keys: readonly P[] }) {
  return (
    <ul className="flex flex-wrap gap-x-3 gap-y-1" aria-label="By provenance">
      {keys.map((k) => (
        <li key={k} className="inline-flex items-center gap-1" data-provenance={k}>
          <span aria-hidden="true" className={`swatch inline-block h-1.5 w-1.5 rounded-sm ${PROVENANCE_SWATCH[k]}`} />
          <span>{PROVENANCE_LABEL[k]}</span>
          <span className="tabular text-ink-2">{formatInt(counts[k])}</span>
        </li>
      ))}
    </ul>
  );
}

const TRACE_KEYS = ["real", "synthetic", "mixed", "unknown"] as const;
const USAGE_KEYS = ["real", "synthetic", "unknown"] as const;

function LatencyFlags({ l }: { l: Latency }) {
  if (l.n === 0) return null;
  return (
    <>
      {l.low_sample.p95 && (
        <Flag tone="warning" title="p95 with fewer than 20 samples">
          Low sample (p95)
        </Flag>
      )}
      {l.low_sample.p99 && (
        <Flag tone="neutral" title="p99 with fewer than 100 samples">
          Low sample (p99)
        </Flag>
      )}
    </>
  );
}

function LatencyDetail({ l }: { l: Latency }) {
  return (
    <p className="tabular">
      p50 {formatMs(l.p50_ms) ?? "—"} · p99 {formatMs(l.p99_ms) ?? "—"} · n = {formatInt(l.n)}
    </p>
  );
}

export function KpiGrid({ data }: { data: Overview }) {
  const t = data.traces;
  const inf = data.inference;
  const traceErr = t.error_rate.total;
  const infErr = inf.errors.total;
  const infLat = inf.latency.total;
  const traceLat = t.latency.total;
  const tok = inf.tokens.total;
  const cost = inf.cost.total;
  const logical = inf.logical_calls.total;
  const knownCost = cost.known_cost_usd === null ? null : formatUsd(cost.known_cost_usd);
  const perCall = cost.known_cost_per_call_usd === null ? null : formatUsd(cost.known_cost_per_call_usd);

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4" data-testid="kpi-grid">
      <MetricCard
        id="traces"
        label="Traces"
        metricId="M01"
        value={formatInt(t.count.total)}
        footer={<ProvenanceSplit counts={t.count.by_provenance} keys={TRACE_KEYS} />}
      />
      <MetricCard
        id="trace-error-rate"
        label="Trace error rate"
        metricId="M05"
        state={traceErr.n === 0 ? "no_data" : "known"}
        value={formatPercent(traceErr.rate)}
        detail={traceErr.n > 0 && <p className="tabular">{formatInt(traceErr.error_traces)} of {formatInt(traceErr.n)} traces with status error</p>}
        footer={<span>Trace-level; separate from the inference error rate.</span>}
      />
      <MetricCard
        id="inference-attempts"
        label="Inference attempts"
        metricId="M02 · M03"
        value={formatInt(inf.attempts.total)}
        detail={
          inf.attempts.total > 0 && (
            <p className="tabular">
              {logical.value === null ? "Logical calls: n/a (no attempt carries a call_id)" : `Logical calls: ${formatInt(logical.value)}`}
              {logical.value !== null && logical.attempts_without_call_id > 0 && ` · ${formatInt(logical.attempts_without_call_id)} attempts without call_id`}
            </p>
          )
        }
        footer={<ProvenanceSplit counts={inf.attempts.by_provenance} keys={USAGE_KEYS} />}
      />
      <MetricCard
        id="inference-error-rate"
        label="Inference error rate"
        metricId="M04"
        state={infErr.n === 0 ? "no_data" : "known"}
        value={formatPercent(infErr.rate)}
        detail={infErr.n > 0 && <p className="tabular">{formatInt(infErr.failed)} of {formatInt(infErr.n)} attempts failed (span status ERROR)</p>}
        footer={<span>Each physical attempt counts, retries included.</span>}
      />
      <MetricCard
        id="inference-latency"
        label="Inference latency p95"
        metricId="M06"
        state={infLat.n === 0 ? "no_data" : infLat.p95_ms === null ? "unknown" : "known"}
        value={formatMs(infLat.p95_ms)}
        flag={<LatencyFlags l={infLat} />}
        detail={infLat.n > 0 && <LatencyDetail l={infLat} />}
        footer={<span>Inference span durations. Time to first chunk: not recorded (no streaming).</span>}
      />
      <MetricCard
        id="trace-latency"
        label="Trace latency p95"
        metricId="M07"
        state={traceLat.n === 0 ? "no_data" : traceLat.p95_ms === null ? "unknown" : "known"}
        value={formatMs(traceLat.p95_ms)}
        flag={<LatencyFlags l={traceLat} />}
        detail={traceLat.n > 0 && <LatencyDetail l={traceLat} />}
        footer={
          <span>
            Root spans only.
            {t.latency.traces_without_root > 0 && ` ${formatInt(t.latency.traces_without_root)} traces without a stored root are excluded.`}
          </span>
        }
      />
      <MetricCard
        id="tokens"
        label="Tokens (input / output)"
        metricId="M08 · M09"
        state={tok.n === 0 ? "no_data" : tok.input_tokens === null && tok.output_tokens === null ? "unknown" : "known"}
        value={
          <span className="tabular">
            {tok.input_tokens === null ? "unknown" : formatInt(tok.input_tokens)}
            <span className="mx-1 text-base font-normal text-ink-3">/</span>
            {tok.output_tokens === null ? "unknown" : formatInt(tok.output_tokens)}
          </span>
        }
        detail={
          tok.n > 0 && (
            <>
              {(tok.calls_with_unknown_input_tokens > 0 || tok.calls_with_unknown_output_tokens > 0) && (
                <p className="tabular">
                  Not reported: input by {formatInt(tok.calls_with_unknown_input_tokens)}, output by {formatInt(tok.calls_with_unknown_output_tokens)} of {formatInt(tok.n)} attempts
                </p>
              )}
              <p className="tabular">
                Cached input {tok.subsets.cached_input_tokens.sum === null ? "not reported" : formatInt(tok.subsets.cached_input_tokens.sum)} · reasoning{" "}
                {tok.subsets.reasoning_output_tokens.sum === null ? "not reported" : formatInt(tok.subsets.reasoning_output_tokens.sum)}
              </p>
            </>
          )
        }
        footer={<span>Sums of reported values only. Cached and reasoning tokens are subsets, never added.</span>}
      />
      <MetricCard
        id="known-cost"
        label="Known cost (estimate)"
        metricId="M10 · M11 · M12"
        state={cost.n === 0 ? "no_data" : knownCost === null ? "unknown" : "known"}
        unknownText="Unknown: no call priced"
        value={knownCost && <span title={`Exact: ${knownCost.exact}`}>{knownCost.text}</span>}
        flag={
          cost.n > 0 &&
          cost.cost_complete === false && (
            <Flag tone="warning" title="At least one attempt in this window has an unknown cost; the known total excludes it">
              Incomplete: {formatInt(cost.unknown_cost_calls)} of {formatInt(cost.n)} unpriced
            </Flag>
          )
        }
        detail={
          cost.n > 0 && (
            <>
              <p className="tabular">
                Priced {formatInt(cost.computed_cost_calls)} of {formatInt(cost.n)} attempts · unknown cost {formatInt(cost.unknown_cost_calls)}
              </p>
              <p className="tabular">Per priced attempt: {perCall === null ? "n/a" : <span title={`Exact: ${perCall.exact}`}>{perCall.text}</span>}</p>
            </>
          )
        }
        footer={<span>USD from provider-reported usage and verified prices; not an invoice. Unknown is never counted as $0.</span>}
      />
    </div>
  );
}
