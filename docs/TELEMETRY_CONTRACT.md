# LLM Insight — Telemetry Contract (OTLP/HTTP + GenAI mapping)

**Contract version:** `telemetry-contract/1`. **Attribute mapping version:** `llminsight.attrmap.v1`. **Status:** binding (ADR-13…21, accepted 2026-10-24).

**Verified against** (accessed 2026-10-24):

| Source | Version / status |
|---|---|
| OTLP spec — https://opentelemetry.io/docs/specs/otlp/ (source: `open-telemetry/opentelemetry-proto` `docs/specification.md`) | Spec 1.11.1 (proto release v1.11.1, 2026-09-29). **Stable** for traces. |
| GenAI client inference — https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/client-inference.md | **Development**. The repo has no releases; pinned at commit `9ffc7e00c639` (2026-10-21). Core attributes `error.type` and `server.*` are Stable (semconv v1.44.0 links). |
| GenAI OpenAI conventions — `.../docs/gen-ai/openai.md` | **Development**. `gen_ai.provider.name` MUST be `openai`. |
| `google.rpc.BadRequest` — https://github.com/googleapis/googleapis/blob/master/google/rpc/error_details.proto | `field_violations[]{field, description, reason, localized_message}` |
| Python `opentelemetry-proto` / `-sdk` / `-exporter-otlp-proto-http` | 1.45.1 (PyPI, 2026-10-06). The exporter depends on `googleapis-common-protos ~=1.52`. |

GenAI conventions are still Development, so every stored span records `attributes_schema_version`, and all mapping lives in one adapter (ADR-14/15).

## 0. Support statement (limited interoperability)

LLM Insight implements an **OTLP/HTTP traces receiver for binary protobuf**: a subset of OTLP 1.11.1. It does **not** claim full OTLP support. The limitations:

| Limitation | Effect on clients | Basis |
|---|---|---|
| No OTLP/JSON (`application/json` gets `415`) | JSON-only exporters (e.g. the OTel JS `exporter-trace-otlp-http`) cannot send | ADR-16; deviation from a spec **SHOULD** |
| No OTLP/gRPC | gRPC exporters must switch to `http/protobuf` | ADR-03 |
| Traces only. `/v1/metrics`, `/v1/logs` and profiles return `404` | Exporters for other signals must not point here | ADR-04 |
| No partial acceptance | One invalid span rejects the whole batch (`400`, which is not retried) | ADR-18 |
| Limits below the spec recommendation (4 / 16 MiB versus 64 MiB) plus a span-count limit | A larger batch gets `413` and is dropped by the client | ADR-17 |
| `X-Project-Key` header is required (not part of OTLP) | Configure it with `OTEL_EXPORTER_OTLP_TRACES_HEADERS` | ADR-08 |
| Port 8000, not the OTLP default 4318 | Set `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` explicitly | Deployment choice |

Expected compatible (still **untested**, see T01-S3): the Python `opentelemetry-exporter-otlp-proto-http` and the OTel Collector `otlphttp` exporter with protobuf encoding.

## 1. Endpoint `POST /v1/traces`

| Aspect | Contract | Spec basis (OTLP 1.11.1) |
|---|---|---|
| Path | `/v1/traces` | Default traces path |
| Request Content-Type | Only `application/x-protobuf` is decoded (parameters ignored, media type compared case-insensitively) | Client MUST set it for binary; servers SHOULD also accept JSON (ADR-16 deviation) |
| Request body | Binary `opentelemetry.proto.collector.trace.v1.ExportTraceServiceRequest` | Spec |
| Content-Encoding | Absent, `identity` or `gzip`. Anything else gets `415` | Servers MUST support none and gzip; other encodings are unspecified |
| Auth | `X-Project-Key`. The project is resolved **server-side**; any client-sent project attribute is dropped | Product (ADR-08) |
| Limits | Wire 4 MiB, decompressed 16 MiB, 2048 spans. All return `413` (ADR-17; the count-based 413 is our policy) | MUST limit including after decompression and use `413`; 64 MiB is RECOMMENDED |
| Success | `200` with `ExportTraceServiceResponse`, `partial_success` **unset**, sent **only after the DB transaction commits** (ADR-18) | MUST 200 + response message; MUST leave `partial_success` unset on full success |
| Response compression | None | Gzip responses are a MAY |

### 1.1 Processing order (first failing step decides the response)

