/**
 * SYNTHETIC test fixtures shaped exactly like `GET /api/v1/overview` and
 * `GET /api/v1/models/compare` (backend/app/services/metrics.py). Used by unit tests only;
 * production components never import this file (tests/unit/security-static.test.ts).
 */

import type { Cost, Latency, ModelsCompare, Overview, ReferenceListPrice, TimeToFirstChunk, Tokens } from "../../lib/api/types";

const latency = (n: number, p50: number | null, p95: number | null, p99: number | null): Latency => ({
  n,
  p50_ms: p50,
  p95_ms: p95,
  p99_ms: p99,
  low_sample: { p95: n < 20, p99: n < 100 },
});

const tokens = (n: number, input: number | null, output: number | null, unknownIn = 0, unknownOut = 0): Tokens => ({
  n,
  input_tokens: input,
  calls_with_unknown_input_tokens: unknownIn,
  output_tokens: output,
  calls_with_unknown_output_tokens: unknownOut,
  subsets: {
    cached_input_tokens: { sum: null, calls_reporting: 0 },
    cache_write_input_tokens: { sum: null, calls_reporting: 0 },
    reasoning_output_tokens: { sum: null, calls_reporting: 0 },
  },
});

const cost = (n: number, known: string | null, computed: number, unknown: number, perCall: string | null): Cost => ({
  n,
  currency: "USD",
  known_cost_usd: known,
  computed_cost_calls: computed,
  unknown_cost_calls: unknown,
  unknown_cost_by_reason: { price_missing: unknown, usage_missing: 0, usage_inconsistent: 0 },
  cost_complete: n === 0 ? null : unknown === 0,
  known_cost_per_call_usd: perCall,
});

const empty3 = <T,>(v: T) => ({ real: v, synthetic: v, unknown: v });
/** M20 with no streaming attempt in scope (Phase 10B). */
export const NO_TTFC: TimeToFirstChunk = {
  available: false,
  reason: "no_streaming_attempts",
  detail: "no streaming inference attempt (gen_ai.request.stream true) in scope",
  streaming_attempts: 0,
  n: 0,
  p50_ms: null,
  p95_ms: null,
  low_sample: { p95: true },
};
const empty4 = <T,>(v: T) => ({ real: v, synthetic: v, mixed: v, unknown: v });

