# LLM Insight — Architecture, Data & API Contracts

**Status:** binding contract (Phase 0 closed by ADR-13…21, 2026-10-24). Implementation state per phase: `PHASE_STATUS.md`. Decisions are in `ADR.md`, wire details in `TELEMETRY_CONTRACT.md`, security in `SECURITY.md`.

## 1. Components

| Component | Tech | Responsibility | Phase |
|---|---|---|---|
| `backend/` | FastAPI modular monolith, SQLAlchemy 2, Alembic, PostgreSQL | OTLP ingest, query API, pricing/ledger, trace diagnostics (4A, derived, not persisted), reviews, datasets, experiments | 1–4 |
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

**Phase 4B implementation of §4.8–4.10 (migration `0003`, no column change).** The three tables are created column for column (`tests/integration/test_migrations.py` parses this section). §4.11–4.12 are left to Phase 4C. Notes, none of which adds a column, enum value or relationship:
- `ck_test_cases_rule_type_enum` is written `COALESCE(rule->>'type', '') IN (…)`: the bare `rule->>'type' IN (…)` is NULL for a rule without a type (or a non-object rule), and a CHECK accepts NULL. This is the contract's evident meaning, not a new rule.
- The `rule` payload per type (decision D1, `reports/phase-4b.md` §2.4) is enforced by the API only: `{"type":"json_schema","schema":<object|boolean>}` (local `$ref` only), `{"type":"field_match","fields":[dotted paths]}` (compared with `expected_output`), `{"type":"exact"}`, `{"type":"regex","pattern":<1–1000 chars, compiles>}`; unknown keys are refused. JSON Schema meta-validation is a Phase 4C item.
- At most one test case per (dataset, review) (decision D5) is enforced in the insert transaction under `pg_advisory_xact_lock`, not by a new unique index.
- Downgrade of `0003` refuses to run while any review, dataset or test case exists (human work that nothing else could restore). Upgrade leaves every Phase 1–3 row unchanged (tested with data, and on the dev database by row counts and content checksums).

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
| `/api/v1/traces/{trace_id}/diagnostics` | GET | Read-only, derived on request: deterministic, metadata-only findings (`llminsight.diagnostics.v1`, §5.6). Same auth, project scope, `404` and bounds as the detail; nothing persisted | 4A |
| `/api/v1/overview` | GET | KPIs and trends (`METRICS_CATALOG.md`), each with `n` and a provenance split | 2 (backend, done in 2C, §5.2) / 3 (UI) |
| `/api/v1/models/compare` | GET | Per provider/model/prompt_version totals, `cost_status` counts, sample N | 2C (backend, §5.2; M15) / 3 (UI) |
| `/api/v1/projects` | GET | Bounded project listing for the dashboard selector: `{items[{id, name, created_at}], truncated, limit}`, `limit` 1–200 (default 100); no `project` parameter | 3C (§5.5) |
| `/api/v1/failure-reviews` | POST | Body `{trace_id, span_id?, issue_type, evidence, hypothesis, created_by}` → `201`; duplicate open review → `409`; `PATCH /{id}` for status/decision | 4 (done in 4B, §5.7) |
| `/api/v1/datasets/{dataset_id}/cases` | POST | Requires `approved: true`, `approved_by` and sanitized input, otherwise `422` with nothing stored | 4 (done in 4B, §5.7) |
| `/api/v1/datasets` | GET, POST | Additive (decision D2): list a project's datasets with case counts (`limit` 1–200) / create one `{name}` → `201`, duplicate name → `409` | 4B (§5.7) |
| `/api/v1/datasets/{dataset_id}/cases` | GET | Additive (D2): the dataset and its cases by id, keyset `after`, `limit` 1–200 | 4B (§5.7) |
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
- `reviews` was always `[]` until the Phase 4 table existed. **Since Phase 4B** it holds the trace's human failure reviews (oldest first, at most 200, plus `reviews_truncated`), each with its `test_cases` links `{id, dataset_id, dataset_name}` (§5.7).
- **Phase 4B (additive):** every list item carries `reviews: {total, not_closed}` from one grouped statement over the page's trace ids (`ix_failure_reviews_project_id_trace_id`), so reviewed and unreviewed traces can be told apart. The list statement, cursor and window are unchanged.

**Timestamps (T26):** every `*_at` value is ISO-8601 UTC with `Z`. Trace and span values are rendered from the stored nanoseconds with all 9 fraction digits. The raw nanoseconds are decimal strings (`*_unix_nano`, `duration_ns`, as in OTLP/JSON), because JSON numbers lose precision above 2^53.

**Storage access:** every request runs in one `REPEATABLE READ, READ ONLY` transaction, so the read API cannot write and the detail queries see one snapshot. SQL is built from SQLAlchemy expressions with bound parameters. Index use was verified with `EXPLAIN ANALYZE` on 10,000 traces per project (`reports/phase-1c.md` §4):
- the list is a range scan on `traces(project_id, started_at DESC)` that stops after one page;
- the per-trace usage summary and the `model` filter are lookups on `usage_records UNIQUE(project_id, trace_id, span_id)`;
- detail spans use `spans(project_id, trace_id, start_ns)`.

No new index was needed. Responses carry `Cache-Control: no-store` and no CORS headers, so browser JavaScript on another origin cannot read them.

