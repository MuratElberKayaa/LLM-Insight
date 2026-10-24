# Phase 1C — Delivery Report (ingestion hardening, trace read API, Phase 1 acceptance)

- **Date:** 2026-10-24
- **Baseline:** Phase 1B commit `9a09e44` (on 1A `f17d3c4` and Phase 0 closure `0929a14`). Clean tree before work; baseline full suite 374 passed / 0 failed / 0 skipped.
- **Commit:** one project-only commit, recorded in the Phase 1C Completion Report message (a commit cannot contain its own hash; `git log -1 --grep "Phase 1C"`). The `phase-0` tag was not moved and **no `phase-1` tag was created**, as instructed.
- **Scope executed:** Phase 1C Tasks A–F only. Not built, as instructed: the telemetry SDK package, OpenAI calls, a cost calculator or price catalogue, the frontend, the Optimization Lab, Redis, ClickHouse and Kubernetes. No paid API was called; all telemetry used is **synthetic**. No migration was added.

## 1. Implementation summary

| Task | What changed |
|---|---|
| A. Normalization | `gen_ai.request.max_tokens` is stored as an int ≥ 0. An explicit credential classifier replaces the substring deny rule: credential stems anywhere, credential words such as singular `token`, and `tokens`/`key` after a credential qualifier. No allowlisted key, alias or semconv token counter is classified as a credential. `gen_ai.prompt.version` is a bounded label (≤ 64, `[A-Za-z0-9][A-Za-z0-9._:+/-]*`), dropped and never truncated otherwise. `error.type` is an identifier. `server_dropped_attributes_count` saturates at the `INT` limit. Provenance only ever downgrades |
| B. R21 | `Status.message` is no longer carried past normalization or written (`spans.status_message` is always `NULL`; schema unchanged). `exception.type` is used only when it is an identifier. The best-effort scrubber was removed |
| C. R22 | `read_bounded` enforces one overall deadline (`LLMI_OTLP_BODY_READ_TIMEOUT_S`, default 10 s, 0 < x ≤ 300) after authentication. Timeout → `408`; a client disconnect → value-free `400` (`INCOMPLETE_BODY`, not delivered); both close the body stream, and `408`/`413` add `Connection: close`. Cancellation propagates. Nothing touches the DB before the body is complete |
| D. Read API | `GET /api/v1/traces` and `GET /api/v1/traces/{trace_id}` (`ARCHITECTURE.md` §5.1): admin bearer token checked before parameter validation; auth-failure bucket shared with ingest plus the `/api/v1` bucket; mandatory `project`; one indistinguishable `404`; keyset pagination; validated filters and a bounded 31-day window; ns-exact ISO `Z` timestamps; unknown cost as `null`; `provenance_verified: false`; read-only `REPEATABLE READ` transactions; no `status_message`; no CORS; `Cache-Control: no-store` |
| E. Integrity | Re-exports whose content differs from the stored span, or from an earlier duplicate in the same batch, are counted (`span_content_conflicts`) and logged as counts; the first write is kept. Plain re-exports are counted too (`spans_reexported`). In `resp:{provider}:{id}` the provider is percent-encoded for `%` and `:`, so keys are unambiguous |

## 2. Gap-fill decisions and ADR review (step 4, done before any change)

Each `reports/phase-1b.md` §7 item and each 1C task was compared with ADR-01…21 and CR-01…09. **No contradiction required stopping.** No ADR text was changed, and no migration was needed.

