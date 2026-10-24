# Phase 1 — Final Security Hardening and Closure Report

- **Date:** 2026-10-24
- **Baseline:** Phase 1C commit `f63e1e1` (626 passed / 0 failed / 0 skipped), accepted by the user subject to closing R08.
- **Closure commit and tag:** one project-only commit; the annotated tag `phase-1` points to it (the hash is given in the closure message, because a commit cannot contain its own hash). `phase-0` (→ `a16d64b`) was not moved. The home-directory repository and global Git config were not touched.
- **Scope:** R08 string-value safety, adversarial regression tests, final protocol decisions, risk review, final verification. No Phase 2 functionality, no new infrastructure, no migration, no paid API call. All telemetry used is **synthetic**.

## 1. Files changed (since `f63e1e1`)

| File | Change |
|---|---|
| `backend/app/services/ingestion/sensitive.py` (new) | Heuristic detectors: `contains_credential` (obvious key formats, JWT, private keys, `Bearer`/`Basic` values, URL user-info, `secret=` assignments), `contains_personal_data` (e-mail, 11+ digit runs; in free text also international/grouped phone, card and spaced IBAN numbers), `has_unsafe_characters` (Unicode `Cc`/`Cf`/`Co`/`Cs`/`Zl`/`Zp`) |
| `backend/app/services/ingestion/attrmap.py` | Every string spec gets an explicit class (identifier / opaque / label) plus a format and a length bound. New `safe_string()` checks the full value before any truncation. Arrays are checked item by item. `clean_str` removed (NUL now drops the value instead of being replaced) |
| `backend/app/services/ingestion/normalize.py` | Span name, scope name and scope version pass `safe_string`. An unsafe span name becomes `redacted_span` and unsafe scope fields become `NULL`, each counted in `server_dropped_attributes_count`. `exception.type` uses the same rule as `error.type` |
| `backend/tests/unit/test_metadata_privacy.py` (new, 247 tests) | Detectors, valid GenAI/OTel metadata preserved, per-field formats, secrets hidden in format-valid values, personal data, unsafe characters, check-before-truncation, span/scope fallbacks |
| `backend/tests/integration/test_ingest_metadata_privacy.py` (new, 3 tests) | End to end through `/v1/traces` (plain and gzip): DB dump, list and detail API, DEBUG logs; valid batch preserved; re-export idempotent |
| `backend/tests/unit/test_ingest_attrmap.py`, `test_normalization_hardening.py`, `tests/integration/test_ingest_dedup_integrity.py` | Updated for intended behaviour changes: models are not truncated; `call_id` must be a UUID (as the contract already said); a NUL span name → `redacted_span`; a provider containing `:` is dropped → `unknown` |
| `docs/TELEMETRY_CONTRACT.md` | New §3.1 string-value safety (field table, fallbacks, stated limits); §2 span/scope fallbacks; §3 intro; final protocol decisions in §1.1 |
| `docs/SECURITY.md` | §1 string-value safety and truncation; threat row; standing limitations (no public deployment, provenance, unknown cost, R23, R24, protocol) |
| `docs/SOURCES_AND_RISKS.md` | R08 mitigated (residual Low); R16 wording; R23, R24 open and accepted |
| `docs/ARCHITECTURE.md` | No-`403` rule marked as confirmed |
| `docs/PHASE_STATUS.md`, `docs/TEST_MATRIX.md` (evidence note only), `docs/reports/phase-1c.md` (§11 addendum), this report | Status and corrections |

Not changed: ADR-01…21, CR-01…09, migration `0001`, the models, the lockfile, compose, `.env.example`, `CLAUDE.md`, `scripts/verify_phase0.py`, the fixture, the read API and the ingest protocol.

## 2. Security fixes implemented (R08)

**Audit:** every untrusted string that the ingest can persist was listed, and each one now has a rule:
- span name, scope name and scope version;
- 20 string span attributes plus `finish_reasons[]`;
- 8 resource attributes;
- `error.type` and event `exception.type`;
- the values derived from them: `traces.service_name` and `traces.prompt_version`, and in `usage_records` `provider`, the model columns, `provider_response_id`, `call_id` and `canonical_call_key`.

IDs are validated hex, enums come from protobuf integers, and `provenance`/`usage_source` are fixed vocabularies.

**Layers, in order, on the complete original value:**
1. Type check (unchanged) and non-empty.
2. Unsafe characters → reject (NUL, CR/LF, tab, bidi override, zero-width, BOM, line separator).
3. Obvious credentials → reject in **every** field.
4. Personal data → reject in identifiers and labels. Opaque generated IDs skip this layer only, so a random response ID or UUID that happens to contain a digit run is not lost.
5. Field format (full match) and maximum length. Identifiers are dropped when too long; labels are truncated **after** the checks, so no secret prefix can survive truncation.