**Phase 3C addition (R40, additive):** `usage.models` on every list item and on the detail's `trace`: `{items: [{provider, requested_model, response_model, calls}], distinct, truncated}`. Every distinct combination of the trace's usage rows with its row count, largest first (ties by provider, then models with `NULL` first), at most 10 (`MAX_MODELS_PER_TRACE`); `distinct` counts all combinations and `truncated` is `distinct > len(items)`. `NULL` models stay `null`. It comes from one extra grouped statement per page over the page's trace ids (`trace_models_stmt`: `GROUP BY` + `row_number()`/`count()` windows), resolved through `uq_usage_records_project_id_trace_id_span_id` with no sequential scan (EXPLAIN-tested on 10,000 traces), in the same read-only snapshot; the list statement, its plan, the cursor and the window are unchanged. A trace without inference has `{items: [], distinct: 0, truncated: false}`.

### 5.2 Phase 2C implementation of the aggregate endpoints (`backend/app/api/overview.py`)

**Access and storage** are those of §5.1: `require_admin` (auth-failure bucket, bearer token, `/api/v1` bucket) runs before any parameter is validated or any query runs; the mandatory `project` id scopes every query; an unknown project is the §5.1 `404`, byte-identical across the three read endpoints; one `REPEATABLE READ, READ ONLY` transaction per request; SQL from SQLAlchemy expressions with bound parameters; `Cache-Control: no-store`.

**Parameters (both):** `project`; `from` / `to` exactly as for `GET /api/v1/traces` (UTC only, `[from, to)` on **event time**, non-empty, ≤ 31 days, default the last 31 days, echoed as `window`); `model` (exact `response_model` or `requested_model`, ≤ 256); `status` (`ok` | `error`); `prompt_version` (≤ 64); `provenance` (`real` | `synthetic` | `mixed` | `unknown`). Each filter applies at the metric's own level: trace-level metrics use `traces.status`, `traces.prompt_version`, `traces.provenance` and "any usage row with that model"; usage-level metrics use the inference span's status (`error` = `ERROR`), its `gen_ai.prompt.version`, `usage_records.provenance` and the row's models. No usage row is `mixed`, so `provenance=mixed` leaves the usage-level population empty (`n = 0`). The filters are echoed as `filters`. `models/compare` adds `limit` (1–200, default 50 groups). Invalid values are `422 invalid_request` naming the parameter, never echoing it.

**`GET /api/v1/overview`** → `{project, window, filters, provenance_label, provenance_verified: false, traces, inference, trend}`:
- `traces.count` (M01), `traces.error_rate` (M05), `traces.latency` (M07, root spans; `traces_without_root` counts the excluded traces);
- `inference.attempts` (M02), `logical_calls` (M03; `value` is `null` when no attempt has a `call_id`), `errors` (M04, with `by_error_type`, at most 50), `latency` (M06), `tokens` (M08 with the M09 `subsets`), `cost` (M10–M12), `price_versions` (M13, at most 200), `usage_source` (M14), `time_to_first_chunk` (`available: false`, not recorded);
- every metric object is `{total, by_provenance}` with the buckets `real` / `synthetic` / `unknown` (plus `mixed` for trace-level metrics), all present; `total` is computed by PostgreSQL over all rows (`GROUP BY ROLLUP(provenance)`), never summed from buckets;
- `trend`: hourly buckets for windows ≤ 2 days, otherwise daily, aligned to UTC (`date_bin` from the epoch): traces, error traces, attempts, failed attempts, known cost, unknown-cost calls. Buckets with no data have zero counts and a `null` known cost.

