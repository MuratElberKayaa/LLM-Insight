import Link from "next/link";
import type { Latency, Overview, TimeToFirstChunk, TraceProvenance } from "../../lib/api/types";
import { formatUsd } from "../../lib/decimal";
import { formatInt, formatMs, formatPercent } from "../../lib/format";
import { PROVENANCE_LABEL } from "../../lib/provenance";
import { toTrendPoints } from "../../lib/trend";
import { Flag, MetricCard } from "../metric-card";
import { MetricGroup } from "../page-header";
import { PROVENANCE_SWATCH } from "../provenance-badge";
import { CompletenessBar, ErrorTypeChips, RateSplit, Sparkline } from "./kpi-visuals";

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

/** M20 (Phase 10B, ADR-55): one plain sentence; never an estimate. */
export function ttfcSentence(t: TimeToFirstChunk): string {
  if (t.reason === "no_streaming_attempts") return "Time to first chunk: no streaming attempt in scope.";
  if (t.reason === "not_recorded") return `Time to first chunk: not recorded by ${formatInt(t.streaming_attempts)} streaming attempt(s).`;
  return `Time to first chunk p50 ${formatMs(t.p50_ms) ?? "—"}, p95 ${formatMs(t.p95_ms) ?? "—"} (n ${formatInt(t.n)}${t.low_sample.p95 ? ", low sample" : ""}).`;
}

function LatencyDetail({ l }: { l: Latency }) {
  return (
    <p className="tabular">
      p50 {formatMs(l.p50_ms) ?? "—"} · p99 {formatMs(l.p99_ms) ?? "—"} · n = {formatInt(l.n)}
    </p>
  );
}

/** UI-R2: links from the trace metrics to the trace list of the same scope (built by the page). */
export interface KpiLinks {
  traces: string;
  errors: string;
}

function MetricLink({ href, children, testId }: { href: string; children: string; testId: string }) {
  return (
    <Link href={href} prefetch={false} className="text-accent underline-offset-2 hover:underline" data-testid={testId}>
      {children} →
    </Link>
  );
}