**Fallbacks:**
- an attribute is dropped and counted;
- a span name becomes `redacted_span` and is counted (an empty name stays `unknown_span`);
- a scope name or version becomes `NULL` and is counted on every span of that scope;
- a dropped `service.name` gives `traces.service_name = unknown_service`;
- a dropped response ID or call ID makes the call key fall through to the next source, so no unsafe value enters `canonical_call_key`.

**Requirement checklist:**

| # | Requirement | How |
|---|---|---|
| 1 | Explicit allowlists, type checks, bounded lengths | `TELEMETRY_CONTRACT.md` §3.1 table: a pattern and maximum for every field with a known format |
| 2 | Block obvious credential / API-key / bearer / sensitive patterns | `sensitive.py`; tested with 15 synthetic credential formats and 6 personal-data shapes |
| 3 | No broad substring matching that removes token counters | Value checks look for credential *formats*, not words; the key classifier is unchanged; `test_valid_genai_and_otel_values_are_preserved` and the counters in the end-to-end test |
| 4 | Drop or replace with a documented fallback | §3.1 "On failure" column |
| 5 | Preserve valid GenAI metadata | 60 valid field values (snapshot, fine-tune, `@version`, `models/…`, `org/model` names; semconv provider/operation/finish-reason/tier values; hosts; routes; UUIDs) and 8 resource attributes pass unchanged |
| 6 | Rejected values never in logs, debug output, API responses, errors | No code path logs a value (only counts); the end-to-end test checks DEBUG logs from every logger, the ingest response, the list and detail responses, and a full DB dump |
| 7 | No guarantee claimed | Stated in §3.1, SECURITY §1 and R08 |
| 8 | Preserve ADRs and DB contracts | No migration, no column change; `alembic check` clean; ADR text unchanged. The rules complete `llminsight.attrmap.v1` before the `phase-1` tag (TELEMETRY_CONTRACT §3, ADR-15) |

**Behaviour changes worth knowing:**
- A model name over 256 chars is now dropped, not truncated (a truncated model name would never match a price).
- `llminsight.call_id` must be a UUID, as the contract already stated.
- A provider name with `:` or spaces is dropped (`unknown`). The `%`/`:` escaping in call keys remains as a second layer.
- Unusual but valid-looking labels with long digit runs (e.g. a span name with an 11+ digit order number) become `redacted_span`. This is intended: such values are high-cardinality and often personal.

## 3. Real test commands and exit codes

Run on 2026-10-24 from `backend/` in `.venv` with `LLMI_TEST_REQUIRE_DB=1` (a real disposable PostgreSQL is required), unless noted.

| # | Command | Result | Exit |
|---|---|---|---|
| 1 | `python -m pytest tests/unit -q` | 597 passed | 0 |
| 2 | `python -m pytest tests/integration -q` | 279 passed | 0 |
| 3 | `python -m pytest -q -rs` (complete suite) | **876 passed, 0 failed, 0 skipped**, 1 warning (R20) | 0 |
| 4 | `python -m pytest tests/unit/test_metadata_privacy.py tests/integration/test_ingest_metadata_privacy.py` (R08) | 250 passed | 0 |
| 5 | `python -m pytest tests/integration/test_exporter_roundtrip.py` (official OTLP exporter 1.45.1, gzip and none) | 5 passed | 0 |
| 6 | `python -m pytest tests/integration/test_ingest_auth.py test_ingest_db_failure.py test_ingest_protocol.py test_ingest_concurrency.py` (auth, atomic rollback, commit-before-200, concurrency) | 55 passed | 0 |
| 7 | `ruff check .` / `ruff format --check .` | `All checks passed!` / `67 files already formatted` | 0 / 0 |
| 8 | `python -m pip check` | `No broken requirements found.` | 0 |
| 9 | `alembic current` / `alembic check` (dev DB) | `0001 (head)` / `No new upgrade operations detected.` | 0 |
| 10 | Migration cycle on disposable `llminsight_gate1final_test` (upgrade → downgrade → upgrade → check) | tables 6 → 1 → 6; no drift; DB dropped | — |
| 11 | `docker compose config --quiet` (root); container health | exit 0; `healthy` | 0 |
| 12 | `python -I scripts/verify_phase0.py --quiet` (root) | `SUMMARY: 684 PASS, 0 FAIL` | 0 |
| 13 | `git diff --check` and, after staging, `git diff --cached --check` (root) | no whitespace errors (only `core.autocrlf` notices) | 0 |