**`GET /api/v1/models/compare`** (M15) → `{project, window, filters, provenance_label, provenance_verified, groups, groups_truncated, limit}`. One group per (`provider`, `requested_model`, `response_model`, `prompt_version`) with attempts, errors, latency, cost and the provenance counts, ordered by attempts (descending) then the keys; at most `limit` groups, `groups_truncated` when more exist. This is a bounded top-N, not a paginated list. **Phase 3C (additive):** each group also has `tokens` (the overview's M08/M09 block) and `usage_source` (M14 counts).

**Values:** counts are true counts; unknown values are `null` (rates without a denominator, percentiles without a sample, token sums with no reported value, known cost without a computed row, `cost_complete` with no row). Money is a decimal string (exact `Decimal` sums of `NUMERIC(20,10)`; M12 rounded once, half-even, to 10 places). Rates are JSON numbers rounded to 6 places and always come with numerator and denominator. Latency is in milliseconds (6 decimals) from `percentile_cont` over `duration_ns`, with `n` and `low_sample` (p95: n < 20, p99: n < 100). No response contains a trace, span, call or response ID, a key or any telemetry content.

**Index use** (EXPLAIN on 36,000 generated usage rows, one-day window; `tests/integration/test_metrics_plan.py`): usage-level queries range-scan `ix_usage_records_project_id_started_at` and reach each span through a spans index on `(project_id, trace_id, …)`; trace-level queries use `ix_traces_project_id_started_at`; no sequential scan. No new index was needed.

### 5.3 Phase 3A dashboard access to the read API (`frontend/lib/server/`)

The dashboard is the only consumer of `/api/v1` and adds no endpoint; the backend contract is unchanged.
- **Server-side only.** Next.js server components call `lib/server/backend.ts`, the one module that sends `Authorization: Bearer <LLMI_API_TOKEN>`. Every `lib/server/*` module imports `server-only`; there are no route handlers and no Server Functions, so the browser cannot reach the backend through the dashboard (no generic proxy). **Phase 4B amendment (§5.8):** four narrow Server Actions now write through four allowlisted endpoints; there are still no route handlers and no generic proxy. The token is never in a `NEXT_PUBLIC_*` variable, HTML, RSC payload, client bundle, cookie or browser storage (T42).
- **Requests.** Fixed paths (`/api/v1/overview`, `/api/v1/models/compare?limit=10`), the configured `project`, and `from`/`to` from a validated preset (6 h, 24 h, 7 d, 31 d; UTC; aligned to the trend bucket so every bucket is full width), plus an optional validated `provenance`. Unsupported URL values fall back to defaults with a notice and are never forwarded. `cache: "no-store"`, `redirect: "error"`, a deadline (`LLMI_API_TIMEOUT_MS`, default 5 s), a 2 MiB response cap, and a loopback-only base URL.
- **Responses.** Each body is decoded against the §5.2 schema field by field (`frontend/lib/api/schema.ts`); a mismatch rejects the whole response (`malformed`) instead of rendering a guess. Failures map to fixed kinds (`config`, `unreachable`, `timeout`, `unauthorized`, `not_found`, `invalid_request`, `rate_limited`, `storage_unavailable`, `server_error`, `unexpected_status`, `malformed`) with fixed copy; no backend body, URL or credential is shown or logged. The compare request degrades its own section only. `GET /health/ready` (no token) feeds the header status.
- **Presentation.** Values are the backend's: no metric is recomputed (the only arithmetic is `attempts − failed` for a stacked bar, the backend's own `succeeded` definition). Money stays a decimal string formatted with BigInt arithmetic; `null` is shown as unknown / not reported / no data, never 0. Provenance uses the backend buckets with text labels; synthetic data gets a banner; "real" is shown as an instrumentation claim.
- **Request guard (`frontend/proxy.ts`).** Bound to `127.0.0.1:3001`; requests whose `Host` is not a loopback name get `421` (DNS-rebinding defence); methods other than GET/HEAD get `405` (Phase 4B: `POST` is accepted only for Server Actions on the form pages, with the origin checks of §5.8); each response carries a per-request nonce CSP plus `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, COOP/CORP.

### 5.4 Phase 3B trace explorer access (`frontend/lib/server/traces.ts`)

Uses the §5.1 endpoints exactly as implemented; no backend change, no new field.
- **Paths.** `/api/v1/traces` and `/api/v1/traces/{trace_id}`. `backend.ts` checks every path against a runtime allowlist (`overview`, `models/compare`, `traces`, `traces/<32 lowercase hex>`) before any request; the detail path is built only from a validated id. Trace ids are otherwise opaque (never parsed, shortened or logged; failure logs show `/api/v1/traces/{trace_id}`).
- **List parameters.** Only §5.1 filters: `from`/`to` (a preset resolved like the Overview, or a fixed UTC window ≤ 31 days with ≤ 6 fraction digits), `status`, `provenance`, `model` (exact; restricted to the stored model-identifier format, so other values are refused instead of forwarded), `limit` ∈ {10, 25, 50, 100}, the opaque `cursor`. Unsupported values fall back to defaults with a notice. The next-page link carries the backend-echoed window plus `next_cursor`, so every page of a listing is cut from one window. **Contract gap (not closed, no backend change):** list items carry no provider/model fields and the API has no provider or service filter, so the list shows none; models and providers appear on the detail page.
- **Responses.** Parsed with source-text access (`lib/json.ts`): an integer above 2^53 (a JSONB attribute or a BIGINT token count) keeps its exact digits or is rejected, never rounded. Decoded field by field (`lib/api/trace-schema.ts`) including the ledger checks (`cost_usd` present exactly when `computed`, a reason exactly when `unknown`), root ⇔ no parent, end ≥ start; a mismatch rejects the response. Detail responses may be up to 8 MiB (2000 spans + 2000 usage rows); other reads keep 2 MiB.
- **Presentation.** Nanoseconds stay strings and BigInt (`lib/nanos.ts`); timestamps are printed from the backend's 9-digit ISO strings without a `Date` round trip; waterfall offsets are BigInt ratios. The tree (`lib/span-tree.ts`) is built from `parent_span_id` among the returned spans, ordered by (start ns, span id); several roots, orphans, spans whose parent was cut by the 2000-span limit, and client-made parent cycles are shown at the top level and labeled. Physical attempts are grouped by `llminsight.call_id`; trace status is the stored one (§4.4: `error` if any span is ERROR), and recovery after a failed attempt is shown beside it, not instead of it. `error.type` is shown as recorded; no cause is derived. The waterfall renders 200 rows at a time.

### 5.5 Phase 3C: project listing, project context, model comparison (`backend/app/api/projects.py`, `frontend/lib/server/{context,models}.ts`)

**`GET /api/v1/projects`:** `require_admin` first (auth-failure bucket, bearer token, `/api/v1` bucket), then `limit` (1–200, default 100; `422` naming `limit` otherwise). One read-only snapshot; `SELECT id, name, created_at FROM projects ORDER BY id LIMIT limit + 1` (the bound is in SQL, tested); `truncated` when more exist. No key prefix, hash, count or telemetry is returned; names are returned as stored (1–100 characters, admin-chosen) and rendered as text by the dashboard. `Cache-Control: no-store`. Other methods get the JSON `405`. No schema change; the single admin token already reads every project (CR-06), so the listing adds no permission.

**Dashboard project context.** `?project=<id>` on every page (validated positive BIGINT), else `LLMI_DASHBOARD_PROJECT_ID`. Invalid → `invalid_project` state, no request, no substitution; unknown → the backend's `404`. `getJson(config, path, scope, params, …)` takes an explicit scope: project paths require a valid id, `/api/v1/projects` requires none, and a `project` key inside `params` is refused. `requireProject` rejects overview/compare bodies that echo another project. Each render also fetches the listing (selector); a failed listing degrades the selector to one line. The selector is a GET form (full navigation) that carries only filters and window (never cursor, page or span); from a trace detail it targets `/traces` of the chosen project. The side navigation carries an explicit project.

**Model comparison (`/models`).** `GET /api/v1/models/compare` with a window (preset or custom UTC ≤ 31 days), `provenance`, attempt `status`, `limit` ∈ {10, 25, 50, 100, 200}. Backend order, no ranking; group-level invariants checked by the decoder (failed + succeeded = attempts, computed + unknown = attempts, `cost_complete` ⇔ no unknown, known cost ⇔ a priced call, token `n` = attempts).

**Custom UTC ranges** on Overview, Models and Traces: form fields `from_utc`/`to_utc` (UTC by name), link fields `from`/`to` (ISO `Z`, ≤ 6 fraction digits); checked at microsecond precision against the 31-day maximum; refused ranges fall back to the default preset with the reason. On the Overview, a custom range's first and last trend buckets may be partial (stated on the page).

### 5.6 Phase 4A: trace diagnostics (`backend/app/api/diagnostics.py`, `backend/app/services/diagnostics/`)

**Status:** additive read-only endpoint, added by the Phase 4A user instruction (2026-10-25). No table, column, migration or index is added: results are derived on request and never persisted (ADR-21 unchanged). The endpoint is **not** part of the Failure Review contract (§4.9, CR-03): it writes nothing to `failure_reviews`, and its categories are a separate vocabulary from `issue_type`. It does not implement or satisfy T33–T40.

**`GET /api/v1/traces/{trace_id}/diagnostics?project=<id>`.** Access is exactly that of the trace detail (§5.1): `require_admin` before any parameter is validated; `trace_id` 32 lowercase hex, else `422`; `project` required; an unknown project, an unknown trace and another project's trace are one byte-identical `404`; one `REPEATABLE READ, READ ONLY` snapshot; `Cache-Control: no-store`; the `/api/v1` rate limit.
- **Work bound:** the detail's own statements (`get_trace`, `get_spans`, `get_usage`: at most 2000 spans by start time and 2000 usage rows), which were EXPLAIN-tested in Phase 1C. Four statements per request whatever the trace size (no per-span query, integration-tested). The rule pass is linear in the analysed rows and runs in a worker thread.
- **Output bound:** at most 100 findings (catalogue order), 25 evidence spans per finding, 20 values per list fact; totals are always reported (`findings_total`, `evidence_total`, `*_count`).

**Contract `llminsight.diagnostics.v1`, ruleset `llminsight.diagnostics.rules.v1`.** A schema change needs `diagnostics.v2`; a change of rule semantics or text needs `rules.v2`.

```text
{contract_version, ruleset_version,
 method: {kind: "deterministic_rules", language_model_used: false, content_used: false, inputs},
 trace: {trace_id, status, provenance, provenance_verified: false, span_count},
 coverage: {complete, spans_stored, spans_analyzed, spans_truncated,
            usage_rows_analyzed, usage_truncated, limitations[{code, text}]},
 summary: {inference_attempts, failed_inference_attempts, logical_calls|null,
           attempts_without_call_id, error_spans, usage_rows, cost_computed,
           cost_unknown{price_missing, usage_missing, usage_inconsistent}, findings_by_category},
 findings[{id, category, group, subject{type: trace|logical_call|span, ...}, trigger,
           observed_facts[{name, value}], evidence[...], evidence_total, evidence_truncated,
           hypothesis{code, statement, error_interpretation{code, statement}|null, verified: false},
           established[], not_established[], limitations[{code, text}], data_complete,
           investigation_steps[]}],
 findings_total, findings_truncated,
 not_assessed[{category, reason}]}
