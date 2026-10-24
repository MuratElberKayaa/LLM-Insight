# Phase 1B — Delivery Report (OTLP/HTTP ingestion)

- **Date:** 2026-10-24
- **Baseline:** Phase 1A commit `f17d3c4` (on Phase 0 closure `0929a14`). The `phase-0` tag (→ `a16d64b`) was not moved. No tag is created for 1B: the `phase-1` tag belongs to the Phase 1C gate.
- **Commit:** recorded in the Phase 1B Completion Report message (a commit cannot contain its own hash; `git log -1 --grep "Phase 1B"`).
- **Scope executed:** Phase 1B only: the `POST /v1/traces` receiver, ingest-key authentication and the key CLI, rate limiting, attribute mapping, idempotent persistence, tests and docs. Not implemented, as instructed: the SDK package, the OpenAI adapter, prices and cost calculation, `/api/v1`, the frontend and evaluation. No paid API was called; all telemetry used is **synthetic**.

## 1. Created / changed files

| File | Change |
|---|---|
| `backend/app/api/otel.py` | New. `POST /v1/traces` (steps 1–13 of `TELEMETRY_CONTRACT.md` §1.1) and `405` for every other method |
| `backend/app/services/ingestion/body.py` | New. Media type and encoding checks, wire-bounded body read, bounded multi-member gunzip |
| `backend/app/services/ingestion/errors.py` | New. `google.rpc.Status` (binary or OTLP/JSON), `BadRequest` details (max 20), fixed value-free messages |
| `backend/app/services/ingestion/attrmap.py` | New. `llminsight.attrmap.v1`: allowlists, always-deny list, ADR-14 aliases, type/range rules, truncation, status-message scrubbing |
| `backend/app/services/ingestion/normalize.py` | New. Span validation (§1.3), resource/scope traversal (§2), span provenance (§5) |
| `backend/app/services/ingestion/usage.py` | New. Usage rows for inference spans, canonical call key, token and unknown-cost rules (§4) |
| `backend/app/services/ingestion/service.py` | New. Auth, throttling and the transaction; 503/500 mapping; process-local counters |
| `backend/app/repositories/telemetry.py` | New. Trace/span/usage `INSERT … ON CONFLICT DO NOTHING`, derived trace recompute (one SQL statement) |
| `backend/app/repositories/api_keys.py` | New. Project/key create, prefix lookup, revoke, list |
| `backend/app/core/security.py`, `app/core/ratelimit.py` | New. Key format, HMAC-SHA256, constant-time compare; keyed token buckets |
| `backend/app/cli.py` | New. `python -m app.cli create-key / list-keys / revoke-key` |
| `backend/app/main.py` | Changed. Mounts the OTLP router; builds the ingestor and both buckets in the lifespan |
| `backend/pyproject.toml`, `backend/requirements.lock` | Changed. Runtime: `opentelemetry-proto` 1.45.1, `protobuf` 7.36.2, `googleapis-common-protos` 1.75.5. Dev: `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http` 1.45.1. Lock: 33 → 45 packages, additions only |
| `backend/tests/otlp.py`, `tests/integration/ingest_helpers.py` | New. Real protobuf builders, the fixture loader (hex IDs → bytes), HTTP/DB helpers |
| `backend/tests/unit/test_ingest_*.py`, `test_security_and_ratelimit.py` | New. 163 unit tests |
| `backend/tests/integration/test_ingest_*.py`, `test_exporter_roundtrip.py`, `test_cli_keys.py` | New. 93 integration tests against real PostgreSQL |
| `backend/tests/integration/conftest.py` | Changed. Disposable `llminsight_ingest_test` DB truncated per test, `TestClient`, key factory, live uvicorn server |
| `backend/tests/unit/test_health.py` | Changed. The Phase 1A test "no `/v1/traces` route yet" now asserts the 1B route set and that `/api/v1` does not exist |
| `scripts/verify_phase0.py` | Changed. "All tests NOT RUN" became "NOT RUN, or `PASS`/`PARTIAL`/`FAIL (phase-N)` with `reports/phase-N.md` existing and naming the ID" |
| `docs/TEST_MATRIX.md` | Status column updated from evidence (§4); legend for `PARTIAL` |
| `docs/TELEMETRY_CONTRACT.md`, `docs/fixtures/README.md` | Status notes only: T01-S3 exporter now tested; the fixture is now parsed. No rule changed |
| `docs/ARCHITECTURE.md` | §6 install note only |
| `docs/SOURCES_AND_RISKS.md` | New risks R21 (stored status messages), R22 (no body-read timeout) |
| `docs/PHASE_STATUS.md`, this report | Updated / new |

