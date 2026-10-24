# LLM Insight — Architecture, Data & API Contracts

**Status:** binding contract (Phase 0 closed by ADR-13…21, 2026-10-24). No component is implemented yet. Decisions are in `ADR.md`, wire details in `TELEMETRY_CONTRACT.md`, security in `SECURITY.md`.

## 1. Components

| Component | Tech | Responsibility | Phase |
|---|---|---|---|
| `backend/` | FastAPI modular monolith, SQLAlchemy 2, Alembic, PostgreSQL | OTLP ingest, query API, pricing/ledger, reviews, datasets, experiments | 1–4 |
| `sdk/` | Independent Python package on the OTel SDK | Tracer/Resource setup, `BatchSpanProcessor`, OTLP/HTTP protobuf exporter, OpenAI wrapper, mock adapter | 2 |
| `demo_app/` | Small FastAPI app | Root HTTP span → LLM child span; real (opt-in) or mock provider | 2 |
| `frontend/` | Next.js, TypeScript, Tailwind, Recharts | Server-side data fetching, KPIs, trace explorer, review / dataset / experiment UI | 3–4 |
| PostgreSQL | Docker Compose | Single store (ADR-02) | 1 |

The platform does **not** proxy model calls. The instrumented app calls OpenAI directly; only telemetry flows to LLM Insight.

## 2. Data flow

```mermaid
flowchart TB
    U[Demo FastAPI app] -->|OTel context + llm_insight_sdk| X[BatchSpanProcessor + OTLP/HTTP protobuf exporter]
    U -->|direct HTTPS, real mode only| O[(OpenAI API)]
    X -->|POST /v1/traces, X-Project-Key, gzip| A[Ingest: header checks, auth, rate, size]
    A --> B[Protobuf decode, validate, allowlist/redact, attrmap v1]
    B -->|one transaction; 200 only after COMMIT| C[(PostgreSQL)]
    C --> D[Query / Pricing / Quality / Experiment services]
    D -->|/api/v1/*, Bearer admin token| E[Next.js server components]
    E --> F[Browser: KPIs, Trace Explorer, Failure Review]
    F --> G[Human-approved test case]
    G --> H[Prompt A vs B offline experiment]
    H --> C
```

## 3. Sequence: one instrumented request

```mermaid
sequenceDiagram
    participant Client
    participant Demo as Demo app (+SDK)
    participant LLM as OpenAI or mock adapter
    participant Exp as BatchSpanProcessor
    participant Ing as Backend /v1/traces
    participant DB as PostgreSQL
    participant UI as Next.js (server)
    Client->>Demo: POST /v1/summarize
    Demo->>Demo: start SERVER span (root)
    Demo->>LLM: chat request (CLIENT span "chat {model}", attempt=1)
    LLM-->>Demo: response + usage (or 429/timeout → error.type)
    Demo->>Demo: end CLIENT span with gen_ai.usage.* and provenance
    Demo-->>Client: response (never waits on export)
    Exp-)Ing: async batch, protobuf (+gzip), short timeout
    Ing->>Ing: method/type → auth → rate → size → decode → validate → allowlist
    Ing->>DB: BEGIN; upsert traces, spans, usage_records; COMMIT
    Ing-->>Exp: 200 ExportTraceServiceResponse after COMMIT (or 4xx/5xx Status)
    Note over Exp,Ing: Backend down → export fails after timeout, app unaffected (ADR-09)
    UI->>DB: (via /api/v1, server-side token) overview / trace tree
```

## 4. Database contract (PostgreSQL 17) — ADR-21

### 4.1 Conventions
- **Primary keys:** internal surrogate keys are `id BIGSERIAL PRIMARY KEY`. OTel identifiers are kept as **external identifiers**: `trace_id TEXT` (32 lowercase hex chars) and `span_id TEXT` (16 lowercase hex chars), checked by regex and non-zero, and unique per project. They are never replaced by surrogate keys in APIs.
- **Timestamps:** `TIMESTAMPTZ` in UTC (microsecond precision). OTel nanosecond values are kept losslessly in `BIGINT` `*_ns` columns.
- **Money:** `NUMERIC(20,10)`. Token counts are `BIGINT`.
- **Enums:** `TEXT` + `CHECK (col IN (...))`, not PostgreSQL `ENUM`.
- **Shorthand in the tables below:**
  - `HEX32` = `CHECK (col ~ '^[0-9a-f]{32}$' AND col <> repeat('0',32))`
  - `HEX16` = `CHECK (col ~ '^[0-9a-f]{16}$' AND col <> repeat('0',16))`
  - `≥0` = `CHECK (col >= 0)`
  - `LEN(n)` = `CHECK (char_length(col) BETWEEN 1 AND n)`
