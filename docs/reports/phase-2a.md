# Phase 2A — Python Telemetry SDK and Deterministic Mock Integration

- **Date:** 2026-10-24
- **Baseline:** Phase 1 closure `cec668b`, annotated tag `phase-1` (876 passed / 0 failed / 0 skipped).
- **Scope:**
  - an independent, installable Python SDK (`sdk/`);
  - real OTLP/HTTP protobuf export through the official exporter;
  - a deterministic mock provider with sync and async instrumentation;
  - a demo application (`demo_app/`);
  - reliability and security tests against the live backend.

  **Not in scope, not done:** the OpenAI adapter, any real provider call, `model_prices`, cost calculation, aggregate metric services, frontend, evaluation. There is no migration and no backend code change.
- **Commit:** one project-only commit on `main` (its hash is in the closure message; a commit cannot contain its own hash). No `phase-2` tag. The home-directory repository, the global Git config and the unrelated `docker-flowise-1` container were not touched.
- All telemetry produced in this phase is **SYNTHETIC**.

## 1. Start-of-phase verification

| Check | Result |
|---|---|
| `git rev-parse --show-toplevel` | `C:/Users/PC/Desktop/LLM_Insight_Claude_Code_MVP_Paketi`; branch `main`; clean tree at `cec668b` |
| Tags | `phase-0`, `phase-1` (annotated: "Phase 1 closed … 876 passed, 0 failed, 0 skipped") |
| PostgreSQL | `llminsight-postgres-1` `Up (healthy)` on `127.0.0.1:55432`; dev DB `alembic current` = `0001 (head)` |
| Baseline suite | `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs` (backend) → **876 passed**, 1 warning (R20), exit 0, before any edit |
| Contracts read | ADR, ARCHITECTURE §1/§4.4–4.7/§5/§6/§7, TELEMETRY_CONTRACT (all), SECURITY, METRICS_CATALOG (conventions, provenance, M01–M10), TEST_MATRIX, PHASE_STATUS, phase prompts §3, `reports/phase-1-final.md` |
| Installed exporter source read | `opentelemetry-exporter-otlp-proto-http` 1.45.1, `opentelemetry-sdk` 1.45.1 `BatchSpanProcessor`/`BatchProcessor` (facts used in §2) |

## 2. Contract check and design decisions

**No conflict with ADR-01…21 required a stop, and no migration is needed.** The SDK writes only through the existing `/v1/traces` contract. All values it emits are allowlisted `llminsight.attrmap.v1` attributes.

**ADR-15 semconv re-check (start of Phase 2):**
- `semantic-conventions-genai` `main` HEAD is still `9ffc7e00c639` (2026-10-21).
- The last commit touching `docs/gen-ai/client-inference.md` is `ac2cc8868cf4` (2026-10-17), before the pin.
- **The pin is unchanged and there is no `attrmap.v2`.** The locked mapping was not modified.
- Upstream now says optional usage subsets are "Recommended when available and non-zero". Our locked contract (`TELEMETRY_CONTRACT.md` §4) says the SDK emits provider-reported subsets explicitly, including 0. The SDK follows the contract.

**Facts verified in the pinned OpenTelemetry 1.45.1 source** (they drove the design):

| Fact | Consequence |
|---|---|
| `OTLPSpanExporter(timeout=…)` is in **seconds** and covers the exporter's internal retries (429/502/503/504 and connection errors, exponential backoff, `Retry-After`) | `export_timeout_seconds` in the SDK |
| The exporter reads `OTEL_EXPORTER_OTLP_TRACES_TIMEOUT` as `float` **seconds**, while the spec says milliseconds (R03) | The SDK always passes the timeout explicitly and ignores that variable with a warning. A test sets it to `10000` and shows the 0.5 s bound still holds |
| `BatchSpanProcessor(export_timeout_millis=…)` is accepted but **not used** (source comment) | The SDK sets it to the same value and documents that it is not enforced |
| `max_request_size` is checked on the **uncompressed** serialized request; a larger batch is dropped before sending (default 64 MiB) | `max_request_bytes` ≤ 4 MiB − 64 KiB, which bounds both the 4 MiB wire limit and the 16 MiB decompressed limit |
| A full BSP queue drops the **oldest** span (`deque(maxlen)`) | Documented |
| `force_flush()` ignores its timeout, runs on the calling thread and returns `True` even if the export failed. `BatchSpanProcessor.shutdown()` has no timeout argument (30 s internally) | The SDK runs both on a helper thread with a real deadline and reports failure (§3) |
| `set_tracer_provider` is set-once | `set_global=True` installs only over the proxy provider and never replaces an existing one |

