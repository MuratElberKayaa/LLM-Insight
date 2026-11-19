import { array, boolean, count, DecodeError, isoUtc, keyed, literal, nonNegativeNumber, nullable, object, positiveInt, rate, string, type Decoder } from "../validate";
import { checkPairedTest, intervalDecoder } from "./experiment-schema";

export const LIVE_RULES = ["json_object", "no_markdown", "no_duplicate_keys", "exact_keys", "category_enum", "priority_enum", "expected_category", "expected_priority"] as const;
const variant = literal("v1", "v2");
const hex32: Decoder<string> = (v, path) => {
  const id = string(32)(v, path);
  if (!/^[0-9a-f]{32}$/.test(id)) throw new DecodeError(path, "a trace/run id");
  return id;
};
const group = object({ variant, prompt_version: string(64), passed: count, evaluated: literal(6), client_p50_ms: nonNegativeNumber,
  input_tokens: count, output_tokens: count, missing_input_usage: count, missing_output_usage: count });
const groups: Decoder<ReturnType<typeof group>[]> = (v, path) => {
  const rows = array(group, 2)(v, path);
  if (rows.length !== 2 || new Set(rows.map(r => r.variant)).size !== 2 || rows.some(r => r.passed > 6 || r.missing_input_usage > 6 || r.missing_output_usage > 6 || r.prompt_version !== `live-ticket-${r.variant}`)) throw new DecodeError(path, "two complete variants");
  return rows;
};
/** Phase 9D (ADR-53): derived at read time from the stored cases; never names a winner. */
const statisticsVariant = object({ variant, passed: count, evaluated: literal(6), wilson_95: intervalDecoder });
const statisticsDecoder = (v: unknown, path: string) => {
  const s = object({
    derived_at_read: literal(true),
    variants: array(statisticsVariant, 2),
    paired_test: object({ method: literal("exact_mcnemar"), paired_cases: literal(6), v1_only: count, v2_only: count, p_value: rate, significant_at_0_05: boolean, min_discordant_for_0_05: positiveInt }),
  })(v, path);
  if (s.variants.length !== 2 || s.variants[0]!.variant !== "v1" || s.variants[1]!.variant !== "v2") throw new DecodeError(`${path}.variants`, "v1 then v2");
  const t = s.paired_test;
  checkPairedTest({ method: t.method, a_only: t.v1_only, b_only: t.v2_only, p_value: t.p_value, significant_at_0_05: t.significant_at_0_05, min_discordant_for_0_05: t.min_discordant_for_0_05 }, t.paired_cases, `${path}.paired_test`);
  if (s.variants[0]!.passed - s.variants[1]!.passed !== t.v1_only - t.v2_only) throw new DecodeError(`${path}.paired_test`, "discordant pairs matching the passes");
  return s;
};
const noWinner: Decoder<null> = (v, path) => { if (v !== null) throw new DecodeError(path, "no general winner"); return null; };
export const importedSummaryDecoder = object({
  id: positiveInt, project: positiveInt, run_id: hex32,
  model: literal("openai/gpt-oss-20b", "gemini-3.1-flash-lite"), provider: literal("groq", "gcp.gemini"),
  suite: literal("ticket-ab-v1"), rule_version: literal("ticket-category-priority-v1"), groups,
  started_at: isoUtc, finished_at: isoUtc, imported_at: isoUtc,
  cost_status: literal("unknown"), evaluation_source: literal("operator_import"), provenance: literal("real_asserted"),
  inputs: literal("synthetic"), winner: noWinner, statistical_significance: literal("exact_mcnemar"),
  statistics: statisticsDecoder,
});
const caseDecoder = object({ case_id: literal("t01", "t02", "t03", "t04", "t05", "t06"), variant,
  prompt_version: string(64), trace_id: hex32, rules: keyed(LIVE_RULES, boolean), passed: boolean,
  client_latency_ms: nonNegativeNumber, input_tokens: nullable(count), output_tokens: nullable(count) });
export const importedReportDecoder = (v: unknown, path: string) => {
  const base = importedSummaryDecoder(v, path);
  const { cases } = object({ cases: array(caseDecoder, 12) })(v, path);
  if (cases.length !== 12 || new Set(cases.map(c => c.trace_id)).size !== 12 || new Set(cases.map(c => `${c.case_id}/${c.variant}`)).size !== 12 || cases.some(c => c.passed !== LIVE_RULES.every(k => c.rules[k]) || c.prompt_version !== `live-ticket-${c.variant}`)) throw new DecodeError(path, "complete, consistent paired evidence");
  for (const g of base.groups) if (cases.filter(c => c.variant === g.variant && c.passed).length !== g.passed) throw new DecodeError(path, "scores matching the case rules");
  for (const s of base.statistics.variants) if (base.groups.find(g => g.variant === s.variant)!.passed !== s.passed) throw new DecodeError(`${path}.statistics`, "statistics matching the groups");
  return { ...base, cases };
};
export const importedListDecoder = object({ items: array(importedSummaryDecoder, 200), truncated: boolean });
export type ImportedSummary = ReturnType<typeof importedSummaryDecoder>;
export type ImportedReport = ReturnType<typeof importedReportDecoder>;
