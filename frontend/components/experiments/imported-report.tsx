import Link from "next/link";
import { DataTable } from "../data-table";
import type { ImportedReport, ImportedSummary } from "../../lib/api/imported-experiments";
import { LIVE_RULES } from "../../lib/api/imported-experiments";

export const modelLabel = (model: string) => model === "openai/gpt-oss-20b" ? "Groq · GPT-OSS 20B" : "Gemini · Flash Lite";
const labels = { t01: "Login blocked", t02: "Invoice download workaround", t03: "Urgent feature request", t04: "Saving blocked", t05: "Browser login workaround", t06: "User guide question" };
const ruleLabels = { json_object: "JSON object", no_markdown: "No Markdown", no_duplicate_keys: "Unique keys", exact_keys: "Exact fields", category_enum: "Category label", priority_enum: "Priority label", expected_category: "Expected category", expected_priority: "Expected priority" };

export function ImportedList({ items }: { items: ImportedSummary[] }) {
  return <DataTable caption="Completed live A/B comparisons" testId="live-experiment-table" rows={items} rowKey={r => String(r.id)} empty="No completed live comparison imported for this project." columns={[
    { key: "model", header: "Model", rowHeader: true, render: r => <Link className="underline underline-offset-2" href={`/experiments/live/${r.id}?project=${r.project}`}>{modelLabel(r.model)}</Link> },
    { key: "prompts", header: "Prompt versions", render: () => <span>live-ticket-v1 vs live-ticket-v2</span> },
    { key: "v1", header: "V1 passed", render: r => `${r.groups.find(g => g.variant === "v1")!.passed} / 6` },
    { key: "v2", header: "V2 passed", render: r => `${r.groups.find(g => g.variant === "v2")!.passed} / 6` },
    { key: "source", header: "Source", render: () => "Live calls · imported results" },
    { key: "calls", header: "Calls", render: () => "12 · 6 paired cases" },
  ]} />;
}

export function ImportedReportView({ report }: { report: ImportedReport }) {
  return <div className="space-y-6" data-testid="live-experiment-report">
    <section className="rounded-lg border border-series-1/50 bg-series-1/10 p-4 space-y-2">
      <h2 className="text-lg font-semibold">Real provider calls · synthetic test inputs</h2>
      <p className="text-sm text-ink-2">12 completed calls, six paired cases, eight fixed checks. Opening this report sends no model request.</p>
      <p className="text-sm text-ink-2">Telemetry was checked against this project’s traces at import. Rule results come from the recorded evaluator; raw responses are not stored and are not re-evaluated here.</p>
      <p className="text-sm text-ink-2">Six cases, one sample per version: no general winner or statistical significance. Cost is unknown. “Real” is the SDK’s declared provenance.</p>
    </section>
    <div className="grid gap-4 md:grid-cols-2">
      <section className="rounded-lg border border-line bg-raised p-4"><h2 className="font-semibold">V1 · Short instruction</h2><p className="mt-2 text-sm text-ink-2">Choose category and priority; return only the two required JSON fields.</p></section>
      <section className="rounded-lg border border-line bg-raised p-4"><h2 className="font-semibold">V2 · Explicit business rules</h2><p className="mt-2 text-sm text-ink-2">Adds category definitions and impact-based priority, ignoring urgency words. Requires JSON without Markdown.</p></section>
    </div>
    <DataTable caption="Observed results on this six-case dataset" rows={report.groups} rowKey={g => g.variant} columns={[
      { key: "version", header: "Prompt", rowHeader: true, render: g => g.prompt_version },
      { key: "passed", header: "All checks passed", render: g => <strong>{g.passed} / {g.evaluated}</strong> },
      { key: "latency", header: "CLIENT p50", render: g => `${g.client_p50_ms.toFixed(1)} ms` },
      { key: "tokens", header: "Reported tokens (input / output)", render: g => <span>{g.input_tokens} / {g.output_tokens}{(g.missing_input_usage + g.missing_output_usage > 0) && " · incomplete usage"}</span> },
      { key: "cost", header: "Cost", render: () => "Unknown" },
    ]} />
    <DataTable caption="Paired cases · a pass requires every fixed check" testId="live-case-table" rows={Object.keys(labels) as (keyof typeof labels)[]} rowKey={c => c} columns={[
      { key: "case", header: "Case", rowHeader: true, render: c => <span>{c} · {labels[c]}</span> },
      ...(["v1", "v2"] as const).map(variant => ({ key: variant, header: variant.toUpperCase(), render: (id: keyof typeof labels) => {
        const c = report.cases.find(r => r.case_id === id && r.variant === variant)!;
        return <div className="max-w-xs space-y-1 py-2"><strong>{c.passed ? "Passed" : "Failed checks"}</strong><p className="text-xs text-ink-2">{LIVE_RULES.filter(k => !c.rules[k]).map(k => ruleLabels[k]).join(", ") || "All eight checks passed"}</p><Link className="text-xs underline underline-offset-2" href={`/traces/${c.trace_id}?project=${report.project}`}>Open trace · {c.trace_id.slice(0, 8)}</Link></div>;
      } })),
    ]} />
    <section className="rounded-lg border border-line p-4 text-sm text-ink-2 space-y-2">
      <h2 className="font-semibold text-ink">Reading the result</h2>
      <p>A strict pass includes both format and label checks. A higher total does not necessarily mean higher category/priority accuracy; inspect the failed rules above.</p>
      <p>The invoice case can overlap billing and bug definitions. A reference-label mismatch alone does not establish model error. The original rubric is kept unchanged.</p>
      <p>These are live-ticket-v1/v2, actual instructions sent to the models. The older golden-prompt-v1/v2 mock fixtures remain separate.</p>
      <details><summary className="cursor-pointer">Evidence identifiers</summary><p className="mt-2 break-all">Run: {report.run_id} · Suite: {report.suite} · Rule: {report.rule_version}</p><p>Started: {report.started_at} · Finished: {report.finished_at}</p></details>
    </section>
  </div>;
}
