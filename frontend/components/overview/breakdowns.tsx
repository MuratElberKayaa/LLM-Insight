import type { CostUnknownReason, Overview, TraceProvenance, UsageSource } from "../../lib/api/types";
import { formatUsd } from "../../lib/decimal";
import { formatInt } from "../../lib/format";
import { DataTable, NullValue, type Column } from "../data-table";
import { SectionHeading } from "../page-header";
import { ProvenanceBadge } from "../provenance-badge";

function Money({ value, empty }: { value: string | null; empty: string }) {
  if (value === null) return <NullValue label={empty}>{empty}</NullValue>;
  const f = formatUsd(value);
  return f === null ? <NullValue label="invalid" /> : <span title={`Exact: ${f.exact}`}>{f.text}</span>;
}

// ---------------------------------------------------------------- provenance breakdown
interface ProvRow {
  key: TraceProvenance | "total";
  traces: number;
  errorTraces: number;
  attempts: number | null; // null: no usage-level bucket (mixed is trace-level only)
  failed: number | null;
  knownCost: string | null;
  usageBucket: boolean;
  unknownCost: number | null;
}

export function ProvenanceTable({ data }: { data: Overview }) {
  const t = data.traces;
  const i = data.inference;
  const rows: ProvRow[] = (["real", "synthetic", "mixed", "unknown"] as const).map((p) => {
    const usage = p === "mixed" ? null : p;
    return {
      key: p,
      traces: t.count.by_provenance[p],
      errorTraces: t.error_rate.by_provenance[p].error_traces,
      attempts: usage ? i.attempts.by_provenance[usage] : null,
      failed: usage ? i.errors.by_provenance[usage].failed : null,
      knownCost: usage ? i.cost.by_provenance[usage].known_cost_usd : null,
      usageBucket: usage !== null,
      unknownCost: usage ? i.cost.by_provenance[usage].unknown_cost_calls : null,
    };
  });
  rows.push({
    key: "total",
    traces: t.count.total,
    errorTraces: t.error_rate.total.error_traces,
    attempts: i.attempts.total,
    failed: i.errors.total.failed,
    knownCost: i.cost.total.known_cost_usd,
    usageBucket: true,
    unknownCost: i.cost.total.unknown_cost_calls,
  });
  const na = <NullValue label="no usage-level bucket: inference attempts are never mixed">n/a</NullValue>;
  const cols: Column<ProvRow>[] = [
    {
      key: "p",
      header: "Provenance",
      rowHeader: true,
      render: (r) => (r.key === "total" ? "All provenance" : <ProvenanceBadge provenance={r.key} emphasis={r.key === "synthetic"} />),
    },
    { key: "traces", header: "Traces", align: "right", render: (r) => formatInt(r.traces) },
    { key: "errTraces", header: "Error traces", align: "right", render: (r) => formatInt(r.errorTraces) },
    { key: "attempts", header: "Inference attempts", align: "right", render: (r) => (r.attempts === null ? na : formatInt(r.attempts)) },
    { key: "failed", header: "Failed", align: "right", render: (r) => (r.failed === null ? na : formatInt(r.failed)) },
    { key: "cost", header: "Known cost", align: "right", render: (r) => (r.usageBucket ? <Money value={r.knownCost} empty="none computed" /> : na) },
    { key: "unk", header: "Unknown-cost calls", align: "right", render: (r) => (r.unknownCost === null ? na : formatInt(r.unknownCost)) },
  ];
  return (
    <section aria-labelledby="prov-heading" className="min-w-0">
      <SectionHeading
        id="prov-heading"
        title="Provenance breakdown"
        description="Trace columns use trace provenance; attempt and cost columns use each attempt's own provenance. Totals come from the backend, not from adding rows."
      />
      <DataTable caption="Traces, inference attempts and cost by provenance" captionHidden columns={cols} rows={rows} rowKey={(r) => r.key} testId="table-provenance" />
    </section>
  );
}