**Decisions inside the phase scope** (no ADR changed):
1. **Mock identity, so there is no fake real-provider evidence:**
   - provider `llminsight.mock`, never `openai`;
   - models `mock-*`, with response model `<model>-2026-10-01`, so request and response models differ;
   - response IDs `mockresp_<uuid4 hex>`, unique per successful attempt, so repeated demo runs never collide in the usage ledger;
   - `llminsight.provenance = synthetic` on every inference span.
2. **No REAL by construction:**
   - `provenance` accepts only `synthetic`, and `real` raises `ConfigError` (reserved for the OpenAI adapter);
   - the span API has no provenance parameter;
   - `content_policy` accepts only `metadata_only` (ADR-19).
3. **Errors carry no free text:**
   - exceptions are not recorded as span events (`record_exception=False`);
   - the status is `ERROR` with no description;
   - `error.type` is the provider code (`429`, `500`, `timeout`), else the fully-qualified class name, else `_OTHER`.
4. **Client-side R08 rules** (`_guards.py`) mirror `TELEMETRY_CONTRACT.md` §3.1, because ADR-01 forbids importing the backend. A rejected value is withheld and counted. If the request model is withheld, the span is named after the operation alone (semconv fallback). Drift is caught by the live tests (R25).
5. **Explicit resource.** `Resource(...)`, not `Resource.create()`, so `OTEL_RESOURCE_ATTRIBUTES` and detectors cannot add host, process or user data.
6. **src layout** (`sdk/src/llm_insight_sdk/`) instead of the master plan's suggested `sdk/llm_insight_sdk/` (§G says file names are suggestions). Tests then always import the installed, non-editable package.
7. **Demo as a CLI app with an SDK root span** (`INTERNAL`), not the FastAPI demo. Phase 2A asks for a minimal demo, and adding FastAPI + uvicorn + server instrumentation would widen scope. **Consequence:** T10 is only PARTIAL, because its "demo FastAPI root span" is still to be built.

## 3. What was built

**`sdk/` — `llm-insight-sdk` 0.1.0**
- **Python and dependencies:** Python `>=3.12,<3.13`. Runtime deps pinned exactly: `opentelemetry-api` / `-sdk` / `-exporter-otlp-proto-http` 1.45.1. Typed (`py.typed`).
- **`config.py`:** `LLMInsightConfig`, a frozen dataclass with `from_env()`. Every setting is validated (table in `sdk/README.md`), and `ConfigError` names the setting, never the value. The key is excluded from `repr`, has the backend format `lli_<8>_<43+>`, and is sent only as the `X-Project-Key` header. Plain `http` is accepted only for loopback endpoints. The endpoint path must end in `/v1/traces`, with no user info, query or fragment.
- **`telemetry.py`:**
  - `init()` / `get_active()` / `shutdown()` and the `Telemetry` handle.
  - A private `TracerProvider` (`ALWAYS_ON`, explicit resource, `shutdown_on_exit=False`), one `BatchSpanProcessor` (bounded queue and batch) and one `OTLPSpanExporter`, with every setting passed explicitly.
  - Idempotent `init`: an equal config returns the same instance; a different config raises `AlreadyInitializedError`.
  - Optional `set_global`, which never overrides an existing provider.
  - Bounded `force_flush` / `shutdown` that return `bool`.
  - Exporter wrapper counters (`stats()`), and a bounded `atexit` shutdown.
- **`instrumentation.py`:** `inference_span` (CLIENT, `{operation} {model}`, the locked GenAI attributes, `llminsight.call_id` UUIDv4 and `llminsight.attempt`, usage counts with explicit subsets, `usage_source`), `InferenceRecorder` (never raises), `classify_error`, `call_with_retries` / `acall_with_retries` (one span per physical attempt, ≤ 10 attempts), `TokenUsage`, `RequestParams`.
- **`mock.py`:**
  - `MockLLMProvider`, deterministic: `success`, `delayed` (300 ms), `rate_limited` (429), `server_error` (500), `timeout`, `retry_then_success`. Fixed token counts and latencies; `time_scale` for tests.
  - `ObservedMockClient.chat()` / `.achat()`. Message content is accepted and never read or exported.
