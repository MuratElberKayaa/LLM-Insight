# LLM Insight — Telemetry Contract (OTLP/HTTP + GenAI mapping)

Contract version: `telemetry-contract/1` · Attribute mapping version: `llminsight.attrmap.v1`
Verified against (accessed 2026-10-24):

| Source | Version / status |
|---|---|
| OTLP spec — https://opentelemetry.io/docs/specs/otlp/ (source: `open-telemetry/opentelemetry-proto` `docs/specification.md`) | Spec 1.11.1 (proto release v1.11.1, 2026-09-29). **Stable** for traces. |
| GenAI client inference — https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/client-inference.md | **Development**. Repo has no releases; pinned at commit `9ffc7e00c639` (2026-10-21). Core attrs `error.type`, `server.*` are Stable (semconv v1.44.0 links). |
| GenAI OpenAI conventions — `.../docs/gen-ai/openai.md` | **Development**. `gen_ai.provider.name` MUST be `openai`. |
| Python `opentelemetry-proto` / `-sdk` / `-exporter-otlp-proto-http` | 1.45.1 (PyPI, 2026-10-06) |

Because GenAI conventions are Development, every stored span records `attributes_schema_version` and all mapping lives in one adapter (PADR-02/03). Upstream renames are absorbed by adding alias rows, never by changing stored column semantics.

## 1. Endpoint `POST /v1/traces`

| Aspect | Contract | Spec basis |
|---|---|---|
| Method / path | `POST /v1/traces`; other methods → `405` | OTLP default path `/v1/traces` (default port 4318 not required; we use backend port) |
| Auth | Header `X-Project-Key: <key>`; project resolved **server-side** from the key. Any client-sent `project_id` attribute is ignored and dropped. | Product decision (ADR-08) |
| Request Content-Type | `application/x-protobuf` only (parameters such as `; charset` ignored). Anything else, including `application/json` → `415` | Spec: client MUST set `application/x-protobuf` for binary; server SHOULD also accept JSON → deviation PADR-04 |
| Request body | Binary `opentelemetry.proto.collector.trace.v1.ExportTraceServiceRequest` | Spec |
| Content-Encoding | absent / `identity` / `gzip`. Others → `415`. Corrupt gzip → `400` | Spec: servers MUST support none and gzip |
| Size limits | Wire body > `LLMI_OTLP_MAX_BODY_BYTES` (4 MiB) → `413`; decompressed > `LLMI_OTLP_MAX_DECOMPRESSED_BYTES` (16 MiB, enforced while streaming decompression) → `413`; spans > `LLMI_OTLP_MAX_SPANS_PER_REQUEST` (2048) → `413` | Spec: MUST limit incl. after decompression, `413 Content Too Large`; RECOMMENDED 64 MiB → deviation PADR-05 |
| Response Content-Type | `application/x-protobuf` (same as request) for every response with a body, including errors | Spec: server MUST use same Content-Type as request |
| Response compression | Not compressed in MVP (gzip response is MAY) | Spec |

### 1.1 Response semantics

| Case | HTTP | Body | Client retry (per spec) |
|---|---|---|---|
| Full success (incl. full idempotent duplicate) | `200` | `ExportTraceServiceResponse`, `partial_success` **unset** | — |
| Empty request (no spans) | `200` | `ExportTraceServiceResponse`, unset | — |
| Undecodable protobuf, corrupt gzip, invalid IDs/timestamps/enum, any invalid span (batch is atomic, PADR-06) | `400` | `google.rpc.Status{message}`; `details` MAY carry `BadRequest` | MUST NOT retry |
| Missing / unknown / revoked key (same generic message for all) | `401` | `google.rpc.Status` | not retryable |
| Wrong method | `405` | `google.rpc.Status` | not retryable |
| Body / decompressed / span-count limit | `413` | `google.rpc.Status` | not retryable |
| Unsupported Content-Type / Content-Encoding | `415` | `google.rpc.Status` | not retryable |
| Rate limited (in-process bucket) | `429` + `Retry-After: <seconds>` | `google.rpc.Status` | SHOULD retry, honour Retry-After |
| DB unavailable / transaction failed (rolled back) | `503` + `Retry-After` | `google.rpc.Status` | SHOULD retry |
| Unexpected server error (rolled back) | `500` | `google.rpc.Status` | not retryable per spec |