## 2. Architecture decisions respected

- **ADR-03 / ADR-16:** real binary protobuf. The request is `ExportTraceServiceRequest.ParseFromString` (`opentelemetry-proto`, upb backend). Success is an empty serialized `ExportTraceServiceResponse` (0 bytes, `partial_success` unset). Only `application/x-protobuf` is decoded. `application/json` gets a JSON `Status`; every other or missing type gets a binary `Status`.
- **ADR-17:** 4 MiB wire (declared `Content-Length` and streamed bytes), 16 MiB decompressed (zlib `max_length`, so memory never exceeds limit + 1 byte), 2048 spans. Each returns `413`.
- **ADR-18:** one transaction per request. Any invalid span → `400` with nothing written. Retryable DB errors (`OperationalError`, `InterfaceError`, pool timeout; this includes serialization failures and deadlocks) → rollback and `503` + `Retry-After: 5`. Other errors → rollback and `500`. The `200` is built only after `session.commit()` returns.
- **ADR-08 / SECURITY §2, §4:** `X-Project-Key` is checked with a prefix lookup, `HMAC-SHA256(pepper, key)` and `hmac.compare_digest`. A malformed key or unknown prefix still runs one HMAC and compare. Missing, malformed, multiple, unknown, forged and revoked keys all get a byte-identical `401`. The auth-failure bucket (per client address, 30/min) is checked before verification, and every failure consumes from it. The ingest bucket (20 req/s, burst 40) is keyed by the **verified** key ID. Steps 1–6 read headers only: an unauthenticated body is never read (a test replaces the body reader with one that raises).
- **ADR-13 / ADR-14 / ADR-15:** inference is detected by `gen_ai.operation.name` only. Aliases are mapped, and the standard name wins and the alias is counted. Every span stores `attributes_schema_version = llminsight.attrmap.v1`.
- **ADR-19:** the content keys, URLs, headers, identity keys, exception text and credential-like keys are denied before the allowlist (a test proves deny wins even with a broken allowlist). Span events and links are not stored; only `exception.type` is used, as `error_type`.
- **ADR-21 / §4:** call key precedence `resp:{provider}:{response_id}` → `sdk:{call_id}:{attempt}` → `span:{trace_id}:{span_id}`. Usage rows are created only for inference spans that this batch actually inserted. Both unique keys resolve to `DO NOTHING`, and a skipped usage row increments the process-local `usage_key_collisions` counter.
- **ADR-11:** no money is computed. Every usage row is `cost_usd = NULL`, `cost_status = unknown`, with `usage_inconsistent`, `usage_missing` or `price_missing` as the reason. Missing tokens stay `NULL`.
- **Schema:** no migration and no new column. `alembic check` reports no drift. The collision counter lives in memory, because the contract calls it a "server counter" and gives it no column.

## 3. Endpoint behaviour: actual HTTP responses

Real `python -m app` process (uvicorn, single worker) on `127.0.0.1:8765`, against a disposable `llminsight_smoke_test` database (created, migrated, dropped afterwards). The key was created with `app.cli create-key` and never printed. The script asserted that the key never appears in any response body.

