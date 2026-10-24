# Fixtures

All fixtures are **SYNTHETIC**. They don't come from a real provider call and must never be displayed as real data.

## `otlp_trace_3span.json`

This file is an OTLP/JSON rendering of one `ExportTraceServiceRequest`, following `TELEMETRY_CONTRACT.md` §6. It is illustrative and hasn't been parsed by `opentelemetry-proto` yet; that happens in Phase 1, as part of T01.

- **Encoding notes:**
  - IDs are lowercase hex (OTLP/JSON rule).
  - `kind` and `status` use protobuf enum integers: 1 = INTERNAL, 2 = SERVER, 3 = CLIENT. An empty `status` means UNSET.
  - 64-bit integers are JSON strings.
  - To build the binary request body, hex-decode the IDs into bytes. The generic protobuf JSON parser expects base64, so it will not do this for you.
- **Trace:** `0af7651916cd43dd8448eb211c80319c`, starting 2026-10-24T10:00:00Z.

```text
POST /v1/summarize          SERVER   b7ad6b7169203331  0 → 1240 ms   (scope: fastapi instrumentation)
├── chat gpt-4o-mini        CLIENT   00f067aa0ba902b7  15 → 1215 ms  (scope: llm_insight_sdk)
└── validate_output         INTERNAL 53995c3f42cd8ad8  1215 → 1228 ms
```

### Expected ingest result (contract, NOT RUN)

1. `POST /v1/traces` with a valid key returns HTTP `200` and an `ExportTraceServiceResponse` with `partial_success` unset.
2. `traces`: 1 row. `service_name=demo-summarizer`, `status=ok`, `prompt_version=v1`, `provenance=synthetic`, duration 1240 ms.
3. `spans`: 3 rows. Both children have `parent_span_id=b7ad6b7169203331`, and the CLIENT span's duration is 1200 ms.
4. `usage_records`: exactly 1 row, for span `00f067aa0ba902b7`:
   - `canonical_call_key=sdk:5f0c6e1a-3b7d-4c2e-9a41-7d2b8e6f1c03:1`, `call_key_source=sdk_attempt`
   - `provider=openai`, `requested_model=gpt-4o-mini`, `response_model=gpt-4o-mini-2024-07-18`
   - `input_tokens=1250`, of which `cached_input_tokens=1024` (uncached = 226)
   - `output_tokens=180`, `reasoning_output_tokens=NULL` (not reported)
   - `usage_source=provider_reported`
   - No verified price is seeded in Phase 0, so `cost_usd=NULL`, `cost_status=unknown` and `cost_unknown_reason=price_missing`.
5. Sending the identical request again returns `200`, and the row counts stay 1 / 3 / 1 (T04, T23).

### Planned variants (Phase 1–2, created with the tests)

| Variant | Change | Expected result | Test |
|---|---|---|---|
| Orphan | Parent ID not present | Span stored and shown as orphan | T27 |
| Corrupt | Truncated protobuf body | `400`, nothing persisted | T06 |
| Forbidden content | Adds `gen_ai.input.messages` | Attribute dropped and counted in `dropped_attributes_count` | T16 |
| Legacy aliases | Uses `gen_ai.system` / `prompt_tokens` | Values mapped to normalized fields | T03/T17 |
| Missing usage | No `gen_ai.usage.*` | `NULL` tokens, `usage_source=unknown`, cost unknown | T18 |