export function KpiGrid({ data, links }: { data: Overview; links?: KpiLinks }) {
  const t = data.traces;
  const inf = data.inference;
  const traceErr = t.error_rate.total;
  const infErr = inf.errors.total;
  const infLat = inf.latency.total;
  const traceLat = t.latency.total;
  const tok = inf.tokens.total;
  const cost = inf.cost.total;
  const logical = inf.logical_calls.total;
  const ttft = inf.time_to_first_chunk.total;
  const knownCost = cost.known_cost_usd === null ? null : formatUsd(cost.known_cost_usd);
  const perCall = cost.known_cost_per_call_usd === null ? null : formatUsd(cost.known_cost_per_call_usd);
  // The backend's own trend buckets (same series as the trend charts below).
  const trend = toTrendPoints(data.trend);
  const traceSpark = trend.map((p) => ({ label: p.label, value: p.traces }));
  const attemptSpark = trend.map((p) => ({ label: p.label, value: p.attempts }));
  const traceRates = TRACE_KEYS.map((k) => ({ provenance: k, n: t.error_rate.by_provenance[k].n, rate: t.error_rate.by_provenance[k].rate }));

  return (
    <div className="space-y-7" data-testid="kpi-grid">
      <MetricGroup id="kpi-traffic" title="Traffic & reliability" description="Traces and physical inference attempts in the window, with their error rates.">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <MetricCard
        id="traces"
        accent="blue"
        action={links && traceErr.n > 0 && <MetricLink href={links.traces} testId="kpi-traces-link">Open these traces</MetricLink>}
        label="Traces"
        metricId="M01"
        value={formatInt(t.count.total)}
        visual={t.count.total > 0 && <Sparkline points={traceSpark} noun="Traces" testId="kpi-traces-spark" />}
        footer={<ProvenanceSplit counts={t.count.by_provenance} keys={TRACE_KEYS} />}
      />
      <MetricCard
        id="trace-error-rate"
        accent="blue"
        action={links && traceErr.error_traces > 0 && <MetricLink href={links.errors} testId="kpi-error-traces-link">Open failed traces</MetricLink>}
        label="Trace error rate"
        metricId="M05"
        state={traceErr.n === 0 ? "no_data" : "known"}
        value={formatPercent(traceErr.rate)}
        detail={traceErr.n > 0 && <p className="tabular">{formatInt(traceErr.error_traces)} of {formatInt(traceErr.n)} traces with status error</p>}
        visual={traceErr.n > 0 && <RateSplit rows={traceRates} unit="traces" testId="kpi-trace-error-split" />}
        footer={<span>Trace-level; separate from the inference error rate.</span>}
      />
      <MetricCard
        id="inference-attempts"
        accent="blue"
        label="Inference attempts"
        metricId="M02 · M03"
        value={formatInt(inf.attempts.total)}
        visual={inf.attempts.total > 0 && <Sparkline points={attemptSpark} noun="Inference attempts" testId="kpi-attempts-spark" />}
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
        accent="blue"
        label="Inference error rate"
        metricId="M04"
        state={infErr.n === 0 ? "no_data" : "known"}
        value={formatPercent(infErr.rate)}
        detail={infErr.n > 0 && <p className="tabular">{formatInt(infErr.failed)} of {formatInt(infErr.n)} attempts failed (span status ERROR)</p>}
        visual={infErr.failed > 0 && <ErrorTypeChips items={inf.errors.by_error_type} truncated={inf.errors.by_error_type_truncated} />}
        footer={<span>Each physical attempt counts, retries included.</span>}
      />
      </div>
      </MetricGroup>
      <MetricGroup id="kpi-latency" title="Latency" description="Percentiles with their sample size; a p95 from fewer than 20 samples is flagged.">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
      <MetricCard
        id="inference-latency"
        accent="teal"
        label="Inference latency p95"
        metricId="M06"
        state={infLat.n === 0 ? "no_data" : infLat.p95_ms === null ? "unknown" : "known"}
        value={formatMs(infLat.p95_ms)}
        flag={<LatencyFlags l={infLat} />}
        detail={infLat.n > 0 && <LatencyDetail l={infLat} />}
        footer={<span>Inference span durations (one physical attempt each).</span>}
      />
      <MetricCard
        id="trace-latency"
        accent="teal"
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
        id="ttft"
        accent="teal"
        label="Time to first chunk p95"
        metricId="M20"
        state={ttft.n === 0 ? "no_data" : "known"}
        noDataText={ttft.reason === "not_recorded" ? "Not recorded" : "No streaming"}
        value={formatMs(ttft.p95_ms)}
        flag={
          ttft.n > 0 &&
          ttft.low_sample.p95 && (
            <Flag tone="warning" title="p95 with fewer than 20 recorded values">
              Low sample (p95)
            </Flag>
          )
        }
        detail={
          ttft.n > 0 && (
            <p className="tabular">
              p50 {formatMs(ttft.p50_ms) ?? "—"} · n = {formatInt(ttft.n)} of {formatInt(ttft.streaming_attempts)} streaming attempt(s)
            </p>
          )
        }
        footer={<span data-testid="ttfc-footer">{ttfcSentence(ttft)} Never estimated from duration or tokens.</span>}
      />
      </div>
      </MetricGroup>
      <MetricGroup id="kpi-usage" title="Tokens & cost" description="Provider-reported usage and estimated cost; unknown cost is never counted as $0.">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <MetricCard
        id="tokens"
        accent="violet"
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
        accent="violet"
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
        visual={cost.n > 0 && <CompletenessBar priced={cost.computed_cost_calls} unknown={cost.unknown_cost_calls} testId="kpi-cost-completeness" />}
        footer={<span>USD from provider-reported usage and verified prices; not an invoice. Unknown is never counted as $0.</span>}
      />
      </div>
      </MetricGroup>
    </div>
  );
}