```

- **Evidence item:** `span_id`, `parent_span_id`, `role`, `kind`, `status`, `error_type`, `start_time_unix_nano`, `duration_ns`, `provenance`, `provenance_verified: false`, `server_dropped_attributes_count`, plus `inference` (operation, provider, models, `call_id`, `attempt`, `has_response_id`, `finish_reasons`, `request_max_tokens`), `usage` (`usage_source`, token fields, `cost_status`, `cost_unknown_reason`) and `validation` (`rule`, `passed`), each `null` when absent. These are stored, allowlisted fields only: no span name, no raw attribute object, no response ID value, no `cost_usd` (an unknown cost is never rendered, so it can never appear as `0`).
- **Text:** every `trigger`, `statement`, `established`, `not_established`, limitation text and investigation step is a constant of the catalogue (`catalog.py`). No stored value is interpolated into prose; client metadata appears only as facts and evidence fields.
- **Hypothesis:** always `verified: false`, always phrased "Possible explanation (not verified)"; no score, probability or confidence. `error_interpretation` applies only to failed **inference** attempts and only to the exact `error.type` identifiers in the catalogue table (HTTP `429`/`4xx`/`5xx` status codes, `timeout`, and the OpenAI Python SDK classes `openai.RateLimitError`, `openai.InternalServerError`, `openai.APITimeoutError`, `openai.APIConnectionError`); everything else is `error_type_not_interpreted` or `error_type_not_recorded`. A 5xx is never presented as a provider outage.
- **Order and determinism:** findings by catalogue order, then first evidence span (start ns, span id), then subject; evidence by (start ns, span id). The engine is pure: the same stored rows give byte-identical responses, whatever the ingest order.
- **Absence rule:** a finding that rests on missing data (`no_root_span`, `usage_ledger_row_missing`) is suppressed when the analysis was truncated; every other finding is marked `data_complete: false` with the limitation `analysis_truncated`.

**Rule catalogue** (inputs are stored columns of §4.4–4.6 only; full trigger, limitation and false-positive/negative notes in `reports/phase-4a.md` §4):

| Category | Group | Fires when (stored data) |
|---|---|---|
| `inference_call_failed_after_retries` | reliability | ≥ 2 attempts of one `llminsight.call_id`, all `status = ERROR` |
| `inference_call_failed` | reliability | the only stored attempt of a call is ERROR, or an ERROR attempt without a usable call ID |
| `inference_call_recovered_after_retry` | reliability | a call with an ERROR attempt whose highest-numbered attempt is not ERROR |
| `non_inference_span_error` | reliability | a non-inference span is ERROR (with or without failed inference in its subtree) |
| `application_validation_failed` | application_signal | `llminsight.validation.passed` is boolean false |
| `inference_output_truncated` / `inference_content_filtered` | provider_signal | an `openai` attempt records finish reason `length` / `content_filter` |
| `token_usage_missing` | usage_and_cost | a non-ERROR attempt's usage row has NULL `input_tokens`, or NULL `output_tokens` (not embeddings) |
| `usage_ledger_row_missing` | usage_and_cost | an inference span has no usage row (call-identity collision) |
| `usage_inconsistent` | usage_and_cost | `cost_unknown_reason = usage_inconsistent` |
| `cost_unknown_price_missing` | usage_and_cost | `cost_unknown_reason = price_missing` |
| `orphan_spans`, `multiple_root_spans`, `no_root_span`, `parent_cycle` | trace_structure | stored parent links: missing parent, ≥ 2 roots, no root (complete analysis only), a link cycle |
| `retry_sequence_incomplete` | metadata_integrity | a call's attempt numbers are not 1..n without gaps |
| `attempt_metadata_inconsistent` | metadata_integrity | duplicate attempt number, unusable number, failure after success, attempt without call ID, unusable call ID |
| `attributes_dropped_at_ingest` | metadata_integrity | `server_dropped_attributes_count > 0` |

**Not assessed** (listed in every response with the reason): elevated latency (no approved comparison basis), semantic quality, prompt quality, provider outage, root cause.

**Frontend:** none in Phase 4A. **Phase 4B** added the read to the dashboard's path allowlist and shows the findings on the Trace Detail page as a separate, labelled "Machine observations (automatic, unverified, not a human review)" panel with evidence-span links; the panel has no form and never fills in or decides a review (§5.8).

### 5.7 Phase 4B: failure reviews, datasets and test cases (`backend/app/api/{reviews,datasets,human_input,json_body}.py`)

**Status:** implements the §5 contract rows for `POST /api/v1/failure-reviews`, `PATCH /api/v1/failure-reviews/{id}` and `POST /api/v1/datasets/{id}/cases`, plus the additive dataset endpoints (decision D2). Schema: §4.8–4.10 (migration `0003`). Decisions D1–D9 are recorded in `reports/phase-4b.md` §2.4.

**Access:** as §5.1: `require_admin` before anything else, mandatory `project`, one indistinguishable `404` for an unknown project, trace, review or dataset or one of another project, no `403`, the `/api/v1` rate limit, `Cache-Control: no-store`.

**Bodies (D7):** `Content-Type: application/json` only (`415 unsupported_media_type`), at most 64 KiB (`413 payload_too_large`), strict UTF-8 JSON without NaN/Infinity or duplicate keys, strict types, unknown fields refused; `422 invalid_request` names the fields and never echoes values.

**Writes:** one read-write transaction per request that commits only when everything succeeded; storage errors map to `503`/`500` as for reads. Error codes added: `409 duplicate_open_review`, `409 duplicate_dataset`, `409 duplicate_test_case`, `409 review_not_decided`.

- `POST /failure-reviews`: the trace must exist in the project (`404` otherwise); a `span_id` must be a span of that trace (`422`); `issue_type` is one of the six CR-03 values (`422`); `evidence`/`hypothesis` are masked by the server (`services/redaction.py`) and length-checked after masking; `created_by` is a single-line label without contact data or secrets. Stored `open`/`pending`. Response: the review plus `redaction: {applied: true, masked: {evidence, hypothesis}}` (counts per category).
- `PATCH /failure-reviews/{id}` (D3): `review_status` and/or `reviewer_decision`; the result must satisfy the §4.9 table check (`422`); reopening into a duplicate open review is `409`; text is immutable; an unchanged request writes nothing. Who changed it is not recorded (no column, D8).
- `POST /datasets/{id}/cases`: `approved` must be the JSON boolean `true` (T34); `approved_by` label; `sanitized_input` masked before insert and `redaction_applied = true` (T35); `expected_output` and `rule` are bounded JSON (16 KiB, depth 32) whose strings carrying credentials, personal data or invisible characters are **refused** rather than masked (D9); `rule` per D1. A trace-origin case needs both `origin_trace_id` and `origin_review_id` (D4): the trace and review must be in the project and match, and the review's decision must not be `pending` (`409 review_not_decided`). At most one case per (dataset, review) (D5, `409 duplicate_test_case`). `approved_at` is the server time.
- `GET /datasets`, `POST /datasets`, `GET /datasets/{id}/cases`: as in the §5 table; cases are returned exactly as stored, ordered by id (byte-identical repeated reads).

Nothing derives a review or a case from telemetry or diagnostics: these handlers are the only writers of `failure_reviews` and `test_cases` (static test `tests/unit/test_phase4b_boundaries.py`). Nothing evaluates a case (Phase 4C).

### 5.8 Phase 4B: dashboard writes (`frontend/lib/actions/`, `frontend/proxy.ts`)

This amends §5.3 ("no route handlers and no Server Functions") for the Phase 4B instruction, which requires a genuine human review workflow in the dashboard.
- **Server Actions, narrowly.** Exactly two `"use server"` modules (`lib/actions/reviews.ts`, `lib/actions/datasets.ts`) with four exported actions (`submitReview`, `submitDecision`, `submitDataset`, `submitTestCase`). Each re-validates its untrusted `FormData` (`lib/review-forms.ts`), then calls one `lib/server` function that sends one allowlisted write (`sendJson` in `lib/server/backend.ts`: `POST /api/v1/failure-reviews`, `PATCH /api/v1/failure-reviews/<id>`, `POST /api/v1/datasets`, `POST /api/v1/datasets/<id>/cases`, each with exactly one method). The token stays server-side (CR-06). There are still no route handlers and no generic proxy; static tests pin all of this.
- **CSRF.** The dashboard has no cookie or session, so the checks concern where a request comes from. `proxy.ts` allows `POST` only on the three pages that host forms (`/traces/<id>`, `/traces/<id>/reviews/<id>/test-case`, `/datasets`) and only when `Origin` is present and equals the loopback host, `Sec-Fetch-Site` (when sent) is `same-origin`, and the `Next-Action` header is present. A cross-site HTML form cannot send a header, and a cross-site script would need a CORS preflight that is never answered. Everything else is `403` (or `405` for other paths/methods). Next.js additionally compares `Origin` with `Host` for every Server Action; `serverActions.bodySizeLimit` is 128 KB; no extra allowed origins. Forms therefore need JavaScript.
- **Explicit actions only.** No write runs on render or page load; submit buttons are disabled while a submission is pending; a refused submission keeps the typed values, and the form is cleared only after a stored success. A timeout after sending is reported as "outcome unknown", never as "nothing stored".
- **Presentation.** Review text, test-case content and diagnostics are rendered as React text only (T41 rules). The human review section and the machine-observation panel are visually distinct and labelled; the review form starts empty, with no preselected issue type, and the test-case form starts empty with the approval box unticked.

## 6. Version pins (candidates; CR-09)

Data comes from PyPI, npm and endoflife.date on 2026-10-24. Each candidate is the newest stable release published **on or before 2026-10-10**, so it is at least two weeks old. Lockfiles are produced when a component is first implemented, with versions re-checked then. Nothing is installed in Phase 0.

| Area | Pin | Local status / note |
|---|---|---|
| Python | 3.12.x (Docker `python:3.12-slim`) | 3.12.6 installed. SQLAlchemy 2.1 and pydantic-settings need ≥ 3.11 |
| Node.js | 22 LTS (EOL 2027-04-30) | **22.23.4** for the frontend since Phase 3A, project-scoped in `.tools/` (`scripts/dev/install_node.py`); the global 22.11.0 is unchanged. Next 16 needs ≥ 20.9 |
| PostgreSQL | 17 (Docker `postgres:17`, EOL 2029-11) | Conservative choice over 18 |
| Backend | fastapi 0.143.0 · uvicorn 0.54.0 · pydantic 2.14.0 · pydantic-settings 2.15.0 · sqlalchemy 2.1.4 · alembic 1.20.0 · psycopg 3.3.6 | |
| OTel / protos | opentelemetry-api / -sdk / -proto / -exporter-otlp-proto-http 1.45.1 · protobuf 7.36.2 (proto requires `>=5,<8`) · googleapis-common-protos 1.75.5 (`google.rpc.Status` / `BadRequest`; requires protobuf `>=6.33.5,<8`) · opentelemetry-instrumentation-fastapi 0.66b1 (beta line, demo only) | |
| Provider | openai 3.28.0 | SDK adapter and demo only |
| Eval / test | jsonschema 4.26.0 · pytest 9.1.1 · httpx 0.28.1 · ruff 0.17.0 | |
| Frontend | next **16.4.3** · react / react-dom / react-is **19.3.1** · tailwindcss 4.3.3 · recharts 3.10.1 · @playwright/test 1.64.0 · eslint **9.39.5** | Installed in Phase 3A (below). 16.4.0 and 19.3.0 were replaced for security fixes; ESLint 10 is blocked by plugin peer ranges |
| TypeScript | **6.0.3** (decided in Phase 3A, R07) | `typescript-eslint` 8.x supports `<6.1.0`, which rules out 7.x |

**Phase 1A install (2026-10-24):** the backend runtime and dev pins above were re-checked on PyPI and installed into `backend/.venv`. The OTel/protobuf, provider and eval pins are not installed until the phase that uses them. Transitive versions are frozen in `backend/requirements.lock`. Three transitive packages were held below their newest release to keep the two-week rule: `starlette` 1.7.0 (1.10.0 was released 2026-10-24), `certifi` 2026.7.22 and `pluggy` 1.6.0. `opentelemetry-api` 1.45.1 arrives transitively through `fastapi` 0.143.0.

**Phase 1B install (2026-10-24):** the OTel/protobuf pins above were re-checked on PyPI and installed into `backend/.venv`. Runtime: `opentelemetry-proto` 1.45.1, `protobuf` 7.36.2, `googleapis-common-protos` 1.75.5. Dev only, for the standard-exporter round trip (T01-S3): `opentelemetry-sdk` and `opentelemetry-exporter-otlp-proto-http` 1.45.1. Their transitives (`opentelemetry-semantic-conventions`, `-exporter-otlp-proto-common` / `-otlp-common` / `-http-transport` 0.66b1 or 1.45.1, `requests` 2.34.2, `urllib3` 2.8.0, `charset-normalizer` 3.5.2) were all first uploaded on or before 2026-10-10. No existing pin changed; `pip check` is clean.

**Phase 2A install (2026-10-24):** a separate `sdk/.venv` (Python 3.12.6). SDK runtime: `opentelemetry-api` / `-sdk` / `-exporter-otlp-proto-http` 1.45.1, exactly pinned in `sdk/pyproject.toml` and installed with `backend/requirements.lock` as the constraint file, so every shared transitive has the backend's version. SDK dev: `pytest` 9.1.1, `ruff` 0.17.0, `psycopg[binary]` 3.3.6 (live-backend tests), plus the new type checker `mypy` 2.4.0 (2026-10-01) and its transitives `mypy_extensions` 1.1.0, `pathspec` 1.1.1 and `librt` 0.16.0. `ast_serialize` is held at 0.12.1 (2026-10-03), because 0.13.0/0.13.1 (2026-10-12/17) are newer than the two-week rule allows; mypy accepts `>=0.11.2,<1.0.0`. The build backend is pinned to `setuptools==84.0.0` (2026-08-08). Frozen in `sdk/requirements-dev.lock`; `pip check` is clean. The backend lock is unchanged.

**Phase 2B install (2026-10-24, UTC):** `sdk/.venv` only. `openai` 3.28.0 (uploaded 2026-10-09) as the optional SDK extra `openai` and a dev dependency, with its new transitives `httpx2` / `httpcore2` 2.13.1 (2026-09-23), `jiter` 0.17.0 (2026-09-12), `sniffio` 1.3.1 and `truststore` 0.10.4 (held: 0.10.5 is newer than the two-week rule allows); `pydantic` 2.14.0, `anyio` 4.15.1, `h11` 0.16.0 and the rest come from `backend/requirements.lock` as the constraint file. Note: `openai` 3.x uses `httpx2`, not `httpx`. Frozen in `sdk/requirements-dev.lock`; `pip check` is clean. The backend gets no new dependency, and its lock is unchanged.

**Phase 2C install (2026-10-24, UTC):** `sdk/.venv` only, for the FastAPI demo (`demo_app/web.py`): `fastapi` 0.143.0, `uvicorn` 0.54.0 and their transitives `starlette` 1.7.0, `click` 8.5.0, `annotated-doc` 0.0.5, all **identical** to `backend/requirements.lock` (installed with it as the constraint file), so no new version enters the project. Added to the SDK `dev` extra and `sdk/requirements-dev.lock` (48 pins); `pip check` is clean. The backend gained no dependency.

**Phase 3A install (2026-10-25, UTC):** `frontend/` only, with Node 22.23.4 / npm 10.9.9 from `.tools/` (official zip, SHA-256 checked against `SHASUMS256.txt`). Exact pins in `frontend/package.json`, lockfile `frontend/package-lock.json` (lockfileVersion 3), `npm ci` reproducible, `npm audit` 0 vulnerabilities, `npm ls` clean. Dependencies: `next` 16.4.3, `react`/`react-dom`/`react-is` 19.3.1, `recharts` 3.10.1, `server-only` 0.0.1. Dev: `typescript` 6.0.3, `eslint` 9.39.5, `eslint-config-next` 16.4.3, `tailwindcss` and `@tailwindcss/postcss` 4.3.3, `@playwright/test` 1.64.0, `@types/node` 22.20.5, `@types/react`/`@types/react-dom` 19.3.0. Exceptions to the two-week rule, each with a reason: Next.js 16.4.3 (2026-10-23; 16.4.1 fixed two critical unauthenticated RCE advisories in 16.4.0, and 16.4.3 is the current backport line, requested by the user), React 19.3.1 (2026-10-14; RSC transport hardening released with that fix), Node 22.23.4 (2026-10-20 security release, requested by the user), `zod` 4.6.6 (2026-10-11; first release fixing GHSA-mrhx-c7xc-m3w5, a dev-only transitive of `eslint-plugin-react-hooks`). Every other transitive package newer than 2026-10-10 is held by `overrides` at its newest earlier release (`typescript-eslint` family 8.71.1, `browserslist` 4.29.3 with its data packages, `@babel/*`, `immer`, `es-toolkit`, `ignore`, `napi-postinstall`, and `postcss` 8.5.29 under `@tailwindcss/postcss`; Next keeps its exact `postcss` 8.5.23). Vitest was evaluated and dropped (npm 10.9.9 crashes resolving its optional peer cycle with Vite 8); unit tests run on `node:test` after `tsc`, with no extra dependency. The backend and SDK gained no dependency.

## 7. Repository layout (target)

This follows master plan §G. Folders are created only by the phase that fills them, with no empty layers.

| Phase | Creates |
|---|---|
| 0 | `docs/` (incl. `fixtures/`, `reports/`), `scripts/verify_phase0.py`, `.gitignore`, `.env.example` |
| 1 | `backend/` (`pyproject.toml`, `alembic/`, `app/{main,api/otel,core,models,repositories,services/ingestion}`, `tests/`), `docker-compose.yml`, `scripts/dev` |
| 2 | `sdk/`, `demo_app/`, `backend/app/services/{pricing,metrics}.py`, `scripts/seed`, `scripts/smoke`. Phase 2A created `sdk/` (src layout: `sdk/src/llm_insight_sdk/`, so tests import the installed package, not the source tree) and `demo_app/` (a CLI demo; the FastAPI root span is still to come). Phase 2B added `backend/app/services/{pricing,pricing_catalog}.py`, `backend/app/repositories/pricing.py`, `backend/app/models/pricing.py`, migration `0002` and `sdk/src/llm_insight_sdk/openai_adapter.py`; the seed is a CLI command (`app.cli seed-prices`), so no `scripts/seed` was needed. Phase 2C added `backend/app/{repositories,services}/metrics.py`, `backend/app/api/overview.py` (the backend half of the Phase 3 row below), `backend/app/core/eventloop.py` and `demo_app/web.py` (the FastAPI demo root span); no `scripts/smoke` (the real smoke run is not authorized) |
| 3 | `frontend/`, `backend/app/api/{overview,traces}.py` (full). Phase 3A created `frontend/` (`app/`, `components/`, `lib/{api,server}/`, `tests/{unit,e2e,fixtures,setup}/`, `scripts/`; see `frontend/README.md`) plus `scripts/dev/{install_node,init_frontend_env}.py`; no backend change. Phase 3B added `frontend/app/traces/`, `frontend/components/traces/`, the trace modules in `frontend/lib/` and `scripts/dev/seed_trace_fixtures.py`; no backend change. Phase 3C added `backend/app/{api,repositories}/projects.py` (additive endpoint), `frontend/app/models/`, `frontend/components/models/`, `scripts/dev/e2e_fixtures.py`, `backend/Dockerfile`, `frontend/Dockerfile`, `.dockerignore` files, the compose `app` profile and `.github/workflows/ci.yml` |
| 4 | `backend/app/api/{reviews,datasets,experiments}.py`, `services/evaluation.py`. Phase 4A added `backend/app/{api,services}/diagnostics`. Phase 4B added `backend/app/api/{reviews,datasets,human_input,json_body}.py`, `backend/app/repositories/{reviews,datasets}.py`, `backend/app/services/{redaction,case_rules}.py`, `backend/app/models/{review,dataset}.py`, migration `0003`; `frontend/app/datasets/`, `frontend/app/traces/[traceId]/reviews/`, `frontend/components/{reviews,datasets}/`, `frontend/lib/actions/`, `frontend/lib/server/{reviews,datasets,review-page}.ts`, `frontend/lib/api/{review-types,review-schema,diagnostics,ids}.ts`, `frontend/lib/{review-forms,action-state}.ts`. `experiments.py` and `services/evaluation.py` remain Phase 4C |
| 5 | `README.md`, `docs/FINAL_DELIVERY_REPORT.md` (the CI workflow `.github/workflows/ci.yml` was added early, in Phase 3C) |

## 8. Deployment posture

Local-only. The backend runs on the host as `127.0.0.1:8000` (`python -m app`); the frontend runs on `127.0.0.1:3001` (Phase 3A; `3000` is taken on the dev machine by another service, R19, and is never used). **Phase 3C compose `app` profile** (opt-in, `docker compose --profile app up -d --build`; the default `docker compose up` still starts PostgreSQL only): `backend` and `frontend` containers share one network namespace (`network_mode: service:backend`), so the backend keeps its loopback-only bind and the dashboard its loopback-only backend URL; only the dashboard is published, on `127.0.0.1:3001`; the backend is not published (host ingest still uses `python -m app`, R43); each container receives only its listed variables; the backend container runs `alembic upgrade head` before starting; PostgreSQL is not published, or only on `127.0.0.1` in dev (Phase 1A: `127.0.0.1:55432`, `POSTGRES_HOST_PORT`, because a native PostgreSQL already listens on 5432 on the dev machine; R19). Any public deployment requires a separate auth and threat review (`SECURITY.md` §7). There is no claim of production or billing-grade reliability.

**Event loop (Phase 2C, R32):** the backend always runs on asyncio's selector loop (`app/core/eventloop.py`, passed to uvicorn by `python -m app`). On Windows uvicorn's default Proactor loop closes its listening socket after a client resets a connection before it is accepted (CPython 3.12). The selector loop's `select()` limits a Windows process to 512 sockets (R34), which suits the local single-user MVP only.
