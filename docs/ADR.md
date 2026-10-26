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

**Supersession rule:** changing an accepted ADR needs evidence and a new ADR entry that names the one it supersedes.