- **Origin column:**
  - `§E`: the field is in master plan §E.
  - `ADD`: an addition approved by ADR-21. The reason is given in the table.
  - `CR-03`: renamed per CR-03.
- **Retention:** cascades flow `projects → traces → spans → usage_records`. Human work (`failure_reviews`, `test_cases`) references traces **softly** (no FK), so that lineage survives retention. The API validates the trace exists in the project at insert time.

### 4.2 `projects`
| Column | Type | Null | Default | Check / rule | Origin |
|---|---|---|---|---|---|
| id | BIGSERIAL | PK | | | §E |
| name | TEXT | NOT NULL | | LEN(100), UNIQUE | §E |
| created_at | TIMESTAMPTZ | NOT NULL | `now()` | | §E |

Tests: T28.

### 4.3 `project_api_keys`
| Column | Type | Null | Default | Check / rule | Origin |
|---|---|---|---|---|---|
| id | BIGSERIAL | PK | | | §E |
| project_id | BIGINT | NOT NULL | | FK `projects(id)` ON DELETE CASCADE; INDEX | §E |
| prefix | TEXT | NOT NULL | | `^[A-Za-z0-9]{8}$`, UNIQUE | §E |
| key_hash | TEXT | NOT NULL | | `^[0-9a-f]{64}$` (HMAC-SHA256 hex) | §E |
| created_at | TIMESTAMPTZ | NOT NULL | `now()` | | ADD: audit and rotation order |
| revoked_at | TIMESTAMPTZ | NULL | | ≥ `created_at` | §E |

Tests: T05, T44.

### 4.4 `traces` (derived summary, recomputed from stored spans in the ingest transaction)
| Column | Type | Null | Default | Check / rule | Origin |
|---|---|---|---|---|---|
| id | BIGSERIAL | PK | | | ADD: internal surrogate key (ADR-21) |
| project_id | BIGINT | NOT NULL | | FK `projects(id)` ON DELETE CASCADE | §E |
| trace_id | TEXT | NOT NULL | | HEX32 | §E |
| service_name | TEXT | NOT NULL | `'unknown_service'` | LEN(256). Root span's resource, else the earliest span's | §E |
| start_ns | BIGINT | NOT NULL | | > 0. Min span start | ADD: lossless ns time (ADR-21) |
| end_ns | BIGINT | NOT NULL | | ≥ `start_ns`. Max span end | ADD: lossless ns time |
| started_at | TIMESTAMPTZ | NOT NULL | | Derived from `start_ns` | §E |
| ended_at | TIMESTAMPTZ | NOT NULL | | Derived from `end_ns` | §E |
| status | TEXT | NOT NULL | | IN (`ok`,`error`). `error` if any span is ERROR | §E |
| prompt_version | TEXT | NULL | | NULL if the inference spans disagree or have none | §E |
| provenance | TEXT | NOT NULL | `'unknown'` | IN (`real`,`synthetic`,`mixed`,`unknown`) (`TELEMETRY_CONTRACT.md` §5) | ADD: safe real/synthetic split (T48) |
| span_count | INT | NOT NULL | | ≥ 1 | ADD: tree completeness display |
| updated_at | TIMESTAMPTZ | NOT NULL | `now()` | | ADD: recompute audit |

Constraints: `UNIQUE(project_id, trace_id)`; `INDEX(project_id, started_at DESC)` (§E).
Tests: T02, T03, T25, T26, T28.

