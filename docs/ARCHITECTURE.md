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
| status_message | TEXT | NULL | | ≤ 256. Since Phase 1C ingest never writes it: always NULL (R21) | ADD: OTLP Status.message for diagnosis |
| error_type | TEXT | NULL | | ≤ 256; an identifier only (`TELEMETRY_CONTRACT.md` §3) | ADD: semconv `error.type` (Stable) |
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

**Phase 1C note (no column change, no migration):** `status_message` keeps its type and check so the schema matches migration `0001`, but ingest no longer writes it (R21: free text cannot be made safe by scrubbing). The read API never returns it. Removing the column would need a migration plus an ADR note (ADR-21) and was not necessary.

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

**Phase 2B note (migration `0002`, columns exactly as above):**
- **Validity:** a row is valid from `effective_from` until the next `effective_from` of the same `(provider, model)`; `UNIQUE(provider, model, effective_from)` leaves no ambiguous overlap. A row is a standard-tier text price; tiers, regional endpoints and long-context rates are not columns, so such calls are `price_missing` (`PRICING.md` §3). Representing them would need a new column, a migration and an ADR note (ADR-21).
- **Extra checks** beyond the table (stricter, no column change): `provider` and `model` must match the ingest identifier formats of `TELEMETRY_CONTRACT.md` §3.1, so a model name can never be a wildcard (`ck_model_prices_provider_format`, `ck_model_prices_model_format`).
- **Append-only:** trigger function `model_prices_append_only()` rejects `UPDATE`, `DELETE` and `TRUNCATE … CASCADE`, and rejects an `INSERT` when the same `(provider, model)` already has a later `effective_from` (no back-dating). Inserts per `(provider, model)` take a transaction advisory lock. `alembic check` does not compare triggers; `tests/integration/test_migrations.py` asserts them.
- **Downgrade** of `0002` refuses to run while any `usage_records.model_price_id` is set.
- **Seeding:** only the verified catalogue (`backend/app/services/pricing_catalog.py`, `PRICING.md` §2) through `python -m app.cli seed-prices`, which never updates a stored row.

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

**Migration sequencing (Phase 1A note, no column change):** `usage_records.model_price_id` references `model_prices`, which does not exist before Phase 2. The Phase 1 migration (`0001`) therefore creates the column without its FK. The Phase 2 migration that creates §4.7 adds `FK model_prices(id) ON DELETE RESTRICT`. Until then no row can be `computed` (no price exists), and the `cost_computed_has_price` check already holds. **Done in Phase 2B:** migration `0002` creates §4.7 and adds `fk_usage_records_model_prices` (`ON DELETE RESTRICT`); existing rows are unchanged (their `model_price_id` is `NULL`), verified by an upgrade-with-data test.

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

### 5.1 Phase 1C implementation of the trace endpoints (`backend/app/api/traces.py`)

**Access, in this order, before any parameter is validated** (so an unauthenticated caller learns nothing about parameters or records):
1. The auth-failure bucket per client address, shared with `/v1/traces` (`LLMI_RATE_LIMIT_AUTHFAIL_PER_MIN`) → `429 rate_limited` + `Retry-After`.
2. Exactly one `Authorization: Bearer <LLMI_ADMIN_TOKEN>` header. The scheme is case-insensitive. The token is compared as SHA-256 digests with `hmac.compare_digest`. Missing, multiple, malformed and wrong headers all get one identical `401 unauthorized` with `WWW-Authenticate: Bearer`, and each one consumes from step 1. An ingest key is not a read credential.
3. The `/api/v1` bucket (`LLMI_RATE_LIMIT_API_*`) keyed by client address. There is a single verified token, so this is per token and address → `429 rate_limited` + `Retry-After`.

**No `403`** (confirmed by the user at the Phase 1 closure). The single admin token (CR-06) reads every project, so there is no authenticated-but-forbidden state. A `403` for another project's trace would confirm that the trace exists. An unknown project, an unknown trace and another project's trace all return the same `404 not_found`, with identical body and headers (T28).

**Error codes:** `unauthorized` (401), `not_found` (404), `method_not_allowed` (405), `invalid_request` (422, with `fields`: the offending parameter *names*, never their values), `rate_limited` (429), `internal_error` (500), `storage_unavailable` (503 + `Retry-After: 5`). Unknown `/api/*` routes use the same shape.

