# LLM Insight — Metrics Catalog (MVP)

Every metric is computed from stored spans, usage records and evaluation results. ADR-04 means no OTLP metrics are ingested. Every displayed value carries its **sample size `n`**, its **provenance split** (real / synthetic / unknown) and its **null status**. A missing value is shown as "unknown" or "not evaluated", never as `0`.

Master plan §D3 says the earlier report's 64-metric catalog is a reference, not an implementation requirement. That catalog is **not present** in this repository, so this file defines only the metrics the MVP needs. P2 entries are placeholders for later scope, not a reconstruction of the 64.

## Conventions

- **Units:** durations come from `end_ns - start_ns` and are shown in ms. Money is shown in USD as `Decimal`.
- **Counting:** "Inference call" means a distinct `usage_records.canonical_call_key`, which is one physical attempt. "Trace" means a distinct `trace_id`. The two counts are never mixed.
- **Percentiles:** PostgreSQL `percentile_cont` (linear interpolation) over the stated population. The UI always shows `n`, and flags *low sample* when `n < 20` for p95 and `n < 100` for p99. The NumPy `linear` method gives the same result and is used for cross-checks in T24.
- **Filters:** project (mandatory), time window on span start, model, status, prompt_version.

## P0 metrics

| ID | Metric | Definition / formula | Source | Null / provenance rule |
|---|---|---|---|---|
| M01 | Trace count | `COUNT(DISTINCT trace_id)` | traces | — |
| M02 | Inference calls | `COUNT(DISTINCT canonical_call_key)` | usage_records | Retries count as separate attempts; `call_id` groups them into logical calls |
| M03 | Logical LLM calls | `COUNT(DISTINCT llminsight.call_id)` | spans | Only where `call_id` is present; otherwise shown "n/a" |
| M04 | Inference error rate | inference spans with `status=ERROR` ÷ M02 | spans + usage | `n` = M02; `error_type` breakdown |
| M05 | Trace error rate | traces with `status=error` ÷ M01 | traces | Labeled separately from M04 |
| M06 | Inference latency p50 / p95 / p99 | percentiles of inference span `duration_ns` | spans | Shows `n`, low-sample flag. Not mixed with trace latency |
| M07 | Trace latency p50 / p95 / p99 | percentiles of root span duration | spans | Root = no parent; orphans excluded and counted |
| M08 | Input / output tokens | `SUM` over rows where the value is not null | usage_records | Also shows "k of n calls have unknown tokens". Unknown is never summed as 0 |
| M09 | Cached input / reasoning output tokens | `SUM` of the subsets | usage_records | Shown as a *subset of* M08, never added to it |
| M10 | Known cost | `SUM(cost_usd)` where `cost_status='computed'` | usage_records | Always shown with M11. Per row: `((in − cache_read − cache_write)·p_in + cache_read·p_cached + cache_write·p_cw + out·p_out) / 1e6`, NULL `input_tokens`/`output_tokens` or a NULL price for a non-zero term makes the row unknown. An absent cache subset counts as 0 in the formula, because semconv emits it only "when available and non-zero"; the stored value stays NULL |
| M11 | Unknown-cost calls | `COUNT(*)` where `cost_status='unknown'`, grouped by `cost_unknown_reason` | usage_records | — |
| M12 | Cost per inference call (known) | M10 ÷ count(computed) | usage_records | Denominator shown |
| M13 | Price versions used | distinct `price_version` per model | usage_records | — |
| M14 | Usage source mix | count by `usage_source` | usage_records | — |
| M15 | Provider / model / prompt-version breakdown | M02, M04, M06, M10, M11 grouped | usage + spans | `requested_model` vs `response_model` both available |
| M16 | Eval pass rate per prompt_version | `passed=true` ÷ evaluated results | experiment_results | `passed IS NULL` means "not evaluated" and is excluded from the denominator and shown separately |
| M17 | Experiment cost & latency per version | M06 / M10-style aggregates over experiment_results | experiment_results | `mode=mock` results labeled SYNTHETIC |
| M18 | Review counts | count by `issue_type`, `review_status`, `reviewer_decision` | failure_reviews | Counts of hypotheses, not of root causes |
| M19 | Dropped attribute count | `SUM(dropped_attributes_count)` | spans | Privacy health signal |

## P1 metrics (only if P0 is complete)

| ID | Metric | Note |
|---|---|---|
| M20 | Client time-to-first-chunk | Streaming. Semconv `gen_ai.response.time_to_first_chunk` and metric `gen_ai.client.inference.time_to_first_chunk` (Development) |
| M21 | Threshold alerts (cost / error / latency) | Simple static thresholds |
| M22 | Retry amplification | attempts ÷ logical calls (M02 ÷ M03) |

## P2 (post-MVP placeholders)

Server-side TTFT/TPOT (server metrics, not available from client spans), throughput tokens/s, cache hit ratio trends, per-tenant cost, LLM-as-judge quality scores, RAG retrieval metrics.

## Naming alignment with OTel GenAI (Development)

The semconv metric `gen_ai.client.inference.duration` (histogram, seconds) corresponds conceptually to M06. The `gen_ai.client.inference.usage.*` counters correspond to M08/M09. We don't emit or ingest these metrics; the names are recorded for future alignment. Per the master plan, `gen_ai.client.operation.duration` is not used, and the current semconv page uses the `gen_ai.client.inference.*` names.