### 4.5 `spans`
| Column | Type | Null | Default | Check / rule | Origin |
|---|---|---|---|---|---|
| id | BIGSERIAL | PK | | | ADD: internal surrogate key |
| project_id | BIGINT | NOT NULL | | | §E |
| trace_id | TEXT | NOT NULL | | HEX32. FK `(project_id, trace_id)` → `traces` ON DELETE CASCADE | §E |
| span_id | TEXT | NOT NULL | | HEX16 | §E |
| parent_span_id | TEXT | NULL | | HEX16; ≠ `span_id`. A missing parent row means orphan (no FK) | §E |
| name | TEXT | NOT NULL | | LEN(256) (truncated) | §E |
| kind | TEXT | NOT NULL | | IN (`UNSPECIFIED`,`INTERNAL`,`SERVER`,`CLIENT`,`PRODUCER`,`CONSUMER`) | §E |
| start_ns | BIGINT | NOT NULL | | > 0 | §E |
| end_ns | BIGINT | NOT NULL | | ≥ `start_ns` | §E |
| duration_ns | BIGINT | NOT NULL | GENERATED ALWAYS AS (`end_ns - start_ns`) STORED | ≥0 | ADD: one source of truth for latency math |
| status | TEXT | NOT NULL | | IN (`UNSET`,`OK`,`ERROR`) | §E |
| status_message | TEXT | NULL | | ≤ 256 (truncated) | ADD: OTLP Status.message for diagnosis |
| error_type | TEXT | NULL | | ≤ 256 | ADD: semconv `error.type` (Stable) |
| scope_name | TEXT | NULL | | ≤ 256 | ADD: OTLP scope traversal |
| scope_version | TEXT | NULL | | ≤ 64 | ADD: OTLP scope traversal |
| resource_attributes | JSONB | NOT NULL | `'{}'` | `jsonb_typeof = 'object'`; allowlisted only | ADD: separates resource from span attributes |
| attributes | JSONB | NOT NULL | `'{}'` | `jsonb_typeof = 'object'`; allowlisted only | §E ("whitelisted JSONB") |
| attributes_schema_version | TEXT | NOT NULL | | e.g. `llminsight.attrmap.v1` | §E |
| provenance | TEXT | NOT NULL | `'unknown'` | IN (`real`,`synthetic`,`unknown`) | ADD: provenance source (ADR-21) |
| server_dropped_attributes_count | INT | NOT NULL | `0` | ≥0 | ADD: privacy health M19. Renamed per ADR-21 |
| ingested_at | TIMESTAMPTZ | NOT NULL | `now()` | | ADD: ingest audit, re-export diagnosis |

Constraints: `UNIQUE(project_id, trace_id, span_id)`; `INDEX(project_id, trace_id, start_ns)` (§E).
Tests: T01–T04, T06, T16, T26, T27, T30, T31.

### 4.6 `usage_records` (one row per physical inference attempt)
| Column | Type | Null | Default | Check / rule | Origin |
|---|---|---|---|---|---|
| id | BIGSERIAL | PK | | | ADD: internal surrogate key |
| project_id | BIGINT | NOT NULL | | | §E |
| canonical_call_key | TEXT | NOT NULL | | LEN(300) | §E |
| call_key_source | TEXT | NOT NULL | | IN (`provider_response_id`,`sdk_attempt`,`span_fallback`) | ADD: key strength audit |
| trace_id | TEXT | NOT NULL | | HEX32 | §E |
| span_id | TEXT | NOT NULL | | HEX16. FK `(project_id, trace_id, span_id)` → `spans` ON DELETE CASCADE | §E |
| call_id | TEXT | NULL | | ≤ 64 (`llminsight.call_id`) | ADD: logical-call grouping (M03, T14) |
| attempt_id | INT | NULL | | ≥ 1 (`llminsight.attempt`) | §E |
| provider_response_id | TEXT | NULL | | ≤ 256 (`gen_ai.response.id`) | ADD: dedup audit |
| operation_name | TEXT | NOT NULL | | IN (`chat`,`text_completion`,`generate_content`,`embeddings`) | ADD: ADR-13 detection result |
| provider | TEXT | NOT NULL | | LEN(64); `unknown` if absent | §E |
| requested_model | TEXT | NULL | | ≤ 256 | §E |
| response_model | TEXT | NULL | | ≤ 256 | §E |
| input_tokens | BIGINT | NULL | | ≥0 | §E |
| output_tokens | BIGINT | NULL | | ≥0 | §E |
| cached_input_tokens | BIGINT | NULL | | ≥0 | §E |
| cache_write_input_tokens | BIGINT | NULL | | ≥0 | ADD: semconv cache-write subset |
| reasoning_output_tokens | BIGINT | NULL | | ≥0 | §E |
| usage_source | TEXT | NOT NULL | | IN (`provider_reported`,`estimated`,`unknown`) | §E |
| cost_usd | NUMERIC(20,10) | NULL | | ≥0 | §E |
| cost_status | TEXT | NOT NULL | | IN (`computed`,`unknown`) | §E |
| cost_unknown_reason | TEXT | NULL | | IN (`price_missing`,`usage_missing`,`usage_inconsistent`) | ADD: explains unknown without changing `cost_status` |
| price_version | TEXT | NULL | | | §E |
| model_price_id | BIGINT | NULL | | FK `model_prices(id)` ON DELETE RESTRICT | ADD: exact price row used (T22) |
| provenance | TEXT | NOT NULL | | IN (`real`,`synthetic`,`unknown`). Copied from the span | ADD: real/synthetic cost split without a join |
| started_at | TIMESTAMPTZ | NOT NULL | | Span start | ADD: event-time windows (ingest time ≠ event time) |
| created_at | TIMESTAMPTZ | NOT NULL | `now()` | | §E |

