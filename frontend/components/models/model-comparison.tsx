import type { ReactNode } from "react";
import type { ModelGroup, ModelsCompare } from "../../lib/api/types";
import { formatUsd } from "../../lib/decimal";
import { formatInt, formatMs, formatPercent } from "../../lib/format";
import { CHART_GROUP_LIMIT, compareNotice, groupKey, toGroupPoints } from "../../lib/model-groups";
import { ChartCard, LegendItem } from "../chart-card";
import { GroupAttemptsChart, GroupLatencyChart } from "../charts/model-charts";
import { DataTable, NullValue, type Column } from "../data-table";
import { Flag } from "../metric-card";
import { SectionHeading } from "../page-header";
import { ProvenanceBadge } from "../provenance-badge";
import { Notice } from "../states";

const REASON_LABEL = { price_missing: "no price", usage_missing: "no usage", usage_inconsistent: "inconsistent usage" } as const;

function Code({ value, missing }: { value: string | null; missing: string }) {
  return value === null ? <NullValue label={missing}>not reported</NullValue> : <code className="break-all font-mono text-xs">{value}</code>;
}

function GroupName({ g }: { g: ModelGroup }) {
  return (
    <span className="block min-w-0" data-testid="group-name">
      <span className="block text-ink">
        <code className="font-mono text-xs">{g.provider}</code>
      </span>
      <span className="block">
        <Code value={g.requested_model} missing="requested model not reported" />
        <span aria-hidden="true" className="text-ink-3">
          {" "}
          →{" "}
        </span>
        <span className="sr-only"> response model: </span>
        <Code value={g.response_model} missing="response model not reported" />
      </span>
      <span className="block text-xs text-ink-3">
        Prompt version{" "}
        {g.prompt_version === null ? <NullValue label="no prompt version">none</NullValue> : <code className="font-mono">{g.prompt_version}</code>}
      </span>
    </span>
  );
}

function Errors({ g }: { g: ModelGroup }) {
  return (
    <span title={`${g.errors.failed} of ${g.attempts} failed`}>
      {formatPercent(g.errors.rate) ?? <NullValue />}
      <span className="block text-xs text-ink-3">
        {formatInt(g.errors.failed)} failed
        {g.errors.failed > 0 && (
          <span className="text-critical-ink">
            {" "}
            <span aria-hidden="true">✕</span>
          </span>
        )}
      </span>
    </span>
  );
}

function LatencyCell({ g }: { g: ModelGroup }) {
  const l = g.latency;
  return (
    <span className="block" data-testid="group-latency">
      <span className="block">
        p50 {formatMs(l.p50_ms) ?? <NullValue>no sample</NullValue>} · p95 {formatMs(l.p95_ms) ?? <NullValue>no sample</NullValue>}
        {l.low_sample.p95 && (
          <span className="ml-1 text-warning" title="Fewer than 20 samples: the p95 is not reliable">
            ▲<span className="sr-only"> low sample for p95</span>
          </span>
        )}
      </span>
      <span className="block text-xs text-ink-3">
        p99 {formatMs(l.p99_ms) ?? "no sample"}
        {l.low_sample.p99 ? " (low sample)" : ""} · n {formatInt(l.n)}
      </span>
    </span>
  );
}

function TokensCell({ g }: { g: ModelGroup }) {
  const t = g.tokens;
  const unknownIn = t.calls_with_unknown_input_tokens;
  const unknownOut = t.calls_with_unknown_output_tokens;
  return (
    <span className="block" data-testid="group-tokens">
      <span className="block">
        {t.input_tokens === null ? <NullValue>unknown</NullValue> : formatInt(t.input_tokens)} / {t.output_tokens === null ? <NullValue>unknown</NullValue> : formatInt(t.output_tokens)}
      </span>
      {(unknownIn > 0 || unknownOut > 0) && (
        <span className="block text-xs text-ink-3">
          unknown: {formatInt(unknownIn)} in / {formatInt(unknownOut)} out of {formatInt(t.n)}
        </span>
      )}
      <span className="block text-xs text-ink-3">
        {t.subsets.cached_input_tokens.sum === null && t.subsets.reasoning_output_tokens.sum === null
          ? "subsets not reported"
          : `subsets: cached ${t.subsets.cached_input_tokens.sum === null ? "n/r" : formatInt(t.subsets.cached_input_tokens.sum)} · reasoning ${t.subsets.reasoning_output_tokens.sum === null ? "n/r" : formatInt(t.subsets.reasoning_output_tokens.sum)}`}
      </span>
      {g.usage_source.estimated > 0 && <span className="block text-xs text-warning">{formatInt(g.usage_source.estimated)} estimated</span>}
    </span>
  );
}

