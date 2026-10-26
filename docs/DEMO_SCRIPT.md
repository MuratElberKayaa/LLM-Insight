# LLM Insight — Demo Script (3–5 min)

Status: **final for the MVP (Phase 5B; re-checked for the Phase 5 final release, `FINAL_DELIVERY_REPORT.md`).** Every step below was run end to end on a clean clone in Phase 5B (`reports/phase-5b.md` §7, screenshots in `reports/phase-5b-screenshots/`). The demo uses **mock mode only**: every trace, output, token count, duration and experiment result is SYNTHETIC, nothing is sent to a provider and nothing is spent. Real mode is not part of the MVP.

## Before the demo (setup, not shown)

Follow the root `README.md` quick start (or the clean-clone setup below): PostgreSQL, backend with migrations at head, a project and ingest key, seeded prices, the dashboard on `http://127.0.0.1:3001`, demo traffic (`sdk/.venv/Scripts/python -m demo_app.web --drive`, 5 SYNTHETIC traces) and the golden dataset (`backend/.venv/Scripts/python scripts/dev/seed_golden_dataset.py --project 1 --approved-by <name>`, 12 cases in `golden-synthetic-v1`). Keep the G02 golden input at hand (`backend/app/services/experiments/golden.py`, case `G02`: input, JSON Schema and expected output), so the scripted mock can answer the case typed in step 5.

## Script

| # | Time | Step | What is shown | Data source |
|---|---|---|---|---|
| 1 | 0:00 | "An instrumented FastAPI app sent five requests through our SDK" (terminal output of `--drive`) | five trace ids; OTLP/HTTP protobuf export delivered; provenance `synthetic` | demo app output |
| 2 | 0:20 | **Overview** | traces and inference calls (the retry counts as two attempts), error rate, p50/p95 latency with `n` and a low-sample note, known estimated cost next to the number of calls whose cost is unknown, the SYNTHETIC notice | `GET /api/v1/overview` |
| 3 | 0:50 | **Traces** → the failing trace `POST /v1/summarize/failure` | root SERVER span → child CLIENT `chat …` span in the tree and waterfall, the error status; model, tokens, usage source, provenance, price version | `GET /api/v1/traces/{id}` |
| 4 | 1:20 | Machine observations panel on the same page | labelled "Automatic · Unverified · Not a human review"; evidence spans; no form | `GET /api/v1/traces/{id}/diagnostics` |
| 5 | 1:40 | **Failure review**: issue type `schema_violation`, evidence ("validate_output failed rule json_schema"), hypothesis ("prompt v1 does not ask for the priority field"), reviewer name; then decision "closed / hypothesis supported" | the cause is stated as a hypothesis, never as a verified root cause; the name is self-declared | `failure_reviews` |
| 6 | 2:10 | **Convert to a regression test case**: dataset `golden-synthetic-v1`, type the G02 input, rule JSON Schema, expected output, approver; press save **without** the approval tick, then with it | without the tick nothing is stored; with it the case is stored, masked if needed (`redaction_applied`), and linked to its review and trace | `test_cases` |
| 7 | 2:50 | **Experiments → New mock experiment**: `golden-synthetic-v1`, `golden-prompt-v1` vs `golden-prompt-v2`, a priced model, tick "mock only", run | "mock only · real mode not available"; "13 test cases → 26 simulated calls"; "26 results (26 SYNTHETIC, 0 real)" | `POST /api/v1/experiments` |
| 8 | 3:20 | **Results** | SYNTHETIC banner; per version passed / failed / not evaluated and the pass rate with its denominator (7/12 vs 5/11 on the Phase 5B run); synthetic p50/p95 marked unreliable below 20 samples; cost of v1 vs "Total unknown" for v2; the backend's verdict: **no better version is named** (G12 timed out under v2, so the evidence is incomplete) | `GET /api/v1/experiments/{id}` |
| 9 | 3:50 | Open the human-approved case from the results table | its lineage ("Trace …, review #…"), rule and version, the outcome under both versions (v1 failed `schema_violation`, v2 passed) | per-case page |
| 10 | 4:10 | "How we measure" | OTLP protobuf ingest and idempotent ledger (re-ingest changes nothing), unknown cost ≠ $0, metadata-only privacy, deterministic rules instead of an LLM judge, local-only boundary | `ARCHITECTURE.md`, `SECURITY.md` |