| Item | Decision applied | ADR check |
|---|---|---|
| 1B §7.1 `max_tokens` vs. the `token` deny rule | Task A1–A3: keep the counter; classify credentials explicitly | The substring rule lived in `TELEMETRY_CONTRACT.md` §3, not in ADR-19 (whose list is the content attributes, all still denied). Contract text updated. The deny-before-allowlist rule (SECURITY §1) still holds and is tested with a broken allowlist |
| 1B §7.2 all-unknown trace | `unknown`; real/synthetic mixed with anything else → `mixed` | Contract §5 table gained an explicit row (the literal "any other combination" would have said `mixed`). Decided by the user in the 1C instruction; ADR-21 is unchanged |
| 1B §7.3 empty name | `unknown_span` display fallback | Unchanged; documented in contract §2 |
| 1B §7.4 IDs | Never truncated; wrong length → `400` | Matches §1.3 |
| 1B §7.5 `usage_source` | Unchanged; contract wording clarified (the server never estimates; a client claim is stored) | Matches the §4.6 enum |
| 1B §7.6 reason precedence | Unchanged (`usage_inconsistent` > `usage_missing` > `price_missing`) | ADR-11 |
| 1B §7.7 `prompt_version` | Plus a bound and a pattern | Contract §3 edit; consistent with the Phase 4 `LEN(64)` columns |
| Mapping version | Kept `llminsight.attrmap.v1`, finalized at this gate | ADR-14 requires a bump for a new **alias** (none added); ADR-15 for a semconv pin change (none). The dev DB held **0 rows** when checked, so no stored row has a different v1 meaning. Any change after the `phase-1` tag → `attrmap.v2` |
| Task B column | Kept, always `NULL` | ADR-21: removing a column needs a migration plus an ADR note; not necessary |
| Task C `408` | New control | ADR-17's "every exceeded limit returns 413" lists the three size/count limits; a time limit is not among them, and `408` is the HTTP code for it. **Flagged for your review** as an interpretation |
| Task D `403` | Not emitted | CR-06: one admin token reads all projects, so there is no authenticated-but-forbidden state. A `403` on another project's trace would leak its existence (T28 requires `404`) |
| Task E key escaping | Provider percent-encoded in `resp:` keys | ADR-21 precedence unchanged; identical keys for every provider name without `%` or `:` |

Also verified (step 1): git root = the project folder; `main`; clean tree at `9a09e44`; `phase-0` → `a16d64b`; dev DB `0001 (head)`; PostgreSQL `healthy` / `accepting connections`. The unrelated `C:/Users/PC` repository and the running containers were not touched.

**Semconv audit (Task A2):** the pinned `semantic-conventions-genai@9ffc7e00c639/docs/gen-ai/client-inference.md`, re-read on 2026-10-24, defines `gen_ai.request.max_tokens`, the five usage counters exactly as v1 names them, and modality breakdowns (`gen_ai.usage.{text,audio,image}.*_tokens`) that v1 does not allowlist. They are dropped by the allowlist and counted, never by the credential rule. Adding them is a Phase 2 / ADR-15 decision.

## 3. Tests, commands and results

### Final gate run (2026-10-24, from `backend/` in `.venv` unless noted)

| # | Command | Outcome |
|---|---|---|
| 1 | `git rev-parse --show-toplevel` / `git status` | project folder / only the files in §6 changed |
| 2 | `docker compose config --quiet` (root) / `docker inspect … llminsight-postgres-1` | exit 0 / `healthy` |
| 3 | Migration cycle on disposable `llminsight_gate1c_test`: `upgrade head` → `downgrade base` → `upgrade head` → `alembic check` | 5 tables + `alembic_version` → `alembic_version` only → 5 + `alembic_version`; `No new upgrade operations detected.`; DB dropped |
| 4 | `alembic current` / `alembic check` (dev DB) | `0001 (head)` / `No new upgrade operations detected.`, exit 0 |
| 5 | `LLMI_TEST_REQUIRE_DB=1 python -m pytest tests/unit` | **350 passed**, 0 failed, 0 skipped, 1 warning |
| 6 | `LLMI_TEST_REQUIRE_DB=1 python -m pytest tests/integration` | **276 passed**, 0 failed, 0 skipped, 1 warning |
| 7 | `LLMI_TEST_REQUIRE_DB=1 python -m pytest -rs` (full) | **626 passed, 0 failed, 0 skipped**, 1 warning (R20) |
| 8 | `ruff check .` / `ruff format --check .` | `All checks passed!` / `64 files already formatted` |
| 9 | `python -m pip check` | `No broken requirements found.` |
| 10 | `python -I scripts/verify_phase0.py --quiet` (root) | `SUMMARY: 683 PASS, 0 FAIL`, exit 0 (681 at 1B; the two new status claims T25 and the new report add checks) |
| 11 | `git diff --check` (root) | exit 0 (only the usual `core.autocrlf` LF→CRLF notices) |
| 12 | Live `python -m app` smoke (§4) | all expectations met |