**`GET /api/v1/traces`:**
- `project` (required, ≥ 1).
- `from` / `to`: ISO-8601 with an explicit UTC offset (`Z` or `+00:00`, URL-encoded as `%2B00:00`). Naive timestamps, other offsets, epoch numbers and dates without a time get `422`. The window is `[from, to)` on `traces.started_at` (event time). It must be non-empty and ≤ 31 days. Defaults: `to` = now, `from` = `to` − 31 days, so the window is always bounded. The effective window is echoed as `window`.
- `model`: exact match on `response_model` **or** `requested_model` of any usage row of the trace (≤ 256 chars).
- `status`: `ok` | `error`. `provenance`: `real` | `synthetic` | `mixed` | `unknown`.
- `limit` 1–200 (default 50). `cursor`: opaque, from `next_cursor`. Malformed cursors get `422`.
- Order: `started_at DESC, trace_id DESC`. Keyset pagination on that pair: pages are disjoint and complete for a fixed dataset, and newer traces arriving between pages do not shift later pages. Known limit: a trace's `started_at` can move earlier when a late span arrives (the trace row is derived), so such a trace may appear on a different page than before.
- Response: `{items, next_cursor, window}`. Each item has `trace_id`, `service_name`, `started_at`/`ended_at`, `start_time_unix_nano`/`end_time_unix_nano`/`duration_ns`, `status`, `prompt_version`, `provenance`, `provenance_verified` (always `false`), `span_count`, and a `usage` summary: `inference_calls`, `input_tokens`/`output_tokens` (sums of known values; `null` when none is known), `calls_with_unknown_tokens`, `known_cost_usd` (decimal string; `null` when no row has a computed cost, never `"0"`), `unknown_cost_calls`.

**`GET /api/v1/traces/{trace_id}`** (`trace_id`: 32 lowercase hex, else `422`): `{trace, spans, spans_truncated, usage, usage_truncated, reviews}`.
- `trace` is the list item.
- `spans` is a flat list ordered by `start_ns, span_id`. Each span has a `role` (`root`: no parent; `child`: its parent is stored; `orphan`: its parent is not stored, T27) and its `children` span IDs, plus the stored span fields **except** `status_message` and internal ids.
- At most 2000 spans and 2000 usage rows are returned (`*_truncated: true` beyond that). Roles are computed against all stored spans, so truncation never mislabels an orphan.
- `usage` holds the usage rows: tokens stay `null` when unknown; `cost_usd` is a decimal string or `null`, with `cost_status`/`cost_unknown_reason`.
- `reviews` is always `[]` until the Phase 4 table exists.

**Timestamps (T26):** every `*_at` value is ISO-8601 UTC with `Z`. Trace and span values are rendered from the stored nanoseconds with all 9 fraction digits. The raw nanoseconds are decimal strings (`*_unix_nano`, `duration_ns`, as in OTLP/JSON), because JSON numbers lose precision above 2^53.

**Storage access:** every request runs in one `REPEATABLE READ, READ ONLY` transaction, so the read API cannot write and the detail queries see one snapshot. SQL is built from SQLAlchemy expressions with bound parameters. Index use was verified with `EXPLAIN ANALYZE` on 10,000 traces per project (`reports/phase-1c.md` §4):
- the list is a range scan on `traces(project_id, started_at DESC)` that stops after one page;
- the per-trace usage summary and the `model` filter are lookups on `usage_records UNIQUE(project_id, trace_id, span_id)`;
- detail spans use `spans(project_id, trace_id, start_ns)`.

No new index was needed. Responses carry `Cache-Control: no-store` and no CORS headers, so browser JavaScript on another origin cannot read them.

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

**Phase 1A install (2026-10-24):** the backend runtime and dev pins above were re-checked on PyPI and installed into `backend/.venv`. The OTel/protobuf, provider and eval pins are not installed until the phase that uses them. Transitive versions are frozen in `backend/requirements.lock`. Three transitive packages were held below their newest release to keep the two-week rule: `starlette` 1.7.0 (1.10.0 was released 2026-10-24), `certifi` 2026.7.22 and `pluggy` 1.6.0. `opentelemetry-api` 1.45.1 arrives transitively through `fastapi` 0.143.0.