| Request | Status | Content-Type | Body (decoded) |
|---|---|---|---|
| Phase 0 fixture, valid key | 200 | `application/x-protobuf` | `ExportTraceServiceResponse`, 0 bytes |
| Same fixture again / gzip | 200 / 200 | `application/x-protobuf` | same |
| Empty request (0 bytes) | 200 | `application/x-protobuf` | same |
| `GET /v1/traces` | 405, `Allow: POST` | `application/x-protobuf` | `Status{message:"method not allowed; use POST"}` |
| `Content-Type: application/json` | 415 | `application/json` | `{"message": "unsupported media type; only application/x-protobuf is accepted"}` |
| `Content-Type: text/plain` | 415 | `application/x-protobuf` | `Status{same message}` |
| `Content-Encoding: br` | 415 | `application/x-protobuf` | `Status{"unsupported content encoding; use gzip or identity"}` |
| Missing key / forged key (real prefix) | 401 / 401 | `application/x-protobuf` | `Status{"missing or invalid project key"}` |
| Corrupt protobuf | 400 | `application/x-protobuf` | `Status` + `BadRequest[(body, INVALID_PROTOBUF)]` |
| Corrupt gzip | 400 | `application/x-protobuf` | `Status` + `BadRequest[(body, INVALID_GZIP)]` |
| One span with `end < start` | 400 | `application/x-protobuf` | `"1 invalid span field(s); the batch was rejected (showing 1)"` + `BadRequest[(resource_spans[0].scope_spans[0].spans[0].end_time_unix_nano, END_BEFORE_START)]` |
| Wire body 4 MiB + 1 | 413 | `application/x-protobuf` | `"request body exceeds the size limit"` |
| gzip bomb (32 MiB inflated, 32 KiB on the wire) | 413 | `application/x-protobuf` | `"decompressed request body exceeds the size limit"` |
| 2049 spans | 413 | `application/x-protobuf` | `"request exceeds the span count limit"` |

`Status.code` is 0 (omitted) in every error. `429` and `503` (both with `Retry-After`) are covered by the tests in §4, not by this smoke.

DB afterwards: `traces=1, spans=3, usage_records=1`. Trace: `demo-summarizer`, `ok`, `prompt_version=v1`, `synthetic`, 3 spans, 10:00:00 → 10:00:01.240 UTC. Usage: `resp:openai:chatcmpl-SYNTHETIC-0001`, `provider_response_id`, `synthetic`, tokens 1250 / 1024 cached / 180, `cost_usd=NULL`, `unknown`, `price_missing`.

The server log had 23 lines: uvicorn access lines plus `ingest auth failed reason=missing prefix=- client=127.0.0.1` and `reason=hash_mismatch prefix=<8-char prefix> client=127.0.0.1`. The script checked the log for the raw key, its secret part, the pepper, the admin token and the DB password: **0 found**.

## 4. Tests, commands and results

### Final gate run (2026-10-24)

| # | Command | Outcome |
|---|---|---|
| 1 | `git rev-parse --show-toplevel` / branch / HEAD / `phase-0^{commit}` | project folder / `main` / `f17d3c4` / `a16d64b` (unchanged) |
| 2 | `docker compose config --quiet` | exit 0 |
| 3 | `docker inspect … llminsight-postgres-1` / `pg_isready` | `healthy` / `accepting connections` |
| 4 | Migration cycle on disposable `llminsight_gate1b_test` (`upgrade head` → `downgrade base` → `upgrade head`, then `alembic check`) | 5 tables + `alembic_version` → `alembic_version` only → 5 + `alembic_version`; no drift; DB dropped |
| 5 | `alembic current` / `alembic check` (dev DB) | `0001 (head)` / `No new upgrade operations detected.`, exit 0 |
| 6 | `python -m pytest tests/unit` | **194 passed**, 0 failed, 0 skipped, 1 warning |
| 7 | `LLMI_TEST_REQUIRE_DB=1 python -m pytest tests/integration` | **180 passed**, 0 failed, 0 skipped, 1 warning |
| 8 | `LLMI_TEST_REQUIRE_DB=1 python -m pytest` (full) | **374 passed, 0 failed, 0 skipped**, 1 warning, exit 0 |
| 9 | `ruff check .` / `ruff format --check .` | `All checks passed!` / `54 files already formatted` |
| 10 | `pip check` | `No broken requirements found.` |
| 11 | `git diff --check` | exit 0 (only the usual `core.autocrlf` CRLF notices) |
| 12 | `python -I scripts/verify_phase0.py --quiet` | `SUMMARY: 681 PASS, 0 FAIL`, exit 0 (660 at Phase 1A; the status rule now runs one check per claimed status, 20 in total) |