**Count reconciliation:** 374 at baseline − 6 scrubber tests removed with the scrubber + 1 replacement = 369 existing, plus **257 new** (161 unit, 96 integration) = 626. Three existing tests were changed on purpose: the route-set test (now includes the two API routes and expects `401` without a token), the `max_tokens` test (now kept), and the status-message test (now absent).

New test files: `tests/unit/test_normalization_hardening.py` (153), `tests/unit/test_body_read_deadline.py` (8), `tests/integration/test_normalization_persistence.py` (9), `test_ingest_free_text.py` (3), `test_ingest_slow_client.py` (5), `test_ingest_dedup_integrity.py` (12), `test_trace_api.py` (67).

### Failures found and fixed during the phase (root causes)

- **Edit-tool escaping:** a shell heredoc turned `\1_\2` into control characters in the camelCase splitter, so `accessToken` was not classified. Caught by a spot check before any test; fixed and covered by tests (`accessToken`, `idToken`, `refreshTokens`).
- **Test data, not code:** one adversarial marker (`LONG-MARKER-AAA…`) is identifier-shaped, so its first 200 characters were legitimately kept as an `error.type`. The marker became realistic prose. The underlying fact is recorded as a residual risk (R08, §8).
- **Weak index test:** the first version asserted plan names on an empty table and passed for the wrong reason (the unique key appeared only in the semi-join). It was replaced by `EXPLAIN ANALYZE` on 10,000 traces with fresh statistics (§4).

### Test-matrix mapping (status column updated in `TEST_MATRIX.md`)

**PASS (phase-1c), re-executed in the full run above:**

| Test | Evidence |
|---|---|
| T01 (+S1–S3) | `test_ingest_protocol.py` (commit-before-200 seen from a second connection, empty request), `test_exporter_roundtrip.py` (official `opentelemetry-exporter-otlp-proto-http` 1.45.1, gzip and none, env-var configuration, re-export) |
| T02 | `test_t02_parent_child_tree_reconstructs`, `test_fixture_expected_ingest_result`, `test_detail_of_the_fixture` (API tree: root → 2 children) |
| T03 (+S1, S2) | `test_t03_*`; alias precedence also in `test_a5_standard_name_overrides_every_legacy_alias` (3 aliases × 2 orders) |
| T04 | `test_t04_same_payload_twice…`, `test_concurrent_identical_exports_store_exactly_once` (12 concurrent), `test_e1_identical_reexports_are_idempotent…` |
| T05 | `test_t05_t44_missing_unknown_revoked_keys…` (7 variants), CLI revoke |
| T06 (+S1–S6) | `test_ingest_protocol.py`; plus `test_a8_*` (17/15-byte trace IDs, 9/7-byte span IDs → `400`, nothing truncated) and `test_r21_invalid_utf8_status_message…` |
| T07 | gzip decode and `INVALID_GZIP` tests |
| T08 (+S1–S3) | wire, gzip-bomb and 2049-span `413` tests; `413` now also closes the connection |
| T25 | `test_t25_cursor_pages_are_newest_first_complete_and_disjoint` (7 traces, limit 3 → 3/3/1), `…ties_on_start_time…` (5 equal starts ordered by `trace_id` across pages), `…keyset_is_stable_when_newer_traces_arrive…`, `…limit_default_is_50_and_maximum_200…`, limit 0/201/−5 → `422`, 6 malformed cursors → `422` |
| T26 | `test_t26_api_returns_iso_utc_z_with_all_nanoseconds` (`…10:00:00.123456789Z`, `*_unix_nano` exact), `test_t26_largest_storable_timestamp_round_trips_exactly` (2^63−1 → `2262-04-11T23:47:16.854775807Z`), plus the 1B storage tests |
| T27 | `test_t27_orphan_is_returned_as_orphan_until_its_parent_arrives` (`role: orphan` → `child` after the parent arrives), `test_detail_is_bounded_and_roles_stay_correct_when_truncated`, plus 1B storage tests |
| T28 | `test_t28_other_projects_trace_is_an_indistinguishable_404` (cross-project, missing trace and unknown project: identical status, body **and headers**), `test_t28_list_never_mixes_projects_even_with_identical_trace_ids`, `test_unknown_project_is_404`, `test_e4_conflict_detection_is_project_scoped`, plus 1B ingest isolation |
| T30 (+S1) | `test_ingest_db_failure.py`; API `test_database_outage_is_503_without_details` |
| T31 | `test_migrations.py` plus the CLI cycle (row 3) |
| T44 | `test_t05_t44_*`, `test_t44_success_path…`, `test_tokens_never_appear_in_logs_or_responses` (admin token, a wrong token and the ingest key absent from DEBUG logs and responses), smoke log check (§4) |