**Phase 1B install (2026-10-24):** the OTel/protobuf pins above were re-checked on PyPI and installed into `backend/.venv`. Runtime: `opentelemetry-proto` 1.45.1, `protobuf` 7.36.2, `googleapis-common-protos` 1.75.5. Dev only, for the standard-exporter round trip (T01-S3): `opentelemetry-sdk` and `opentelemetry-exporter-otlp-proto-http` 1.45.1. Their transitives (`opentelemetry-semantic-conventions`, `-exporter-otlp-proto-common` / `-otlp-common` / `-http-transport` 0.66b1 or 1.45.1, `requests` 2.34.2, `urllib3` 2.8.0, `charset-normalizer` 3.5.2) were all first uploaded on or before 2026-10-10. No existing pin changed; `pip check` is clean.

**Phase 2A install (2026-10-24):** a separate `sdk/.venv` (Python 3.12.6). SDK runtime: `opentelemetry-api` / `-sdk` / `-exporter-otlp-proto-http` 1.45.1, exactly pinned in `sdk/pyproject.toml` and installed with `backend/requirements.lock` as the constraint file, so every shared transitive has the backend's version. SDK dev: `pytest` 9.1.1, `ruff` 0.17.0, `psycopg[binary]` 3.3.6 (live-backend tests), plus the new type checker `mypy` 2.4.0 (2026-10-01) and its transitives `mypy_extensions` 1.1.0, `pathspec` 1.1.1 and `librt` 0.16.0. `ast_serialize` is held at 0.12.1 (2026-10-03), because 0.13.0/0.13.1 (2026-10-12/17) are newer than the two-week rule allows; mypy accepts `>=0.11.2,<1.0.0`. The build backend is pinned to `setuptools==84.0.0` (2026-08-08). Frozen in `sdk/requirements-dev.lock`; `pip check` is clean. The backend lock is unchanged.

**Phase 2B install (2026-10-24, UTC):** `sdk/.venv` only. `openai` 3.28.0 (uploaded 2026-10-09) as the optional SDK extra `openai` and a dev dependency, with its new transitives `httpx2` / `httpcore2` 2.13.1 (2026-09-23), `jiter` 0.17.0 (2026-09-12), `sniffio` 1.3.1 and `truststore` 0.10.4 (held: 0.10.5 is newer than the two-week rule allows); `pydantic` 2.14.0, `anyio` 4.15.1, `h11` 0.16.0 and the rest come from `backend/requirements.lock` as the constraint file. Note: `openai` 3.x uses `httpx2`, not `httpx`. Frozen in `sdk/requirements-dev.lock`; `pip check` is clean. The backend gets no new dependency, and its lock is unchanged.

## 7. Repository layout (target)

This follows master plan §G. Folders are created only by the phase that fills them, with no empty layers.

| Phase | Creates |
|---|---|
| 0 | `docs/` (incl. `fixtures/`, `reports/`), `scripts/verify_phase0.py`, `.gitignore`, `.env.example` |
| 1 | `backend/` (`pyproject.toml`, `alembic/`, `app/{main,api/otel,core,models,repositories,services/ingestion}`, `tests/`), `docker-compose.yml`, `scripts/dev` |
| 2 | `sdk/`, `demo_app/`, `backend/app/services/{pricing,metrics}.py`, `scripts/seed`, `scripts/smoke`. Phase 2A created `sdk/` (src layout: `sdk/src/llm_insight_sdk/`, so tests import the installed package, not the source tree) and `demo_app/` (a CLI demo; the FastAPI root span is still to come). Phase 2B added `backend/app/services/{pricing,pricing_catalog}.py`, `backend/app/repositories/pricing.py`, `backend/app/models/pricing.py`, migration `0002` and `sdk/src/llm_insight_sdk/openai_adapter.py`; the seed is a CLI command (`app.cli seed-prices`), so no `scripts/seed` was needed. `metrics.py` (aggregates) is still to come |
| 3 | `frontend/`, `backend/app/api/{overview,traces}.py` (full) |
| 4 | `backend/app/api/{reviews,datasets,experiments}.py`, `services/evaluation.py` |
| 5 | CI workflow, `README.md`, `docs/FINAL_DELIVERY_REPORT.md` |

## 8. Deployment posture

Local-only. Compose publishes the backend as `127.0.0.1:8000` and the frontend as `127.0.0.1:3000`; PostgreSQL is not published, or only on `127.0.0.1` in dev (Phase 1A: `127.0.0.1:55432`, `POSTGRES_HOST_PORT`, because a native PostgreSQL already listens on 5432 on the dev machine; R19). Any public deployment requires a separate auth and threat review (`SECURITY.md` §7). There is no claim of production or billing-grade reliability.