- **`_guards.py`:** a copy of the §3.1 value rules (formats, lengths, unsafe characters, obvious credentials, personal data).
- **Packaging and docs:** `pyproject.toml` (build backend `setuptools==84.0.0`, dynamic version, Ruff and strict mypy config), `requirements-dev.lock` (ARCHITECTURE §6), `README.md` (install, configuration contract, lifecycle, delivery guarantees, what is and is not recorded).

**`demo_app/`** (`sdk/.venv/Scripts/python -m demo_app`)
- Runs scenarios A (success), B (2 sync + 2 async children under one root), C (provider 500 handled with a fallback answer) and D (429 then success).
- Then E: export failure. First an unreachable endpoint, then a rejected (unknown, well-formed) key; the application answers both times.
- Prints one JSON summary with trace IDs, outcomes and SDK counters, and never the key or content. Refuses `LLMI_PROVIDER_MODE` ≠ `mock`.

## 4. Tests: exact commands and results

All run on 2026-10-24, Windows 11, Python 3.12.6, PostgreSQL 17 container. `LLMI_TEST_REQUIRE_DB=1` means an unreachable database fails instead of skipping.

| # | Command (directory) | Result | Exit |
|---|---|---|---|
| 1 | `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs` (backend, before editing) | **876 passed**, 1 warning (R20) | 0 |
| 2 | `python -m pytest tests/unit -q -rs` (sdk) | **237 passed**, 0 skipped | 0 |
| 3 | `LLMI_TEST_REQUIRE_DB=1 python -m pytest tests/integration -q -rs` (sdk; live backend) | **10 passed**, 0 skipped | 0 |
| 4 | `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs` (sdk, complete) | **247 passed, 0 failed, 0 skipped** | 0 |
| 5 | `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs` (backend, final gate after all changes) | **876 passed, 0 failed, 0 skipped**, 1 warning (R20) | 0 |
| 6 | `ruff check .` / `ruff format --check .` (backend) | `All checks passed!` / `67 files already formatted` | 0 / 0 |
| 7 | `ruff check .` / `ruff format --check .` (sdk; includes README code blocks) | `All checks passed!` / `20 files already formatted` | 0 / 0 |
| 8 | `ruff check --config pyproject.toml ../demo_app` / `ruff format --check …` (sdk) | `All checks passed!` / `2 files already formatted` | 0 / 0 |
| 9 | `python -m mypy src` (strict) / `python -m mypy --config-file pyproject.toml ../demo_app` (sdk) | `Success: no issues found in 7 source files` / `… in 2 source files` | 0 / 0 |
| 10 | `python -m pip check` (backend venv; sdk venv) | `No broken requirements found.` (both) | 0 |
| 11 | `alembic current` / `alembic check` (backend, dev DB) | `0001 (head)` / `No new upgrade operations detected.` | 0 |
| 12 | `docker compose config --quiet` (root); `docker compose ps` | exit 0; `llminsight-postgres-1 Up (healthy)` | 0 |
| 13 | `python -I scripts/verify_phase0.py --quiet` (root) | `SUMMARY: 691 PASS, 0 FAIL` (685 before the matrix update; each new `(phase-2a)` status is checked against this report) | 0 |
| 14 | `git diff --check` (root) | no whitespace errors (`core.autocrlf` notice only) | 0 |
| 15 | Clean-room install (scratch dir): `pip wheel --no-deps -w wheelhouse ./sdk`; new `py -3.12 -m venv`; `pip install -c backend/requirements.lock wheelhouse/llm_insight_sdk-0.1.0-py3-none-any.whl`; `python -I -c "import llm_insight_sdk …"` from the scratch dir | imported from the new venv's `site-packages`; no `app` module loaded; mock call answered with the backend down; `force_flush` → `False`; `pip check` clean; 13 pinned dependencies | 0 |
| 16 | Mutation check (scratch script; each mutation applied to the installed copy, guarding tests run, file restored) | **9 / 9 detected**: exception events recorded, status description set, duplicate processor on re-init, ambient exporter timeout, no request-size bound, global override, value guards disabled, usage subsets of 0 dropped, constant `call_id`. One weak assertion found and strengthened (raw protobuf body check in `test_non_retryable_errors_are_not_retried`), then re-verified | — |

During development, earlier runs found two test bugs and one doc-check failure, all fixed at the root:
- the live test picked the root span by time order, but spans can share a `start_ns` at zero mock latency, so it now selects by `parent_span_id`;
- the retry tests were made to order by attempt number for the same reason;
- `verify_phase0.py` flagged two synthetic secret literals in `test_guards.py`; they are now split like the backend's test literals.