The full suite is 118 Phase 1A tests (unchanged: 31 unit + 87 integration; one assertion updated, see §1) plus **256 new**: 163 unit and 93 integration. The warning is R20 (Starlette `TestClient` with `httpx`).

### Failures found and fixed during the phase (root causes)

- **Scrubber gap:** `Authorization: Bearer <token>` kept the token, because the key=value branch consumed only the word "Bearer". The pattern now also covers an auth scheme after the separator.
- **Trace provenance for all-unknown inference spans** was derived as `mixed`. It is now `unknown`; this is a contract gap (§7 item 2).
- **Secret-scan hits:** a fake key in a scrubber test matched the doc check's OpenAI-key pattern, both in the test file and in pytest's node-id cache (parameter values become test IDs). The literal was shortened, the parametrize got explicit IDs and the regenerable `.pytest_cache` was deleted. No real secret was involved.

### Test-matrix status (only where executed evidence exists)

**PASS (phase-1b), the Phase 1 critical gates owned by 1B:**

| Test | Evidence (test functions) |
|---|---|
| T01 | `test_t01_valid_protobuf_returns_export_response_after_commit`; **S1** rows visible from a separate connection when the `200` arrives; **S2** `test_t01_s2_empty_request…` (2 shapes); **S3** `test_t01_s3_standard_exporter_round_trip_and_reexport` (gzip and none), `test_batch_span_processor_flush…`, `test_exporter_configured_only_by_the_documented_env_vars` |
| T02 | `test_t02_parent_child_tree_reconstructs`, `test_fixture_expected_ingest_result`, `test_child_arriving_before_parent_in_separate_batches` |
| T03 | `test_t03_missing_service_name_is_unknown_service`; **S1** `test_t03_s1_multiple_resource_spans_and_scopes`; **S2** `test_t03_s2_alias_precedence_is_persisted` plus unit precedence tests |
| T04 | `test_t04_same_payload_twice…` (identity, identity, gzip: counts 1/3/1, `ingested_at` unchanged); `test_concurrent_identical_exports_store_exactly_once` (12 simultaneous HTTP requests); exporter re-export |
| T05 | `test_t05_t44_missing_unknown_revoked_keys…` (7 variants, byte-identical `401`, 0 rows); CLI revoke → `401` |
| T06 | 3 corrupt payloads; **S1** 5 ID cases; **S2** 3 timestamp cases; **S3** JSON `415`; **S4** `text/plain`, `application/protobuf`, missing; **S5** `br`, `deflate`, `zstd`; **S6** 1 invalid among 10 valid → 0 rows |
| T07 | gzip decode, `identity`, 3 corrupt-gzip cases → `INVALID_GZIP` |
| T08 | **S1** 4 MiB + 1 → `413` (exactly 4 MiB passes the size step); **S2** gzip bomb → `413`; **S3** 2049 → `413`, 2048 → `200` and stored; the count spans resources |
| T30 | **S1** `_insert_usage` raises after traces and spans were inserted → `503`, 0 rows, retry `200`, re-retry idempotent; commit failure → `503`; DB unreachable → `503` + `Retry-After`; `/health/ready` `503` (Phase 1A tests, still passing) |
| T31 | `test_migrations.py` (7) in the full run plus the CLI cycle in row 4 |
| T44 | Logs after 7 bad-auth variants and after a successful ingest contain no key, secret part or body content; only reason codes and prefixes appear |