**PARTIAL (phase-1c): their owning phase is 2, 3 or 4; 1C added evidence only for the parts below:**

| Test | Added in 1C | Still owed |
|---|---|---|
| T14 | Failed attempt + successful retry → 2 rows (`sdk_attempt`, `provider_response_id`); attempts with no shared identifier stay separate; distinct response IDs under one `call_id` → 2 rows | SDK-generated attempts (2) |
| T16 | Credential classifier (37 credential keys denied, 10 counter/look-alike keys kept), content keys still denied; adversarial free text absent from a full DB dump | SDK never exports content (2) |
| T18 | API returns unknown tokens as `null` and unknown cost as `null` with counts; never `0` | Cost engine (2), UI (3) |
| T23 (+S1, S2) | Re-export adds nothing; same response ID with different tokens keeps the first row (+1 collision); provider namespace cannot collide | "Cost unchanged" once cost exists (2) |
| T43 | All three buckets now exist and are tested: ingest, auth-failure (now shared by ingest and `/api/v1`), and `/api/v1` (`test_api_bucket_returns_429_with_retry_after`) | Dashboard path re-verification (3) |
| T48 (+S1, S2) | `test_a12_*` (7 claim/ID/status cases: a response ID never creates `real`), `test_a6_trace_provenance_unknown_vs_mixed`, API `provenance_verified: false`, provenance filter | Dashboard badges (3) |

Not marked: T15 (SDK export scenario, Phase 2) and every Phase 2–5 test.

## 4. Trace API responses and access isolation (real process)

Real `python -m app` (uvicorn, 1 worker) on `127.0.0.1:8766` against disposable `llminsight_smoke1c_test` (created, migrated, dropped). The key was created with `app.cli create-key`, the admin token read from `.env`, and neither was printed. Read deadline: 2 s for this run.

| Request | Status | Body (abridged) |
|---|---|---|
| `POST /v1/traces` fixture, twice | 200, 200 | empty `ExportTraceServiceResponse` |
| `POST /v1/traces` orphan span with `status.message` "Incorrect API key provided: sk-SMOKE-…" | 200 | empty response |
| `GET /api/v1/traces?project=1` without a token | 401 | `{"error":{"code":"unauthorized","message":"missing or invalid credentials"}}`, `WWW-Authenticate: Bearer` |
| `GET /api/v1/traces?project=1&limit=1&from=…&to=…` | 200 | newest item: the orphan trace, `started_at "2026-10-24T10:00:05.123456789Z"`, `start_time_unix_nano "1792836005123456789"`, `status "error"`, `provenance "unknown"`, `provenance_verified false`, plus `next_cursor` |
| … with that `cursor` | 200 | the fixture trace, `prompt_version "v1"`, `provenance "synthetic"`, `span_count 3` |
| `GET /api/v1/traces/{fixture}?project=1` | 200 | `POST /v1/summarize` root with children `[00f067aa0ba902b7, 53995c3f42cd8ad8]`, both `child`. Usage `resp:openai:chatcmpl-SYNTHETIC-0001`, `input_tokens 1250`, `cost_usd null`, `cost_status unknown`, `price_missing`, `provenance_verified false` |
| `GET /api/v1/traces/{orphan}?project=1` | 200 | `role "orphan"`, `parent_span_id "2222222222222222"`, `status "ERROR"`, `error_type null`, no `status_message` key |
| `GET /api/v1/traces/{fixture}?project=2` (another or unknown project) | 404 | `{"error":{"code":"not_found","message":"resource not found"}}` |
| `GET /api/v1/traces/XYZ?project=1` | 422 | `{"error":{"code":"invalid_request","message":"request parameters are invalid","fields":["trace_id"]}}` |
| `GET /api/v1/traces?project=1&limit=500` | 422 | same shape, `fields ["limit"]`; the value is not echoed |
| Raw socket: valid key, `Content-Length` of the fixture, 10 bytes sent, then silence | **408** after **2.02 s**, then the server closed the connection | `Status{"request body was not received within the time limit"}` |

