# LLM Insight — Architecture Decision Record

Decision source: `docs/MVP_MASTER_PLAN.md` (ADR-MVP-1.0, 2026-10-24). External specs checked 2026-10-24 — see `docs/SOURCES_AND_RISKS.md`.

Status legend: **Accepted (locked)** = from master plan, changes need new evidence + a superseding ADR · **Accepted (user, 2026-10-24)** = contract resolution approved during Phase 0 discovery · **Proposed** = awaiting user approval; not yet binding, implementation must not assume it until approved.

## 1. Locked decisions (confirmed, unchanged)

| ID | Decision | Consequence for implementation |
|---|---|---|
| ADR-01 | Modular-monolith FastAPI backend + independent Python SDK package + Next.js dashboard | One backend process; `sdk/` installable on its own, no import from `backend/`. |
| ADR-02 | Single PostgreSQL (normalized tables + whitelisted JSONB). No ClickHouse | Scale review later; migrations via Alembic only. |
| ADR-03 | Direct OTLP/HTTP **binary protobuf** ingest at `POST /v1/traces`; Collector-compatible but not required | Use `opentelemetry-proto` types; respond with protobuf `ExportTraceServiceResponse`. |
| ADR-04 | Traces signal only; no `/v1/metrics`, `/v1/logs` | Latency/token metrics are computed from spans + usage ledger. |
| ADR-05 | One working provider adapter: OpenAI; provider-agnostic data model | `provider` / `model` are data, not schema. Gemini etc. later. |
| ADR-06 | Sampling off (`always_on`); spans + usage ledger in the same DB, idempotent | Not billing-grade; no persistent queue. |
| ADR-07 | Metadata-only by default; synthetic content only by explicit opt-in | See CR-08 / PADR-07 for the concrete rule. |
| ADR-08 | Single-project local/admin demo with ingestion API key; no public multi-tenant SaaS | Local bind only; see `SECURITY.md`. |
| ADR-09 | Async bounded buffer: `BatchSpanProcessor` + short export timeout; export failure never stops the app | Fail-open SDK; verified by T13. |
| ADR-10 | Quality = deterministic / golden-set checks + human approval; no LLM-as-judge | Failure cause = hypothesis, never asserted fact. |
| ADR-11 | Versioned price matrix, `Decimal` cost, explicit `unknown` cost state | Unknown cost is `NULL` + status, never `0`. |
| ADR-12 | End-to-end lineage `trace_id → review → test case → experiment result` | FK / reference columns carry the lineage. |

No source conflict was found that requires changing ADR-01…12.

## 2. Contract resolutions (Accepted by user, 2026-10-24)

| ID | Discovery ref | Resolution |
|---|---|---|
| CR-01 | (a)/(C1) | Independent Git repository rooted at the project folder; never touch the enclosing home-directory repo. |
| CR-02 | (C2) | Existing folder is the repository root; no rename. |
| CR-03 | C3 | Failure Review fields standardized: `issue_type`, `evidence`, `hypothesis`, `review_status`, `reviewer_decision` (enums in `ARCHITECTURE.md` §4.8). Replaces `category` / `status` naming in master plan §E. |
| CR-04 | C4 | "Critical" = the **39 starred** tests; the **10 presentation blockers** are a subset of them. Phase 5 gate: 0 FAIL on all 39. No acceptance criterion is downgraded. |
| CR-05 | C5 | GenAI field names verified against the official GenAI semconv repo (see PADR-01…03). Locked ADRs unchanged; differences handled by a versioned mapping adapter. |
| CR-06 | C6 | Dashboard → backend auth: one server-side admin/read token (`LLMI_ADMIN_TOKEN`), held only in backend and Next.js **server** env. Never `NEXT_PUBLIC_*`, never in the browser. |
| CR-07 | C7 | gzip request decoding is implemented (spec MUST) with a decompressed-size cap. |
| CR-08 | C8 | Rate limiting is in-process (token bucket). Valid for a **single process**; not a distributed or abuse-proof control. |
| CR-09 | C9 | Version pins documented in `ARCHITECTURE.md` §6 only; no `pyproject.toml` / `package.json` until the phase that implements the component. |

## 3. Proposed ADRs (from official-spec verification) — require approval

| ID | Proposal | Reason / evidence | Locked ADR affected? |
|---|---|---|---|
| PADR-01 | Inference span **name** follows semconv `{gen_ai.operation.name} {gen_ai.request.model}` (e.g. `chat gpt-4o-mini`), kind `CLIENT`. Inference spans are identified by attributes (`gen_ai.operation.name` ∈ inference ops), never by name. The plan's "`gen_ai.client.inference` span" is read as the *operation type*. | GenAI `client-inference.md` (Development). | No — clarifies master plan §D1 wording. |
| PADR-02 | Provider attribute is `gen_ai.provider.name` (`openai`). Ingest adapter also maps legacy aliases: `gen_ai.system` → `gen_ai.provider.name`, `gen_ai.usage.prompt_tokens` → `input_tokens`, `gen_ai.usage.completion_tokens` → `output_tokens`. Store normalized name + `attributes_schema_version`. | `gen_ai.system` absent from current semconv; older instrumentations still emit it. | No |
| PADR-03 | Pin GenAI semconv reference to `open-telemetry/semantic-conventions-genai` @ `9ffc7e00c639` (main, 2026-10-21; repo has no releases). Mapping version `llminsight.attrmap.v1`. GenAI conventions moved out of `open-telemetry/semantic-conventions`. | Old location now says "Moved"; new repo has no tagged release. | No |
| PADR-04 | `/v1/traces` accepts **only** `Content-Type: application/x-protobuf`; OTLP/JSON → `415`. | OTLP spec: servers SHOULD accept both binary and JSON. Deviation from a SHOULD, consistent with ADR-03. | No (documents a deviation) |
| PADR-05 | Default limits: 4 MiB wire body, 16 MiB decompressed, 2048 spans/request — configurable; exceed → `413`. | Spec MUST-limit incl. after decompression; RECOMMENDED default 64 MiB. Lower for local, metadata-only demo. | No (documents a deviation from RECOMMENDED) |
| PADR-06 | No `partial_success` in MVP: batch is atomic. Any invalid span → `400` with protobuf `google.rpc.Status`, nothing persisted. Empty request → `200`. | Master plan §F: use partial success only with a real partial mechanism. | No |
| PADR-07 | Concrete ADR-07 rule: the backend **never persists** telemetry message content (`gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions`, `gen_ai.tool.definitions`, `gen_ai.prompt.variable`) in MVP, even if a client sends it. Opt-in synthetic content exists only as human-entered dataset cases (Phase 4). SDK content capture is not implemented in MVP. | Strictest reading of metadata-only; removes a PII path. | Clarifies ADR-07 |
| PADR-08 | Promote T15 (no secrets in span), T16 (no content by default) and T44 (auth log sanitization) to critical. | They protect ADR-07/08 guarantees. **Upgrade only**; current matrix stays as-is until approved. | No |
| PADR-09 | Additive data-model columns beyond master plan §E: `usage_records.call_key_source`, `cache_write_input_tokens`, `cost_unknown_reason`, `provenance`; `spans.provenance`, `dropped_attributes_count`; `model_prices.cache_write_input_per_million`. | Needed to express semconv cache-write, key provenance and the unknown-cost reason without overloading `cost_status`. | No (additive) |

Supersession rule: a Proposed ADR becomes binding only when the user approves it; record the date here. A rejected PADR reverts to the master-plan wording.
