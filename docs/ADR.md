# LLM Insight — Architecture Decision Record

The decision source is `docs/MVP_MASTER_PLAN.md` (ADR-MVP-1.0, 2026-10-24). External specs were checked on 2026-10-24; see `docs/SOURCES_AND_RISKS.md`.

**Status legend**
- **Accepted (locked):** comes from the master plan. Changing it needs new evidence and a superseding ADR.
- **Accepted (user, 2026-10-24):** approved by the user during the Phase 0 gates.

There are no open proposals. ADR-13…21 were proposed as PADR-01…09 and accepted by the user on 2026-10-24 (final ADR approval).

## 1. Locked decisions (confirmed, unchanged)

| ID | Decision | Consequence for implementation |
|---|---|---|
| ADR-01 | Modular-monolith FastAPI backend + independent Python SDK package + Next.js dashboard | One backend process. `sdk/` is installable on its own and never imports from `backend/`. |
| ADR-02 | Single PostgreSQL (normalized tables + whitelisted JSONB). No ClickHouse | Scale review later. Migrations go through Alembic only. |
| ADR-03 | Direct OTLP/HTTP **binary protobuf** ingest at `POST /v1/traces`; Collector-compatible but not required | Use `opentelemetry-proto` types and respond with a protobuf `ExportTraceServiceResponse`. |
| ADR-04 | Traces signal only; no `/v1/metrics`, `/v1/logs` | Latency and token metrics are computed from spans and the usage ledger. |
| ADR-05 | One working provider adapter (OpenAI); provider-agnostic data model | `provider` and `model` are data, not schema. Gemini and others come later. |
| ADR-06 | Sampling off (`always_on`); spans and usage ledger in the same DB, idempotent | Not billing-grade; no persistent queue. |
| ADR-07 | Metadata-only by default; synthetic content only by explicit opt-in | The concrete rule is ADR-19. |
| ADR-08 | Single-project local/admin demo with ingestion API key; no public multi-tenant SaaS | Local bind only; see `SECURITY.md`. |
| ADR-09 | Async bounded buffer: `BatchSpanProcessor` + short export timeout; export failure never stops the app | The SDK fails open; verified by T13. |
| ADR-10 | Quality = deterministic / golden-set checks + human approval; no LLM-as-judge | A failure cause is a hypothesis, never an asserted fact. |
| ADR-11 | Versioned price matrix, `Decimal` cost, explicit `unknown` cost state | Unknown cost is `NULL` plus a status, never `0`. |
| ADR-12 | End-to-end lineage `trace_id → review → test case → experiment result` | Reference columns carry the lineage (`ARCHITECTURE.md` §4). |

No source conflict required changing ADR-01…12.

## 2. Contract resolutions (Accepted by user, 2026-10-24)

| ID | Ref | Resolution |
|---|---|---|
| CR-01 | (a)/C1 | Independent Git repository rooted at the project folder. Never touch the enclosing home-directory repo. |
| CR-02 | C2 | The existing folder is the repository root; no rename. |
| CR-03 | C3 | Failure Review fields are `issue_type`, `evidence`, `hypothesis`, `review_status`, `reviewer_decision` (enums in `ARCHITECTURE.md` §4.9). These replace the `category` / `status` naming in master plan §E. |
| CR-04 | C4 (amended by ADR-20) | "Critical" means the 39 tests starred in the master plan **plus** T15, T16 and T44 (ADR-20): **42 critical tests**. The **10 presentation blockers** are a subset. Phase 5 gate: 0 FAIL on all 42. No acceptance criterion is downgraded. |
| CR-05 | C5 | GenAI field names were verified against the official GenAI semconv (ADR-13…15). Locked ADRs are unchanged. |
| CR-06 | C6 | Dashboard → backend auth uses one server-side admin/read token (`LLMI_ADMIN_TOKEN`), held only in the backend env and the Next.js **server** env. It is never in `NEXT_PUBLIC_*` and never reaches the browser. |
| CR-07 | C7 | gzip request decoding is implemented (a spec MUST), with a decompressed-size cap (ADR-17). |
| CR-08 | C8 | In-process rate limiting (token bucket). It is valid for a **single process** only and is not a distributed or abuse-proof control. |
| CR-09 | C9 | Version pins are documented in `ARCHITECTURE.md` §6 only. No `pyproject.toml` / `package.json` until the phase that implements the component. |

## 3. Accepted ADRs from Phase 0 verification (user, 2026-10-24)

### ADR-13 — GenAI inference span naming and detection (formerly PADR-01)
- **Context:** master plan §D1 says "`gen_ai.client.inference` span". GenAI semconv `client-inference.md` (Development) defines the span name as `{gen_ai.operation.name} {gen_ai.request.model}` and the kind as `CLIENT`.
- **Decision:**
  - Inference spans are named per semconv (e.g. `chat gpt-4o-mini`) with kind `CLIENT`.
  - The backend identifies an inference span by attribute only: `gen_ai.operation.name` ∈ {`chat`, `text_completion`, `generate_content`, `embeddings`}. Span name and kind are never used for detection.
  - The plan's wording is read as the *operation type*.