DB afterwards: `traces=2, spans=4, usage_records=1`; **0** non-null `status_message`. Server log: 20 lines, containing `api auth failed reason=missing client=127.0.0.1` and `ingest body rejected status=408 key_id=1 client=127.0.0.1` plus uvicorn access lines. Checked for the raw key, its secret part, the admin token, the pepper and the DB password: **0 found**; the `sk-SMOKE` status text: **not found**.

**Index use** (`test_list_query_plan_reads_one_page_through_the_indexes`, and a manual run on 2 × 20,000 traces / 80,000 usage rows): with fresh statistics, the list is `Index Scan using ix_traces_project_id_started_at` (index condition: project + window + cursor bound) that stopped after 52 rows for `limit=50`. The usage summary is `Index Scan using uq_usage_records_project_id_trace_id_span_id`, `Index Cond: (project_id = traces.project_id) AND (trace_id = traces.trace_id)`, 52 loops. The `model` filter is a semi-join on the same index. No sequential scan. Execution took 0.6 ms (plain) and 2.0 ms (model + cursor) on the 40,000-trace run. On an empty table the planner picks other paths, which is why the test seeds data and runs `ANALYZE`.

## 5. R21 and R22 evidence

**R21 (status messages): resolved.**
- `test_r21_adversarial_status_messages_never_reach_db_logs_or_api` ingests 13 spans whose status messages, `error.type`, `exception.type`, `exception.message` and `exception.stacktrace` carry:
  - a fake OpenAI key, a Bearer JWT, a fake ingest key and a DSN password;
  - an e-mail address, a Turkish phone number and an IBAN;
  - CRLF multiline text, RTL-override / zero-width, combining characters / emoji / BOM, Turkish letters, a NUL byte, and ~100 kB of prose.
- After ingest:
  - every `status_message` is `NULL` and every `error_type` is `NULL` (descriptions are not identifiers);
  - none of 13 distinctive needles appears in a `row_to_json` dump of all 5 tables, in DEBUG-level logs from every logger, in the trace-detail API response or in the ingest response.
- Invalid UTF-8 in the status message is a `400 INVALID_PROTOBUF` with no echo and nothing written.
- A well-formed `error.type` (`openai.RateLimitError`) is still stored.

**R22 (slow bodies): mitigated for the body phase.**
- Unit level, with deterministic async generators:
  - a stalled body → `408` at the deadline, and the stream is closed;
  - a trickle of one chunk per 50 ms is still cut at the overall deadline (a per-chunk timeout would never fire);
  - the size limit still applies inside the deadline;
  - a declared oversize fails before reading;
  - a disconnect → `INCOMPLETE_BODY` with no echo;
  - cancellation propagates and the stream is closed.
- Live uvicorn with raw sockets:
  - a stall → `408` within [0.4 s, 5 s] and the connection is closed by the server;
  - a trickle of one byte per 50 ms → `408`;
  - a disconnect mid-body → logged, 0 rows, and the next request is `200`;
  - a bad key on a stalled body with a 30 s deadline → `401` in < 3 s (authentication still precedes any body read);
  - 8 concurrent stalled clients all get `408` and service continues.
