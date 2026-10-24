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
| Body-read deadline (10 s by default) | A body not fully received in time gets `408` (not retried) | Phase 1C, R22 |
| `X-Project-Key` header is required (not part of OTLP) | Configure it with `OTEL_EXPORTER_OTLP_TRACES_HEADERS` | ADR-08 |
| Port 8000, not the OTLP default 4318 | Set `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` explicitly | Deployment choice |

Tested (T01-S3, Phase 1B, `reports/phase-1b.md`): the Python `opentelemetry-exporter-otlp-proto-http` 1.45.1, gzip and uncompressed, configured by constructor arguments or by the `OTEL_EXPORTER_OTLP_TRACES_*` variables. Since Phase 2A (`reports/phase-2a.md`), the LLM Insight SDK (`sdk/`) uses the same exporter through a BatchSpanProcessor against the live `python -m app` process, and SDK-produced spans are stored with `server_dropped_attributes_count = 0`. Exporter 1.45.1 reads `OTEL_EXPORTER_OTLP_TRACES_TIMEOUT` as **seconds** (the spec says milliseconds), so the SDK always passes its timeout explicitly. Expected compatible but still **untested**: the OTel Collector `otlphttp` exporter with protobuf encoding.

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
| 7 | `Content-Length` and the streamed wire bytes are ≤ 4 MiB, and the whole body arrives within `LLMI_OTLP_BODY_READ_TIMEOUT_S` (default 10 s, one overall deadline from the start of the body read). A client disconnect before the end of the body writes nothing | `413` / `408`, both with `Connection: close` (Phase 1C, R22) |
| 8 | gzip inflates incrementally to ≤ 16 MiB. Corrupt gzip | `413` / `400` |
| 9 | Protobuf decodes | `400` |
| 10 | Total spans ≤ 2048 | `413` |
| 11 | Every span passes §1.3 validation | `400` + `BadRequest` |
| 12 | No spans → nothing written | `200` |
| 13 | One transaction: trace/span/usage upserts, then COMMIT | `200` after commit. DB unavailable or serialization failure: rollback, `503` + `Retry-After`. Other error: rollback, `500` |

Steps 1–6 read headers only, so no unauthenticated body is buffered.

**Final protocol decisions (accepted by the user at the Phase 1 closure):**
- `408` for a request body that is not received completely within the deadline, with no partial DB write;
- `413` for the size and span-count limits only;
- `503` + `Retry-After` for temporary storage unavailability.

**Body-read deadline (step 7, Phase 1C):** `408 Request Timeout` is not retryable for OTLP clients (only `429`, `502`, `503`, `504` are), so exporters may not retry it and, like `413`, the batch is lost by the client. This is deliberate: an authenticated client that cannot send 4 MiB in 10 s over loopback is misbehaving. The deadline is enforced by the application on the ASGI body stream. It does **not** cover the request-header phase or idle connections, which uvicorn handles (`timeout_keep_alive`, default 5 s) and does not bound for slow headers. It is not a defence against connection-exhaustion attacks (`reports/phase-1c.md` §5).

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
- `reason`: an UPPER_SNAKE code from this list: `INVALID_PROTOBUF`, `INVALID_GZIP`, `INCOMPLETE_BODY` (client disconnected mid-body; the response is not delivered), `INVALID_TRACE_ID`, `INVALID_SPAN_ID`, `INVALID_PARENT_SPAN_ID`, `INVALID_TIMESTAMP`, `END_BEFORE_START`, `TIMESTAMP_OUT_OF_RANGE`, `INVALID_SPAN_KIND`, `INVALID_STATUS_CODE`.
- At most 20 violations are returned. `Status.message` gives the total count.

**Validation rules:**
- `trace_id` is exactly 16 bytes and not all-zero. `span_id` is 8 bytes and not all-zero. `parent_span_id` is empty, or 8 bytes, non-zero and ≠ `span_id`.
- IDs are stored as lowercase hex (32 / 16 chars). A wrong-length ID is rejected, never truncated or padded into a valid-looking one.
- `start_time_unix_nano` > 0 and `end_time_unix_nano` ≥ start. Both must be < 2^63, because they are stored as `BIGINT`.
- `kind` and `status.code` must be known enum values.
- A missing parent is **not** an error: the span is stored as an orphan (T27).

### 1.4 Idempotency (spans)