| # | Check | Failure response |
|---|---|---|
| 1 | Method is `POST` | `405` |
| 2 | Content-Type media type is `application/x-protobuf` | `415` |
| 3 | Content-Encoding is absent, `identity` or `gzip` | `415` |
| 4 | Client address is under the auth-failure limit (`LLMI_RATE_LIMIT_AUTHFAIL_PER_MIN`) | `429` + `Retry-After` |
| 5 | `X-Project-Key` is present, known and not revoked. Every failure counts toward step 4 and uses the same generic message | `401` |
| 6 | Per-key token bucket (`LLMI_RATE_LIMIT_INGEST_*`) | `429` + `Retry-After` |
| 7 | `Content-Length` and the streamed wire bytes are ≤ 4 MiB | `413` |
| 8 | gzip inflates incrementally to ≤ 16 MiB. Corrupt gzip | `413` / `400` |
| 9 | Protobuf decodes | `400` |
| 10 | Total spans ≤ 2048 | `413` |
| 11 | Every span passes §1.3 validation | `400` + `BadRequest` |
| 12 | No spans → nothing written | `200` |
| 13 | One transaction: trace/span/usage upserts, then COMMIT | `200` after commit. DB unavailable or serialization failure: rollback, `503` + `Retry-After`. Other error: rollback, `500` |

Steps 1–6 read headers only, so no unauthenticated body is buffered.

### 1.2 Response encoding (spec: "MUST use the same Content-Type in the response as it received in the request")

| Request Content-Type | Response Content-Type | Body |
|---|---|---|
| `application/x-protobuf` | `application/x-protobuf` | Binary `ExportTraceServiceResponse` (200) or binary `google.rpc.Status` (4xx/5xx) |
| `application/json` (always `415`) | `application/json` | `google.rpc.Status` in OTLP JSON encoding: `{"message": "..."}`, with `code` omitted |
| Missing or any other type (always `405`/`415`) | `application/x-protobuf` | Binary `google.rpc.Status` |

**Documented limitation:** for a media type that is neither OTLP encoding (e.g. `text/plain`), the spec rules "same Content-Type" and "body MUST be a Protobuf-encoded Status" can't both be met. We keep the Status body in the default binary encoding.

Other rules for every error body:
- `Status.code` is omitted (the spec allows this).
- `Status.message` is a short developer-facing English text. It **never** contains key or header values, attribute values, span names or IDs.
- The response body is ≤ 4 MiB in practice (spec response limit), which a cap of 20 violations guarantees.

### 1.3 Bad data (`400`) and `BadRequest` details

The spec says `400` `Status.details` "SHOULD contain a BadRequest". We always attach one `google.rpc.BadRequest`, packed as `Any` with type URL `type.googleapis.com/google.rpc.BadRequest`.

- `field`: a proto path only, e.g. `resource_spans[0].scope_spans[1].spans[3].trace_id`, or `body` for decode/gzip errors.
- `description`: the generic rule text, e.g. `must be 16 bytes and not all zero`. **Never the offending value.**
- `reason`: an UPPER_SNAKE code from this list: `INVALID_PROTOBUF`, `INVALID_GZIP`, `INVALID_TRACE_ID`, `INVALID_SPAN_ID`, `INVALID_PARENT_SPAN_ID`, `INVALID_TIMESTAMP`, `END_BEFORE_START`, `TIMESTAMP_OUT_OF_RANGE`, `INVALID_SPAN_KIND`, `INVALID_STATUS_CODE`.
- At most 20 violations are returned. `Status.message` gives the total count.

**Validation rules:**
- `trace_id` is exactly 16 bytes and not all-zero. `span_id` is 8 bytes and not all-zero. `parent_span_id` is empty, or 8 bytes, non-zero and ≠ `span_id`.
- IDs are stored as lowercase hex (32 / 16 chars).
- `start_time_unix_nano` > 0 and `end_time_unix_nano` ≥ start. Both must be < 2^63, because they are stored as `BIGINT`.
- `kind` and `status.code` must be known enum values.
- A missing parent is **not** an error: the span is stored as an orphan (T27).

### 1.4 Idempotency (spans)

OTLP itself has no dedup; the spec says re-sending "may result in duplicate data on the server side". We add it:
- **Spans:** `UNIQUE(project_id, trace_id, span_id)` with `ON CONFLICT DO NOTHING`. The first write wins, and changed content on re-export is ignored.
- **Traces:** recomputed from all stored spans of that trace inside the same transaction (a derived row).
- **Usage:** see §4.

## 2. Resource / scope / span traversal

The request is walked as:
`ExportTraceServiceRequest.resource_spans[]` → `resource.attributes` + `scope_spans[]` → `scope{name,version}` + `spans[]`

