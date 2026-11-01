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

## 4. Accepted ADRs from the Phase 4B architectural review (recorded in Phase 4C, 2026-10-26)

The architectural review of Phase 4B (decisions D1–D9, `reports/phase-4b.md` §2.4) approved D2, D7 and D9 as delivered, approved D6 for recording as an ADR, revised D1, D3, D4 and D5, and deferred D8. ADR-22…24 record the binding result. Final dispositions: `reports/phase-4c.md` §2.

### ADR-22 — Local dashboard writes through narrow Server Actions (records D6)
- **Status:** Accepted (architectural review, 2026-10-26). **Supersedes** the read-only restriction of `ARCHITECTURE.md` §5.3 and `SECURITY.md` §5 ("no route handlers and no Server Functions"; "methods other than GET/HEAD get `405`") **for the writes listed below only**. CR-06 and ADR-08 are unchanged.
- **Context:** CR-03 and the Trace-to-Testset contract need a human to record reviews, decisions, datasets and approved test cases from the dashboard. The Phase 3 dashboard could not write.
- **Decision:**
  - **Permitted writes (exhaustive):** four Server Actions, `submitReview` → `POST /api/v1/failure-reviews`, `submitDecision` → `PATCH /api/v1/failure-reviews/{id}`, `submitDataset` → `POST /api/v1/datasets`, `submitTestCase` → `POST /api/v1/datasets/{id}/cases`. Each is bound to exactly one backend path and method through an allowlist (`sendJson`). No route handlers, no generic proxy, no other Server Function. A new write needs a new ADR entry.
  - **Admin token:** read only by `server-only` modules on the Next.js server and sent only as a bearer header to a loopback backend; never in a `NEXT_PUBLIC_*` variable, HTML, RSC payload, client bundle, cookie, browser storage, URL or log (CR-06, T42).
  - **Origin and request validation:** `proxy.ts` accepts `POST` only on the three pages that host forms, only with an `Origin` equal to the loopback host, `Sec-Fetch-Site: same-origin` when sent, and a `Next-Action` header; everything else is `403` (or `405`). Next.js also compares `Origin` with `Host` for every Server Action. Action bodies are capped at 128 KB.
  - **CSRF:** there is no cookie or session to ride on; a cross-site HTML form cannot send `Next-Action`, and a cross-site script would need a CORS preflight that is never answered. Forms therefore need JavaScript (R59).
  - **Backend validation and authorization:** the backend re-validates every field (strict schemas, `422` without echo), requires the admin token, scopes everything by the mandatory `project` (one indistinguishable `404`), and enforces the data rules itself (approval, redaction, rule contract, review integrity); the dashboard's own checks are convenience only.
  - **Security boundary:** the dashboard and the backend bind to `127.0.0.1` and refuse non-loopback `Host` headers (`421`). The boundary is the local machine.
  - **No authenticated identity:** there is no user login. `created_by` and `approved_by` are self-declared labels and must never be presented as verified approvers or reviewers (R55). Who changed a decision is not recorded (D8, deferred).
- **Why public deployment remains prohibited:** any process or person able to reach the loopback port can write (R54); there is no authentication, per-user authorization, audit trail, TLS or distributed rate limit. Public or shared deployment requires the review of `SECURITY.md` §7 and a superseding ADR.
- **Alternatives rejected:** route handlers as a backend proxy (a generic write surface); writes from the browser with a token (breaks CR-06); a session cookie (needs real authentication, out of MVP scope).
- **Affected files:** `frontend/lib/actions/`, `frontend/proxy.ts`, `frontend/lib/request-guard.ts`, `frontend/lib/server/backend.ts`, `ARCHITECTURE.md` §5.3/§5.8, `SECURITY.md` §5.
- **Tests:** frontend static and unit tests for the action set, allowlist and guard; live E2E CSRF refusals; T41, T42.

### ADR-23 — Versioned deterministic rule contract `llminsight.rule.v1` with bounded engines (revises D1)
- **Status:** Accepted (architectural review, 2026-10-26). Refines ADR-10; no schema change (`rule` stays JSONB, `ARCHITECTURE.md` §4.10).
- **Decision:**
  - Every stored rule carries `"version": 1` (JSON integer) and exactly the keys of its type: `json_schema {schema}`, `field_match {fields}`, `exact {output_format: text|json}`, `regex {pattern, match: full|search}`. Unknown keys, unknown versions and unversioned (Phase 4B) payloads are refused on write; the engine reports a stored rule it cannot read as `rule_unsupported` (not evaluated) and never reinterprets it. The development database held 0 test cases when this was introduced.
  - **Output representation:** the raw model output text (valid Unicode, at most 32 KiB as UTF-8). Structured rules parse it as strict, exact JSON (non-integers as `Decimal`, no NaN/Infinity, no duplicate keys, bounded number literals, depth ≤ 32, ≤ 10,000 values).
  - **Semantics:** structural equality with key order ignored, array order significant, booleans never numbers, numbers by exact value, strings by code point (no normalization). `regex` matches the raw text with RE2 in the stated `match` mode.
  - **Expected output:** every rule type needs an approved `expected_output`; without one a case is never evaluated (`passed = NULL`, T40). A present expected output must satisfy its own rule (refused on write otherwise).
  - **Outcomes:** `true` (pass), `false` (checked and failed, including malformed output), `NULL` (not evaluable or a limit hit), each with a stable code; `NULL` is never a failure and never enters a pass-rate denominator. `evaluation_detail` never contains output or expected values.
  - **Regex:** `google-re2` 1.1.20251105 only (linear time, no backtracking constructs, no fallback engine), pattern ≤ 1000 chars, compiled program ≤ 1500 instructions, 2 MiB memory, errors never logged. User patterns never reach Python `re`.
  - **JSON Schema:** `jsonschema` 4.26.0, draft 2020-12 only; keyword allowlist at every depth; `pattern`, `patternProperties`, `format`, `content*`, `$id`, anchors and dynamic references refused; `$ref` only `#/$defs/<name>`, acyclic, expansion-bounded; meta-validated without a format checker; an explicit registry that cannot retrieve (no network); a per-evaluation keyword budget.