**Table checks:**
- `(cost_status = 'computed') = (cost_usd IS NOT NULL)`: unknown is never stored as 0.
- `(cost_status = 'unknown') = (cost_unknown_reason IS NOT NULL)`.
- `cost_status <> 'computed' OR (price_version IS NOT NULL AND model_price_id IS NOT NULL)`.

**Constraints and indexes:**
- `UNIQUE(project_id, canonical_call_key)`: the call identity.
- `UNIQUE(project_id, trace_id, span_id)`: one row per span.
- `INDEX(project_id, created_at, provider, response_model)` (§E).
- `INDEX(project_id, started_at)` (ADD).
- `INDEX(project_id, call_id) WHERE call_id IS NOT NULL` (ADD).

Tests: T04, T11, T14, T17–T23, T29, T32, T48.

### 4.7 `model_prices` (global; seeded only from verified official prices, Phase 2)
| Column | Type | Null | Default | Check / rule | Origin |
|---|---|---|---|---|---|
| id | BIGSERIAL | PK | | | ADD: internal surrogate key |
| version | TEXT | NOT NULL | | LEN(64) | §E |
| provider | TEXT | NOT NULL | | LEN(64) | §E |
| model | TEXT | NOT NULL | | LEN(256). Exact model name, no wildcard | §E |
| effective_from | TIMESTAMPTZ | NOT NULL | | | §E |
| currency | TEXT | NOT NULL | `'USD'` | `= 'USD'` (MVP) | §E |
| input_per_million | NUMERIC(20,10) | NOT NULL | | ≥0 | §E |
| output_per_million | NUMERIC(20,10) | NOT NULL | | ≥0 | §E |
| cached_input_per_million | NUMERIC(20,10) | NULL | | ≥0 | §E |
| cache_write_input_per_million | NUMERIC(20,10) | NULL | | ≥0 | ADD: cache-write pricing |
| source_url | TEXT | NOT NULL | | `LIKE 'https://%'` | §E |
| verified_at | TIMESTAMPTZ | NOT NULL | | | §E |
| created_at | TIMESTAMPTZ | NOT NULL | `now()` | | ADD: seed audit |

Constraints: `UNIQUE(provider, model, version)`; `UNIQUE(provider, model, effective_from)`.
Tests: T19, T22.

### 4.8 `test_datasets`
| Column | Type | Null | Default | Check / rule | Origin |
|---|---|---|---|---|---|
| id | BIGSERIAL | PK | | | §E |
| project_id | BIGINT | NOT NULL | | FK `projects(id)` ON DELETE CASCADE | §E |
| name | TEXT | NOT NULL | | LEN(100) | §E |
| created_at | TIMESTAMPTZ | NOT NULL | `now()` | | §E |

Constraint: `UNIQUE(project_id, name)`. Tests: T34, T38.