/** 6 synthetic attempts in 4 synthetic traces; 1 failed; 4 priced, 2 unknown cost. */
export function makeOverview(): Overview {
  return {
    project: 1,
    window: { from: "2026-10-24T15:00:00.000000Z", to: "2026-10-25T15:00:00.000000Z" },
    filters: { model: null, status: null, prompt_version: null, provenance: null, provider: null },
    provenance_label: "all provenance",
    provenance_verified: false,
    traces: {
      count: { total: 4, by_provenance: { real: 0, synthetic: 4, mixed: 0, unknown: 0 } },
      error_rate: {
        total: { n: 4, error_traces: 1, rate: 0.25 },
        by_provenance: { ...empty4({ n: 0, error_traces: 0, rate: null }), synthetic: { n: 4, error_traces: 1, rate: 0.25 } },
      },
      latency: {
        total: latency(4, 120.5, 410.25, 450.05),
        by_provenance: { ...empty4(latency(0, null, null, null)), synthetic: latency(4, 120.5, 410.25, 450.05) },
        traces_without_root: 0,
      },
    },
    inference: {
      attempts: { total: 6, by_provenance: { real: 0, synthetic: 6, unknown: 0 } },
      logical_calls: {
        total: { value: 5, attempts_without_call_id: 0, n: 6 },
        by_provenance: { ...empty3({ value: null, attempts_without_call_id: 0, n: 0 }), synthetic: { value: 5, attempts_without_call_id: 0, n: 6 } },
      },
      errors: {
        total: { n: 6, failed: 1, succeeded: 5, rate: 0.166667 },
        by_provenance: { ...empty3({ n: 0, failed: 0, succeeded: 0, rate: null }), synthetic: { n: 6, failed: 1, succeeded: 5, rate: 0.166667 } },
        by_error_type: [{ error_type: "RateLimitError", count: 1 }],
        by_error_type_truncated: false,
      },
      latency: {
        total: latency(6, 85.25, 300.5, 310.1),
        by_provenance: { ...empty3(latency(0, null, null, null)), synthetic: latency(6, 85.25, 300.5, 310.1) },
      },
      time_to_first_chunk: { total: NO_TTFC, by_provenance: empty3(NO_TTFC) },
      tokens: {
        total: tokens(6, 1200, 340, 1, 1),
        by_provenance: { ...empty3(tokens(0, null, null)), synthetic: tokens(6, 1200, 340, 1, 1) },
      },
      cost: {
        total: cost(6, "0.0012345000", 4, 2, "0.0003086250"),
        by_provenance: { ...empty3(cost(0, null, 0, 0, null)), synthetic: cost(6, "0.0012345000", 4, 2, "0.0003086250") },
      },
      price_versions: {
        items: [{ provider: "openai", model: "gpt-5.4-mini", price_version: "openai-2026-10-24", calls: 4 }],
        truncated: false,
      },
      usage_source: {
        total: { provider_reported: 5, estimated: 0, unknown: 1 },
        by_provenance: { ...empty3({ provider_reported: 0, estimated: 0, unknown: 0 }), synthetic: { provider_reported: 5, estimated: 0, unknown: 1 } },
      },
    },
    trend: {
      bucket: "hour",
      items: [
        { start: "2026-10-25T13:00:00.000000Z", traces: 1, error_traces: 0, inference_attempts: 2, failed_attempts: 0, known_cost_usd: null, unknown_cost_calls: 2 },
        { start: "2026-10-25T14:00:00.000000Z", traces: 3, error_traces: 1, inference_attempts: 4, failed_attempts: 1, known_cost_usd: "0.0012345000", unknown_cost_calls: 0 },
      ],
    },
  };
}

/** An empty window: counts 0, every value null (T29). */
export function makeEmptyOverview(): Overview {
  const o = makeOverview();
  o.traces.count = { total: 0, by_provenance: empty4(0) };
  o.traces.error_rate = { total: { n: 0, error_traces: 0, rate: null }, by_provenance: empty4({ n: 0, error_traces: 0, rate: null }) };
  o.traces.latency = { total: latency(0, null, null, null), by_provenance: empty4(latency(0, null, null, null)), traces_without_root: 0 };
  o.inference.attempts = { total: 0, by_provenance: empty3(0) };
  o.inference.logical_calls = { total: { value: null, attempts_without_call_id: 0, n: 0 }, by_provenance: empty3({ value: null, attempts_without_call_id: 0, n: 0 }) };
  o.inference.errors = { total: { n: 0, failed: 0, succeeded: 0, rate: null }, by_provenance: empty3({ n: 0, failed: 0, succeeded: 0, rate: null }), by_error_type: [], by_error_type_truncated: false };
  o.inference.latency = { total: latency(0, null, null, null), by_provenance: empty3(latency(0, null, null, null)) };
  o.inference.tokens = { total: tokens(0, null, null), by_provenance: empty3(tokens(0, null, null)) };
  o.inference.cost = { total: cost(0, null, 0, 0, null), by_provenance: empty3(cost(0, null, 0, 0, null)) };
  o.inference.price_versions = { items: [], truncated: false };
  o.inference.usage_source = { total: { provider_reported: 0, estimated: 0, unknown: 0 }, by_provenance: empty3({ provider_reported: 0, estimated: 0, unknown: 0 }) };
  o.trend.items = o.trend.items.map((i) => ({ ...i, traces: 0, error_traces: 0, inference_attempts: 0, failed_attempts: 0, known_cost_usd: null, unknown_cost_calls: 0 }));
  return o;
}

