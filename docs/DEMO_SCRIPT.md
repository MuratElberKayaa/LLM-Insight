# LLM Insight — Demo Script (draft, 3–5 min)

Status: **draft (Phase 0)**. None of the steps run yet. The default demo uses **mock mode**, and every mock value is labeled SYNTHETIC. Real mode is an optional step that needs explicit approval, an `OPENAI_API_KEY` and a budget.

## Mock demo scenario (default)

| # | Step | What is shown | Evidence / data source |
|---|---|---|---|
| 0 | `docker compose up --build`, then the init command (creates the project and prints the ingest key once) | Services healthy (`/health/ready` 200) | Terminal |
| 1 | Send 3 requests to `demo_app` in mock mode: one success, one deterministic JSON-schema violation, one deterministic 429 | trace IDs returned | demo_app response |
| 2 | Open the dashboard Overview | KPIs with N: 3 traces, inference calls (including the retry attempt), error rate, p95 (low-sample flag), known cost and unknown-cost count | DB-backed `/api/v1/overview`; SYNTHETIC badge |
| 3 | Trace Explorer, then the failed trace | Root `POST /v1/summarize`, then child `chat gpt-4o-mini` and `validate_output` (passed=false). Shows tokens, provenance and price version | `/api/v1/traces/{id}` |
| 4 | Failure Review on that trace | `issue_type=schema_violation`, evidence ("validate_output failed rule json_schema:summary_v1"), hypothesis ("prompt v1 lacks explicit JSON instruction"), `reviewer_decision=hypothesis_supported`. Labeled as a hypothesis | `failure_reviews` row |
| 5 | Propose a test case | The developer **types** a safe synthetic input; telemetry never stored the original prompt. Approve, which runs redaction, then save to the dataset | `test_cases` row with `approved_by` |
| 6 | Run experiment v1 vs v2 (mock) on the 10+ case dataset | Pass rate with N, p50/p95 latency, cost (or unknown), SYNTHETIC label | `experiments` / `experiment_results` |
| 7 | "How we measure" slide | OTLP protobuf ingest, idempotent ledger, unknown ≠ $0, metadata-only privacy | `ARCHITECTURE.md` diagrams |

## Implemented so far (Phase 2C)

The FastAPI demo exists (`demo_app/web.py`, mock and offline fake-OpenAI only): `sdk/.venv/Scripts/python -m demo_app.web` serves `POST /v1/summarize`, `/v1/summarize/multi`, `/v1/summarize/failure`, `/v1/summarize/retry` and `/v1/summarize/openai` on `127.0.0.1:8100`; `--drive` sends one request per route and prints the trace IDs. Each request is a `SERVER` root span (`POST /v1/summarize…`) with one `CLIENT` inference span per physical attempt, and the numbers appear in `/api/v1/overview`. Steps 1–3 can be rehearsed against the API now; the dashboard (Phase 3), the `validate_output` span and steps 4–6 (Phase 4) do not exist yet.

## Optional real-mode step (requires approval)

Before step 1, send one real OpenAI request with `LLMI_PROVIDER_MODE=real`. The trace then shows `provenance=real`. The cost is `computed` only if a verified `model_prices` row exists; otherwise it stays `unknown`.

## Mock fixture design (implemented in Phase 2/4)

- The mock adapter is deterministic: a fixed seed and a fixed response per input key. It reports usage as `provider_reported` with explicit numbers, so the cost path is exercised. Every mock span carries `llminsight.provenance=synthetic`.
- Failure modes are selectable per request: `ok`, `schema_violation`, `rate_limited_429`, `timeout`.
- Dataset: at least 10 synthetic summarization cases with JSON-schema expected outputs. No real personal data.

## Rollback

If the demo breaks, run `docker compose down -v`, then `up --build`, init, and reseed with the synthetic seed script (Phase 2). Recorded screenshots are the fallback.
