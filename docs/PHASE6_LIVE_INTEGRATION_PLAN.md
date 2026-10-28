# LLM Insight — Phase 6 Live Integration Plan

**Status:** Phase 6A design contract (2026-10-27), recorded by ADR-28…30 (accepted within the Phase 6A instruction). Phase 6B preflight (2026-10-28, `reports/phase-6b-preflight.md`): verified offline, hardened by ADR-31 (§C, §G). **No real or paid provider call has been made**; the first real request needs architectural and explicit spending approval.

**Baseline:** Phase 5 MVP, release commit `1a57d65` (annotated tag `phase-5`), 48/48 acceptance scenarios, PostgreSQL revision `0005`, local-first and mock-only.

**Goal of Phase 6A:** a usable, tested foundation for observing real OpenAI API calls, without changing the accepted Phase 5 behaviour. Phase 6A delivers code, contract and offline tests only.

## 1. Architecture inventory (audited from source, 2026-10-27)

| # | Capability | Where (source) | Phase 6A verdict |
|---|---|---|---|
| 1 | SDK tracing: private `TracerProvider`, `BatchSpanProcessor`, bounded flush/shutdown, fail-open export counters | `sdk/src/llm_insight_sdk/telemetry.py` | **Reused unchanged** |
| 2 | Public instrumentation API: `inference_span`, `InferenceRecorder`, `TokenUsage`, `call_with_retries`, `classify_error`, client-side value guards | `sdk/src/llm_insight_sdk/instrumentation.py`, `_guards.py` | **Reused**; one additive method `InferenceRecorder.mark_failed` (§2.E) |
| 3 | Provider adapters: OpenAI **Chat Completions** (sync + async, Phase 2B, offline-tested), deterministic mock provider | `sdk/src/llm_insight_sdk/openai_adapter.py`, `mock.py` | Chat adapter **reused, not duplicated**: the Responses API is added to the same module and the same client class |
| 4 | OTLP exporter: official `opentelemetry-exporter-otlp-proto-http` 1.45.1, gzip, explicit timeout (seconds), `X-Project-Key` header, request-size bound | `telemetry.build_span_exporter`, `config.py` | **Reused unchanged** |
| 5 | Trace/span correlation: CLIENT span as child of the current span; `llminsight.call_id` (UUIDv4) + `llminsight.attempt` per physical attempt | `instrumentation.inference_span` | **Reused unchanged** |
| 6 | Backend ingest and validation: `POST /v1/traces`, binary protobuf, auth, limits, atomic transaction, allowlist `llminsight.attrmap.v1`, value safety | `backend/app/api/otel.py`, `services/ingestion/*` | **Unchanged** (no backend code change) |
| 7 | Usage token normalization: provider-reported only, NULL when missing, subsets never added twice, consistency check | `services/ingestion/usage.py`; contract `TELEMETRY_CONTRACT.md` §4 | **Unchanged**; Responses usage maps to the same five attributes |
| 8 | Price catalogue and Decimal cost: `openai-2026-10-24` (6 rows), `NUMERIC(20,10)`, half-even once, price once at insert, append-only | `services/pricing.py`, `pricing_catalog.py`, `PRICING.md` | **Unchanged**; mirrored read-only in the SDK for pre-call bounds (§2.H) |
| 9 | Cost NULL semantics: `unknown` + reason (`price_missing`, `usage_missing`, `usage_inconsistent`), never 0; applicability `service_tier=default` on `api.openai.com` | `pricing.py` `APPLICABILITY` | **Unchanged**; the Responses request always sends `service_tier="default"` |
| 10 | Project isolation: project resolved server-side from the HMAC'd ingest key; every read scoped by mandatory `project` | `core/security.py`, `repositories/*` | **Unchanged** |
| 11 | Diagnostics inputs: `error.type` table (HTTP status, `timeout`, four OpenAI class names), OpenAI finish reasons `length`/`content_filter`, call/attempt grouping | `services/diagnostics/catalog.py`, `engine.py` | **Unchanged**; Responses `incomplete` reasons are recorded verbatim (limitation L3) |
| 12 | Dashboard trace/overview queries: `/api/v1/traces[/{id}]`, `/overview`, `/models/compare` | `backend/app/api/{traces,overview}.py`, `frontend/` | **Unchanged**; Responses spans appear like any inference span |
| 13 | Live/mock flags: backend `LLMI_ALLOW_REAL_LLM` (experiments, refused `409` — no real executor); SDK/demo `LLMI_PROVIDER_MODE=mock`, `OPENAI_API_KEY`, `LLMI_REAL_LLM_BUDGET_USD` in `.env.example`, used by no code before 6A | `backend/app/api/experiments.py`, `.env.example`, `demo_app/__main__.py` | Backend flag **unchanged** (experiments stay mock-only). `LLMI_PROVIDER_MODE` and `LLMI_REAL_LLM_BUDGET_USD` are now read by the live guard |
| 14 | Secret boundaries: `ConfigError` names settings never values; project key excluded from `repr`; adapter never records headers or exception messages; `Status.message` never stored; test-report redaction | `config.py`, `openai_adapter.py`, `tests/redaction.py` | **Reused**; the live guard follows the same rule |