// ---------------------------------------------------------------- cost details
const REASON_LABEL: Record<CostUnknownReason, string> = {
  price_missing: "No verified price for the model, tier or time",
  usage_missing: "Provider usage not reported",
  usage_inconsistent: "Reported usage inconsistent",
};

const SOURCE_LABEL: Record<UsageSource, string> = {
  provider_reported: "Provider reported",
  estimated: "Estimated",
  unknown: "Unknown",
};

export function CostDetails({ data }: { data: Overview }) {
  const cost = data.inference.cost.total;
  const sources = data.inference.usage_source.total;
  const prices = data.inference.price_versions;
  const reasons = (Object.keys(REASON_LABEL) as CostUnknownReason[]).map((r) => ({ r, n: cost.unknown_cost_by_reason[r] }));
  return (
    <section aria-labelledby="cost-heading" className="min-w-0 space-y-4">
      <SectionHeading id="cost-heading" title="Cost and usage sources" description="Why a cost is unknown, which price versions priced the known cost, and where token counts came from." />
      <DataTable
        caption="Unknown-cost attempts by reason (M11)"
        columns={[
          { key: "r", header: "Reason", rowHeader: true, render: (x) => REASON_LABEL[x.r] },
          { key: "n", header: "Attempts", align: "right", render: (x) => formatInt(x.n) },
        ]}
        rows={reasons}
        rowKey={(x) => x.r}
        testId="table-unknown-reasons"
      />
      <DataTable
        caption="Price versions used for known cost (M13)"
        columns={[
          { key: "m", header: "Provider / model", rowHeader: true, render: (x: Overview["inference"]["price_versions"]["items"][number]) => `${x.provider} / ${x.model}` },
          { key: "v", header: "Price version", render: (x) => x.price_version ?? <NullValue /> },
          { key: "c", header: "Priced attempts", align: "right", render: (x) => formatInt(x.calls) },
        ]}
        rows={prices.items}
        rowKey={(x, i) => `${x.provider}/${x.model}/${x.price_version ?? ""}/${i}`}
        empty="No attempt in this window has a computed cost."
        testId="table-price-versions"
      />
      {prices.truncated && <p className="text-xs text-ink-3">Price-version list truncated by the backend.</p>}
      <DataTable
        caption="Token usage source (M14)"
        columns={[
          { key: "s", header: "Source", rowHeader: true, render: (x: { s: UsageSource; n: number }) => SOURCE_LABEL[x.s] },
          { key: "n", header: "Attempts", align: "right", render: (x) => formatInt(x.n) },
        ]}
        rows={(Object.keys(SOURCE_LABEL) as UsageSource[]).map((s) => ({ s, n: sources[s] }))}
        rowKey={(x) => x.s}
        testId="table-usage-source"
      />
    </section>
  );
}

// ---------------------------------------------------------------- errors
export function ErrorTypes({ data }: { data: Overview }) {
  const e = data.inference.errors;
  return (
    <section aria-labelledby="errors-heading" className="min-w-0">
      <SectionHeading id="errors-heading" title="Failed attempts by error type" description="The instrumentation's error.type identifier; a hypothesis about cause is not implied." />
      <DataTable
        caption="Failed inference attempts by error type (M04)"
        captionHidden
        columns={[
          { key: "t", header: "Error type", rowHeader: true, render: (x: (typeof e.by_error_type)[number]) => (x.error_type === null ? <NullValue>not reported</NullValue> : <code className="font-mono text-xs">{x.error_type}</code>) },
          { key: "n", header: "Failed attempts", align: "right", render: (x) => formatInt(x.count) },
        ]}
        rows={e.by_error_type}
        rowKey={(x, i) => `${x.error_type ?? "null"}-${i}`}
        empty="No failed inference attempts in this window."
        testId="table-error-types"
      />
      {e.by_error_type_truncated && <p className="mt-2 text-xs text-ink-3">Showing the 50 most frequent error types (backend limit).</p>}
    </section>
  );
}