**Say explicitly:** results are SYNTHETIC and say nothing about a real model; estimated costs are not provider invoices; reviewer and approver names are self-declared; redaction is heuristic and not a privacy guarantee; the MVP is authorized for local use only.

## Clean-clone setup (T47; verified in Phase 5A and Phase 5B)

From a fresh clone, mock mode only, nothing real or paid. Commands run from the repository root in Git Bash; on Linux/macOS use `.venv/bin/` instead of `.venv/Scripts/`.

1. `python -I scripts/dev/init_env.py` creates the git-ignored `.env` with generated secrets (never printed).
2. `docker compose --profile app up -d --build --wait` starts PostgreSQL, the backend (migrates to head on start) and the dashboard on `http://127.0.0.1:3001`. The default `docker compose up` starts PostgreSQL only. Keep `--build`: without it compose reuses older local images with their old dependencies.
3. Init, inside the backend container: `docker compose --profile app exec backend python -m app.cli create-key --project demo` (the first project is id 1, the dashboard default; the key is printed once: put it into `OTEL_EXPORTER_OTLP_TRACES_HEADERS=X-Project-Key=<key>` in `.env`), then `docker compose --profile app exec backend python -m app.cli seed-prices`.
4. Host tools for ingest. **The `app` profile does not by itself expose host OTLP ingestion** (R43): create a backend venv (`python -m venv backend/.venv`, `pip install -r backend/requirements.lock`, `pip install --no-deps -e backend`) and an SDK venv (`python -m venv sdk/.venv`, `pip install -r sdk/requirements-dev.lock`, `pip install --no-deps ./sdk`), then start `backend/.venv/Scripts/python -m app` from `backend/` (it uses the same database through `127.0.0.1:55432`).
5. Demo traffic: `set -a; . ./.env; set +a`, then `sdk/.venv/Scripts/python -m demo_app.web --drive` (5 SYNTHETIC traces). Golden dataset: `backend/.venv/Scripts/python scripts/dev/seed_golden_dataset.py --project 1 --approved-by <name>` (add `--api http://127.0.0.1:<port>` if the backend is not on 8000).
6. The script above, in the dashboard.

**Relocated ports** (when a developer stack already holds the defaults, as on the Phase 5A/5B machine): in the clone's `.env` set `POSTGRES_HOST_PORT` and the port in `LLMI_DATABASE_URL` (e.g. `55433`), `LLMI_PORT`, the port in `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` and `LLMI_API_BASE_URL` (e.g. `8010`), and add `LLMI_FRONTEND_HOST_PORT` (e.g. `3011`); run compose with its own project name (`docker compose -p <name> --profile app …`) so the developer's containers and volume are never reused; pass `--api` to the seed script (R73).

## Rollback

If the demo breaks: `docker compose --profile app down -v` (this deletes the demo database volume; never run it on a stack whose data you need), then start again from step 2 of the clean-clone setup. On the host stack: `docker compose down -v`, `docker compose up -d --wait postgres`, migrations, key, prices, demo traffic and golden dataset again (`README.md`). The Phase 5B screenshots are the fallback if the live demo cannot run.

## Mock fixture design

- The mock adapter is deterministic: a fixed response per input key and prompt version. It reports usage as `provider_reported` with explicit numbers, so the cost path is exercised. Every mock span and result carries `synthetic` provenance.
- Failure modes are selectable per request: `ok`, `schema_violation`, `rate_limited_429`, `timeout`.
- The golden dataset has 12 SYNTHETIC cases with deterministic rules (JSON Schema, field, exact, regex) and expected outputs; one case has no expected output and is "not evaluated" by design (T40). The mock answers only the golden inputs (R65).

## Real mode (not part of the MVP)

Real-provider experiments are refused by the backend (`409`) and the backend has no real executor. The SDK's OpenAI adapter is tested offline only. A real run would need explicit approval, an `OPENAI_API_KEY`, a budget, durable idempotency (R66) and a pre-call budget guard first.