`partial_success` is never set in MVP (no partial acceptance mechanism; master plan §F). `Status.code` may be omitted (spec). `Status.message` is developer-facing, English, and never echoes the key, headers or attribute values.

### 1.2 Validation rules (any failure → whole batch `400`)

- `trace_id`: exactly 16 bytes, not all-zero. `span_id`: exactly 8 bytes, not all-zero. `parent_span_id`: empty or 8 bytes non-zero.
- Stored as **lowercase hex** (32 / 16 chars). OTLP/JSON uses hex for these IDs, not base64; API responses use the same hex form.
- `start_time_unix_nano` > 0, `end_time_unix_nano` ≥ start; values are uint64 ns and are stored as `BIGINT` (values ≥ 2^63 → `400`). Duration = `end_ns - start_ns`.
- `kind` and `status.code` must be known enum values.
- Missing parent in DB/batch is **not** an error: the span is stored and shown as orphan (T27).

### 1.3 Idempotency

OTLP itself has no dedup ("may result in duplicate data on the server side" — spec). We add it:
- Span: `UNIQUE(project_id, trace_id, span_id)`; re-export → `ON CONFLICT DO NOTHING` (first write wins; content changes on re-export are ignored and counted in a server log metric).
- Usage: `UNIQUE(project_id, canonical_call_key)` (see §4).
- A batch is processed in one DB transaction; any failure rolls back everything.

## 2. Resource / scope / span traversal

`ExportTraceServiceRequest.resource_spans[]` → `resource.attributes` + `scope_spans[]` → `scope{name,version}` + `spans[]`. Multiple `resource_spans` and `scope_spans` per request are supported (T03). `service.name` from resource; if absent → `unknown_service`.
Span `events` and `links` are **not stored** in MVP; an `exception` event contributes only `exception.type` (mapped to `error_type` if `error.type` absent). `exception.message` / `exception.stacktrace` are dropped (possible PII).

## 3. Attribute allowlist (`llminsight.attrmap.v1`)

Everything not listed is **dropped before persistence** and only counted in `spans.dropped_attributes_count`. String values truncated to 256 chars, arrays to 16 items (truncation flagged).

**Resource:** `service.name`, `service.version`, `service.namespace`, `deployment.environment.name`, `telemetry.sdk.name`, `telemetry.sdk.language`, `telemetry.sdk.version`, `llminsight.sdk.version`.

**Span — GenAI (Development semconv):**

| Attribute | Type | Stored as | Note |
|---|---|---|---|
| `gen_ai.operation.name` | string | span JSONB + inference detection | Required by semconv; inference ops: `chat`, `text_completion`, `generate_content`, `embeddings` |
| `gen_ai.provider.name` | string | `usage_records.provider` | Required; alias `gen_ai.system` (PADR-02) |
| `gen_ai.request.model` / `gen_ai.response.model` | string | `requested_model` / `response_model` | Both kept; never merged |
| `gen_ai.response.id` | string | canonical key input | provider response ID |
| `gen_ai.response.finish_reasons` | string[] | JSONB | |
| `gen_ai.request.max_tokens`, `.temperature`, `.top_p`, `.seed`, `.stream` | num/bool | JSONB | |
| `gen_ai.output.type` | string | JSONB | e.g. `json` |
| `gen_ai.prompt.name`, `gen_ai.prompt.version` | string | JSONB; `traces.prompt_version` | semconv Development attrs replace a custom prompt-version attr |
| `gen_ai.usage.input_tokens` | int | `input_tokens` | semconv: SHOULD include **all** input incl. cached |
| `gen_ai.usage.output_tokens` | int | `output_tokens` | includes reasoning |
| `gen_ai.usage.cache_read.input_tokens` | int | `cached_input_tokens` | subset of input — never added again |
| `gen_ai.usage.cache_write.input_tokens` | int | `cache_write_input_tokens` | subset of input (PADR-09) |
| `gen_ai.usage.reasoning.output_tokens` | int | `reasoning_output_tokens` | subset of output — never added again |
| `error.type` | string | `spans.error_type` | Stable |
| `server.address`, `server.port` | string/int | JSONB | Stable |
| `openai.request.service_tier`, `openai.response.service_tier`, `openai.response.system_fingerprint`, `openai.api.type` | string | JSONB | OpenAI conventions |