**PARTIAL (phase-1b), only the 1B-owned half was executed:**

| Test | Executed now | Still owed (phase) |
|---|---|---|
| T14 | Same `call_id`, attempts 1 and 2 → 2 usage rows, 1 distinct `call_id`; re-sending adds nothing | SDK-generated attempts (2) |
| T16 | Backend drops and counts content and credential attributes; a DB-wide dump contains no marker | SDK never exports content (2) |
| T18 | Missing tokens → `NULL`, `usage_source=unknown`, `cost_status=unknown` + `usage_missing`; `SUM` skips `NULL` | Cost engine (2), API/UI (3) |
| T23 | **S1** same response ID from two spans → 1 row, counter + 1 (sequential, same batch and 6 concurrent requests); **S2** key precedence persisted | "Cost unchanged" once cost exists (2) |
| T26 | ns stored losslessly, `TIMESTAMPTZ` UTC exact to the µs, `2^63-1` storable | API ISO-8601 `Z` (1C/3) |
| T27 | Orphan stored with its `parent_span_id`, no parent invented | Returned as orphan by the API (1C) |
| T28 | Same IDs in two projects stay isolated; client-sent project attributes ignored; a batch for A never changes B's derived trace | API `404` across projects (1C) |
| T43 | Ingest bucket and auth-failure bucket → `429` + `Retry-After` | `/api/v1` bucket (3) |
| T48 | **S1** real + synthetic → trace `mixed`; usage keeps per-row provenance; **S2** real without response ID → `unknown` | Dashboard badges (3) |

All other IDs stay **NOT RUN**. T15 is not marked: the credential-key deny rule is tested, but T15's scenario is the SDK's export.

## 5. Database integrity and deduplication evidence

- **Atomicity:** T06-S6 (1 bad span among 10) and T30-S1 (a failure after spans were inserted) both leave `traces/spans/usage_records = 0/0/0`.
- **Commit before acknowledgement:** T01-S1 reads the rows through a second engine immediately after the `200`. A failing `commit()` never yields `200`.
- **Re-export:** spans use `ON CONFLICT (project_id, trace_id, span_id) DO NOTHING`, so the first write wins (a re-export with changed name or attributes is ignored, test `test_reexport_with_changed_content_keeps_first_write`). Usage rows are derived only from spans inserted by the same batch, so a re-export can never add usage.
- **Concurrency:** 12 identical requests released by a barrier against the live server give 12 × `200` and 1/3/1 rows. Two batches touching the same two traces in opposite order, ×4, give 8 × `200` with no deadlock, because rows are written in sorted key order. Six racing double-instrumentation spans give 6 spans, 1 usage row and 5 counted collisions.
- **Retry versus re-export:** a new `span_id` with `attempt+1` gives a new usage row. The same span again gives nothing.
- **No parent double count:** a SERVER root carrying `gen_ai.usage.input_tokens` and an `execute_tool` span produce no usage row. Only the `chat` child does.
- **Project scope:** every unique key and the trace recompute are filtered by `project_id`. The same fixture in two projects gives 1/3/1 each and 0 collisions.

## 6. Security and cost

- No plaintext key is stored (HMAC only) or logged (T44). The CLI prints it once.
- Error bodies contain no key, header value, attribute value, span name or ID (asserted for the 400, 401, 413 and 503 paths). DB driver messages never reach a response or a log; only exception class names are logged.
- Credential-like substrings in `Status.message` are masked before storage (best effort, R21).
- Local-only posture is unchanged (loopback bind enforced by settings).
- Cost: $0. No provider API calls; local Docker only.

## 7. Contract clarifications needing your decision (implemented conservatively, ADRs untouched)