OTLP itself has no dedup; the spec says re-sending "may result in duplicate data on the server side". We add it:
- **Spans:** `UNIQUE(project_id, trace_id, span_id)` with `ON CONFLICT DO NOTHING`. The first write wins, and changed content on re-export is ignored. Since Phase 1C a re-export whose content differs from the stored span (or a differing duplicate inside one batch) is counted in the process-local `span_content_conflicts` counter and logged as a count; the stored row is never changed or merged.
- **Traces:** recomputed from all stored spans of that trace inside the same transaction (a derived row).
- **Usage:** see §4.

## 2. Resource / scope / span traversal

The request is walked as:
`ExportTraceServiceRequest.resource_spans[]` → `resource.attributes` + `scope_spans[]` → `scope{name,version}` + `spans[]`

- Multiple `resource_spans` and `scope_spans` are supported (T03-S1).
- `service.name` comes from the resource; if absent it is `unknown_service`.
- Span `events` and `links` are **not stored**. An `exception` event contributes only `exception.type`, which is mapped to `error_type` if `error.type` is absent and if it is an identifier under the `error.type` rule (§3). `exception.message` and `exception.stacktrace` are dropped.
- **`Status.message` is never stored** (Phase 1C, R21). It is free text that can quote credentials, prompts or personal data, and best-effort scrubbing cannot make it safe. `spans.status_message` stays in the schema and is always `NULL` from ingest. An error is described only by the span status (`UNSET`/`OK`/`ERROR`) and the validated `error_type` identifier.
- A span with an empty name is stored as `unknown_span`, a display fallback only (`spans.name` is `LEN(256)`). A span name that fails the string-safety rules (§3.1) is stored as `redacted_span` and counted. A scope name or version that fails them is stored as `NULL` and counted on every span of that scope.
- The client's own `Span.dropped_attributes_count` is not stored. Ours is `server_dropped_attributes_count`.

## 3. Attribute allowlist (`llminsight.attrmap.v1`)

Anything not listed below is dropped before persistence and counted in `spans.server_dropped_attributes_count`. That column is an `INT` that saturates at 2^31−1. Resource-attribute and scope-field drops count on every span of that resource or scope, and a withheld span name counts as one. Every string value must also pass the string-safety rules of §3.1. Free-text **labels** are truncated to 256 chars after those checks. **Identifiers** are dropped (and counted) rather than truncated, because a shortened identifier is a different identifier. Arrays keep at most 16 items.

**v1 finalization (Phase 1C, completed by the Phase 1 final hardening):** the max-tokens, prompt-version, error-type, credential-classification and string-safety rules were settled before the `phase-1` tag, while every stored row was disposable test data (the dev database held 0 rows). They are part of `llminsight.attrmap.v1`; any change after the `phase-1` tag needs `attrmap.v2` (ADR-15). Audit against the pinned semconv (`9ffc7e00c639`, `client-inference.md`, re-read 2026-10-24): the five usage counters below and `gen_ai.request.max_tokens` match the pinned names exactly. The pin also defines modality breakdowns (`gen_ai.usage.{text,audio,image}.input_tokens` / `.output_tokens` / `.cache_read.input_tokens`); v1 does not allowlist them, so they are dropped and counted. Adding them is a Phase 2 / ADR-15 decision.

**Resource:** `service.name`, `service.version`, `service.namespace`, `deployment.environment.name`, `telemetry.sdk.name`, `telemetry.sdk.language`, `telemetry.sdk.version`, `llminsight.sdk.version`.

**Span — GenAI (Development semconv):**