**Span — HTTP (root):** `http.request.method`, `http.route` (template only), `http.response.status_code`, `url.scheme`.
**Span — LLM Insight (`llminsight.*`):** `llminsight.call_id` (UUID string, one logical call), `llminsight.attempt` (int ≥ 1, one physical attempt), `llminsight.provenance` (`real`|`synthetic`), `llminsight.usage_source` (`provider_reported`|`estimated`|`unknown`), `llminsight.validation.rule` (string), `llminsight.validation.passed` (bool).

**Always denied (never stored, even if allowlisting is misconfigured):** `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions`, `gen_ai.tool.definitions`, `gen_ai.prompt.variable`, `gen_ai.conversation.id`, `url.full`, `url.query`, `http.request.header.*`, `http.response.header.*`, `client.address`, `user.*`, `enduser.*`, `exception.message`, `exception.stacktrace`, any key containing `authorization`, `api_key`, `apikey`, `password`, `secret`, `token` (except the explicit `gen_ai.usage.*_tokens` keys above).

## 4. Usage ledger mapping

A `usage_records` row is created **only** for spans with an inference `gen_ai.operation.name` (one row per physical attempt). Parent / root spans never produce usage or cost (no double count, T20/T21/T23).

`canonical_call_key` precedence (stored with `call_key_source`):
1. `sdk:{llminsight.call_id}:{llminsight.attempt}` → `sdk_attempt` (preferred; stable across re-export)
2. `resp:{gen_ai.provider.name}:{gen_ai.response.id}` → `provider_response_id`
3. `span:{trace_id}:{span_id}` → `span_fallback` (best effort; documented as weaker)

Token rules: provider-reported values preferred (`usage_source=provider_reported`). Missing → `NULL` + `usage_source=unknown`, never `0`. `estimated` is reserved; MVP ships no tokenizer.
Consistency: `cached_input_tokens + cache_write_input_tokens > input_tokens` or `reasoning_output_tokens > output_tokens` → tokens stored as received, `cost_status=unknown`, `cost_unknown_reason=usage_inconsistent`.

Cost (Decimal, USD, per `model_prices` row effective at span start; full formula in `METRICS_CATALOG.md` M10):
`uncached_in × input_price + cache_read × cached_input_price + cache_write × cache_write_price + output × output_price` (all ÷ 1,000,000). Subset attributes (`cache_read`, `cache_write`, `reasoning`) are emitted by semconv only "when available and non-zero". When one is absent it is stored as `NULL` (not reported) and treated as 0 **only inside the cost formula**. The LLM Insight SDK always emits provider-reported subsets explicitly, including 0. If `input_tokens` / `output_tokens` is missing, or a price needed for a non-zero term is missing, the result is `cost_usd=NULL`, `cost_status=unknown` with reason `price_missing` / `usage_missing` / `usage_inconsistent`. Cached tokens are **not** silently priced at the full input rate.

## 5. Provenance

`llminsight.provenance` on inference spans: `real` (live provider call) or `synthetic` (mock adapter / fixture). Missing → `unknown`. A trace is `synthetic` if any span is synthetic. The UI must show `SYNTHETIC` / `UNKNOWN` badges; real and synthetic aggregates are never silently merged.

## 6. Representative fixture

`docs/fixtures/otlp_trace_3span.json` is a **synthetic**, illustrative OTLP/JSON rendering of one `ExportTraceServiceRequest` with three spans in one trace (`SERVER` root → `CLIENT` inference + `INTERNAL` validation, two scopes). Expected ingest result is in `docs/fixtures/README.md`. Note: protobuf's generic JSON parser treats `bytes` as base64, whereas OTLP/JSON uses hex for `traceId`/`spanId`/`parentSpanId` — Phase 1 tooling must hex-decode IDs before building the binary protobuf request. The fixture has not been parsed by `opentelemetry-proto` yet (Phase 1, T01).