### 4.9 `failure_reviews` (CR-03)
| Column | Type | Null | Default | Check / rule | Origin |
|---|---|---|---|---|---|
| id | BIGSERIAL | PK | | | §E |
| project_id | BIGINT | NOT NULL | | FK `projects(id)` ON DELETE CASCADE | §E |
| trace_id | TEXT | NOT NULL | | HEX32. Soft reference, validated on insert | §E |
| span_id | TEXT | NULL | | HEX16 | ADD: related-span selection required by the Phase 4 prompt |
| issue_type | TEXT | NOT NULL | | IN (`retrieval_missing`,`chunk_boundary`,`prompt_instruction`,`generation_mismatch`,`schema_violation`,`unknown`) | CR-03 (was `category`) |
| evidence | TEXT | NOT NULL | | LEN(4000); redaction applied | §E |
| hypothesis | TEXT | NOT NULL | | LEN(2000); redaction applied | §E |
| review_status | TEXT | NOT NULL | `'open'` | IN (`open`,`in_review`,`closed`) | CR-03 (was `status`) |
| reviewer_decision | TEXT | NOT NULL | `'pending'` | IN (`pending`,`hypothesis_supported`,`hypothesis_rejected`,`inconclusive`) | §E (values defined by CR-03) |
| created_by | TEXT | NOT NULL | | LEN(100). Self-declared: there is no user auth in the MVP | ADD: accountability |
| created_at | TIMESTAMPTZ | NOT NULL | `now()` | | §E |
| updated_at | TIMESTAMPTZ | NOT NULL | `now()` | | ADD: status-change audit |

**Table check:** `review_status <> 'closed' OR reviewer_decision <> 'pending'`.

**Constraints:**
- `INDEX(project_id, trace_id)`.
- Partial `UNIQUE(project_id, trace_id, issue_type) WHERE review_status <> 'closed'`. A duplicate open review gets `409`.

`hypothesis_supported` means the evidence supports the hypothesis; **it is not proof of root cause**.

Tests: T33, T41.

### 4.10 `test_cases`
| Column | Type | Null | Default | Check / rule | Origin |
|---|---|---|---|---|---|
| id | BIGSERIAL | PK | | | §E |
| dataset_id | BIGINT | NOT NULL | | FK `test_datasets(id)` ON DELETE CASCADE; INDEX | §E |
| origin_trace_id | TEXT | NULL | | HEX32. Soft reference (lineage survives retention) | §E |
| origin_review_id | BIGINT | NULL | | FK `failure_reviews(id)` ON DELETE SET NULL | ADD: ADR-12 lineage review → case |
| sanitized_input | TEXT | NOT NULL | | LEN(8000); redaction is applied before insert | §E |
| expected_output | JSONB | NULL | | | §E |
| rule | JSONB | NOT NULL | | `rule->>'type' IN ('json_schema','field_match','exact','regex')` | §E |
| redaction_applied | BOOLEAN | NOT NULL | | Must be `true` on insert via the API | ADD: auditable redaction (T35) |
| approved_by | TEXT | NOT NULL | | LEN(100) | §E |
| approved_at | TIMESTAMPTZ | NOT NULL | | | §E |
| created_at | TIMESTAMPTZ | NOT NULL | `now()` | | ADD: audit |

No row exists without approval. The API rejects `approved != true` with `422` (T34).
Tests: T34, T35, T40.

### 4.11 `experiments`
| Column | Type | Null | Default | Check / rule | Origin |
|---|---|---|---|---|---|
| id | BIGSERIAL | PK | | | §E |
| dataset_id | BIGINT | NOT NULL | | FK `test_datasets(id)` ON DELETE CASCADE; INDEX | §E |
| prompt_version_a | TEXT | NOT NULL | | LEN(64) | §E |
| prompt_version_b | TEXT | NOT NULL | | LEN(64); ≠ `prompt_version_a` | §E |
| model | TEXT | NOT NULL | | LEN(256) | §E |
| mode | TEXT | NOT NULL | | IN (`real`,`mock`) | §E |
| budget_usd | NUMERIC(20,10) | NULL | | > 0; `mode = 'mock' OR budget_usd IS NOT NULL` | ADD: real-spend guard |
| started_at | TIMESTAMPTZ | NOT NULL | `now()` | | §E |
| finished_at | TIMESTAMPTZ | NULL | | ≥ `started_at` | §E |

Tests: T38, T39.