1. **`gen_ai.request.max_tokens` conflict.** §3 allowlists it, but the always-deny rule drops any key containing `token` except `gen_ai.usage.*_tokens`. Deny wins (SECURITY §1), so it is dropped and counted. Options: add it to the deny exception list (a contract edit), or remove it from the allowlist.
2. **All-unknown inference spans.** The §5 table covers none / all real / all synthetic / "any other combination" → `mixed`. A trace whose inference spans are all `unknown` is stored as `unknown`, not `mixed`. Neither counts as real.
3. **Empty span name.** `spans.name` is `LEN(256)` (≥ 1) and OTLP calls an empty name unknown, so it is stored as `unknown_span`.
4. **Identity strings are not truncated.** `gen_ai.response.id` > 256, `llminsight.call_id` > 64 and `gen_ai.provider.name` > 64 chars are dropped and counted, since a truncated ID would merge distinct calls. A `resp:` key longer than 300 chars falls through to the SDK or span key; the response ID column still keeps the value.
5. **`usage_source`:** `unknown` when input or output tokens are missing; otherwise the client's `llminsight.usage_source` claim, defaulting to `provider_reported`. Consequence: `embeddings` spans without output tokens are `unknown` / `usage_missing`.
6. **`cost_unknown_reason` precedence:** `usage_inconsistent` > `usage_missing` > `price_missing`. Every 1B row is unknown. Phase 2 must decide whether rows ingested before prices existed are re-priced or stay `price_missing` (ADR-11 says history is not rewritten).
7. **`prompt_version`** is set only if every inference span carries the same value; one span without it gives `NULL`.
8. **Resource drops** are counted on every span of that resource, because each span stores its own copy. Scope attributes, events and links are ignored and not counted.
9. **Smaller choices:** duplicate attribute keys (first wins, rest counted); duplicate `X-Project-Key` headers → `401`; multi-member gzip accepted, trailing garbage → `INVALID_GZIP`; `HEAD` / `OPTIONS` → `405`; a missing key also consumes the auth-failure bucket (§1.1 step 5 says every failure counts).
10. **Phase 1A item still open:** the `model_price_id` FK sequencing (`reports/phase-1a.md` §2).

## 8. Known limitations and unresolved risks

| Priority | Item |
|---|---|
| Medium | `/api/v1/traces` stubs are **not** in 1B. They were listed under 1B in the earlier status file but not in the 1B instruction, so they moved to Phase 1C (T25, plus the API halves of T26–T28) |
| Medium | R21: status messages are stored after best-effort scrubbing; content in free text is possible |
| Low | R22: no body-read timeout (slow clients); loopback-only mitigates |
| Low | Rate limits and the collision counter are per process (CR-08) and reset on restart; the counter is not exposed by any API yet |
| Low | Prefix lookup timing differs between known and unknown prefixes. The prefix is not secret; the secret compare is constant-time |
| Low | The OTel Collector `otlphttp` exporter is still untested; only the Python exporter is (T01-S3) |
| Low | No throughput benchmark (Phase 5). 2048-span and 1500-inference-span batches pass within the suite's 28 s total |
| Info | R20 deprecation warning unchanged |

## 9. Phase 1C prerequisites

- [ ] Your review of this report and the commit
- [ ] Decisions on §7 items 1–8 (and the Phase 1A FK item)
- [ ] Scope confirmation for 1C: `/api/v1/traces` and `/{trace_id}` stubs with the admin token, project scope and cursor pagination; hardening (R22); the Phase 1 gate report `reports/phase-1.md` and the `phase-1` tag
- [x] PostgreSQL healthy; dev DB at `0001 (head)` with no drift

## 10. Verdict

**Phase 1B: PASS, pending your review.** This rests on the gate run in §4: 374/374 with a real database required and 0 skipped; Ruff, `pip check`, compose validation, the migration cycle and `alembic check` all clean; and the live-process smoke in §3 matching the contract. **Phase 1 is not complete**, and Phase 1C has not started.
