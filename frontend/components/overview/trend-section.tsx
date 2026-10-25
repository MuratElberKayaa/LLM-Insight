import type { Overview } from "../../lib/api/types";
import { formatInt } from "../../lib/format";
import { toTrendPoints, type TrendPoint } from "../../lib/trend";
import { ChartCard, LegendItem } from "../chart-card";
import { AttemptsChart, CostChart, TracesChart } from "../charts/trend-charts";
import { DataTable, NullValue, type Column } from "../data-table";
import { SectionHeading } from "../page-header";

const bucketCol: Column<TrendPoint> = { key: "bucket", header: "Bucket (UTC)", rowHeader: true, render: (p) => p.label };

const attemptsCols: Column<TrendPoint>[] = [
  bucketCol,
  { key: "attempts", header: "Attempts", align: "right", render: (p) => formatInt(p.attempts) },
  { key: "failed", header: "Failed", align: "right", render: (p) => formatInt(p.failedAttempts) },
];

const tracesCols: Column<TrendPoint>[] = [
  bucketCol,
  { key: "traces", header: "Traces", align: "right", render: (p) => formatInt(p.traces) },
  { key: "errors", header: "Error traces", align: "right", render: (p) => formatInt(p.errorTraces) },
];

const costCols: Column<TrendPoint>[] = [
  bucketCol,
  {
    key: "known",
    header: "Known cost (USD, exact)",
    align: "right",
    render: (p) => (p.knownCostText === null ? <NullValue label="no computed cost in this bucket">none computed</NullValue> : p.knownCostText),
  },
  { key: "unknown", header: "Calls with unknown cost", align: "right", render: (p) => formatInt(p.unknownCostCalls) },
];

export function TrendSection({ data }: { data: Overview }) {
  const points = toTrendPoints(data.trend);
  const unit = data.trend.bucket === "hour" ? "per UTC hour" : "per UTC day";
  const key = (p: TrendPoint) => p.start;
  const last = points.at(-1);
  return (
    <section aria-labelledby="trend-heading" className="mt-8">
      <SectionHeading
        id="trend-heading"
        title="Trends"
        description={`Backend trend buckets ${unit}${last ? `; the last bucket (${last.label}) may still be filling` : ""}.`}
      />
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <ChartCard
          id="chart-attempts"
          title="Inference attempts"
          description={`Physical attempts ${unit}, split by span status`}
          legend={
            <div className="flex gap-3">
              <LegendItem swatchClass="bg-series-1" label="Not failed" />
              <LegendItem swatchClass="bg-critical" label="Failed" />
            </div>
          }
          table={<DataTable caption={`Inference attempts ${unit}`} captionHidden columns={attemptsCols} rows={points} rowKey={key} testId="table-attempts" />}
        >
          <AttemptsChart data={points} />
        </ChartCard>
        <ChartCard
          id="chart-traces"
          title="Traces"
          description={`Traces by start time ${unit}, split by trace status`}
          legend={
            <div className="flex gap-3">
              <LegendItem swatchClass="bg-series-1" label="Not error" />
              <LegendItem swatchClass="bg-critical" label="Error" />
            </div>
          }
          table={<DataTable caption={`Traces ${unit}`} captionHidden columns={tracesCols} rows={points} rowKey={key} testId="table-traces" />}
        >
          <TracesChart data={points} />
        </ChartCard>
        <ChartCard
          id="chart-cost"
          title="Known cost (USD, estimate)"
          description="Computed rows only. A bucket without a bar has no priced call; unknown-cost calls are in the table and tooltip."
          table={<DataTable caption={`Known cost ${unit}`} captionHidden columns={costCols} rows={points} rowKey={key} testId="table-cost" />}
        >
          <CostChart data={points} />
        </ChartCard>
      </div>
    </section>
  );
}