### 4.12 `experiment_results`
| Column | Type | Null | Default | Check / rule | Origin |
|---|---|---|---|---|---|
| id | BIGSERIAL | PK | | | ADD: internal surrogate key |
| experiment_id | BIGINT | NOT NULL | | FK `experiments(id)` ON DELETE CASCADE | §E |
| test_case_id | BIGINT | NOT NULL | | FK `test_cases(id)` ON DELETE CASCADE | §E |
| prompt_version | TEXT | NOT NULL | | LEN(64) | §E |
| passed | BOOLEAN | NULL | | NULL means "not evaluated" | §E |
| evaluation_detail | TEXT | NULL | | ≤ 1000; the rule failure reason | ADD: T37 "fails with a reason" |
| duration_ms | INT | NULL | | ≥0 | §E |
| input_tokens | BIGINT | NULL | | ≥0 | §E |
| output_tokens | BIGINT | NULL | | ≥0 | §E |
| cost_usd | NUMERIC(20,10) | NULL | | ≥0 | §E |
| cost_status | TEXT | NOT NULL | | IN (`computed`,`unknown`) | ADD: same unknown-cost rule as usage |
| cost_unknown_reason | TEXT | NULL | | IN (`price_missing`,`usage_missing`,`usage_inconsistent`) | ADD: explains unknown cost, as in §4.6 |
| provenance | TEXT | NOT NULL | | IN (`real`,`synthetic`). `mode = mock` ⇒ `synthetic` (service rule, T39) | §E |
| created_at | TIMESTAMPTZ | NOT NULL | `now()` | | ADD: audit |

**Table checks:** the same two cost checks as `usage_records`.
**Constraint:** `UNIQUE(experiment_id, test_case_id, prompt_version)`.
Tests: T36–T40, T48.

### 4.13 Index summary

| Index | Origin |
|---|---|
| `spans(project_id, trace_id, start_ns)` | §E |
| `traces(project_id, started_at DESC)` | §E |
| `usage_records(project_id, created_at, provider, response_model)` | §E |
| `usage_records(project_id, started_at)` | ADD |
| `usage_records(project_id, call_id) WHERE call_id IS NOT NULL` | ADD |
| `project_api_keys(prefix)` (unique) | ADD |
| `failure_reviews(project_id, trace_id)` | ADD |

The `UNIQUE` constraints listed per table are also indexes. A JSONB GIN index is added only for a measured need.

**Migration tests:** T31 (upgrade → downgrade → upgrade on a disposable DB). The Phase 1 migration creates §4.2–4.6. §4.7 comes in Phase 2, and §4.8–4.12 in Phase 4. Each migration must match this contract column for column.

## 5. API contract

The backend binds to `127.0.0.1`.
- `/v1/traces` uses `X-Project-Key` (`TELEMETRY_CONTRACT.md`).
- Every `/api/v1/*` call requires `Authorization: Bearer <LLMI_ADMIN_TOKEN>` (constant-time compare; `401` otherwise) and a `project` (id) parameter.
- A record belonging to another project returns `404` (T28).
- `/api/v1` is rate limited per token and client address (`429`).

| Endpoint | Method | Contract | Phase |
|---|---|---|---|
| `/health/live` | GET | `200 {"status":"live"}`, no DB access, not traced | 1 |
| `/health/ready` | GET | `200` if DB `SELECT 1` succeeds within 1 s, else `503`; not traced | 1 |
| `/v1/traces` | POST | See `TELEMETRY_CONTRACT.md` | 1 |
| `/api/v1/traces` | GET | Params `project`, `from`, `to` (ISO-8601 UTC, max 31-day window), `model`, `status`, `provenance`, `limit` (default 50, max 200), `cursor`. Returns `{items, next_cursor}`, newest first | 1 (stub) / 3 |
| `/api/v1/traces/{trace_id}` | GET | Span tree (parent-child + orphans), usage rows, linked reviews | 1 (stub) / 3 |
| `/api/v1/overview` | GET | KPIs and trends (`METRICS_CATALOG.md`), each with `n` and a provenance split | 2 (service) / 3 |
| `/api/v1/models/compare` | GET | Per provider/model/prompt_version totals, `cost_status` counts, sample N | 3 |
| `/api/v1/failure-reviews` | POST | Body `{trace_id, span_id?, issue_type, evidence, hypothesis, created_by}` → `201`; duplicate open review → `409`; `PATCH /{id}` for status/decision | 4 |
| `/api/v1/datasets/{dataset_id}/cases` | POST | Requires `approved: true`, `approved_by` and sanitized input, otherwise `422` with nothing stored | 4 |
| `/api/v1/experiments` | POST | `{dataset_id, prompt_version_a, prompt_version_b, model, mode, budget_usd?}`. `mode=real` requires `budget_usd` and `LLMI_ALLOW_REAL_LLM=true` | 4 |
| `/api/v1/experiments/{id}` | GET | Per-version pass/fail, N, p50/p95 duration, cost with unknown count, provenance label per result | 4 |