**Not found (therefore built in 6A):** an OpenAI Responses API path; any explicit permission, budget or concurrency guard for real provider calls; a live runner.

## 2. Design

### A. ProviderAdapter abstraction
`llm_insight_sdk.providers` (no third-party import): `TextRequest` (model, input text — `repr=False`, max output tokens, prompt name/version) → `TextGenerationAdapter.generate_text()` → `TextOutcome` (status, models, response ID, service tier, provider-reported `TokenUsage` or `None`, one `AttemptRecord` per physical attempt, error category, duration; the provider object only on `provider_response`, excluded from `repr` and equality). Adapters return provider failures as outcomes and raise only for requests refused before sending. One implementation: `openai_adapter.OpenAIResponsesTextAdapter` (ADR-05).

### B. OpenAI Responses API integration
`ObservedOpenAIClient.create_response(model=, input=, ...)` wraps `client.responses.create` (openai 3.28.0, pinned) with the existing per-attempt span machinery. The client's own retries stay disabled (`max_retries=0`); only `https://api.openai.com` is accepted (existing check).

### C. First scope: non-streaming text
Refused before any request: `stream` (any value), `stream_options`, `background`, `conversation`, `previous_response_id`, `tools`, `tool_choice`, `store` other than `False`, and (Phase 6B preflight, ADR-31) every argument outside the allowlist `RESPONSES_ALLOWED` (`instructions`, `max_output_tokens`, `metadata`, `prompt_cache_key`, `reasoning`, `safety_identifier`, `service_tier`, `store`, `temperature`, `text`, `timeout`, `top_p`, `truncation`, `user`): `extra_body` could carry `tools`, `prompt` a stored template with its own model and tools, `moderation` a second model, and `context_management` or `access_programs` change processing. `store=False` is always sent. Tools are refused because built-in tool charges are not representable in the catalogue (`PRICING.md` §3): a token-only cost would understate them while being labelled `computed`.

### D. Streaming (later enhancement, not implemented)
Needs: usage from the final `response.completed` event only (never summed from deltas), a span that ends on the terminal event or on abandonment, abandonment marked as an error with unknown usage, and time-to-first-token as an additive attribute (an `attrmap.v2` decision, ADR-15). Until then streaming is refused.

### E. Telemetry normalization and version mapping
Operation `chat` + `openai.api.type = responses`. The pinned semconv (`semantic-conventions-genai@9ffc7e00c639`, `openai.md`, re-read 2026-10-27) defines `openai.api.type ∈ {chat_completions, responses}` and names no Responses-specific operation, and its rule is "SHOULD use applicable predefined value", which is `chat`. ADR-13 detection, the allowlist and `attributes_schema_version = llminsight.attrmap.v1` are therefore **unchanged**; no `attrmap.v2`.