function CostCell({ g }: { g: ModelGroup }) {
  const c = g.cost;
  const reasons = (Object.keys(REASON_LABEL) as (keyof typeof REASON_LABEL)[]).filter((r) => c.unknown_cost_by_reason[r] > 0);
  const known = c.known_cost_usd === null ? null : formatUsd(c.known_cost_usd);
  return (
    <span className="block" data-testid="group-cost" data-cost-state={c.known_cost_usd === null ? "unknown" : c.cost_complete ? "complete" : "incomplete"}>
      <span className="block">
        {known === null ? (
          <NullValue label="no priced attempt">unknown</NullValue>
        ) : (
          <span title={`Exact: ${known.exact}`} data-exact={c.known_cost_usd ?? ""}>
            {known.text}
          </span>
        )}
        {known !== null && !c.cost_complete && (
          <span className="ml-1">
            <Flag tone="warning" title="Some attempts have no computed cost; the known cost is a lower bound">
              Incomplete
            </Flag>
          </span>
        )}
      </span>
      <span className="block text-xs text-ink-3">
        {formatInt(c.computed_cost_calls)} priced · {formatInt(c.unknown_cost_calls)} unknown
      </span>
      {reasons.length > 0 && <span className="block text-xs text-ink-3">({reasons.map((r) => `${formatInt(c.unknown_cost_by_reason[r])} ${REASON_LABEL[r]}`).join(", ")})</span>}
    </span>
  );
}