- **Alternatives rejected:**
  - A literal span name `gen_ai.client.inference`: non-standard, and semconv-aware tools would not recognise it.
  - Detection by span name: brittle.
- **Consequences:** a third-party span without `gen_ai.operation.name` produces no usage row. Such spans are visible in the trace tree but not counted as inference calls. This is documented as a limitation.
- **Affected files:** `TELEMETRY_CONTRACT.md` §3–4, `fixtures/`, `DEMO_SCRIPT.md`; Phase 2 `sdk/`; Phase 1 `services/ingestion`.
- **Tests:** T02, T09, T10, T32.

### ADR-14 — Provider attribute and legacy aliases (formerly PADR-02)
- **Context:** the current semconv uses `gen_ai.provider.name` (Required) and no longer defines `gen_ai.system`. Older instrumentations still emit `gen_ai.system`, `gen_ai.usage.prompt_tokens` and `gen_ai.usage.completion_tokens`.
- **Decision:**
  - The ingest adapter maps these aliases: `gen_ai.system` → `gen_ai.provider.name`, `gen_ai.usage.prompt_tokens` → `gen_ai.usage.input_tokens`, `gen_ai.usage.completion_tokens` → `gen_ai.usage.output_tokens`.
  - **The standard attribute always takes precedence.** If both are present, the standard value is stored and the alias is dropped and counted in `server_dropped_attributes_count`.
  - Only normalized names are stored, together with `attributes_schema_version`.
- **Alternatives rejected:**
  - Rejecting legacy names: loses data from older instrumentations.
  - Storing both raw: makes aggregation ambiguous.
- **Consequences:** the alias table is versioned with the mapping (ADR-15). Adding an alias requires a mapping version bump.
- **Affected files:** `TELEMETRY_CONTRACT.md` §3, `fixtures/README.md`.
- **Tests:** T03 (+T03-S2), T11, T17.

### ADR-15 — Semconv pin and mapping version (formerly PADR-03)
- **Context:** GenAI conventions moved from `open-telemetry/semantic-conventions` (the page says "Moved") to `open-telemetry/semantic-conventions-genai`, which has no tagged releases. Their status is Development.
- **Decision:**
  - The reference is pinned to `semantic-conventions-genai` @ `9ffc7e00c639` (main, 2026-10-21).
  - The mapping version is `llminsight.attrmap.v1`, stored per span in `attributes_schema_version`.
  - The pin is re-checked at the start of Phase 2. A change produces `attrmap.v2` plus a recorded ADR note.
- **Alternatives rejected:**
  - Tracking `main` unpinned: it changes silently.
  - Pinning core semconv v1.44: it no longer contains GenAI.
- **Consequences:** upstream renames are absorbed by alias rows, never by changing stored column meaning (risk R02).
- **Affected files:** `TELEMETRY_CONTRACT.md` header and §3, `SOURCES_AND_RISKS.md`.
- **Tests:** T09, T11.

### ADR-16 — Binary-protobuf-only ingest (formerly PADR-04)
- **Context:** OTLP 1.11.1 says servers **SHOULD** accept both binary-protobuf and JSON payloads. ADR-03 locks binary protobuf.
- **Decision:**
  - `/v1/traces` decodes only `Content-Type: application/x-protobuf`.
  - Other media types get `415`. A request sent as `application/json` gets a **JSON-encoded** `google.rpc.Status` with `Content-Type: application/json`, which honours the spec rule that the response uses the request's Content-Type.
  - Any other or missing type gets a binary-protobuf `Status` (`TELEMETRY_CONTRACT.md` §1.2).
- **Alternatives rejected:** accepting OTLP/JSON. It needs a hex-ID JSON decoder and doubles the parser test surface.
- **Consequences:** **limited interoperability**. JSON-only exporters are rejected (for example, the OTel JS `exporter-trace-otlp-http` sends JSON). The Python `opentelemetry-exporter-otlp-proto-http` exporter and the Collector `otlphttp` exporter in protobuf mode are compatible, but untested until T01-S3. This is a deviation from a SHOULD, not a MUST violation.
- **Affected files:** `TELEMETRY_CONTRACT.md` §1.
- **Tests:** T01, T06-S3/S4.

### ADR-17 — Request limits (formerly PADR-05)
- **Context:** the spec says the server **MUST** limit the request size, including after decompression, and return `413`. It **RECOMMENDS** a 64 MiB default and says limits **SHOULD** be configurable.
- **Decision:**
  - Configurable limits: 4 MiB wire body (`LLMI_OTLP_MAX_BODY_BYTES`), 16 MiB decompressed (`LLMI_OTLP_MAX_DECOMPRESSED_BYTES`, enforced incrementally) and 2048 spans per request (`LLMI_OTLP_MAX_SPANS_PER_REQUEST`).
  - Every exceeded limit returns `413`.
  - **The count-based `413` is our own contract policy.** The spec defines `413` for body size only.