**SDK test files:**
- `test_config.py` (74 tests): every setting valid/invalid, env parsing, the key never in errors or `repr`.
- `test_guards.py` (110 tests): valid metadata preserved; 12 credential formats in 5 rules; personal data; unsafe characters; check before truncation.
- `test_instrumentation.py` (22 tests): span shape, resource, parent/child, failures, retries, no content, synthetic only, no cost attribute, withheld values, async.
- `test_telemetry.py` (27 tests): lifecycle, global provider, atexit, flush/shutdown bounds, queue bounds, batch size, request size, timeouts, outages, headers, log hygiene.
- `test_install.py` (4 tests): installed package, import from outside, metadata pins, no backend import.
- `integration/test_live_backend.py` (10 tests).

Unit tests talk to a local stdlib OTLP stub (`tests/otlp_stub.py`) that decodes the real gzip + protobuf body. Integration tests use no stub.

### 4.1 Live backend evidence (no mocked ingestion)

`sdk/tests/integration/conftest.py` runs the backend from `backend/.venv` as subprocesses against a disposable `llminsight_sdk_it_test` database:
- `alembic upgrade head`;
- `python -m app.cli create-key` (the key is parsed, never printed);
- `python -m app` on a free loopback port, with a fresh pepper and admin token and its log captured to a file.

The database is dropped afterwards. The dev database was not written to.

**Standalone smoke** (same set-up, disposable `llminsight_smoke2a_test`, then dropped): `python -m demo_app` exit 0 in 2.2 s. Database afterwards: **4 traces, 12 spans, 8 usage rows**.

| Scenario | Trace | Spans | Usage rows |
|---|---|---|---|
| A success | `ok`, `synthetic`, span_count 2, prompt_version `v1` | root `INTERNAL` + `chat mock-chat-small` `CLIENT`, dropped count 0 | `provider_response_id`, 120/48 tokens (cache read 32, cache write 0, reasoning 0), `provider_reported`, `synthetic`, `cost_usd NULL`, `unknown` / `price_missing` |
| B multi-call | `ok`, `synthetic`, 5 spans | 4 CLIENT children of one root | 4 rows, 4 distinct `call_id`; the delayed call 480/160 (cache write 64, reasoning 96) |
| C failure | `error`, `synthetic` | CLIENT `ERROR`, `error_type 500`, `status_message NULL` | `sdk_attempt`, tokens NULL, `usage_source unknown`, `usage_missing` |
| D retry | `error` (see R27), `synthetic`, 3 spans | attempt 1 `ERROR 429`, attempt 2 OK, same parent | 2 rows, same `call_id`: `sdk:<id>:1` (NULL tokens) and `resp:llminsight.mock:mockresp_…` (attempt 2) |
| E unreachable / rejected key | — | **0 rows** for both trace IDs | app answered in 16 ms each; `delivered=false`; `spans_export_failed=2` |

More results from the smoke:
- **Ledger counts:** M02 / M03 over the ledger = `COUNT(DISTINCT canonical_call_key)` = 8, `COUNT(DISTINCT call_id)` = 7.
- **Read API:** `GET /api/v1/traces` listed all 4 traces with `provenance: synthetic`, `provenance_verified: false`, `known_cost_usd: null` and `unknown_cost_calls` = number of calls. `GET /api/v1/traces/{D}` returned roles `root`, `child`, `child` and `cost_usd: null` for both usage rows.
- **Logs:** the backend log (10 lines) contained neither the key, its secret part nor the admin token. The demo's stdout/stderr did not contain the key. Its stderr held only two OpenTelemetry exporter lines: "Failed to export spans batch due to timeout, max retries or shutdown." and "… code: 401, reason: Unauthorized".

**Integration tests (all pass):**
- A (rows, attribute values, resource, `server_dropped_attributes_count = 0`, usage identity, NULL cost);
- A via the read API;
- B (sync + async children);
- C;
- D (M02 = 2, M03 = 1 for one call);
- re-export of the same 3 spans twice through a second exporter: counts and usage rows unchanged;
- unknown and revoked keys: `401`, 0 rows, app unaffected, no key in the backend log;
- R08 compatibility with format edge cases (`ft:mock-chat:org:x:id1`, `org/mock-model@v1+build.7`, prompt version `2026-10-01:rc+1/a`) stored unchanged with 0 server drops, while values the backend would refuse are withheld by the SDK;
- the demo app end to end (A–E as above);
- a leak check over a full DB dump, API responses and the backend log: no key, no secret part, no prompt/system sentinel, no completion text, no exception message, no traceback. All usage rows are `synthetic`, 0 `real` traces, 0 rows with a cost.