| Responses API field | Span attribute | Rule |
|---|---|---|
| request `model` | `gen_ai.request.model` | `MODEL` identifier rule |
| `response.model` | `gen_ai.response.model` | kept separate from the request model |
| `response.id` | `gen_ai.response.id` | opaque ID rule; absent → no attribute (backend downgrades a `real` claim to `unknown`, `TELEMETRY_CONTRACT.md` §5) |
| `usage.input_tokens` | `gen_ai.usage.input_tokens` | provider-reported int ≥ 0, else whole usage unknown |
| `usage.output_tokens` | `gen_ai.usage.output_tokens` | idem |
| `usage.input_tokens_details.cached_tokens` | `gen_ai.usage.cache_read.input_tokens` | subset of input; reported 0 kept |
| `usage.input_tokens_details.cache_write_tokens` | `gen_ai.usage.cache_write.input_tokens` | subset of input; reported 0 kept |
| `usage.output_tokens_details.reasoning_tokens` | `gen_ai.usage.reasoning.output_tokens` | subset of output |
| request `max_output_tokens`, `temperature`, `top_p` | `gen_ai.request.max_tokens`, `.temperature`, `.top_p` | numeric only |
| request `service_tier` (≠ `auto`) / `response.service_tier` | `openai.request.service_tier` / `openai.response.service_tier` | pricing applicability uses the response value |
| request `text.format.type` `json_schema`/`json_object`/`text` | `gen_ai.output.type` `json`/`text` | |
| `status = incomplete` + `incomplete_details.reason` | `gen_ai.response.finish_reasons = [reason]` | OpenAI's Responses vocabulary (`max_output_tokens`, `content_filter`, …), not mapped to Chat's `length` |
| `status = completed` | no finish reason | the API reports no per-output reason; none is invented |
| `status = failed` / other status / not a `Response` | `error.type = openai.response.failed` / `.unexpected_status` / `.malformed`, span `ERROR` | via `InferenceRecorder.mark_failed`; the response is still returned |
| — | `llminsight.provenance` | `real` only from the live runner's real client; offline fixtures `synthetic` |

Every field is type-checked before use: `openai` 3.28.0 does not validate bodies (probed: `{}` yields a `Response` with `id=None`, an HTML body yields a `str`, wrong field types pass through). Never recorded: `input`, `instructions`, output text, tool arguments, headers, the API key, exception messages, provider request IDs.

### F. Error classification
Span `error.type` keeps the telemetry-contract values (so diagnostics keep interpreting them): `timeout`, the HTTP status (`401`, `429`, `500`, …), `openai.<Class>`. Callers and the runner get a stable category (`providers.ERROR_CATEGORIES`) from the class, status and OpenAI `code` only:

| Failure | Category | Retried automatically? |
|---|---|---|
| `APITimeoutError` | `timeout` | **No** (ambiguous: may have been processed and billed) |
| `APIConnectionError` | `connection_error` | **No** (ambiguous) |
| 429, `code ≠ insufficient_quota` | `rate_limited` | Only if `LLMI_LIVE_MAX_RETRIES > 0` (rejected before processing) |
| 429, `code = insufficient_quota` | `quota_exhausted` | No (cannot succeed) |
| 401 / 403 / 404 | `authentication_failed` / `permission_denied` / `not_found` | No |
| 400 / 409 / 422 | `invalid_request` | No |
| ≥ 500 | `provider_error` | **No** (ambiguous) |
| other status | `http_error` | No |
| invalid JSON / not a `Response` | `malformed_response` | No |
| `status = failed` / unexpected status | `response_failed` / `unexpected_status` | No |

The Chat Completions path keeps its Phase 2B retry rule (timeouts, 408/409/429/5xx) unchanged for backward compatibility; the live runner does not use it (R80, `SOURCES_AND_RISKS.md`).

### G. Explicit provider execution permission
`llm_insight_sdk.live.evaluate_policy(env, now=)` returns **every** refusal; execution is allowed only with none. An `OPENAI_API_KEY` alone enables nothing. Real mode is off by default (the shipped `.env.example` leaves every live key empty).

| Key | Required | Rule (hard cap) |
|---|---|---|
| `LLMI_PROVIDER_MODE` | yes | must be `real` (default `mock`) |
| `LLMI_LIVE_CONSENT` | yes | exactly `openai-paid-calls` |
| `OPENAI_API_KEY` | yes | presence only; never read into a log, output, span or exception |
| `OPENAI_BASE_URL` | must be **unset** | refused if set; the runner pins `https://api.openai.com/v1` |
| `OPENAI_CUSTOM_HEADERS`, `OPENAI_ORG_ID`, `OPENAI_PROJECT_ID` | must be **unset** | Phase 6B preflight (ADR-31): refused if set (`client_env_override`); `openai` 3.28.0 reads them from the process environment, a custom `Authorization` header replaces the checked key, and all three can route billing away from the dedicated project key |
| `LLMI_LIVE_PROVIDERS` | yes | allowlist; only `openai` |
| `LLMI_LIVE_MODELS` | yes | allowlist; each model needs a verified price |
| `LLMI_LIVE_MAX_REQUESTS` | yes | 1–20 per session |
| `LLMI_LIVE_MAX_INPUT_TOKENS` | yes | 1–8192 (input bound, §H) |
| `LLMI_LIVE_MAX_OUTPUT_TOKENS` | yes | 16–4096; sent as `max_output_tokens` |
| `LLMI_LIVE_MAX_REQUEST_COST_USD` | yes | Decimal in (0, 1.00] |
| `LLMI_REAL_LLM_BUDGET_USD` | yes | session ceiling, Decimal in (0, 5.00] |
| `LLMI_LIVE_TIMEOUT_SECONDS` | no | 1–60, default 20 |
| `LLMI_LIVE_MAX_RETRIES` | no | 0–2, default 0 |
| `LLMI_LIVE_MAX_CONCURRENCY` | no | 1–4, default 1 |