- No partial DB write is possible, because `process()` (decode + one transaction) runs only after `read_bounded` returns the whole body.

**Documented limitations (not claimed as protected):**
- The deadline covers only the body after authentication.
- uvicorn closes idle keep-alive connections after 5 s, but does not bound how long a client takes to send request headers, and the app sets no connection limit. Connection exhaustion by many slow clients is therefore not prevented (R24).
- If cancellation arrives while `process()` is running in its worker thread, the thread finishes its transaction (commit or rollback is still atomic). The client gets no `200` and retries idempotently.
- The mitigation for everything above is the loopback-only bind; a reverse proxy or uvicorn `limit_concurrency` is required before any non-local exposure.

## 6. Exact files changed

| File | Change |
|---|---|
| `backend/app/services/ingestion/attrmap.py` | Credential classifier (`is_credential_key`), `PROMPT_VERSION` / `ERROR_TYPE` specs with full-match patterns, `max_tokens` as int ≥ 0, scrubber removed |
| `backend/app/services/ingestion/normalize.py` | No `status_message`; identifier-only `exception.type`; saturating drop count; provenance docstring |
| `backend/app/services/ingestion/usage.py` | Provider percent-encoding in `resp:` keys |
| `backend/app/services/ingestion/body.py`, `errors.py` | Overall read deadline, disconnect handling, stream close; `408` / `INCOMPLETE_BODY`; `Connection: close` on body-phase errors |
| `backend/app/services/ingestion/service.py` | `spans_reexported` / `span_content_conflicts` counters and count-only warning; imports the retryable-error tuple from `core.db` |
| `backend/app/repositories/telemetry.py` | Content comparison for re-exports and in-batch duplicates; `status_message` never written |
| `backend/app/core/config.py`, `core/db.py` | `otlp_body_read_timeout_s`; `RETRYABLE_DB_ERRORS` moved here (shared by ingest and the API) |
| `backend/app/api/otel.py` | Passes the deadline; logs body-phase rejections (status, key id, client) |
| `backend/app/api/admin.py` (new) | `AdminGuard`, `require_admin`, `APIError`, value-free `/api/*` handlers for 422/404/405 |
| `backend/app/api/traces.py` (new), `backend/app/repositories/traces.py` (new) | The two endpoints; read-only queries |
| `backend/app/main.py` | Shared auth-failure bucket, `/api/v1` bucket, exception handlers, router |
| `backend/tests/**` | 7 new files (§3); `conftest.py` (`run_live_server`, `api_settings`, `api_client`, `ADMIN_AUTH`); 4 existing unit files updated (§3) |
| `.env.example` | `LLMI_OTLP_BODY_READ_TIMEOUT_S=10` (no secret) |
| `docs/TELEMETRY_CONTRACT.md` | §0 support row, §1.1 step 7 + deadline note, `INCOMPLETE_BODY`, no-truncation note, §1.4 conflict counting, §2 status message / `unknown_span`, §3 identifier rules, v1 finalization + semconv audit, credential classification, §4 key encoding / `usage_source` wording, §5 all-unknown row and trust wording |
| `docs/ARCHITECTURE.md` | §4.5 `status_message` / `error_type` rule text and a note (no column change); new §5.1 API implementation contract |
| `docs/SECURITY.md`, `docs/METRICS_CATALOG.md`, `docs/SOURCES_AND_RISKS.md` | Updated rules; R21 resolved, R22 mitigated, R08 widened, new R23, R24 |
| `docs/TEST_MATRIX.md`, `docs/PHASE_STATUS.md`, this report | Status from evidence |

Not changed: ADR-01…21, CR-01…09, the migration, the models, the lockfile, `docker-compose.yml`, `CLAUDE.md`, `scripts/verify_phase0.py`, the fixture.

## 7. Schema and ADR compliance