### 4.2 Test IDs

| ID | Status | Evidence | What is still missing |
|---|---|---|---|
| T09 | PARTIAL (phase-2a) | Mock CLIENT span `chat mock-chat-small` with required `gen_ai.*` + provenance (unit shape test; live rows) | Real-provider half: OpenAI adapter |
| T10 | PARTIAL (phase-2a) | Children of the current span, sync, async (`gather`) and multiple children, same trace (unit + live) | The FastAPI demo root span |
| T11 | PARTIAL (phase-2a) | Mock provider-reported usage → `gen_ai.usage.*` → `usage_source=provider_reported` in the DB | Mapping of the OpenAI usage object |
| T12 | PARTIAL (phase-2a) | 429 / 500 / timeout → `ERROR`, `error.type`, attempt recorded, application handles the error | Classification of OpenAI SDK exceptions |
| T13 | **PASS (phase-2a)** | Backend down, slow, 401/400/413, 503 retried: the app call returns (< 0.5 s in unit tests, 16 ms in the demo), no exception, export bounded by `export_timeout_seconds`, flush/shutdown bounded | Re-verified in Phase 5 |
| T14 | **PASS (phase-2a)** | Same `call_id`, attempts 1..n, one usage row per attempt; M02 > M03 by the catalog definitions in SQL (unit + live + smoke) | Aggregate service (Phase 2B) will reuse the definition |
| T15 | PARTIAL (phase-2a) | Project key never in exported spans, request bodies, DB, API, SDK/backend logs, reprs or stats; credential-shaped metadata withheld | `OPENAI_API_KEY` path (no adapter yet) |
| T16 | PARTIAL (phase-2a) | No content in the raw protobuf body, the DB dump, the API or logs; exceptions not recorded; backend half from Phase 1 | Re-check with the OpenAI adapter |
| T18 | PARTIAL (phase-2a) | SDK-produced failed attempt → NULL tokens, `unknown` + `usage_missing`; success → NULL cost + `price_missing`; API `null` | Interplay with real prices (Phase 2B) |
| T23 | PARTIAL (phase-2a) | SDK spans re-exported twice → one usage row per attempt, rows unchanged | "Cost unchanged" is trivially NULL until pricing exists |

Also re-executed: T01 (+S3), T02 and T04 within the live tests; their matrix cells keep their Phase 1C references. T48 is unchanged (dashboard). **Not run:** T17, T19–T22, T24, T29, T32.

## 5. Requirement checklist