- **Alternatives rejected:**
  - The 64 MiB default: too much memory for a local demo.
  - `400` for span count: chosen against for consistency of "too large" semantics. Both codes are non-retryable.
- **Consequences:** clients **MUST NOT** retry a `413`, so an oversized batch is lost. The Python SDK default batch (512 spans of metadata only) is far below every limit. Collector users must keep batches under the limits.
- **Affected files:** `TELEMETRY_CONTRACT.md` §1, `SECURITY.md` §3, `.env.example`.
- **Tests:** T08 (+S1–S3).

### ADR-18 — Atomic ingestion, no partial success (formerly PADR-06)
- **Context:** the spec uses `partial_success` only when a request is partly accepted, and says it **MUST** be unset on full success. Master plan §F allows partial success only with a real partial mechanism.
- **Decision:**
  - Each request is all-or-nothing in **one DB transaction**. Any invalid span returns `400` with nothing persisted. Any DB failure rolls back and returns `503`. Unexpected errors roll back and return `500`.
  - `partial_success` is never set.
  - **`200` is sent only after the transaction has committed.**
  - An empty request returns `200` (spec SHOULD).
- **Alternatives rejected:** partial acceptance with `rejected_spans`. It would keep the valid spans, but complicates the consistency of derived trace rows and the tests.
- **Consequences:**
  - One malformed span drops the whole batch, because `400` is not retried by clients. This is accepted for the MVP.
  - Retries after a `503` are safe thanks to idempotency (§1.4 of the telemetry contract).
- **Affected files:** `TELEMETRY_CONTRACT.md` §1.
- **Tests:** T01-S1, T04, T06, T30 (+S1).

### ADR-19 — Never persist telemetry content (formerly PADR-07)
- **Context:** ADR-07 is metadata-only by default. Semconv defines Opt-In content attributes that are likely to contain sensitive data.
- **Decision:**
  - The backend **never persists** these attributes in the MVP, even if a client sends them: `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions`, `gen_ai.tool.definitions`, `gen_ai.prompt.variable`.
  - The only "opt-in synthetic content" is human-entered dataset cases (Phase 4).
  - SDK content capture is not implemented.
- **Alternatives rejected:** an opt-in redacted content table. It adds a PII path and a redaction burden.
- **Consequences:** Failure Review cannot show the original prompt. A human types a safe test input instead (`DEMO_SCRIPT.md` step 5).
- **Affected files:** `SECURITY.md` §1, `TELEMETRY_CONTRACT.md` §3, `ARCHITECTURE.md` §4.
- **Tests:** T16, T35.

### ADR-20 — Promote T15, T16, T44 to critical (formerly PADR-08)
- **Decision:**
  - T15 (no secrets in span), T16 (no content by default) and T44 (auth log sanitization) become critical.
  - The critical total is now **42**: the 39 starred in the master plan plus these 3.
  - The 10 presentation blockers are unchanged and remain a subset.
- **Consequences:** the Phase 5 gate is 0 FAIL on 42 tests. T44 becomes a critical test inside the Phase 1 gate. CR-04 is amended.
- **Affected files:** `TEST_MATRIX.md`, `PRD.md`, `PHASE_STATUS.md`, `CLAUDE.md`, `scripts/verify_phase0.py`.
- **Tests:** T15, T16, T44.

### ADR-21 — Complete database contract (formerly PADR-09)
- **Decision:**
  - The full table/column contract in `ARCHITECTURE.md` §4 is binding for the Phase 1+ migrations. Every column beyond master plan §E is marked **ADD** there, with its reason.
  - Internal surrogate primary keys are `BIGSERIAL`. OTel `trace_id` / `span_id` are kept as the original external identifiers (lowercase hex) in project-scoped unique keys.
  - The server-side drop counter is named `server_dropped_attributes_count`, to avoid confusion with the OTLP `Span.dropped_attributes_count` field, which counts client-side drops.
  - Provenance aggregation (`METRICS_CATALOG.md` §Provenance) and usage dedup identity (`TELEMETRY_CONTRACT.md` §4) are part of this contract.
  - The usage canonical-key precedence follows **master plan §E**: provider response ID, then SDK `call_id`+attempt, then span. This corrects the Phase 0 draft, which had put the SDK key first.
  - Enums are `TEXT` + `CHECK` (not PostgreSQL `ENUM`), for migration simplicity.
- **Alternatives rejected:**
  - UUID primary keys: no external exposure needs them.
  - Overloading `cost_status` with reasons: that would change the master-plan enum.
- **Consequences:** any column change after Phase 1 needs a migration plus an ADR note.
- **Affected files:** `ARCHITECTURE.md` §4, `TELEMETRY_CONTRACT.md` §4–5, `METRICS_CATALOG.md`, `fixtures/README.md`.
- **Tests:** listed per table in `ARCHITECTURE.md` §4.

**Supersession rule:** changing an accepted ADR needs evidence and a new ADR entry that names the one it supersedes.