- **Alternatives rejected:** Python `re` with a timeout (a C-level match cannot be interrupted safely); trusting `jsonschema` defaults (remote retrieval via `urlopen`; `regex` format checks with `re.compile`); a default version for unversioned rules (silent reinterpretation).
- **Affected files:** `backend/app/services/evaluation/`, `backend/app/services/case_rules.py`, `backend/app/core/exact_json.py`, `backend/app/api/json_body.py`, `frontend/lib/review-forms.ts`, `ARCHITECTURE.md` §4.10/§5.7/§5.9, `SECURITY.md` §1.
- **Tests:** T36, T37, T40; unit suites for exact JSON, RE2 safety, schema safety, the engine and static boundaries.

### ADR-24 — Review integrity and one case per review in the database (revises D3, D4, D5)
- **Status:** Accepted (architectural review, 2026-10-26). Migration `0004`; no column added or changed (ADR-21 note).
- **Decision:**
  - **Conversion eligibility (D4):** a test case may cite a review only when its `reviewer_decision` is `hypothesis_supported`, or `hypothesis_rejected` together with an independently supplied `expected_output`; undecided reviews (the default decision, see §4.9) and `inconclusive` reviews are not eligible. Conversion always needs the separate explicit approval (`approved: true`, T34).
  - **Freeze (D3):** once a test case cites a review, its `reviewer_decision` cannot change or be cleared (`409 review_decision_frozen`); the status may still change within the §4.9 invariant. `evidence`, `hypothesis` and every reference column of a review are immutable at all times.
  - **Uniqueness (D5):** partial `UNIQUE(dataset_id, origin_review_id) WHERE origin_review_id IS NOT NULL` on `test_cases`; standalone cases are unaffected. The API keeps its advisory lock and friendly `409 duplicate_test_case`.
  - **Transactional enforcement:** the API checks under row locks (the conversion reads the review `FOR SHARE`, the PATCH `FOR UPDATE`), and two triggers enforce the same rules for every writer (`failure_reviews_integrity`, `test_cases_review_eligibility`, SQLSTATE 23000 with a constraint name). The upgrade refuses, changing nothing, if existing rows violate the new rules.
  - **Identity:** nothing is inferred from self-declared names; no identity system is added. D8 (recording the actor of a decision) is deferred to a later phase.
- **Affected files:** `backend/alembic/versions/0004_phase4c_review_case_integrity.py`, `backend/app/api/{reviews,datasets}.py`, `backend/app/models/{review,dataset}.py`, `frontend/components/reviews/*`, `ARCHITECTURE.md` §4.9/§4.10/§5.7.
- **Tests:** `test_phase4c_integrity.py` (API and raw-SQL concurrency), `test_migrations.py` (0004 lifecycle, triggers, index), T33–T35 regression.

## 5. Phase 4C decision (recorded for the architectural review, 2026-10-26)

### ADR-25 — Deterministic mock A/B experiments over the locked §4.11–4.12 schema
- **Status:** Accepted within the Phase 4C instruction (mock-only, deterministic experiments; no real or paid provider); recorded here for the architectural review. Schema exactly `ARCHITECTURE.md` §4.11–4.12 (migration `0005`), no column added.
- **Decision:**
  - **Execution:** only `mode: "mock"` runs. A scripted mock provider returns SYNTHETIC answers keyed by (prompt version, SHA-256 of the case input); an unknown input gets `mock_no_scripted_output` (never an invented or copied answer). One attempt per (case, version), no retries, so one result row is one physical attempt. `mode: "real"` is validated (budget, `LLMI_ALLOW_REAL_LLM`) and then refused with `409`; no real executor exists in this build.
  - **Identity:** cases in id order, each under A then B: both versions always run on the identical case ids (T38). Variant identity is the `prompt_version` string within the experiment, enforced by a trigger.
  - **Provenance:** every mock result is `synthetic` (T39), enforced by the service and by a trigger.
  - **Atomicity and evidence:** one transaction stores the experiment, all results and `finished_at`; results are append-only and an experiment changes only by setting `finished_at` once (triggers). Model output is never stored.
  - **Cost:** synthetic token counts priced at the stored price of (`openai`, model) in force at `started_at`, with the ledger's unknown-cost rules (never 0). This is a labelled synthetic estimate; nothing is spent and no usage-ledger row is written.
  - **Budget:** optional for mock runs; when given, checked before anything runs (unknown estimate → `budget_unverifiable`, over budget → `budget_exceeded`), plus a run-time guard (`budget_exhausted`).
  - **Report:** built only from stored rows; pass rate = passed / (passed + failed); quality compared only on cases evaluated under both versions, and no better version is named when evidence is incomplete; cost compared only when both versions are fully priced; latency per version only.
- **Alternatives rejected:** writing experiment calls into the usage ledger (would mix synthetic estimates with telemetry); measuring wall-clock latency of the in-process mock (meaningless); naming a winner from per-version rates with different denominators.
- **Affected files:** `backend/app/services/experiments/`, `backend/app/api/experiments.py`, `backend/app/repositories/experiments.py`, `backend/app/models/experiment.py`, `backend/alembic/versions/0005_phase4c_experiments.py`, `ARCHITECTURE.md` §4.11–4.12/§5.9.
- **Tests:** T38, T39 (integration), T36, T37, T40; `test_experiment_api.py`, `test_experiment_runner.py`, `test_experiment_report.py`, migration tests for `0005`.

## 6. Phase 4D decision (recorded for the architectural review, 2026-10-26)

### ADR-26 — A fifth dashboard write: the explicit mock experiment run
- **Status:** Accepted within the Phase 4D instruction (the experiment UI, mock-only); recorded here for the architectural review. **Amends ADR-22** by adding exactly one write to its exhaustive list, as ADR-22 requires ("a new write needs a new ADR entry"). ADR-22's other rules (token, origin, CSRF, backend validation, loopback boundary, no identity) apply unchanged. ADR-25 is unchanged.
- **Context:** Phase 4 closure needs the A/B workflow in the dashboard (`reports/phase-4c.md` §12): a person must be able to start a mock experiment on a golden dataset and read its stored results.
- **Decision:**
  - **The write:** one Server Action, `submitExperiment` (`frontend/lib/actions/experiments.ts`) → `POST /api/v1/experiments`, bound by the `sendJson` allowlist to that path and `POST` only. `proxy.ts` accepts its `POST` on `/experiments/new` only, with the ADR-22 origin checks.
  - **Mock only:** the form parser sets `mode: "mock"` as a constant and never reads a mode from the form; the setup page offers no mode control; the options decoder refuses any response offering another mode. The backend still validates and refuses `mode: "real"` (ADR-25).
  - **Explicit start:** no run on render, page load or navigation; the person chooses a dataset, two prompt versions and a model, ticks a confirmation of a mock-only SYNTHETIC run and presses the start button. The submit is ignored while one is in flight (synchronous guard plus disabled button).
  - **Choices come from the backend:** an additive read-only `GET /api/v1/experiment-options` lists the prompt versions the scripted mock answers, the models with a stored price in force, and the limits. No variant parameter is invented; `model` stays identity and price only.
  - **Bounded execution (backend):** at most 4 concurrent runs per process (`429 experiments_busy`, never queued), a 15 s wall-clock deadline per run (`409 experiment_time_limit`), and an unexpected evaluation error answered with a fixed `500 internal_error` (exception type logged, never its message). Every refusal stores nothing. The dashboard's deadline for this one write is 30 s, longer than the backend's, so a finished run is not reported as "outcome unknown".
  - **Reads:** an additive read-only `GET /api/v1/experiments` (project-scoped listing). Report, list and options are decoded strictly, including the report's own definitions (counts, denominators, cost coverage, verdict only on complete evidence, mock ⇒ synthetic); a mismatch rejects the whole response.