/** A group whose model is not in the reference catalogue (Phase 9B). */
export function noReference(attempts: number, usageMissing = 0): ReferenceListPrice {
  return {
    available: false,
    unavailable_reason: "not_in_reference_catalogue",
    label: "reference_not_billed",
    currency: null,
    tier: null,
    catalogue_version: null,
    verified_at: null,
    source_url: null,
    reference_usd: null,
    calls_priced: 0,
    calls_not_priced: attempts,
    not_priced_by_reason: {
      usage_missing: usageMissing,
      usage_inconsistent: 0,
      cache_write_not_representable: 0,
      not_in_reference_catalogue: attempts - usageMissing,
      price_not_listed: 0,
    },
    stale: null,
  };
}

/** A Gemini reference as the backend computes it (Phase 9B): 1200 in (200 cached) / 300 out. */
export function geminiReference(overrides: Partial<ReferenceListPrice> = {}): ReferenceListPrice {
  return {
    available: true,
    unavailable_reason: null,
    label: "reference_not_billed",
    currency: "USD",
    tier: "paid_standard",
    catalogue_version: "reference-2026-11-19",
    verified_at: "2026-11-19T12:51:42.000000Z",
    source_url: "https://aistudio.google.com/docs/pricing",
    reference_usd: "0.0007050000",
    calls_priced: 1,
    calls_not_priced: 0,
    not_priced_by_reason: { usage_missing: 0, usage_inconsistent: 0, cache_write_not_representable: 0, not_in_reference_catalogue: 0, price_not_listed: 0 },
    stale: false,
    ...overrides,
  };
}

export function makeCompare(): ModelsCompare {
  return {
    project: 1,
    window: { from: "2026-10-24T15:00:00.000000Z", to: "2026-10-25T15:00:00.000000Z" },
    filters: { model: null, status: null, prompt_version: null, provenance: null, provider: null },
    provenance_label: "all provenance",
    provenance_verified: false,
    groups: [
      {
        provider: "openai",
        requested_model: "gpt-5.4-mini",
        response_model: "gpt-5.4-mini-2026-03-17",
        prompt_version: "v1",
        attempts: 4,
        errors: { failed: 1, succeeded: 3, rate: 0.25 },
        latency: latency(4, 80, 290, 300),
        time_to_first_chunk: NO_TTFC,
        tokens: tokens(4, 480, 192),
        usage_source: { provider_reported: 4, estimated: 0, unknown: 0 },
        cost: {
          currency: "USD",
          known_cost_usd: "0.0012345000",
          computed_cost_calls: 4,
          unknown_cost_calls: 0,
          unknown_cost_by_reason: { price_missing: 0, usage_missing: 0, usage_inconsistent: 0 },
          cost_complete: true,
        },
        by_provenance: { real: 0, synthetic: 4, unknown: 0 },
        reference_list_price: noReference(4),
      },
      {
        provider: "mock",
        requested_model: "mock-model",
        response_model: null,
        prompt_version: null,
        attempts: 2,
        errors: { failed: 0, succeeded: 2, rate: 0 },
        latency: latency(2, 5, 9, 9.5),
        time_to_first_chunk: NO_TTFC,
        tokens: tokens(2, null, 40, 2, 0),
        usage_source: { provider_reported: 1, estimated: 1, unknown: 0 },
        cost: {
          currency: "USD",
          known_cost_usd: null,
          computed_cost_calls: 0,
          unknown_cost_calls: 2,
          unknown_cost_by_reason: { price_missing: 2, usage_missing: 0, usage_inconsistent: 0 },
          cost_complete: false,
        },
        by_provenance: { real: 0, synthetic: 2, unknown: 0 },
        reference_list_price: noReference(2),
      },
    ],
    groups_truncated: false,
    limit: 10,
  };
}

/** A deep JSON clone, so tests can mutate a copy into malformed shapes. */
export function json<T>(value: T): Record<string, unknown> {
  return JSON.parse(JSON.stringify(value)) as Record<string, unknown>;
}