- **Schema:** no migration. `alembic check` reports no drift on the dev DB and on the disposable cycle DB. Every value written still satisfies the `0001` checks (enum values, `LEN`/`≤` bounds, `INT` drop count saturated, `status_message` `NULL`).
- **ADR-03/16/17/18:** unchanged wire behaviour except the new `408` (§2). Atomicity and commit-before-`200` were re-verified (T01-S1, T06-S6, T30-S1).
- **ADR-08 / CR-06:** the admin token exists only in the backend env; there is no frontend and no `NEXT_PUBLIC_*`. The API sends no CORS headers. Loopback bind is unchanged.
- **ADR-11:** no money computed; unknown cost is `NULL` in the DB and `null` in the API, never `0`.
- **ADR-13/14/15:** detection by `gen_ai.operation.name`; standard names win over aliases; v1 finalized (§2).
- **ADR-19:** content keys always denied; status/exception descriptions no longer stored.
- **ADR-21:** column contract intact; precedence intact; provenance aggregation clarified by the user's decision.

## 8. Remaining risks

| Priority | Item |
|---|---|
| Medium | **`408` vs. ADR-17 wording** is my interpretation (§2): please confirm, or choose `413`/`503` |
| Medium | R24: slow headers and connection exhaustion are not bounded by the app; loopback only |
| Medium | R08: allowlisted strings (span names, models, `http.route`) and identifier-shaped values (a bare `sk-…` string passes the `error.type` pattern) are not scanned for secrets |
| Low | R23: conflict and collision counters are process-local, reset on restart and exposed by no API |
| Low | A trace's `started_at` can move earlier when a late span arrives, so keyset pages are stable only for a fixed dataset (documented) |
| Low | Trace provenance and other trace fields are derived; the list filter reads the derived row (no join to spans) |
| Low | Two spans with no provider name and the same response ID share `resp:unknown:…` (counted collision, span kept). Provider names are not case-folded |
| Low | Failed calls lacking any reliable identifier (no response ID; `call_id` absent or reused) cannot be deduplicated across instrumentations. Their cost is always unknown, so the known total is unaffected; counts may be inflated (R17) |
| Low | The `/api/v1` bucket is keyed by client address (single token): a second admin token would need the key to include the token |
| Info | R20 deprecation warning unchanged; the Collector `otlphttp` exporter is still untested |

## 9. Phase 2 prerequisites

- [ ] Your review of this report and the commit, including the `408` interpretation and the no-`403` rule
- [ ] After review: the `phase-1` tag (and a `reports/phase-1.md` gate summary if you want one)
- [ ] ADR-15 semconv re-check at the start of Phase 2, including whether to allowlist the modality token counters (needs `attrmap.v2`)
- [ ] Phase 2 migration for `model_prices` + the `model_price_id` FK (Phase 1A note), and the decision on re-pricing `price_missing` rows (1B §7.6)
- [ ] Explicit approval before any real OpenAI call (none needed for the mock path)

## 10. Verdict

**Phase 1C: PASS, pending your review.** This rests on the gate run in §3:
- 626/626 with a real database required, 0 skipped;
- Ruff, `pip check`, compose validation, the migration cycle and `alembic check` all clean;
- the live-process smoke and index plans in §4 matching the contract.

**Phase 1 is complete only after your review**; no tag was created and Phase 2 has not started.

## 11. Post-review addendum (Phase 1 closure)

Phase 1C was accepted by the user subject to closing R08. The items this report left open are resolved in `reports/phase-1-final.md`:
- **§2 / §8 `408` interpretation:** accepted by the user. `408` is used for an incomplete body after the deadline, with no partial DB write; OTLP exporters may not retry it. `413` stays for size and span-count limits, `503` for temporary unavailability.
- **§2 no-`403` rule:** confirmed. A cross-project trace lookup returns the same `404` as a missing trace.
- **§8 R08 (allowlisted strings not scanned):** mitigated by the string-value safety rules (`TELEMETRY_CONTRACT.md` §3.1). Two statements in this report are superseded:
  - §2 / §1: a NUL byte inside a label is no longer replaced; the value is dropped (span name → `redacted_span`).
  - Model names are no longer truncated; they are dropped above 256 chars.
- The test counts and gate results in §3 describe the `f63e1e1` tree. The final Phase 1 counts are in `reports/phase-1-final.md`.