- **Alternatives rejected:** a mode selector with real disabled (a crafted POST could still submit it to the action); free-text prompt versions (every unknown version yields only `mock_no_scripted_output`, which looks like a result); running automatically after creating a dataset (violates the explicit-write rule); a background worker (post-MVP, R60).
- **Affected files:** `frontend/lib/actions/experiments.ts`, `frontend/lib/experiment-form.ts`, `frontend/lib/server/{backend,experiments}.ts`, `frontend/lib/request-guard.ts`, `frontend/lib/api/experiment-{types,schema}.ts`, `frontend/app/experiments/`, `frontend/components/experiments/`, `backend/app/api/experiments.py`, `ARCHITECTURE.md` §5.10, `SECURITY.md` §5–§6.
- **Tests:** `frontend/tests/unit/phase4d.test.tsx`, `frontend/tests/unit/security-static.test.ts` (five writes, three action modules), `frontend/tests/e2e/phase4d.spec.ts` (live), `backend/tests/integration/test_phase4d_api.py`.
- **Note (Phase 5A):** the sentence "`proxy.ts` accepts its `POST` on `/experiments/new` only" is superseded by ADR-27 as far as it reads as binding the action to that page. The text above is kept as recorded.

## 7. Phase 5A decision (architectural decision of the Phase 5A instruction, 2026-10-26)