function Provenance({ g }: { g: ModelGroup }) {
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

const COLUMNS: Column<ModelGroup>[] = [
  { key: "group", header: "Provider · requested → response model · prompt", rowHeader: true, render: (g) => <GroupName g={g} /> },
  { key: "prov", header: "Provenance (attempts)", render: (g) => <Provenance g={g} /> },
  { key: "attempts", header: "Attempts", align: "right", render: (g) => <span data-testid="group-attempts">{formatInt(g.attempts)}</span> },
  { key: "errors", header: "Error rate", align: "right", render: (g) => <Errors g={g} /> },
  { key: "latency", header: "Latency (attempt)", render: (g) => <LatencyCell g={g} /> },
  { key: "tokens", header: "Tokens in / out", render: (g) => <TokensCell g={g} /> },
  { key: "cost", header: "Known cost (USD)", render: (g) => <CostCell g={g} /> },
];

function Item({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-ink-3">{label}</dt>
      <dd className="text-sm text-ink-2">{children}</dd>
    </div>
  );
}

/** Below `md`: one card per group with the same values as the table row. */
function GroupCards({ groups }: { groups: ModelGroup[] }) {
  return (
    <ul className="space-y-3 md:hidden" aria-label="Model groups" data-testid="group-cards">
      {groups.map((g) => (
        <li key={groupKey(g)} className="rounded-lg border border-line bg-surface px-4 py-3">
          <GroupName g={g} />
          <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2">
            <Item label="Attempts">{formatInt(g.attempts)}</Item>
            <Item label="Error rate">
              <Errors g={g} />
            </Item>
            <div className="col-span-2">
              <Item label="Latency (attempt)">
                <LatencyCell g={g} />
              </Item>
            </div>
            <div className="col-span-2">
              <Item label="Tokens in / out">
                <TokensCell g={g} />
              </Item>
            </div>
            <div className="col-span-2">
              <Item label="Known cost (USD)">
                <CostCell g={g} />
              </Item>
            </div>
            <div className="col-span-2">
              <Item label="Provenance (attempts)">
                <Provenance g={g} />
              </Item>
            </div>
          </dl>
        </li>
      ))}
    </ul>
  );
}

export function ComparisonNotices({ data }: { data: ModelsCompare }) {
  const n = compareNotice(data);
  return (
    <div className="mb-5 space-y-3">
      {n.present.includes("synthetic") && (
        <Notice tone="caution" title={n.syntheticOnly ? "SYNTHETIC data only" : "Contains SYNTHETIC data"} testId="compare-synthetic">
          {n.syntheticOnly
            ? "Every attempt in these groups was produced by mock or fake providers. These numbers describe test traffic, not a real provider."
            : "Some attempts were produced by mock or fake providers; the provenance column shows the split per group."}
        </Notice>
      )}
      <Notice tone="info" title="Groups are not ranked" testId="compare-not-ranked">
        Groups are listed by number of attempts, as returned by the backend. They can differ in sample size, prompt version, provenance and cost completeness, so no group is presented as the best.
        {n.anyLowSample ? " ▲ marks a p95 from fewer than 20 samples." : ""}
        {n.anyUnknownCost ? " Unknown cost is never counted as $0; a known cost with unknown calls is a lower bound." : ""}
        {n.anyEstimated ? " Some token counts are estimated rather than provider-reported." : ""} No quality score exists in this MVP.
      </Notice>
    </div>
  );
}

function PointsTable({ data, kind }: { data: ModelsCompare; kind: "attempts" | "latency" }) {
  const rows = data.groups.slice(0, CHART_GROUP_LIMIT);
  const cols: Column<ModelGroup>[] =
    kind === "attempts"
      ? [
          { key: "g", header: "Group", rowHeader: true, render: (g) => <GroupName g={g} /> },
          { key: "a", header: "Attempts", align: "right", render: (g) => formatInt(g.attempts) },
          { key: "s", header: "Not failed", align: "right", render: (g) => formatInt(g.errors.succeeded) },
          { key: "f", header: "Failed", align: "right", render: (g) => formatInt(g.errors.failed) },
        ]
      : [
          { key: "g", header: "Group", rowHeader: true, render: (g) => <GroupName g={g} /> },
          { key: "p50", header: "p50", align: "right", render: (g) => formatMs(g.latency.p50_ms) ?? "no sample" },
          { key: "p95", header: "p95", align: "right", render: (g) => formatMs(g.latency.p95_ms) ?? "no sample" },
          { key: "n", header: "n", align: "right", render: (g) => formatInt(g.latency.n) + (g.latency.low_sample.p95 ? " (low)" : "") },
        ];
  return <DataTable caption={kind === "attempts" ? "Attempts per group" : "Latency per group"} captionHidden columns={cols} rows={rows} rowKey={groupKey} />;
}

export function ComparisonCharts({ data }: { data: ModelsCompare }) {
  const points = toGroupPoints(data.groups);
  const more = data.groups.length > CHART_GROUP_LIMIT ? ` First ${CHART_GROUP_LIMIT} groups; the table lists all ${data.groups.length}.` : "";
  return (
    <div className="mb-8 grid grid-cols-1 gap-6 xl:grid-cols-2">
      <ChartCard
        id="group-attempts"
        title="Inference attempts per group"
        description={`Physical attempts (M02) split by span status (M04), backend order.${more}`}
        legend={
          <span className="flex flex-wrap gap-3">
            <LegendItem swatchClass="bg-series-1" label="Not failed" />
            <LegendItem swatchClass="bg-critical" label="Failed ✕" />
          </span>
        }
        table={<PointsTable data={data} kind="attempts" />}
      >
        <GroupAttemptsChart points={points} />
      </ChartCard>
      <ChartCard
        id="group-latency"
        title="p95 attempt latency per group (ms)"
        description={`From span durations (M06). ▲ = fewer than 20 samples. Groups without a sample have no bar.${more}`}
        table={<PointsTable data={data} kind="latency" />}
      >
        <GroupLatencyChart points={points} />
      </ChartCard>
    </div>
  );
}

export function ComparisonTable({ data }: { data: ModelsCompare }) {
  return (
    <section aria-labelledby="compare-table-heading" className="min-w-0">
      <SectionHeading
        id="compare-table-heading"
        title="Comparison table"
        description="One row per provider, requested model, response model and prompt version (M15). Exact backend values; unknown stays unknown."
      />
      <div className="hidden md:block">
        <DataTable caption="Model and provider groups (GET /api/v1/models/compare)" captionHidden columns={COLUMNS} rows={data.groups} rowKey={groupKey} testId="compare-table" />
      </div>
      <GroupCards groups={data.groups} />
      {data.groups_truncated && (
        <p className="mt-2 text-xs text-ink-3" data-testid="groups-truncated">
          <Flag tone="neutral">Top {data.limit}</Flag> More groups exist than are shown; raise the group limit to see more.
        </p>
      )}
    </section>
  );
}