`/api/v1/*` error body: `{"error": {"code": "...", "message": "..."}}` (JSON). Validation errors return `422`. Responses never include keys, tokens or stack traces.

## 6. Version pins (candidates; CR-09)

Data comes from PyPI, npm and endoflife.date on 2026-10-24. Each candidate is the newest stable release published **on or before 2026-10-10**, so it is at least two weeks old. Lockfiles are produced when a component is first implemented, with versions re-checked then. Nothing is installed in Phase 0.

| Area | Pin | Local status / note |
|---|---|---|
| Python | 3.12.x (Docker `python:3.12-slim`) | 3.12.6 installed. SQLAlchemy 2.1 and pydantic-settings need ≥ 3.11 |
| Node.js | 22 LTS (EOL 2027-04-30) | 22.11.0 installed. Next 16 needs ≥ 20.9. Update to the latest 22.x patch is recommended |
| PostgreSQL | 17 (Docker `postgres:17`, EOL 2029-11) | Conservative choice over 18 |
| Backend | fastapi 0.143.0 · uvicorn 0.54.0 · pydantic 2.14.0 · pydantic-settings 2.15.0 · sqlalchemy 2.1.4 · alembic 1.20.0 · psycopg 3.3.6 | |
| OTel / protos | opentelemetry-api / -sdk / -proto / -exporter-otlp-proto-http 1.45.1 · protobuf 7.36.2 (proto requires `>=5,<8`) · googleapis-common-protos 1.75.5 (`google.rpc.Status` / `BadRequest`; requires protobuf `>=6.33.5,<8`) · opentelemetry-instrumentation-fastapi 0.66b1 (beta line, demo only) | |
| Provider | openai 3.28.0 | SDK adapter and demo only |
| Eval / test | jsonschema 4.26.0 · pytest 9.1.1 · httpx 0.28.1 · ruff 0.17.0 | |
| Frontend | next 16.4.0 · react / react-dom 19.3.0 · tailwindcss 4.3.3 · recharts 3.10.1 · @playwright/test 1.64.0 · eslint 10.12.0 | |
| TypeScript | **UNKNOWN, decided in Phase 3.** Options: 5.9.3, 6.0.3 or 7.0.2 | Next 16 compatibility with TS 7 (native port) not verified |

## 7. Repository layout (target)

This follows master plan §G. Folders are created only by the phase that fills them, with no empty layers.

| Phase | Creates |
|---|---|
| 0 | `docs/` (incl. `fixtures/`, `reports/`), `scripts/verify_phase0.py`, `.gitignore`, `.env.example` |
| 1 | `backend/` (`pyproject.toml`, `alembic/`, `app/{main,api/otel,core,models,repositories,services/ingestion}`, `tests/`), `docker-compose.yml`, `scripts/dev` |
| 2 | `sdk/`, `demo_app/`, `backend/app/services/{pricing,metrics}.py`, `scripts/seed`, `scripts/smoke` |
| 3 | `frontend/`, `backend/app/api/{overview,traces}.py` (full) |
| 4 | `backend/app/api/{reviews,datasets,experiments}.py`, `services/evaluation.py` |
| 5 | CI workflow, `README.md`, `docs/FINAL_DELIVERY_REPORT.md` |

## 8. Deployment posture

Local-only. Compose publishes the backend as `127.0.0.1:8000` and the frontend as `127.0.0.1:3000`; PostgreSQL is not published, or only on `127.0.0.1` in dev. Any public deployment requires a separate auth and threat review (`SECURITY.md` §7). There is no claim of production or billing-grade reliability.