### ADR-27 — Write pages are UI entry points, not an authorization boundary (resolves R69; clarifies ADR-22 and ADR-26)
- **Status:** Accepted (architectural decision stated in the user's Phase 5A instruction, 2026-10-26). **Supersedes** one sentence of ADR-26 ("`proxy.ts` accepts its `POST` on `/experiments/new` only") in so far as it reads as binding the action to that page. ADR-22 and the rest of ADR-26 are unchanged and keep their recorded text. Nothing is weakened: `proxy.ts` keeps its page list and every check.
- **Context:** the Phase 4 closure (R69) showed that Next.js 16.4.3 dispatches any of the five Server Action ids on any page where the proxy accepts the `POST`: `submitExperiment` replayed to `/datasets` ran. ADR-26's wording suggested the experiment write was bound to its page.
- **Decision:**
  - `/experiments/new` is the intended UI entry point of `submitExperiment`, and the three ADR-22 form pages are those of the other four actions. The page list in `proxy.ts` limits *where* a Server Action `POST` is accepted at all (every other path gets `405`). It does **not** bind an action id to a page, and neither page paths nor action ids are treated as authorization controls.
  - The write boundary rests on: the loopback-only bind and the loopback `Host` check (ADR-08); explicit server-side validation in each action and again in the backend; the same-origin and CSRF checks (`Origin` required and equal to the host, `Sec-Fetch-Site: same-origin` when sent, the `Next-Action` header, Next.js's own `Origin`/`Host` comparison); the server-side admin token (CR-06); the mandatory project scope with one indistinguishable `404`; mock-only execution enforced by the form parser and by the backend (ADR-25, ADR-26); bounded request processing (body caps, the CR-08 rate limits, the run bounds).
  - These checks establish that a request comes from a page of this origin on the local machine. They do **not** establish a verified human identity (R54, R55). No login system is introduced, and public or shared deployment stays prohibited (ADR-22).
  - **The guard covers every path that can dispatch an action.** `proxy.ts` excludes only whole static-asset segments: `/_next/static/…` and exactly `/_next/image` (R68, R71). A new exclusion needs a live replay showing that the path cannot dispatch an action.
- **Alternatives rejected:** binding action ids to pages in the proxy from the build manifest (it depends on an undocumented manifest format and adds no boundary, because all five writes are permitted from the same origin anyway); a login system (outside the MVP scope and not approved).
- **Affected files:** `frontend/proxy.ts` (matcher, R71), `frontend/tests/unit/security-static.test.ts`, `frontend/tests/e2e/phase4d.spec.ts`, `SECURITY.md` §5, `ARCHITECTURE.md` §5.8.
- **Tests:** the static matcher test; the live replay of a genuine `submitExperiment` request to ten non-write paths, including `/favicon.ico` and the `/_next/static`, `/_next/staticx` and `/_next/image/x` look-alikes (nothing stored; `405`, or `404` from the static handler), with the write-page control (exactly one run); the raw-path probe of `reports/phase-5a.md` §3.

## 8. Phase 6A decisions (recorded for the architectural review, 2026-10-27)

ADR-28…30 are **accepted within the Phase 6A instruction**, which asked for them (provider abstraction, Responses API, execution permission, cost guards, demo runner); they are implemented on branch `feature/phase-6a-live-foundation` and recorded here for the architectural review, which may revise them before Phase 6B. They supersede nothing; ADR-01…27 are unchanged. Design detail: `PHASE6_LIVE_INTEGRATION_PLAN.md`.

### ADR-28 — OpenAI Responses API in the existing adapter, mapped onto `llminsight.attrmap.v1`
- **Status:** Accepted within the Phase 6A instruction; recorded for the architectural review. No migration, no backend change; ADR-05, ADR-13, ADR-15, ADR-19 unchanged.
- **Context:** the Phase 2B adapter instruments Chat Completions only. Observing current OpenAI usage needs the Responses API. The pinned semconv (`9ffc7e00c639`, `openai.md`) defines `openai.api.type = responses` but no Responses-specific operation name, and says instrumentations SHOULD use the applicable predefined value.
- **Decision:**
  - `ObservedOpenAIClient.create_response` (sync) wraps `client.responses.create` in the same per-attempt CLIENT span machinery: operation `chat`, `openai.api.type = responses`, Responses usage fields mapped to the five existing `gen_ai.usage.*` counters, `max_output_tokens` → `gen_ai.request.max_tokens`, `incomplete_details.reason` recorded verbatim as the finish reason of an `incomplete` response, none for `completed`.
  - Non-streaming text only: `stream`, `stream_options`, `background`, `conversation`, `previous_response_id`, `tools`, `tool_choice` and `store ≠ False` are refused before sending; `store=False` is always sent.
  - A returned response that reports failure, an unexpected status or an unreadable body ends its span with status `ERROR` and `error.type` `openai.response.failed` / `.unexpected_status` / `.malformed` (new additive `InferenceRecorder.mark_failed`), and is returned unchanged. Every field is type-checked because `openai` 3.28.0 does not validate bodies.
  - Default one attempt; retry only an unambiguous 429 (not `insufficient_quota`). Timeouts, connection errors and 5xx are never retried automatically (duplicate-billing risk).
  - A provider-neutral `TextGenerationAdapter` protocol (`llm_insight_sdk.providers`) with one implementation, `OpenAIResponsesTextAdapter`, returning metadata-only outcomes and stable error categories.
- **Alternatives rejected:** a separate Responses adapter module (duplicates the span, retry and client checks); a new operation name or `attrmap.v2` (no semconv basis; breaks ADR-13 detection for no gain); mapping `max_output_tokens` to Chat's `length` (invents a provider value); allowing built-in tools (their charges are not in the catalogue, so a `computed` cost would be understated).
- **Consequences:** Responses spans are stored, priced (`service_tier=default` on `api.openai.com`) and shown exactly like Chat spans. Diagnostics do not interpret the Responses `incomplete` vocabulary (R82). Async Responses support and streaming are later work.
- **Affected files:** `sdk/src/llm_insight_sdk/{openai_adapter,providers,instrumentation}.py`, `sdk/README.md`, `TELEMETRY_CONTRACT.md` §3 note.
- **Tests:** `sdk/tests/unit/test_openai_responses.py`, `sdk/tests/integration/test_live_responses.py`; Phase 2B Chat tests unchanged.

### ADR-29 — Fail-closed live execution guard with conservative in-process spend accounting
- **Status:** Accepted within the Phase 6A instruction; recorded for the architectural review. Refines the R05 control ("mock by default, flag, per-run approval and budget") for SDK-side callers; backend `LLMI_ALLOW_REAL_LLM` and ADR-25 are unchanged.
- **Decision:**
  - Real provider execution is allowed only when `llm_insight_sdk.live.evaluate_policy` finds no refusal: `LLMI_PROVIDER_MODE=real`, the exact consent phrase `LLMI_LIVE_CONSENT=openai-paid-calls`, an API key present, no `OPENAI_BASE_URL`, provider and model allowlists (models only with a verified price), positive request/input/output/money limits under hard caps, bounded timeout, retries and concurrency, and price evidence at most 14 days old. An API key alone never enables a call.
  - `LiveSession` reserves each request's worst-case cost before sending (input bound from UTF-8 bytes plus a framing allowance, output bound from `max_output_tokens`, every allowed attempt, the higher input rate, rounded up) against a per-request cap and a session budget, with a request count and a non-blocking concurrency limit. After the call, an attempt with priceable provider-reported usage is booked at the backend formula's estimate; any other attempt at its bound (never 0). Usage or attempts beyond the bound, or an unknown estimate, halt the session.
  - Money is `Decimal` throughout. Every figure is an estimate; the billed amount is never claimed.
  - The SDK carries a read-only mirror of the verified catalogue (ADR-01 forbids importing the backend); a live integration test compares it with the seeded `model_prices` rows.
- **Alternatives rejected:** enabling real mode by the presence of `OPENAI_API_KEY` (the instruction forbids it); booking failed attempts at 0 (unknown is not zero, ADR-11); fetching prices from the backend API at run time (adds an authenticated read path to the SDK for a bound that must hold offline); a persistent cross-run ledger (a file is not a durable, concurrency-safe store; the provider-side project budget is the real account-level control, a 6B prerequisite).
- **Consequences:** limits are per process and reset at exit (R79, like R67); the input bound rests on a tokenization assumption verified only after the first real call (R81); the mirror must be updated together with the backend catalogue (R83).
- **Affected files:** `sdk/src/llm_insight_sdk/live.py`, `.env.example`, `SECURITY.md` §2/§6, `PRICING.md` §2 note.
- **Tests:** `sdk/tests/unit/test_live_guard.py`, `test_live_runner.py`, `test_sdk_price_mirror_equals_the_seeded_catalogue`.

### ADR-30 — Local live demo runner: dry run by default, no browser surface
- **Status:** Accepted within the Phase 6A instruction; recorded for the architectural review. ADR-08, ADR-22, ADR-27 unchanged: no dashboard write, route or Server Action is added.
- **Decision:** `python -m demo_app.live_openai` is the only entry point for a real call. Without `--execute` it prints the policy decision, the backend readiness (an empty OTLP request: `200` and nothing written) and the worst-case plan, and never creates a provider client. With `--execute` it sends at most one fixed SYNTHETIC request when every gate passes, under a root span, then flushes telemetry and prints metadata only (trace ID, status, models, token counts, duration, estimated cost, dashboard URL and API path). It reads the key from the environment and pins `https://api.openai.com/v1`. Exit codes 0/1/2 (done / provider failure / refused).
- **Alternatives rejected:** a dashboard "live call" button or an endpoint that accepts a key (an unauthenticated spend surface on a page without login, R54); arbitrary user prompts in the runner (content and cost become unbounded).
- **Consequences:** a person with shell access and the explicit configuration can spend up to the configured ceilings per run; running the runner repeatedly is bounded per run only (R79).
- **Affected files:** `demo_app/live_openai.py`, `README.md`, `sdk/README.md`, `DEMO_SCRIPT.md` note.
- **Tests:** `sdk/tests/unit/test_live_runner.py`, `sdk/tests/integration/test_live_responses.py::test_live_runner_trace_reaches_the_database_and_the_read_api`.

## 9. Phase 6B preflight decision (recorded for the architectural review, 2026-10-28)

### ADR-31 — Live path hardening found by the Phase 6B preflight (refines ADR-28 and ADR-29)
- **Status:** Recorded within the Phase 6B preflight instruction, which asked for minimal corrections with regression tests; for the architectural review. It **refines** ADR-28 and ADR-29 (both stricter, no guarantee weakened) and supersedes neither; ADR-30 is unchanged except one additive output field.
- **Context (evidence, `reports/phase-6b-preflight.md` §2–§3):** (1) `openai` 3.28.0 reads `OPENAI_CUSTOM_HEADERS`, `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID` from the process environment; an offline probe with fake values showed that a custom `Authorization` line replaces the `OPENAI_API_KEY` the guard checked and that all three set routing headers, so the dedicated, budgeted project key could be bypassed without any gate noticing. (2) ADR-28's refusal list was a deny-list: `extra_body={"tools": …}`, `prompt` (a stored template with its own model and tools), `moderation` (a second model), `context_management` and `access_programs` passed through. (3) The runner printed `status: incomplete` but not the provider's reason.
- **Decision:**
  - `evaluate_policy` refuses (`client_env_override`) when any of `OPENAI_CUSTOM_HEADERS`, `OPENAI_ORG_ID`, `OPENAI_PROJECT_ID` is set, like `OPENAI_BASE_URL`. The credential and billing project come from the project-scoped key alone.
  - `ObservedOpenAIClient.create_response` accepts only `RESPONSES_ALLOWED` (`instructions`, `max_output_tokens`, `metadata`, `prompt_cache_key`, `reasoning`, `safety_identifier`, `service_tier`, `store`, `temperature`, `text`, `timeout`, `top_p`, `truncation`, `user`); everything else, including future library parameters, is refused before sending. The ADR-28 refusals keep their wording.
  - `TextOutcome.incomplete_reason` (additive, default `None`) carries `incomplete_details.reason` for an `incomplete` status; the runner prints it. Exit codes are unchanged (an `incomplete` response is a delivered, billed and observed call; its status is never `completed`).
- **Alternatives rejected:** clearing the variables inside the runner (hides operator configuration instead of refusing it); passing explicit `organization`/`project` arguments (the library treats `None` as "read the environment"); extending the deny-list (misses the next library parameter); a new exit code for `incomplete` (changes ADR-30's documented contract for no safety gain).
- **Consequences:** a shell that sets one of the three variables for other work must unset it before a live run. Callers of `create_response` that relied on `extra_*` or other parameters get `UnsupportedRequestError` (no such caller exists in the repository). No backend, schema, contract or frontend change.
- **Affected files:** `sdk/src/llm_insight_sdk/{live,openai_adapter,providers}.py`, `demo_app/live_openai.py`, `sdk/README.md`, `PHASE6_LIVE_INTEGRATION_PLAN.md` §C/§G, `SECURITY.md` §6, `SOURCES_AND_RISKS.md` R85–R86.
- **Tests:** `test_live_guard.py::test_each_gate_refuses_on_its_own` (3 new cases), `test_live_runner.py` (Phase 6B preflight section), `test_openai_responses.py` (12 new refusal cases, allowlist, incomplete reason), `test_live_responses.py::test_incomplete_runner_response_is_stored_as_incomplete_and_priced_once`.

### ADR-32 — The real-mode credential travels only to the pinned Responses endpoint (resolves R87; refines ADR-29 and ADR-30)
- **Status:** Recorded within the Phase 6B endpoint integrity instruction (R87), which asked for minimal changes with tests; for the architectural review. Refines ADR-29 (one more refused variable) and ADR-30 (how the runner builds its client); supersedes neither.
- **Context (evidence, `reports/phase-6b-preflight.md` §13):** the runner's `openai.OpenAI(...)` used the library's default `httpx2` client, which follows redirects (`follow_redirects=True`) and trusts the environment (`trust_env=True`: `HTTP(S)_PROXY`/`ALL_PROXY`, the Windows system proxy through `getproxies()`, `SSL_CERT_FILE`/`SSL_CERT_DIR`). `httpx2` drops `Authorization` on a cross-origin redirect, but a 307/308 re-sends the body to the new host; a proxy plus a trusted custom CA could read the key. R87 was only an operator checklist. In addition, `with_options(max_retries=0)` rebuilds the client and re-reads `OPENAI_ORG_ID`, `OPENAI_PROJECT_ID`, `OPENAI_ADMIN_KEY` and `OPENAI_CUSTOM_HEADERS`, so a check of the first client alone would not cover the client actually used.
- **Decision:**
  - The runner's real client is `openai.OpenAI(api_key=…, base_url="https://api.openai.com/v1", timeout=…, max_retries=0, http_client=openai.DefaultHttpxClient(trust_env=False, follow_redirects=False))`.
  - `openai_adapter.live_client_problems` accepts a client only if it is exactly `openai.OpenAI` for exactly `https://api.openai.com/v1/`, with a fixed non-empty API key and no admin key, provider runtime, workload identity, key callback, organization, project, custom header or query; `max_retries == 0`; an `httpx2.Client` without redirects, without `trust_env`, without mounts (proxies), hooks, auth or credential/routing headers; the plain `HTTPTransport` with a direct `ConnectionPool`; TLS `CERT_REQUIRED` with hostname checking. Unreadable attributes and library versions other than the audited `openai` 3.28.0 / `httpx2` 2.13.1 fail closed.
  - In real provenance the runner applies it after the reservation and before telemetry starts (refusal `client_not_allowed`, exit 2, reservation released, nothing booked because nothing was sent), and `ObservedOpenAIClient(require_live_endpoint=True)` applies it again to the given client and to the rebuilt copy it sends with. The library audit also runs in the dry run (`client_library_not_audited`). The guard additionally refuses `OPENAI_ADMIN_KEY`.
  - `synthetic` provenance is unaffected, so the offline fake transports keep working; `real` provenance with a fake or custom transport is refused.
- **Alternatives rejected:** refusing proxy and CA variables in the guard (the Windows system proxy is not an environment variable; ignoring all of them in the client is stricter and needs no operator action); a custom `httpx2` transport with host pinning (duplicates what the checked configuration already guarantees); applying the check to every `provenance="real"` SDK user (would break the documented general `openai.OpenAI()` usage outside the live runner).
- **Consequences:** a machine that can reach the internet only through a proxy cannot run the live test (a connection failure, before anything is sent). An `openai` or `httpx2` upgrade refuses real execution until the boundary is re-audited and the pinned versions updated. The TLS trust anchor stays the operating-system certificate store, which `httpx2` uses through `truststore` (R88).
- **Affected files:** `sdk/src/llm_insight_sdk/{openai_adapter,live}.py`, `demo_app/live_openai.py`, `sdk/README.md`, `PHASE6_LIVE_INTEGRATION_PLAN.md` §G/§J, `SECURITY.md` §6, `SOURCES_AND_RISKS.md` R87–R88.
- **Tests:** `sdk/tests/unit/test_live_endpoint.py` (52: 31 refused configurations, environment caught in the rebuilt copy, the exact final request on the real transport class, environment and redirects that cannot move it, overrides the guard did not see, injected clients, reservation release, synthetic fakes still usable).

## 10. Phase 6C-A decisions (recorded for the architectural review, 2026-10-28)

ADR-33…36 are **recorded within the Phase 6C-A instruction**, which asked for them (the user chose the Gemini Free Tier instead of a paid OpenAI request); they are implemented on branch `feature/phase-6c-gemini-free` (from the Phase 6 head `0190bf4`) and stay open to revision by the architectural review. ADR-28…32 are unchanged and keep their recorded text. Evidence: `reports/phase-6c-a.md`; design: `PHASE6_LIVE_INTEGRATION_PLAN.md` Part II.

### ADR-33 — A second provider adapter: Gemini Developer API `generateContent` on `llminsight.attrmap.v1` (amends ADR-05)
- **Status:** Recorded within the Phase 6C-A instruction; for the architectural review. **Amends ADR-05** ("one working provider adapter (OpenAI) … Gemini and others come later") for the post-MVP Phase 6 only: a second adapter is added; the provider-agnostic data model, the OpenAI adapter and the accepted Phase 5 release are unchanged. ADR-13, ADR-15, ADR-19 unchanged.
- **Context:** the user chose a Gemini Free Tier call for the first real trace. The pinned semconv (`9ffc7e00c639`, `model/gen-ai/registry.yaml`, SHA-256 `63b40577…4937`, read 2026-10-28) defines `gen_ai.provider.name = gcp.gemini` ("used when accessing the 'generativelanguage.googleapis.com' endpoint") and the operation `generate_content`; both already pass the `attrmap.v1` allowlist. Google's documentation (2026-10-15) calls `generateContent` legacy but "fully supported"; the newer Interactions API stores requests server-side by default.
- **Decision:**
  - SDK `llm_insight_sdk.gemini_adapter`: `ObservedGeminiClient.generate_content` over the official `google-genai` 2.30.1 (optional extra `gemini`, exact pin), and `GeminiTextAdapter` implementing the ADR-28 `TextGenerationAdapter` protocol. One CLIENT span per physical request: operation `generate_content`, provider `gcp.gemini`, `modelVersion` → `gen_ai.response.model`, `responseId` → `gen_ai.response.id`, the candidate's finish reason verbatim, `server.address = generativelanguage.googleapis.com`.
  - Scope: one single-turn text string, non-streaming; only `max_output_tokens`, `temperature`, `top_p`; a plain model ID. Refused before sending: tools, tool config, grounding/search, code execution, files, history, cached content, `http_options`, `thinking_config`, system instructions, safety settings, structured output, labels and every other setting. `automatic_continuation=False` and `automatic_function_calling.disable=True` are always set: google-genai 2.30.1 follows continuation tokens **by default**, sending further billable requests and merging their usage into one response (probed offline).
  - Usage: `input = promptTokenCount + toolUsePromptTokenCount`, `output = candidatesTokenCount + thoughtsTokenCount` (Google: output pricing is candidates plus thinking), `cache_read = cachedContentTokenCount` (inside the prompt count), `reasoning = thoughtsTokenCount`. Absent (JSON-omitted) counts are 0 only when the reported `totalTokenCount` equals that sum; a missing prompt or total count, a sum that does not match the total, or a cached count above the prompt count makes the usage unknown. Nothing is estimated.
  - Outcomes: `STOP` completed; `MAX_TOKENS`/`CONTINUATION` incomplete (reason kept); a blocked prompt (span ERROR `gcp.gemini.prompt_blocked`) or a safety-type finish (span UNSET, reason recorded) → additive status `blocked`; no candidate, another finish reason or none → `failed` with `gcp.gemini.response.*`. Additive `TextOutcome.provider_reason` and four error categories (`payment_required`, `precondition_failed`, `content_blocked`, `empty_response`).
  - One attempt, no adapter retry; the library's retry options stay unset (its default is one attempt).
- **Alternatives rejected:** the Interactions API (server-side storage by default, a different usage vocabulary, not needed for one stateless text call); `gcp.gen_ai` (semconv reserves it for an unknown backend); output = candidates only (understates the billed output by the thoughts); a new `attrmap` version (nothing needed one); Vertex AI (a paid Google Cloud billing path).
- **Consequences:** Gemini spans are stored and shown like any inference span; the backend gains no column, migration or allowlist entry. Diagnostics do not interpret Gemini finish reasons (R89).
- **Affected files:** `sdk/src/llm_insight_sdk/{gemini_adapter,providers}.py`, `sdk/pyproject.toml`, `sdk/requirements-dev.lock`, `TELEMETRY_CONTRACT.md` §3 note, `ARCHITECTURE.md` §6.
- **Tests:** `sdk/tests/unit/test_gemini_adapter.py`, `sdk/tests/integration/test_live_gemini.py`.

### ADR-34 — The real-mode Gemini key travels only to the Developer API `generateContent` endpoint (refines ADR-32's rule for a second provider)
- **Status:** Recorded within the Phase 6C-A instruction; for the architectural review. ADR-32 is unchanged and still governs the OpenAI client.
- **Context (source audit of google-genai 2.30.1 and httpx 0.28.1, `reports/phase-6c-a.md` §4):** the library reads `GOOGLE_API_KEY` (wins over `GEMINI_API_KEY`), `GOOGLE_GENAI_USE_VERTEXAI`/`_ENTERPRISE` (switch to Vertex AI), `GOOGLE_CLOUD_PROJECT`/`_LOCATION`, `GOOGLE_GEMINI_BASE_URL`/`GOOGLE_VERTEX_BASE_URL`, `GOOGLE_GENAI_CLIENT_MODE` and the replay variables (a record/replay client), mTLS switches and `SSL_CERT_FILE`/`SSL_CERT_DIR`. Its default HTTP client follows redirects and trusts the environment's proxies; `httpx` strips only `Authorization` on a cross-origin redirect, so the `x-goog-api-key` header **would be forwarded** to another host.
- **Decision:**
  - The runner's client is `genai.Client(vertexai=False, api_key=…, debug_config=DebugConfig(client_mode=None, …), http_options=HttpOptions(base_url="https://generativelanguage.googleapis.com/", api_version="v1beta", timeout=…, httpx_client=httpx.Client(verify=<certifi context>, trust_env=False, follow_redirects=False)))`.
  - `gemini_adapter.live_gemini_client_problems` accepts it only if it is exactly `genai.Client` with the plain `BaseApiClient`, no replay mode, `vertexai is False`, no custom base URL, project, location or credentials, exactly that base URL and `v1beta`, a non-ephemeral API key carried only by `x-goog-api-key` (no other credential or routing header), no `extra_body`, no retry options and one library attempt, no mTLS or google-auth session; an `httpx.Client` without redirects, `trust_env`, mounts, hooks, auth or credential headers, using the plain `HTTPTransport` with a direct pool and `CERT_REQUIRED` + hostname checking. Library versions other than the audited ones fail closed.
  - Applied in real provenance after the reservation and before telemetry (`client_not_allowed`, exit 2, nothing sent), again when the instrumented client is built, and **again immediately before the send**. `synthetic` provenance keeps the offline fake transports; `real` with a mock transport is refused; Vertex AI is refused in every mode.
- **Alternatives rejected:** trusting the library defaults (redirect key forwarding, proxies); clearing variables inside the runner (hides operator configuration; the guard refuses them instead); `truststore` (not a google-genai dependency; the pinned `certifi` bundle, R74-patched, is explicit and ignores `SSL_CERT_FILE`).
- **Consequences:** a proxy-only network cannot run the live call (connection failure before anything is sent). An unreadable `SSL_CERT_FILE` makes the library itself fail at construction (refusal, nothing sent). Endpoint integrity says nothing about the project's billing tier (ADR-35).
- **Affected files:** `sdk/src/llm_insight_sdk/gemini_adapter.py`, `demo_app/live_gemini.py`, `SECURITY.md` §6.
- **Tests:** `sdk/tests/unit/test_gemini_endpoint.py`.

### ADR-35 — Gemini Free Tier execution guard and cost semantics: operator attestation, no paid path, unknown cost
- **Status:** Recorded within the Phase 6C-A instruction; for the architectural review. ADR-29 is unchanged (the OpenAI guard); ADR-11 applies (unknown is never 0).
- **Context:** Google documents Free Tier eligibility per active project and billing tiers per billing account (Free; Tier 1 after linking a billing account), visible in AI Studio's *Billing Tier* column; no API reports a project's tier to a client. Free Tier prompts and responses may be used to improve Google's products (pricing page); paid tiers are not. A paid project's depleted prepay credit answers `402`.
- **Decision:**
  - `llm_insight_sdk.gemini_live.evaluate_free_tier_policy` allows a real call only with none of its refusals: `LLMI_PROVIDER_MODE=real`; `LLMI_GEMINI_MODE=free-tier` (no paid mode exists); `LLMI_LIVE_CONSENT=gemini-free-tier-single-call` (distinct from OpenAI's); a `GEMINI_API_KEY` (presence only, never accepted as an argument); none of the client-routing variables of ADR-34; the **operator attestation** `LLMI_GEMINI_BILLING_TIER=free` and `LLMI_GEMINI_TIER_CHECKED_ON` (UTC date, at most one day old; missing → `free_tier_unverified`, any other tier → `billing_tier_not_free`); provider `gcp.gemini` only; models from the verified Free Tier allowlist only (`gemini-3.1-flash-lite`, evidence read 2026-10-28T13:00Z, refused after 14 days); hard caps: 1 request, concurrency 1, 0 retries, timeout ≤ 30 s, output 16–1024 tokens (thinking included), input bound ≤ 1024.
  - The attestation is described everywhere as an operator check, **not** a proof from Google's billing backend; endpoint integrity (ADR-34) does not prove the tier either.
  - Cost: the Free Tier has no billed amount the API reports, so the runner prints `cost.status = unknown`, `billed_amount_usd = null`, `paid_tier_list_price_estimate_usd = null`, the attested tier and its basis. The backend gains one applicability rule: **no stored price ever applies to `gcp.gemini`** (`PRICING.md` §3), so every Gemini usage row is `cost_unknown_reason = price_missing` (or `usage_missing`/`usage_inconsistent`), even if Gemini price rows were seeded. OpenAI rows never match a Gemini call (exact provider match). No migration; existing Phase 5 cost records are untouched.
  - `FreeTierSession` counts the one request and its physical attempts, refuses a second or concurrent one, and halts if reported usage exceeds the bounds.
- **Alternatives rejected:** treating a Free Tier call as cost 0 (`computed` 0 would be a claim about billing the system cannot verify); computing a paid-tier list price "for information" (could be read as a charge; no verified Gemini catalogue exists); a new `cost_unknown_reason` value such as `billing_tier_unverified` (a `CHECK` change and migration for no stronger guarantee); probing the tier with a test request (spends a request and still proves nothing).
- **Consequences:** the dashboard shows Gemini calls with unknown cost and `cost_complete = false`. A paid project whose operator wrongly attests `free` would be billed by Google; the provider-side controls (no billing account linked, spend caps) are the real account-level boundary (R90).
- **Affected files:** `sdk/src/llm_insight_sdk/gemini_live.py`, `backend/app/services/pricing.py`, `backend/tests/unit/test_pricing.py`, `PRICING.md` §3, `SECURITY.md` §6, `.env.example`.
- **Tests:** `sdk/tests/unit/test_gemini_live_guard.py`, `backend/tests/unit/test_pricing.py` (Gemini rule), `sdk/tests/integration/test_live_gemini.py`.

### ADR-36 — Local Gemini demo runner: dry run by default, one authorized call, no browser surface
- **Status:** Recorded within the Phase 6C-A instruction; for the architectural review. ADR-08, ADR-22, ADR-27 and ADR-30 unchanged.
- **Decision:** `python -m demo_app.live_gemini` (SDK venv, repository root) is the only entry point for a real Gemini call. Without `--execute` it prints the policy decision, the library audit, the backend readiness (an empty OTLP request) and the plan, and never creates a Gemini client. With `--execute` it sends at most one fixed SYNTHETIC `generateContent` request when every gate passes, under the root span `demo.live-gemini`, then flushes telemetry and prints metadata only: trace ID, model, outcome and reason, normalized and provider token counts with the usage check, duration, the cost block of ADR-35, the session counters and the dashboard link. Exit codes 0/1/2 (completed or incomplete / failed or blocked / refused). The provider-neutral readiness, link and output helpers move to `demo_app/live_common.py`, which the OpenAI runner now imports (same behaviour).
- **Alternatives rejected:** a dashboard button or route that calls Gemini (an unauthenticated spend and key surface, R54); automatic resubmission after a failure (a timeout may still have been processed; the operator decides).
- **Consequences:** each runner invocation is bounded on its own (R79 applies to this runner as well).
- **Affected files:** `demo_app/live_gemini.py`, `demo_app/live_common.py`, `demo_app/live_openai.py` (imports only), `README.md`, `sdk/README.md`.
- **Tests:** `sdk/tests/unit/test_gemini_runner.py`, `test_live_runner.py` (unchanged, OpenAI), `sdk/tests/integration/test_live_gemini.py`.

## 11. Phase 6C-C decisions (recorded for the architectural review, 2026-11-01)

### ADR-37 — Exact `provider` filter on the three read endpoints (additive, read-only)
- **Status:** Recorded within the Phase 6C-C instruction; for the architectural review. ADR-21 (provenance) and the §5.1/§5.2 filter semantics are unchanged; no migration, no new index, no write.
- **Context:** the dashboard needs provider exploration (`openai`, `gcp.gemini`, `unknown`) over stored telemetry. A client-side filter of one cursor page or of the top-N groups would look like a dataset-wide filter while covering only what is on screen.
- **Decision:** `GET /api/v1/traces`, `GET /api/v1/overview` and `GET /api/v1/models/compare` accept an optional `provider`: exact `usage_records.provider`, restricted to the stored identifier format `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` (anything else is `422` naming `provider`, never echoed; auth first). Usage-level metrics use the row's provider; trace-level metrics and the trace list use "the trace has a usage row with that provider" (one `EXISTS` on the usage unique key); with `model`, **one usage row must match both** (the same call). `unknown` selects spans that named no provider (the stored default). The aggregate responses echo `filters.provider` (additive; `null` when unset). Not case-folded, not a prefix.
- **Alternatives rejected:** client-side filtering of a page (misleading scope, breaks paging); a provider column on `traces` (schema change for a derived value); separate `EXISTS` clauses for provider and model (would select a trace whose provider and model come from different calls).
- **Consequences:** older dashboards keep working (the parameter is optional, the echo key is additive). The dashboard refuses an aggregate whose echo does not show the requested provider, and a trace list containing a trace without a matching call (`malformed`), so a backend that ignores the parameter is never shown as filtered data. A provider literally named `unknown` and a missing provider are indistinguishable (as before).
- **Affected files:** `backend/app/api/{traces,overview}.py`, `backend/app/repositories/{traces,metrics}.py`, `backend/app/services/metrics.py`, `ARCHITECTURE.md` §5.11.
- **Tests:** `backend/tests/integration/test_phase6c_provider_filter.py` (30 cases), the provider variants of `test_metrics_plan.py` and `test_trace_api.py::test_list_query_plan_reads_one_page_through_the_indexes` (no sequential scan), five backend mutations caught.

### ADR-38 — Controlled dashboard refresh: cooldown, backoff, stop on fatal failures, labelled stale data
- **Status:** Recorded within the Phase 6C-C instruction; for the architectural review. Extends the Phase 3C auto-refresh; ADR-22/26/27 (the dashboard writes) and the server-side-only backend access (§5.3) are unchanged; no route handler, Server Action or client-side backend call is added.
- **Decision:**
  - A refresh re-runs the current server render (`router.refresh()`), as before. It is **off by default**, 30 s / 1 min / 5 min, never below 15 s, at most one in flight (Next.js serializes router actions and discards an unfinished refresh when a navigation starts), counted from the completed refresh, **paused with no timer while the tab is hidden**, stopped after 30 minutes. The state lives in one `RefreshController` per page (`frontend/lib/auto-refresh.ts`, read with `useSyncExternalStore`): at most one refresh timer plus a display-only countdown, both cleared on unmount, Strict-Mode safe.
  - Each server render reports the outcome of all its backend reads (`renderOutcome`: kind and class only, no data). `429` (and `503` with `Retry-After`) starts a cooldown from the backend's `Retry-After` (delay-seconds or IMF-fixdate, rounded up; 60 s when absent) that the timer **and the manual Refresh button** respect; transient failures (outage, timeout, 5xx) double the interval up to 5 minutes; failures a retry cannot fix (bad configuration, invalid or unknown project, rejected token, refused query, a response that does not match the contract or names another project) **stop polling** with the reason, leaving a deliberate manual refresh possible.
  - `LiveRegion` keeps the content of the last fully successful render of the **same view** (project, filters, window) and, after a transient or rate-limit failure of that view, shows the failure first and the old content below it, dimmed and labelled with its load time. Never for another view or project, never after a fatal failure; a failed read is never rendered as an empty result.
  - The pages state three times separately: when the dashboard read the backend, the latest stored trace's event time (reported by the instrumentation, not the ingest time) and its duration. Nothing is pushed or streamed.
- **Alternatives rejected:** client-side polling of a dashboard route handler (a new browser-reachable surface; §5.3); keeping the old content without a label (stale data that looks current); retrying immediately after an error (refresh storms against the T43 bucket); a WebSocket/SSE push (not needed for stored telemetry; a new surface).
- **Consequences:** a render reads more endpoints (Overview 5 `/api/v1` reads, Traces and Models 3 each: the filter suggestions and the latest trace); a full browser reload is not blocked by the client cooldown (the backend still answers `429`, R98); the cooldown compares the server's clock with the browser's, which is the same machine for the loopback-only dashboard (R98).
- **Affected files:** `frontend/lib/auto-refresh.ts`, `frontend/components/{navigation-transition,auto-refresh,live-region,scope-filter-form}.tsx`, `frontend/components/overview/latest-trace.tsx`, `frontend/lib/server/{backend,overview,traces,models,filter-options}.ts`, the three page components.
- **Tests:** `frontend/tests/unit/phase6c.test.tsx` (controller on a fake clock, outcomes, Retry-After, stale rules, filters, labels), `frontend/tests/e2e/phase6c.spec.ts` (7 browser tests on the live stack), eleven frontend mutations caught.

**Supersession rule:** changing an accepted ADR needs evidence and a new ADR entry that names the one it supersedes.