| Attribute | Type | Stored as | Note |
|---|---|---|---|
| `gen_ai.operation.name` | string | JSONB; inference detection (ADR-13) | Inference ops: `chat`, `text_completion`, `generate_content`, `embeddings` |
| `gen_ai.provider.name` | string | `usage_records.provider` | Required by semconv. Alias `gen_ai.system` (ADR-14) |
| `gen_ai.request.model` / `gen_ai.response.model` | string | `requested_model` / `response_model` | Both kept, never merged |
| `gen_ai.response.id` | string | `provider_response_id`; canonical key input | |
| `gen_ai.response.finish_reasons` | string[] | JSONB | |
| `gen_ai.request.max_tokens` | int ≥ 0 | JSONB | A count, not a credential: kept by the credential classifier below (Phase 1C decision on `reports/phase-1b.md` §7 item 1) |
| `gen_ai.request.temperature`, `.top_p`, `.seed`, `.stream` | num/bool | JSONB | |
| `gen_ai.output.type` | string | JSONB | e.g. `json` |
| `gen_ai.prompt.name` | string | JSONB | |
| `gen_ai.prompt.version` | string, ≤ 64, `[A-Za-z0-9][A-Za-z0-9._:+/-]*` | JSONB; `traces.prompt_version` | A bounded label, not free text. Anything else (spaces, line breaks, `@`, non-ASCII, > 64) is dropped and counted, never truncated. ≤ 64 matches the Phase 4 `experiments.prompt_version_*` columns |
| `gen_ai.usage.input_tokens` | int | `input_tokens` | Semconv: SHOULD include **all** input, cached included. Alias `gen_ai.usage.prompt_tokens` |
| `gen_ai.usage.output_tokens` | int | `output_tokens` | Includes reasoning. Alias `gen_ai.usage.completion_tokens` |
| `gen_ai.usage.cache_read.input_tokens` | int | `cached_input_tokens` | Subset of input; never added again |
| `gen_ai.usage.cache_write.input_tokens` | int | `cache_write_input_tokens` | Subset of input |
| `gen_ai.usage.reasoning.output_tokens` | int | `reasoning_output_tokens` | Subset of output; never added again |
| `error.type` | string, ≤ 256, `[A-Za-z0-9_][A-Za-z0-9_.:$/-]*` | `spans.error_type` | Stable. A low-cardinality identifier (`timeout`, `500`, `openai.RateLimitError`, `_OTHER`); a description with spaces or control characters is dropped and counted (R21) |
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
- Credentials, by **explicit classification** (Phase 1C; replaces the 1B substring rule, which also matched the counter `gen_ai.request.max_tokens`). The key is split into lower-case words at `.`, `_`, `-` and camelCase boundaries. It is denied if:
  - it contains a credential stem anywhere: `password`, `passwd`, `secret`, `authorization`, `apikey`/`api_key`, `credential`, `cookie`, `privatekey`/`private_key`; or
  - it has a credential word: `token` (singular, e.g. `auth.token`, `access_token`, `accessToken`), `auth`, `bearer`, `jwt`, `pwd`; or
  - `tokens`, `key` or `keys` follows a credential qualifier (`access`, `refresh`, `id`, `session`, `auth`, `bearer`, `api`, `oauth`, `csrf`, `xsrf`, `secret`, `private`, `signing`, `encryption`, `client`, `master`, `security`).
  - Plural `tokens` after any other word is a count (`input_tokens`, `max_tokens`, `cache_read.input_tokens`) and is not denied. A test proves that no allowlisted key, no alias and no semconv token counter is classified as a credential.

### 3.1 String-value safety (R08, Phase 1 final)

The key rules above decide *which* attributes may be stored. These rules decide whether a permitted string *value* may be stored. Every untrusted string the ingest persists passes them on its complete original value, before any truncation, so a secret cannot survive as a truncated prefix. A rejected value is dropped (attributes, scope fields) or replaced by a documented fallback (span name), and counted. It is never stored, logged, echoed in an error, or returned by the API.

1. **Unsafe characters:** any control, format (bidi override, zero-width, BOM), private-use, surrogate, or line/paragraph separator character (Unicode categories `Cc`, `Cf`, `Co`, `Cs`, `Zl`, `Zp`) rejects the value. That includes U+0000, which was previously replaced.
2. **Obvious credentials** reject the value in every field:
   - `sk-…` keys and LLM Insight `lli_…` keys;
   - AWS `AKIA`/`ASIA` IDs, GitHub `ghp_`/`github_pat_`, Slack `xox?-`, Google `AIza…`;
   - JWTs (`eyJ….…`) and private-key blocks;
   - `Bearer`/`Basic` followed by a credential-like value (12+ chars with a digit);
   - URL user-info (`scheme://user:pass@`);
   - assignments such as `api_key=`, `secret:`, `password=`, `token=`, `authorization:`.
3. **Personal data:**
   - e-mail addresses and runs of 11+ digits (national IDs, phone and card numbers) reject identifiers and labels;
   - labels are also rejected for international phone numbers, digit groups totalling 10+ digits (`0532 111 22 33`, `4111 1111 1111 1111`) and spaced IBANs.

   Opaque generated IDs (`gen_ai.response.id`, `llminsight.call_id`, the system fingerprint) skip this layer only, because random IDs can contain digit runs.
4. **Field format**, a full match with no repair:

| Field(s) | Class | Format | Max | On failure |
|---|---|---|---|---|
| `gen_ai.request.model`, `gen_ai.response.model` | identifier | `[A-Za-z0-9][A-Za-z0-9._:/@+-]*` (snapshots, `ft:` fine-tunes, `models/…`, `org/model`, `model@version`) | 256 | dropped |
| `gen_ai.provider.name` (and alias `gen_ai.system`) | identifier | `[A-Za-z0-9][A-Za-z0-9._-]*` (no `:`) | 64 | dropped → `usage_records.provider = unknown` |
| `gen_ai.operation.name` | identifier | `[a-z][a-z0-9_.]*` | 64 | dropped |
| `gen_ai.output.type`, `openai.*.service_tier`, `openai.api.type`, `http.request.method`, `telemetry.sdk.name`, `telemetry.sdk.language`, each `gen_ai.response.finish_reasons` item | identifier | `[A-Za-z0-9_][A-Za-z0-9_.-]*` | 64 | dropped (one bad array item drops the array) |
| `service.version`, `telemetry.sdk.version`, `llminsight.sdk.version` | identifier | `[A-Za-z0-9][A-Za-z0-9._+-]*` | 128 | dropped |
| `gen_ai.prompt.name`, `llminsight.validation.rule` | identifier | `[A-Za-z0-9][A-Za-z0-9._:+/-]*` | 128 | dropped |
| `gen_ai.prompt.version` | identifier | `[A-Za-z0-9][A-Za-z0-9._:+/-]*` | 64 | dropped → `traces.prompt_version` NULL unless all others agree |
| `error.type`, event `exception.type` | identifier | `[A-Za-z0-9_][A-Za-z0-9_.:$/-]*` | 256 | dropped → `error_type` NULL |
| `server.address` | identifier | `[A-Za-z0-9\[][A-Za-z0-9._:\[\]-]*` (host, IPv4, `[IPv6]`; no `user@`) | 253 | dropped |
| `http.route` | identifier | `/` then printable ASCII without `?` or `#` (a template, never a query) | 256 | dropped |
| `url.scheme` | identifier | `[A-Za-z][A-Za-z0-9+.-]*` | 16 | dropped |
| `gen_ai.response.id` | opaque | `[A-Za-z0-9_][A-Za-z0-9._:/+=-]*` | 256 | dropped → next call-key source |
| `llminsight.call_id` | opaque | UUID (`8-4-4-4-12` hex) | 64 | dropped → next call-key source |
| `openai.response.system_fingerprint` | opaque | `[A-Za-z0-9_][A-Za-z0-9_.-]*` | 128 | dropped |
| `service.name`, `service.namespace`, `deployment.environment.name` | label | free text (spaces and non-ASCII letters allowed) | 256, truncated | dropped (`service.name` → `traces.service_name = unknown_service`) |
| span `name` | label | free text | 256, truncated | `redacted_span` (empty name: `unknown_span`) |
| scope `name` / `version` | label | free text | 256 / 64, truncated | `NULL` |
| `llminsight.provenance`, `llminsight.usage_source` | fixed vocabulary | the listed values only | — | dropped |

Other columns are not free text from the client: IDs are validated hex and enums are mapped from protobuf integers. `canonical_call_key` is built only from values that passed these rules.

**Limit (stated, not hidden):** these are heuristics for *obvious* credential formats and common personal-data shapes. They do not and cannot detect every secret or every piece of personal data. Examples of what passes: an unprefixed random token in a model-name-shaped value, a person's name in a span name, a non-standard key format. Instrumentations must still never put secrets or content into metadata (SECURITY.md §1). Deterministic rules keep re-exports idempotent: the same input always yields the same stored row.

## 4. Usage ledger: call identity and deduplication (ADR-21)

A `usage_records` row is created **only** for inference spans (ADR-13): one row per **physical attempt**. Parent and root spans never produce usage or cost (T20, T21, T23).

**Project-scoped unique call identity:** `(project_id, canonical_call_key)`. A second guard allows at most one usage row per span: `UNIQUE(project_id, trace_id, span_id)`.

`canonical_call_key` precedence (master plan §E order), stored with `call_key_source`:

| # | Key | `call_key_source` | Available when | Limitation |
|---|---|---|---|---|
| 1 | `resp:{provider}:{gen_ai.response.id}` | `provider_response_id` | The provider returned a response | Assumes response IDs are unique per provider. Absent on failed attempts. `{provider}` has `%` and `:` percent-encoded (Phase 1C), so a `:` inside a provider name cannot make two different (provider, ID) pairs share a key. A missing provider is the namespace `unknown`: two spans without a provider name and with the same response ID share one key, and the second is a counted collision (documented limitation). Provider names are not case-folded, so `OpenAI` and `openai` are different namespaces (semconv requires `openai`) |
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
- A missing `input_tokens` / `output_tokens` is stored as `NULL` with `usage_source=unknown`, never `0`. The server never estimates (the MVP has no tokenizer). When both counts are present, `usage_source` is the instrumentation's `llminsight.usage_source` claim (`provider_reported` | `estimated` | `unknown`), defaulting to `provider_reported`, which is what the semconv `gen_ai.usage.*` attributes carry.
- Subset attributes (`cache_read`, `cache_write`, `reasoning`) are emitted by semconv only "when available and non-zero". An absent subset is stored as `NULL` (not reported) and treated as 0 **only inside the cost formula**. The LLM Insight SDK always emits provider-reported subsets explicitly, including 0.
- Consistency check: if `cached_input_tokens + cache_write_input_tokens > input_tokens` or `reasoning_output_tokens > output_tokens`, the tokens are stored as received, with `cost_status=unknown` and `cost_unknown_reason=usage_inconsistent`.

**Cost** (Decimal, USD; full formula in `METRICS_CATALOG.md` M10; implementation, catalogue and rounding in `PRICING.md`):
- The price is the `model_prices` row whose `model` exactly equals `response_model` (falling back to `requested_model`) and whose `effective_from` ≤ the span start. There is no fuzzy or prefix model matching.
- If a needed price or token count is unknown, then `cost_usd=NULL`, `cost_status=unknown`, and `cost_unknown_reason` is `price_missing`, `usage_missing` or `usage_inconsistent`. **Unknown cost is NULL, never 0.** Cached tokens are not silently priced at the full input rate.
- **Phase 2B precisions (no ADR or column change; `PRICING.md` §3–4):**
  - *Fallback* means: `requested_model` is used only when the span has no `response_model`. A response model without its own price row is `price_missing`; it is never priced at the requested alias's rate.
  - Only `usage_source=provider_reported` usage is priced. Estimated or unknown usage gives `cost_unknown_reason=usage_missing` (T17).
  - A stored price is a standard-tier text price. For `gen_ai.provider.name=openai` it applies only when `openai.response.service_tier` is `default` and `server.address` is `api.openai.com`; Flex, Batch, Fast/`priority`, `scale`, an unreported tier and regional endpoints are `price_missing`.
  - The row is priced once, in the ingest transaction that first writes it, and never updated: re-exports and later catalogue versions do not change a stored cost.

## 5. Provenance (ADR-21; aggregation rules in `METRICS_CATALOG.md`)

- **Span level:** only inference spans carry meaningful provenance, from `llminsight.provenance` (`real` | `synthetic`). Missing gives `unknown`; non-inference spans are `unknown`.
- **Server consistency downgrade:** an inference span that claims `real` with `status≠ERROR` but has no `gen_ai.response.id` is stored as `unknown`. A real successful provider call always has a response ID.
- **Trace level** (derived over the trace's inference spans):

| Inference-span provenances | Trace provenance |
|---|---|
| none (no inference spans) | `unknown` |
| all `unknown` (Phase 1C decision on `reports/phase-1b.md` §7 item 2) | `unknown` |
| all `real` | `real` |
| all `synthetic` | `synthetic` |
| any other combination (e.g. real + synthetic, real + unknown, synthetic + unknown) | `mixed` |

- **Trust caveat:** `real` is **asserted by the instrumentation**, not cryptographically verified. The UI wording is "reported real", and `unknown` / `mixed` are never counted as real. The server rule only ever **downgrades** a claim: a response ID never creates or verifies `real` (a span with a response ID and no claim stays `unknown`). The read API returns `provenance_verified: false` next to every provenance value (`ARCHITECTURE.md` §5).

## 6. Representative fixture

`docs/fixtures/otlp_trace_3span.json` is a **synthetic**, illustrative OTLP/JSON rendering of one `ExportTraceServiceRequest`. It has three spans in one trace: a `SERVER` root, a `CLIENT` inference span and an `INTERNAL` validation span, across two scopes. The expected ingest result is in `docs/fixtures/README.md`.

Phase 1 tooling must hex-decode the IDs before building the binary request. Protobuf's generic JSON parser reads `bytes` as base64, whereas OTLP/JSON uses hex. Phase 1B parses the fixture with `opentelemetry-proto` this way (`backend/tests/otlp.py`) and ingests it in T01/T04.