| Task | Requirement | Evidence |
|---|---|---|
| 1 | Independent package, Python 3.12, pinned OTel, typed API, `pyproject.toml`, isolated tests | §3; clean-room install (#15); `test_install.py` |
| 1 | Documented config contract (endpoint, key, service, timeout, batching, provenance, content policy) | `sdk/README.md` "Configuration"; `test_config.py` |
| 1 | No unexpected global override; no duplicate processors | `test_init_does_not_touch_the_global_provider`, the `set_global` subprocess tests, `test_repeated_init_…_reuses_one_processor` (mutation-checked) |
| 2 | Official OTLP/HTTP protobuf exporter, explicit `/v1/traces`, `X-Project-Key` | `test_wire_format_and_project_key_header` (gzip and none); live tests |
| 2 | BatchSpanProcessor with bounded queue and batch; 4 / 16 MiB and 2048 limits | `test_queue_is_bounded…`, `test_batches_never_exceed…` (2100 spans → max 2048 per request), `test_oversized_request_is_dropped_client_side`; config bounds |
| 2 | Timeouts and their units | §2 table; `test_export_timeout_is_in_seconds…` (mutation-checked) |
| 2 | `force_flush()` / `shutdown()`; never log keys; fail open; no delivery promise | lifecycle tests; log tests; T13; README "Delivery guarantees" |
| 3 | Deterministic success / failed / delayed / retry scenarios; SYNTHETIC; UUID `call_id`; per-attempt identity; GenAI attributes; sync + async; parent/child; no content; R08 | §3, §4 |
| 3 | Never fake real-provider evidence | §2 decision 1; `test_every_mock_span_is_synthetic_and_never_real` |
| 4 | Demo A–E against the real backend, verified in PostgreSQL and the read API | §4.1 |
| 5 | All listed reliability and security tests | §4 file list, §4.2 |
| 6 | README, verification runs, matrix, status, this report | `sdk/README.md`; §4; `TEST_MATRIX.md`; `PHASE_STATUS.md` |

## 6. Files changed

| File | Change |
|---|---|
| `sdk/pyproject.toml`, `sdk/requirements-dev.lock`, `sdk/README.md` (new) | Package metadata, pinned lock, documentation |
| `sdk/src/llm_insight_sdk/{__init__,_version,config,telemetry,instrumentation,mock,_guards}.py`, `py.typed` (new) | The SDK |
| `sdk/tests/{conftest,otlp_stub}.py`, `sdk/tests/unit/test_{config,guards,instrumentation,telemetry,install}.py`, `sdk/tests/integration/{conftest,test_live_backend}.py` (new) | Tests |
| `demo_app/{__init__,__main__}.py` (new) | Demo application |
| `.env.example` | SDK variables; `OTEL_EXPORTER_OTLP_TRACES_TIMEOUT` note replaced by the verified finding (placeholders only) |
| `docs/ARCHITECTURE.md` | §6 Phase 2A install note; §7 layout note |
| `docs/TELEMETRY_CONTRACT.md` | §0 tested-clients sentence (SDK, timeout unit) |
| `docs/SOURCES_AND_RISKS.md` | Semconv re-check recorded; R03 mitigated; R25–R27 added |
| `docs/TEST_MATRIX.md` | T09–T16, T18, T23 statuses per §4.2; sub-case evidence pointer |
| `docs/PHASE_STATUS.md`, this report | Status |

**Not changed:**
- ADR-01…21 and CR-01…09;
- `llminsight.attrmap.v1` and the semconv pin;
- migration `0001`, the backend code, `backend/requirements.lock`, `docker-compose.yml`, `CLAUDE.md`, `scripts/verify_phase0.py`;
- the dev database contents.

## 7. Risks and limitations

- **R26: delivery is best effort** (accepted, ADR-06/09). Spans are lost on a crash or kill, a shutdown over its timeout, a full queue (oldest dropped), an outage longer than the export timeout, and non-retryable rejections (`400`/`401`/`408`/`413`). `stats()` makes the loss visible. There is no persistent queue and no billing-grade claim.
- **R25: duplicated value rules** in the SDK. The backend is authoritative, and the live tests detect drift.
- **R27: a recovered retry marks its trace `error`** (contract §4.4). Phase 3 should present it clearly.
- **R03 mitigated.** `OTEL_EXPORTER_OTLP_TRACES_TIMEOUT` is ignored by design. A user who relies on it gets a warning, not a silent effect.
- **Provenance** stays client-asserted (R16). The SDK cannot emit `real` in this release.
- **Daemon threads.** After a timed-out flush or shutdown, an in-flight HTTP request may continue on a daemon thread until `export_timeout_seconds`. At interpreter exit it is abandoned.
- **Global provider.** With `set_global=False` (the default), spans from other instrumentations on the application's own global provider are exported there, not to LLM Insight. An SDK inference span whose parent lives in another provider is stored as an orphan (T27 behaviour). Documented.
- **Windows-only test runs.** Every run in §4 was on Windows. The harness defaults to `backend/.venv/Scripts/python.exe` (`LLMI_BACKEND_PYTHON` overrides it). The POSIX `bin/python` path is coded but not exercised.
- **Unchanged backend risks:** R08 residual, R23, R24.

## 8. Open items for the rest of Phase 2 (not started)

- The OpenAI adapter. A real smoke run only with explicit approval and a budget; otherwise it stays NOT RUN.
- The FastAPI demo root span (completes T10).
- `model_prices` and the `model_price_id` FK migration; Decimal cost (T17, T19–T22).
- Aggregate services M01–M15 (T24, T29, T32).
- The `phase-2` tag after the full Phase 2 gate.

## 9. Verdict

**Phase 2A: complete, pending your review.**
- All acceptance runs pass: backend 876/876 before and after, SDK 247/247 with 0 skipped and the live backend required, Ruff, strict mypy, `pip check`, `alembic check`, compose, doc check 691/0, `git diff --check`, the clean-room install and the 9/9 mutation check.
- The SDK exports genuine OTLP/HTTP protobuf to the real backend. Every stored inference row is synthetic, and every cost is `NULL`.
- No real API was called and no price was seeded.
- Phase 2 continues only on your instruction.
