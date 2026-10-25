import type { ApiResult } from "../../lib/api/result";
import type { ModelGroup, ModelsCompare } from "../../lib/api/types";
import { formatUsd } from "../../lib/decimal";
import { formatInt, formatMs, formatPercent } from "../../lib/format";
import { DataTable, NullValue, type Column } from "../data-table";
import { Flag } from "../metric-card";
import { SectionHeading } from "../page-header";
import { ProvenanceBadge } from "../provenance-badge";
import { ErrorState } from "../states";

const label = (v: string | null) => (v === null ? <NullValue /> : <code className="font-mono text-xs">{v}</code>);

const COLUMNS: Column<ModelGroup>[] = [
  { key: "provider", header: "Provider", rowHeader: true, render: (g) => <code className="font-mono text-xs">{g.provider}</code> },
  {
    key: "model",
    header: (
      <>
        Requested model
        <span className="block font-normal text-ink-3">→ response model</span>
      </>
    ),
    render: (g) => (
      <span className="block">
        <span className="block">{label(g.requested_model)}</span>
        <span className="block text-ink-3">
          <span aria-hidden="true">→ </span>
          <span className="sr-only">response model: </span>
          {label(g.response_model)}
        </span>
      </span>
    ),
  },
  { key: "pv", header: "Prompt version", render: (g) => label(g.prompt_version) },
  { key: "attempts", header: "Attempts", align: "right", render: (g) => formatInt(g.attempts) },
  {
    key: "err",
    header: "Error rate",
    align: "right",
    render: (g) => (
      <span title={`${g.errors.failed} of ${g.attempts} failed`}>{formatPercent(g.errors.rate) ?? <NullValue />}</span>
    ),
  },
  {
    key: "p95",
    header: "p95 latency (n)",
    align: "right",
    render: (g) => (
      <span>
        {formatMs(g.latency.p95_ms) ?? <NullValue />} <span className="text-ink-3">({formatInt(g.latency.n)})</span>
        {g.latency.low_sample.p95 && (
          <span className="ml-1 text-warning" title="Fewer than 20 samples">
            ▲<span className="sr-only"> low sample</span>
          </span>
        )}
      </span>
    ),
  },
  {
    key: "cost",
    header: "Known cost",
    align: "right",
    render: (g) => {
      if (g.cost.known_cost_usd === null) return <NullValue label="no priced attempt">unknown</NullValue>;
      const f = formatUsd(g.cost.known_cost_usd);
      return <span title={f ? `Exact: ${f.exact}` : undefined}>{f?.text ?? "—"}</span>;
    },
  },
  {
    key: "unk",
    header: "Unknown-cost calls",
    align: "right",
    render: (g) => (g.cost.unknown_cost_calls > 0 ? <span className="text-ink">{formatInt(g.cost.unknown_cost_calls)}</span> : "0"),
  },
  {
    key: "prov",
    header: "Provenance (attempts)",
    render: (g) => (
      <span className="flex flex-wrap gap-1">
        {(["real", "synthetic", "unknown"] as const)
          .filter((p) => g.by_provenance[p] > 0)
          .map((p) => (
            <ProvenanceBadge key={p} provenance={p} count={formatInt(g.by_provenance[p])} />
          ))}
      </span>
    ),
  },
];

/** Top model groups (M15). A failed compare request degrades this section only. */
export function ModelGroups({ result }: { result: ApiResult<ModelsCompare> }) {
  return (
    <section aria-labelledby="models-heading" className="mt-8 min-w-0">
      <SectionHeading
        id="models-heading"
        title="Model and provider activity"
        description="Largest groups by attempts per provider, requested model, response model and prompt version (backend top-N)."
      />
      {result.ok ? (
        <>
          <DataTable
            caption="Inference attempts by model group (M15)"
            captionHidden
            columns={COLUMNS}
            rows={result.data.groups}
            rowKey={(g, i) => `${g.provider}|${g.requested_model ?? ""}|${g.response_model ?? ""}|${g.prompt_version ?? ""}|${i}`}
            empty="No inference attempts in this window."
            testId="table-model-groups"
          />
          {result.data.groups_truncated && (
            <p className="mt-2 text-xs text-ink-3">
              <Flag tone="neutral">Top {result.data.limit}</Flag> More groups exist than are shown.
            </p>
          )}
        </>
      ) : (
        <ErrorState error={result.error} resource="the model comparison" compact />
      )}
    </section>
  );
}
