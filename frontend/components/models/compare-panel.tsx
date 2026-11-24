import Link from "next/link";
import type { ReactNode } from "react";
import type { ModelGroup, ModelsCompare } from "../../lib/api/types";
import { formatUsd } from "../../lib/decimal";
import { formatInt, formatMs, formatPercent } from "../../lib/format";
import { comparability, type PairSelection } from "../../lib/model-compare";
import { groupKey, groupLabel } from "../../lib/model-groups";
import { NullValue } from "../data-table";
import { Flag } from "../metric-card";
import { CompletenessBar } from "../overview/kpi-visuals";
import { SectionHeading } from "../page-header";
import { ProvenanceBadge } from "../provenance-badge";

/**
 * UI-R3: latency percentiles of one group on a scale shared by both sides: a bar to p95, a dot at
 * p50 and a tick at p99, with the values as text. Geometry only in style attributes.
 */
export function LatencyRange({ g, max, testId }: { g: ModelGroup; max: number; testId: string }) {
  const l = g.latency;
  const pct = (v: number | null) => (v === null || max <= 0 ? null : Math.min(100, (v / max) * 100));
  const p50 = pct(l.p50_ms);
  const p95 = pct(l.p95_ms);
  const p99 = pct(l.p99_ms);
  const label = `p50 ${formatMs(l.p50_ms) ?? "no sample"}, p95 ${formatMs(l.p95_ms) ?? "no sample"}, p99 ${formatMs(l.p99_ms) ?? "no sample"}, ${l.n} samples`;
  return (
    <div className="min-w-0" data-testid={testId}>
      {l.n === 0 ? (
        <p className="text-xs text-ink-3">No latency sample</p>
      ) : (
        <div role="img" aria-label={label} className="relative h-3 w-full rounded-full bg-raised">
          {p95 !== null && <span aria-hidden="true" className="absolute inset-y-0 left-0 rounded-full bg-series-1/45" style={{ width: `${p95}%` }} />}
          {p50 !== null && <span aria-hidden="true" className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-surface bg-series-1" style={{ left: `${p50}%` }} />}
          {p99 !== null && <span aria-hidden="true" className="absolute -inset-y-0.5 w-0.5 -translate-x-1/2 rounded-full bg-ink-2" style={{ left: `${p99}%` }} />}
        </div>
      )}
      <p className="tabular mt-1.5 text-xs text-ink-2">
        p50 {formatMs(l.p50_ms) ?? "—"} · p95 {formatMs(l.p95_ms) ?? "—"} · p99 {formatMs(l.p99_ms) ?? "—"} · n {formatInt(l.n)}
      </p>
      {(l.low_sample.p95 || l.low_sample.p99) && l.n > 0 && (
        <p className="mt-1 flex flex-wrap gap-1">
          {l.low_sample.p95 && (
            <Flag tone="warning" title="Fewer than 20 samples: the p95 is not reliable">
              Low sample (p95)
            </Flag>
          )}
          {l.low_sample.p99 && <Flag tone="neutral" title="Fewer than 100 samples: the p99 is not reliable">Low sample (p99)</Flag>}
        </p>
      )}
    </div>
  );
}

const tok = (v: number | null, unknownCalls: number) =>
  v === null ? (
    <NullValue label="not reported">unknown</NullValue>
  ) : (
    <>
      {formatInt(v)}
      {unknownCalls > 0 && <span className="block text-xs text-ink-3">{formatInt(unknownCalls)} attempt(s) not reported</span>}
    </>
  );

const subset = (s: { sum: number | null; calls_reporting: number }, of: string) =>
  s.sum === null ? (
    <NullValue label="the provider reported no value for this subset">not reported</NullValue>
  ) : (
    <>
      {formatInt(s.sum)}
      <span className="block text-xs text-ink-3">
        part of {of}, not added · {formatInt(s.calls_reporting)} reporting
      </span>
    </>
  );

function CostValue({ g, id }: { g: ModelGroup; id: string }) {
  const c = g.cost;
  const known = c.known_cost_usd === null ? null : formatUsd(c.known_cost_usd);
  return (
    <div className="min-w-0" data-testid={`pair-cost-${id}`} data-cost-state={c.known_cost_usd === null ? "unknown" : c.cost_complete ? "complete" : "incomplete"}>
      <p>
        {known === null ? <NullValue label="no priced attempt">unknown</NullValue> : <span title={`Exact: ${known.exact}`}>{known.text}</span>}
        {known !== null && !c.cost_complete && (
          <span className="ml-1">
            <Flag tone="warning" title="Some attempts have no computed cost; the known cost is a lower bound">
              Lower bound
            </Flag>
          </span>
        )}
      </p>
      <div className="mt-2 max-w-64">
        <CompletenessBar priced={c.computed_cost_calls} unknown={c.unknown_cost_calls} testId={`pair-completeness-${id}`} />
      </div>
    </div>
  );
}

function Row({ label, a, b, note }: { label: string; a: ReactNode; b: ReactNode; note?: string }) {
  return (
    <tr className="border-t border-line align-top">
      <th scope="row" className="w-44 px-3 py-2.5 text-left text-xs font-medium text-ink-2">
        {label}
        {note && <span className="mt-0.5 block font-normal text-ink-3">{note}</span>}
      </th>
      <td className="min-w-0 px-3 py-2.5 text-sm text-ink-2">{a}</td>
      <td className="min-w-0 px-3 py-2.5 text-sm text-ink-2">{b}</td>
    </tr>
  );
}

function Head({ g, side }: { g: ModelGroup; side: "A" | "B" }) {
  return (
    <div className="min-w-0">
      <span className="mr-2 inline-flex h-5 w-5 items-center justify-center rounded bg-accent/15 text-[11px] font-bold text-ink">{side}</span>
      <code className="font-mono text-xs text-ink">{g.provider}</code>
      <span className="mt-0.5 block break-all font-mono text-xs text-ink-2">
        {g.requested_model ?? "requested model not reported"}
        <span aria-hidden="true" className="text-ink-3">
          {" "}
          →{" "}
        </span>
        <span className="sr-only"> response model: </span>
        {g.response_model ?? "not reported"}
      </span>
      <span className="block text-xs text-ink-3">prompt {g.prompt_version ?? "none"}</span>
    </div>
  );
}

function Provenances({ g }: { g: ModelGroup }) {
  return (
    <span className="flex flex-wrap gap-1">
      {(["real", "synthetic", "unknown"] as const)
        .filter((p) => g.by_provenance[p] > 0)
        .map((p) => (
          <ProvenanceBadge key={p} provenance={p} count={formatInt(g.by_provenance[p])} emphasis={p === "synthetic"} />
        ))}
    </span>
  );
}

function Ttft({ g }: { g: ModelGroup }) {
  const t = g.time_to_first_chunk;
  if (t.streaming_attempts === 0) return <span className="text-ink-3">no streaming attempt</span>;
  if (!t.available) return <span className="text-ink-3">not recorded ({formatInt(t.streaming_attempts)} streaming)</span>;
  return (
    <span className="tabular">
      p50 {formatMs(t.p50_ms)} · p95 {formatMs(t.p95_ms)} · n {formatInt(t.n)} of {formatInt(t.streaming_attempts)}
      {t.low_sample.p95 && (
        <span className="ml-1">
          <Flag tone="warning" title="Fewer than 20 recorded values">
            Low sample
          </Flag>
        </span>
      )}
    </span>
  );
}

function PairTable({ a, b }: { a: ModelGroup; b: ModelGroup }) {
  const max = Math.max(a.latency.p99_ms ?? a.latency.p95_ms ?? 0, b.latency.p99_ms ?? b.latency.p95_ms ?? 0);
  return (
    <div className="relative overflow-x-auto rounded-lg border border-line" role="region" aria-label="Group A and group B side by side" tabIndex={0}>
      <table className="w-full min-w-[36rem] border-collapse text-sm" data-testid="pair-table">
        <caption className="sr-only">Group A and group B side by side: the backend&apos;s values for each group, not a ranking</caption>
        <thead className="bg-raised">
          <tr>
            <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-ink-2">
              Measure
            </th>
            <th scope="col" className="px-3 py-2 text-left font-normal">
              <Head g={a} side="A" />
            </th>
            <th scope="col" className="px-3 py-2 text-left font-normal">
              <Head g={b} side="B" />
            </th>
          </tr>
        </thead>
        <tbody>
          <Row label="Provenance (attempts)" a={<Provenances g={a} />} b={<Provenances g={b} />} />
          <Row label="Physical attempts" a={<span className="tabular">{formatInt(a.attempts)}</span>} b={<span className="tabular">{formatInt(b.attempts)}</span>} />
          <Row
            label="Error rate"
            note="span status ERROR"
            a={
              <span className="tabular">
                {formatPercent(a.errors.rate) ?? "—"} <span className="text-ink-3">({formatInt(a.errors.failed)} of {formatInt(a.attempts)})</span>
              </span>
            }
            b={
              <span className="tabular">
                {formatPercent(b.errors.rate) ?? "—"} <span className="text-ink-3">({formatInt(b.errors.failed)} of {formatInt(b.attempts)})</span>
              </span>
            }
          />
          <Row
            label="Inference latency"
            note="shared scale · bar to p95, ● p50, | p99"
            a={<LatencyRange g={a} max={max} testId="pair-latency-a" />}
            b={<LatencyRange g={b} max={max} testId="pair-latency-b" />}
          />
          <Row label="Time to first chunk" note="M20, recorded values only" a={<Ttft g={a} />} b={<Ttft g={b} />} />
          <Row label="Input tokens" a={tok(a.tokens.input_tokens, a.tokens.calls_with_unknown_input_tokens)} b={tok(b.tokens.input_tokens, b.tokens.calls_with_unknown_input_tokens)} />
          <Row label="Cached input" a={subset(a.tokens.subsets.cached_input_tokens, "input")} b={subset(b.tokens.subsets.cached_input_tokens, "input")} />
          <Row label="Output tokens" a={tok(a.tokens.output_tokens, a.tokens.calls_with_unknown_output_tokens)} b={tok(b.tokens.output_tokens, b.tokens.calls_with_unknown_output_tokens)} />
          <Row label="Reasoning output" a={subset(a.tokens.subsets.reasoning_output_tokens, "output")} b={subset(b.tokens.subsets.reasoning_output_tokens, "output")} />
          <Row
            label="Token source"
            a={<span className="text-xs">{`${formatInt(a.usage_source.provider_reported)} reported · ${formatInt(a.usage_source.estimated)} estimated · ${formatInt(a.usage_source.unknown)} unknown`}</span>}
            b={<span className="text-xs">{`${formatInt(b.usage_source.provider_reported)} reported · ${formatInt(b.usage_source.estimated)} estimated · ${formatInt(b.usage_source.unknown)} unknown`}</span>}
          />
          <Row label="Known cost (USD)" note="unknown is never $0" a={<CostValue g={a} id="a" />} b={<CostValue g={b} id="b" />} />
        </tbody>
      </table>
    </div>
  );
}

/** Hidden inputs that keep the page's filters when the pair changes (no a/b). */
function Hidden({ fields }: { fields: [string, string][] }) {
  return (
    <>
      {fields.map(([k, v]) => (
        <input key={`${k}=${v}`} type="hidden" name={k} value={v} />
      ))}
    </>
  );
}

export function ComparePanel({ data, pair, hidden, clearHref }: { data: ModelsCompare; pair: PairSelection; hidden: [string, string][]; clearHref: string }) {
  const options = data.groups.map((g) => ({ key: groupKey(g), label: `${groupLabel(g)} (${formatInt(g.attempts)} attempts)` }));
  const select = (name: "a" | "b", current: string | null) => (
    <label className="flex min-w-0 flex-1 flex-col gap-1 text-xs font-medium uppercase tracking-wide text-ink-3">
      Group {name.toUpperCase()}
      <select
        name={name}
        defaultValue={current ?? ""}
        className="h-9 w-full min-w-0 rounded-md border border-line bg-canvas px-2 text-sm normal-case tracking-normal text-ink"
        data-testid={`pair-select-${name}`}
      >
        <option value="">Choose a group…</option>
        {options.map((o) => (
          <option key={o.key} value={o.key}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
  const differences = pair.a && pair.b ? comparability(pair.a, pair.b) : [];
  const unlike = differences.filter((d) => !d.same).length;
  return (
    <section aria-labelledby="pair-heading" className="mb-8 min-w-0 rounded-xl border border-line bg-surface p-5" data-testid="model-pair">
      <SectionHeading
        id="pair-heading"
        title="Side by side"
        description="Two groups the backend returned, with their own values. Descriptive only: no statistical test is run and no group is named better."
      />
      <form method="get" action="/models" className="flex flex-wrap items-end gap-3" aria-label="Choose two groups to compare" data-testid="pair-form">
        <Hidden fields={hidden} />
        {select("a", pair.aKey)}
        {select("b", pair.bKey)}
        <button type="submit" className="h-9 rounded-md border border-line bg-raised px-4 text-sm font-medium text-ink hover:border-axis">
          Compare
        </button>
        {(pair.aKey !== null || pair.bKey !== null) && (
          <Link href={clearHref} prefetch={false} className="h-9 content-center text-sm text-ink-2 underline underline-offset-2 hover:text-ink" data-testid="pair-clear">
            Clear
          </Link>
        )}
      </form>
      {pair.missing.length > 0 && (
        <p className="mt-3 text-sm text-warning-ink" data-testid="pair-missing" role="note">
          ▲ Group {pair.missing.map((m) => m.toUpperCase()).join(" and ")} from the link is not among the groups returned for these filters and this window (or the top-N limit).
        </p>
      )}
      {pair.a && pair.b ? (
        <div className="mt-5 space-y-4">
          <PairTable a={pair.a} b={pair.b} />
          <div className="rounded-lg border border-line bg-raised/30 px-4 py-3" role="note" data-testid="pair-differences" data-unlike={unlike}>
            <p className="text-sm font-semibold text-ink">
              {unlike === 0 ? "Comparable on every listed aspect" : `${unlike} aspect(s) differ between these groups`}
            </p>
            <ul className="mt-2 space-y-1 text-sm text-ink-2">
              {differences.map((d) => (
                <li key={d.aspect} className="flex gap-2" data-aspect={d.aspect} data-same={d.same ? "true" : "false"}>
                  <span aria-hidden="true" className={d.same ? "text-ink-3" : "text-warning-ink"}>
                    {d.same ? "=" : "≠"}
                  </span>
                  <span>
                    <span className="sr-only">{d.same ? "Alike: " : "Differs: "}</span>
                    {d.text}
                  </span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-ink-3">
              These are observed groups, not a controlled experiment: they may have run on different inputs, at different times and in different numbers. A difference here is not
              evidence that one model is better. No quality score exists in this MVP.
            </p>
          </div>
        </div>
      ) : (
        <p className="mt-4 text-sm text-ink-3" data-testid="pair-empty">
          {data.groups.length < 2 ? "Only one group in this window: there is nothing to set side by side." : "Choose two groups, or use “Compare as A / B” in the table below."}
        </p>
      )}
    </section>
  );
}

