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

## Implemented in Phase 4B

Steps 4 and 5 run in the dashboard: on the failed trace's page, the reviewer reads the unverified machine observations, records a review (an issue type from CR-03, evidence, hypothesis, self-declared name), then a decision; "Convert to a regression test case" opens a form that starts empty, where the developer types a synthetic input, chooses a rule, optionally an expected output, ticks the approval box and saves into a dataset (`/datasets`). The server masks e-mail, phone, number and key-like values. Step 6 (experiments) is Phase 4C.

## Implemented in Phase 4C–4D

Step 6 runs in the dashboard (Phase 4D, mock only). Load the SYNTHETIC golden dataset (`scripts/dev/seed_golden_dataset.py --project <id> --approved-by <name>`), or type a golden input such as `SYNTHETIC G02: …` as the test input of a reviewed trace's case so the scripted mock can answer it. Then open **Experiments → New mock experiment** (or "Set up a mock experiment" on the dataset page): choose the dataset, `golden-prompt-v1` as A, `golden-prompt-v2` as B and a priced model, review the summary, tick the mock-only confirmation and press **Run mock experiment**. The results page shows the SYNTHETIC banner, per-version passed/failed/not evaluated with the pass-rate denominator, synthetic p50/p95 with `n`, the estimated cost or "Total unknown", the backend's verdict (on the 12-case golden set: no better version is named, because G12 timed out under v2) and every test case's outcome under both versions, each with a detail page. Nothing runs on page load; real mode is not offered and is refused by the backend.

## Clean-clone setup (T47, verified in Phase 5A)

From a fresh clone, mock mode only, nothing real or paid (`reports/phase-5a.md` §5). Commands run from the repository root; on Windows use `.venv\Scripts\python`.

1. `python -I scripts/dev/init_env.py` creates the git-ignored `.env` with generated secrets (never printed).
2. `docker compose --profile app up -d --build --wait` starts PostgreSQL, the backend (migrates to head on start) and the dashboard on `http://127.0.0.1:3001`. The default `docker compose up` starts PostgreSQL only.
3. Init, inside the backend container: `docker compose --profile app exec backend python -m app.cli create-key --project demo` (the first project is id 1, the dashboard default; the key is printed once: put it into `OTEL_EXPORTER_OTLP_TRACES_HEADERS=X-Project-Key=<key>` in `.env`), then `docker compose --profile app exec backend python -m app.cli seed-prices`.
4. Host tools for ingest (the `app` profile does not publish the backend, R43): a backend venv (`python -m venv backend/.venv`, then `pip install -r backend/requirements.lock` and `pip install --no-deps -e backend`) and an SDK venv (`python -m venv sdk/.venv`, then `pip install -r sdk/requirements-dev.lock` and `pip install --no-deps ./sdk`). Start `python -m app` in `backend/` (same database through `127.0.0.1:55432`).
5. Demo traffic: load `.env` into the environment and run `sdk/.venv/Scripts/python -m demo_app.web --drive` (5 SYNTHETIC traces). Golden dataset: `backend/.venv/Scripts/python scripts/dev/seed_golden_dataset.py --project 1 --approved-by <name>` (add `--api http://127.0.0.1:<port>` if the backend is not on 8000).
6. Steps 2–6 above in the dashboard. Rollback: `docker compose --profile app down -v`, then start again from step 2.

## Optional real-mode step (requires approval)

Before step 1, send one real OpenAI request with `LLMI_PROVIDER_MODE=real`. The trace then shows `provenance=real`. The cost is `computed` only if a verified `model_prices` row exists; otherwise it stays `unknown`.

## Mock fixture design (implemented in Phase 2/4)

- The mock adapter is deterministic: a fixed seed and a fixed response per input key. It reports usage as `provider_reported` with explicit numbers, so the cost path is exercised. Every mock span carries `llminsight.provenance=synthetic`.
- Failure modes are selectable per request: `ok`, `schema_violation`, `rate_limited_429`, `timeout`.
- Dataset: at least 10 synthetic summarization cases with JSON-schema expected outputs. No real personal data.

## Rollback

If the demo breaks, run `docker compose down -v`, then `up --build`, init, and reseed with the synthetic seed script (Phase 2). Recorded screenshots are the fallback.