**Count reconciliation:** 626 at `f63e1e1` + 247 (new unit) + 3 (new integration) = 876. The changed existing tests kept their count: one parametrized case was removed and one new test added (`test_call_id_must_be_a_uuid_string`).

**Mutation check:** with `contains_credential` and `contains_personal_data` disabled, both variants of `test_r08_injected_secrets_never_reach_db_api_or_logs` fail (the span-name secret appears in the dump). The tests therefore guard the layer instead of passing trivially.

### Test IDs covered

- **`PASS (phase-1c)`, all re-executed in run #3** (the matrix cell keeps its 1C report reference; this report adds the closure run): T01–T08 (+ sub-cases), T25, T26, T27, T28, T30 (+S1), T31, T44.
- **R08 evidence:** belongs to T16's backend half (content and secrets never persisted). T16 stays **PARTIAL**, because its SDK half is Phase 2.
- **Still `PARTIAL`:** T14, T18, T23, T43, T48 (owned by Phases 2–4).
- **Not marked:** every Phase 2–5 test, and T15 (an SDK export scenario).

## 4. Updated risk statuses

| Risk | Status |
|---|---|
| R08 allowlisted strings carrying secrets/PII | **Mitigated** (residual Low). Heuristics cannot detect every secret or every piece of personal data: unprefixed random tokens, names, non-standard key formats |
| R21 status messages | Resolved (Phase 1C) |
| R22 slow bodies | Mitigated for the body phase (Phase 1C) |
| R23 in-process counters and buckets reset on restart, not exposed | **Open, accepted** for Phase 1 |
| R24 slow headers and connection exhaustion | **Open, not completely mitigated**; acceptable only with the loopback bind |
| R16 client provenance | Standing limitation: client-supplied provenance is **not proof** of provider origin; the API says `provenance_verified: false` |
| Public deployment | **Not authorized.** Requires the review in `SECURITY.md` §7 |
| Unknown cost | Stays `NULL` (DB) / `null` (API), never `0` |

## 5. Protocol decisions and limitations (final)

- **`408`** for a request body not received completely within `LLMI_OTLP_BODY_READ_TIMEOUT_S` after authentication; nothing is written, and the connection is closed. **OTLP exporters may not retry `408`** (their retryable codes are `429`, `502`, `503`, `504`), so such a batch is lost.
- **`413`** remains for the wire size, decompressed size and span-count limits. **`503`** + `Retry-After` remains for temporary storage unavailability.
- **No `403`** with the local-only, all-project admin token. A cross-project trace lookup returns the **same `404`** (body and headers) as a missing trace or project.
- Unchanged limitations:
  - binary protobuf only (no OTLP/JSON, no gRPC); atomic batches; 4 / 16 MiB and 2048 spans;
  - `X-Project-Key` required; single worker, in-process rate limits;
  - the Collector `otlphttp` exporter is still untested.

## 6. Phase 1 acceptance evidence

Master plan Phase 1 acceptance, with evidence in this report and `phase-1b.md` / `phase-1c.md`:

| Criterion | Evidence |
|---|---|
| Standard exporter or protobuf fixture → `/v1/traces` → DB | T01 (+S1–S3), exporter round trip (run #5) |
| 3-span trace linked correctly | T02, detail API tree |
| Same payload twice stays unique | T04, Task E tests |
| Missing/invalid key, corrupt payload, gzip/size, transaction rollback | T05, T06 (+S1–S6), T07, T08 (+S1–S3), T30 (+S1) |
| Read API, pagination, timestamps, orphans, project isolation | T25–T28 |
| Migrations | T31, run #10 |
| Log sanitization; no content, status text or credential-shaped metadata stored | T44, R21 and R08 adversarial tests |

All Phase 1 critical gates in `TEST_MATRIX.md` that Phase 1 owns pass. The complete suite has 0 failures and 0 skips with the database required.

## 7. Phase 2 prerequisites

- [ ] Architectural review of Phase 1 (this report, the closure commit and the `phase-1` tag)
- [ ] ADR-15 semconv re-check, including whether to allowlist the modality token counters (`attrmap.v2`)
- [ ] Phase 2 migration: `model_prices` + the `model_price_id` FK; decide whether `price_missing` rows are re-priced (ADR-11)
- [ ] The SDK must emit `llminsight.call_id` as a UUID and keep span names, models and labels within §3.1. Otherwise values are withheld (by design)
- [ ] Explicit approval before any real OpenAI call; mock by default

## 8. Verdict

**Phase 1: closed.** R08 is mitigated without weakening any earlier guarantee, the protocol decisions are recorded, and the complete verification passes (876/876, 0 skipped, all tooling checks clean). Phase 2 has not been started and awaits architectural review.