Further refusals: price evidence older than 14 days (`price_catalogue_stale`; the current catalogue's window ends 2026-11-07T21:40Z), clock before the evidence, limits that cannot hold a full-size request (`request_cost_bound_exceeds_limit`, `session_budget_too_small`). Messages name the setting and rule, never a value.

Example for a Phase 6B smoke test (placeholders only, set in the operator's own shell, never committed): `LLMI_PROVIDER_MODE=real`, `LLMI_LIVE_CONSENT=openai-paid-calls`, `LLMI_LIVE_PROVIDERS=openai`, `LLMI_LIVE_MODELS=gpt-5.4-mini`, `LLMI_LIVE_MAX_REQUESTS=1`, `LLMI_LIVE_MAX_INPUT_TOKENS=256`, `LLMI_LIVE_MAX_OUTPUT_TOKENS=64`, `LLMI_LIVE_MAX_REQUEST_COST_USD=0.01`, `LLMI_REAL_LLM_BUDGET_USD=0.02`.

### H. Request-level and session-level spending guards
- **Before sending** (`LiveSession.reserve`): input bound = UTF-8 bytes of the input + 64 framing tokens (byte-level BPE: a token covers ≥ 1 byte; the allowance is an assumption checked after the call). Attempt bound = input bound × max(input, cached-input price) + `max_output_tokens` × output price (reasoning is inside output), rounded **up** to 10 decimals. Request bound = attempt bound × (1 + retries). Refused if over the request cap, if booked + outstanding + request bound would exceed the session budget, if the request count is reached, or if `max_concurrency` requests are in flight (non-blocking refusal, never queued).
- **After the call** (`LiveSession.settle`): each attempt with provider-reported, priceable usage is booked at its estimate (the backend's formula, half-even); any attempt without one (every failure, a malformed body) is booked at its **bound, never 0**. The session **halts** (no further request) if reported usage exceeds the bound, the estimate is unknown (unpriced served model, non-`default` tier) or exceeds the bound, or more attempts happened than reserved. An interrupted call books every allowed attempt at its bound.
- **Estimated vs billed:** every figure is an estimate (`estimated_cost_usd`, `booked_against_budget_usd`); the billed amount exists only in the provider's invoice/usage dashboard and is never claimed.
- **Scope:** in-process (like CR-08/R67), reset at exit; not distributed, not billing-account-wide. R66's durable idempotency concerns backend experiments, which stay mock-only; the runner avoids duplicate billing by **not retrying ambiguous outcomes** and sending one request per run.

### I. Safe metadata-only recording
ADR-19 unchanged. The runner's input is a fixed SYNTHETIC sentence; its output text is never printed, logged or exported. The runner prints one JSON line of metadata (§J). Tests assert that the key, input, output, provider error text and request ID never appear on the OTLP wire, in SDK logs, in the runner output, in the backend log or in the read API.

### J. Demo runner architecture
`python -m demo_app.live_openai` (SDK venv):
1. evaluate the policy; 2. load the SDK telemetry config; 3. readiness probe: an empty OTLP request to `/v1/traces` (`200` and nothing written when the backend is up and the project key accepted; `401` → `project_key_rejected`); 4. **default: print the dry-run plan** (model, limits, worst-case cost, catalogue) and exit 0 without creating an OpenAI client; 5. with `--execute` and no refusal: reserve, open root span `demo.live-openai`, call the adapter (`store=False`, `service_tier="default"`), settle, flush and shut down telemetry, print trace ID, status, models, token counts (or `null` = unknown), duration, estimated cost, the dashboard URL `http://127.0.0.1:3001/traces/{trace_id}?project=<LLMI_DASHBOARD_PROJECT_ID>` and the API path. Exit codes: 0 dry run / completed, 1 provider failure, 2 refused. No browser endpoint, no live-call button, no route that accepts a key.

### K. Future multi-provider extensibility
A provider adds: a `TextGenerationAdapter` implementation; its usage mapping onto the five semconv counters; its error categories; a backend pricing applicability rule (`PRICING.md` §3 — required, otherwise its prices apply unconditionally); verified price rows (new catalogue version); `SUPPORTED_PROVIDERS` in `live.py`; and an ADR. The data model is already provider-agnostic (ADR-05).

### L. Data flow
```
OpenAI Responses API ──(HTTPS, store=false, tier=default)── openai 3.28.0 client (max_retries=0)
        │ Response (usage, id, model, status, service_tier) — typed-checked, content discarded
        ▼
ObservedOpenAIClient.create_response ── one CLIENT span per physical attempt (child of demo.live-openai)
        │ BatchSpanProcessor → OTLP/HTTP protobuf, gzip, X-Project-Key
        ▼
POST /v1/traces (backend, unchanged) ── attrmap.v1 allowlist, value safety, atomic txn
        ▼
PostgreSQL: spans, traces (derived), usage_records (one row per attempt; key resp:openai:{id};
            cost priced once at insert, Decimal, NULL+reason when unknown)
        ▼
/api/v1/traces/{id}, /overview, /models/compare ──► Next.js dashboard (server-side token)
```

## 3. Backward compatibility

- **Zero migrations, zero backend code changes.** Every Responses field fits `llminsight.attrmap.v1` and the §4 schema; nothing needed a new column.
- Chat Completions adapter: behaviour and tests unchanged; the shared span helper gained an `api_type` parameter defaulting to `chat_completions`.
- `InferenceRecorder.mark_failed` is additive; without it `_finish` behaves exactly as before.
- Backend `LLMI_ALLOW_REAL_LLM` and experiments: unchanged (mock-only, `mode: real` refused).
- `.env.example`: new keys are empty (fail-closed); existing keys and defaults unchanged.

## 4. Test plan (offline; evidence in `reports/phase-6a.md`)

| Requirement | Tests |
|---|---|
| Success, token accounting, trace correlation, flush, no duplicates | `test_openai_responses.py` (span shape, parent/trace IDs, one span per call, stats), `test_live_runner.py` (root + one child), `test_live_responses.py` (DB rows, API) |
| Missing usage / response ID; malformed / unexpected bodies | `test_openai_responses.py` (`usage_source=unknown`, malformed × 5, statuses), DB: `usage_missing`, NULL cost |
| Timeout, 429, 401, 403, 404, 400, 5xx, connection, quota | parametrized in `test_openai_responses.py` and `test_live_runner.py`; DB `error_type` |
| Bounded retry, no unauthorized retry | 429 retry with shared `call_id`, bound stop; ambiguous failures never retried even with 3 attempts allowed |
| Default OFF, consent, key, unknown price, budget, token ceilings, concurrency | `test_live_guard.py` (37 single-gate refusals, stale catalogue, unsatisfiable limits, session ledger, 8-thread concurrency) |
| No secret leakage, no content persistence | wire, SDK log, runner output, backend log and API checked for key, input, output, error text |
| Price mirror drift | `test_sdk_price_mirror_equals_the_seeded_catalogue` (live DB) |
| Phase 5 behaviour intact | full backend, SDK, frontend unit and Playwright suites |

## 5. Phase 6B prerequisites (not done in 6A)
1. Architectural review and acceptance of ADR-28…30.
2. Explicit user approval of the spend, the model and the ceilings, given for that run.
3. Re-verify the price catalogue (`PRICING.md` §2); after 2026-11-07T21:40Z the guard refuses until a new catalogue version is released in both copies.
4. The operator sets `OPENAI_API_KEY` in their own shell (never pasted into a chat, file or document), preferably a dedicated OpenAI project key with a provider-side monthly budget and a low rate limit.
5. Running backend (`python -m app`), seeded prices, a project key in `OTEL_EXPORTER_OTLP_TRACES_HEADERS`; dry run reviewed first.
6. Afterwards: compare the reported `input_tokens` with the bound (the framing assumption), check the stored row against the provider's usage page, and record the result.