- Multiple `resource_spans` and `scope_spans` are supported (T03-S1).
- `service.name` comes from the resource; if absent it is `unknown_service`.
- Span `events` and `links` are **not stored**. An `exception` event contributes only `exception.type`, which is mapped to `error_type` if `error.type` is absent. `exception.message` and `exception.stacktrace` are dropped.
- The client's own `Span.dropped_attributes_count` is not stored. Ours is `server_dropped_attributes_count`.

## 3. Attribute allowlist (`llminsight.attrmap.v1`)

Anything not listed below is dropped before persistence and counted in `spans.server_dropped_attributes_count`. String values are truncated to 256 chars and arrays to 16 items.

**Resource:** `service.name`, `service.version`, `service.namespace`, `deployment.environment.name`, `telemetry.sdk.name`, `telemetry.sdk.language`, `telemetry.sdk.version`, `llminsight.sdk.version`.

**Span — GenAI (Development semconv):**

| Attribute | Type | Stored as | Note |
|---|---|---|---|
| `gen_ai.operation.name` | string | JSONB; inference detection (ADR-13) | Inference ops: `chat`, `text_completion`, `generate_content`, `embeddings` |
| `gen_ai.provider.name` | string | `usage_records.provider` | Required by semconv. Alias `gen_ai.system` (ADR-14) |
| `gen_ai.request.model` / `gen_ai.response.model` | string | `requested_model` / `response_model` | Both kept, never merged |
| `gen_ai.response.id` | string | `provider_response_id`; canonical key input | |
| `gen_ai.response.finish_reasons` | string[] | JSONB | |
| `gen_ai.request.max_tokens`, `.temperature`, `.top_p`, `.seed`, `.stream` | num/bool | JSONB | |
| `gen_ai.output.type` | string | JSONB | e.g. `json` |
| `gen_ai.prompt.name`, `gen_ai.prompt.version` | string | JSONB; `traces.prompt_version` | |
| `gen_ai.usage.input_tokens` | int | `input_tokens` | Semconv: SHOULD include **all** input, cached included. Alias `gen_ai.usage.prompt_tokens` |
| `gen_ai.usage.output_tokens` | int | `output_tokens` | Includes reasoning. Alias `gen_ai.usage.completion_tokens` |
| `gen_ai.usage.cache_read.input_tokens` | int | `cached_input_tokens` | Subset of input; never added again |
| `gen_ai.usage.cache_write.input_tokens` | int | `cache_write_input_tokens` | Subset of input |
| `gen_ai.usage.reasoning.output_tokens` | int | `reasoning_output_tokens` | Subset of output; never added again |
| `error.type` | string | `spans.error_type` | Stable |
| `server.address`, `server.port` | string/int | JSONB | Stable |
| `openai.request.service_tier`, `openai.response.service_tier`, `openai.response.system_fingerprint`, `openai.api.type` | string | JSONB | OpenAI conventions |

**Alias precedence (ADR-14):** when both the standard name and its alias are present on one span, the **standard value wins**. The alias is dropped and counted.

**Span — HTTP (root):** `http.request.method`, `http.route` (template only), `http.response.status_code`, `url.scheme`.

**Span — LLM Insight:**
- `llminsight.call_id`: UUID string, one logical call.
- `llminsight.attempt`: int ≥ 1, one physical attempt.
- `llminsight.provenance`: `real` | `synthetic`.
- `llminsight.usage_source`: `provider_reported` | `estimated` | `unknown`.
- `llminsight.validation.rule`: string.
- `llminsight.validation.passed`: bool.

**Always denied** (never stored, even under a misconfigured allowlist; ADR-19):
- Content: `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions`, `gen_ai.tool.definitions`, `gen_ai.prompt.variable`, `gen_ai.conversation.id`.
- URLs and headers: `url.full`, `url.query`, `http.request.header.*`, `http.response.header.*`.
- Identity: `client.address`, `user.*`, `enduser.*`.
- Exceptions: `exception.message`, `exception.stacktrace`.
- Any key containing `authorization`, `api_key`, `apikey`, `password`, `secret` or `token`, except the explicit `gen_ai.usage.*_tokens` keys above.

## 4. Usage ledger: call identity and deduplication (ADR-21)

A `usage_records` row is created **only** for inference spans (ADR-13): one row per **physical attempt**. Parent and root spans never produce usage or cost (T20, T21, T23).

**Project-scoped unique call identity:** `(project_id, canonical_call_key)`. A second guard allows at most one usage row per span: `UNIQUE(project_id, trace_id, span_id)`.

`canonical_call_key` precedence (master plan §E order), stored with `call_key_source`:

| # | Key | `call_key_source` | Available when | Limitation |
|---|---|---|---|---|
| 1 | `resp:{provider}:{gen_ai.response.id}` | `provider_response_id` | The provider returned a response | Assumes response IDs are unique per provider. Absent on failed attempts |
| 2 | `sdk:{llminsight.call_id}:{llminsight.attempt}` | `sdk_attempt` | The span was produced by the LLM Insight SDK | Client-asserted. Relies on the SDK generating a fresh UUIDv4 per logical call and incrementing `attempt` |
| 3 | `span:{trace_id}:{span_id}` | `span_fallback` | Always | Weakest. It cannot dedupe the same physical call reported by two instrumentations |

**Retry versus re-export:**

| Situation | What arrives | Result |
|---|---|---|
| **Re-export** (the exporter or Collector resends the same span) | Same `trace_id` + `span_id`, same attributes | Span: `DO NOTHING`. Usage: same key, so `DO NOTHING`. Counts and cost unchanged (T04, T23) |
| **Retry attempt** (a new physical API call after 429/timeout) | New span (new `span_id`), same `call_id`, `attempt` + 1, new response ID if it succeeded | A **new** usage row: each attempt is its own physical call and may be billable. M02 counts attempts and M03 counts logical calls (T14) |
| **Double instrumentation** (two libraries report the same call) | Two spans with the same `gen_ai.response.id` | The second usage insert conflicts on the key. The first row is kept and the server counter `usage_key_collisions` is incremented. No error (T23-S1) |
| Double instrumentation of a **failed** attempt | No response ID, so keys differ (`sdk_` versus `span_`) | **Not deduplicated** (known limitation). Both rows have unknown cost, so the inference count is inflated but known cost isn't |

**Token rules:**
- Provider-reported values are preferred (`usage_source=provider_reported`).
- A missing `input_tokens` / `output_tokens` is stored as `NULL` with `usage_source=unknown`, never `0`. `estimated` is reserved; the MVP has no tokenizer.
- Subset attributes (`cache_read`, `cache_write`, `reasoning`) are emitted by semconv only "when available and non-zero". An absent subset is stored as `NULL` (not reported) and treated as 0 **only inside the cost formula**. The LLM Insight SDK always emits provider-reported subsets explicitly, including 0.
- Consistency check: if `cached_input_tokens + cache_write_input_tokens > input_tokens` or `reasoning_output_tokens > output_tokens`, the tokens are stored as received, with `cost_status=unknown` and `cost_unknown_reason=usage_inconsistent`.

**Cost** (Decimal, USD; full formula in `METRICS_CATALOG.md` M10):
- The price is the `model_prices` row whose `model` exactly equals `response_model` (falling back to `requested_model`) and whose `effective_from` ≤ the span start. There is no fuzzy or prefix model matching.
- If a needed price or token count is unknown, then `cost_usd=NULL`, `cost_status=unknown`, and `cost_unknown_reason` is `price_missing`, `usage_missing` or `usage_inconsistent`. **Unknown cost is NULL, never 0.** Cached tokens are not silently priced at the full input rate.

## 5. Provenance (ADR-21; aggregation rules in `METRICS_CATALOG.md`)

- **Span level:** only inference spans carry meaningful provenance, from `llminsight.provenance` (`real` | `synthetic`). Missing gives `unknown`; non-inference spans are `unknown`.
- **Server consistency downgrade:** an inference span that claims `real` with `status≠ERROR` but has no `gen_ai.response.id` is stored as `unknown`. A real successful provider call always has a response ID.
- **Trace level** (derived over the trace's inference spans):

| Inference-span provenances | Trace provenance |
|---|---|
| none (no inference spans) | `unknown` |
| all `real` | `real` |
| all `synthetic` | `synthetic` |
| any other combination (e.g. real + synthetic, real + unknown) | `mixed` |

- **Trust caveat:** `real` is **asserted by the instrumentation**, not cryptographically verified. The UI wording is "reported real", and `unknown` / `mixed` are never counted as real.

## 6. Representative fixture

`docs/fixtures/otlp_trace_3span.json` is a **synthetic**, illustrative OTLP/JSON rendering of one `ExportTraceServiceRequest`. It has three spans in one trace: a `SERVER` root, a `CLIENT` inference span and an `INTERNAL` validation span, across two scopes. The expected ingest result is in `docs/fixtures/README.md`.

Phase 1 tooling must hex-decode the IDs before building the binary request. Protobuf's generic JSON parser reads `bytes` as base64, whereas OTLP/JSON uses hex. The fixture hasn't been parsed by `opentelemetry-proto` yet (Phase 1, T01).
