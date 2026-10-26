# Phase 5B — Final QA, security audit, benchmark and MVP delivery readiness — Delivery Report

- **Instruction (user, Phase 5B, 2026-10-26):** complete the locked MVP quality, security, performance and delivery acceptance checks under the Phase 4 feature freeze. No Optimization Lab subsystem and no new features; commit locally only after the mandatory checks pass; no tag, no push, no real or paid provider call, no public deployment, no history rewrite; stop after Phase 5B.
- **Baseline:** `fe259e7` (Phase 5A, local only), annotated `phase-4` → `394920c`, migration `0005`.
- **Commit/tag:** one Phase 5B commit on `main`. No tag, no push.
- **Environment:** Windows 11 Home 10.0.26200; AMD Ryzen 7 6800HS (8 cores / 16 threads), 15.3 GiB RAM, NVMe SSD, power plan "Performance"; Python 3.12.6; Node 22.23.4 / npm 10.9.9 (project-local); Docker Engine 28.5.1 (Docker Desktop, WSL2 kernel 6.18.54, 16 CPUs, 7.4 GiB); PostgreSQL 17.11 (compose image `postgres:17`, default settings: `shared_buffers` 128 MB, `work_mem` 4 MB); Microsoft Edge (Playwright). `LLMI_ALLOW_REAL_LLM=false`; no `OPENAI_API_KEY`.
- **Cost and model usage:** infrastructure cost $0 (local machine); **no LLM provider API call** was made by the project, its tests, the benchmark or the demo. All telemetry and experiment data are SYNTHETIC.
- **Untouched:** the home-directory repository, global Git and Node configuration, the native PostgreSQL on 5432, `docker-flowise-1`, the dev containers `llminsight-postgres-1` (up 38 h before and after) and their volume `llminsight_pgdata`, the dev images `llminsight-backend` / `llminsight-frontend`, the dev database content (2 projects, 0 datasets, 0 experiments before and after; E2E teardown `remaining: []`), the schema (no migration), provider pricing, the tags, `origin`.

## 1. Baseline verification (Step 1)

| Check | Result |
|---|---|
| Git root | `C:/Users/PC/Desktop/LLM_Insight_Claude_Code_MVP_Paketi` (own repository) |
| Branch, tree, HEAD | `main`, clean, `fe259e7f614723b85a1f73534662646cf54e0f32` |
| Tags | `phase-0`…`phase-4` unchanged (`phase-4`: tag object `4d24a91` → `394920c`); no `phase-5` |
| `origin` | `https://github.com/MuratElberKayaa/LLM-Insight` (fetch/push); Phase 5A not pushed |
| PostgreSQL / Alembic | `llminsight-postgres-1` healthy; `alembic current` `0005 (head)`; `alembic check`: no new operations |
| Backend / dashboard | not running at the start; started for this phase: `python -m app` on 127.0.0.1:8000 (`/health/ready` 200); production build served on 127.0.0.1:3005 (probe) and 3001–3004 (E2E) |
| Baseline suites (before any edit) | backend **1726 passed** (227 s), SDK **350 passed** (83 s), frontend unit **311 passed**; 0 failed, 0 skipped. `npm audit` 0 vulnerabilities |

**Scope decisions.** Phase 5 is final QA (master plan §H: "after Phase 4 no architecture or feature change"). No Optimization Lab feature was built; the existing deterministic evaluation and synthetic A/B experiments are the MVP capability. The phase prompt's `docs/FINAL_DELIVERY_REPORT.md` and the short project pitch are not produced here: this instruction names `phase-5b.md` as the deliverable, and the final release document belongs to the Phase 5 final release step after the architectural review (§11).

## 2. Changes in Phase 5B

| Change | Why | Regression evidence |
|---|---|---|
| `backend/requirements.lock`, `sdk/requirements-dev.lock`: `certifi` 2026.7.22 → 2026.10.19 | security finding R74 (§4.1) | full suites, `pip check`, clean-clone image build, `pip-audit` clean |
| Seven test files: raw bidi / zero-width characters in string literals → `\uXXXX` escapes | security finding R78 (bandit B613) | constants identical (AST check); affected tests 491 + 184 passed; full suites |
| `scripts/qa/` (new): `secret_scan.py`, `route_probe.py`, `backend_probe.py`, `benchmark.py` | reproducible audit, probe and benchmark (QA tools, not product code) | Ruff clean; secret-scan control run detects planted values |
| `README.md` (new) | Step 6 | every command verified on the clean clone (§7) |
| Docs: `DEMO_SCRIPT.md` (final), `TEST_MATRIX.md`, `PHASE_STATUS.md`, `SOURCES_AND_RISKS.md` (R74–R78; notes on R66–R69, R71), `SECURITY.md` (secret scan), `ARCHITECTURE.md` §6–7 (pin note, layout) | Step 9 | `verify_phase0.py` (§8) |
| `docs/reports/phase-5b-screenshots/` (8), `docs/reports/phase-5b-benchmark.json` | evidence | — |

No production backend, SDK, dashboard or schema code changed. No ADR was changed.

## 3. T01–T48 evidence matrix (Step 2)

Run evidence codes (final runs on the Phase 5B tree, §8): **BE** backend pytest 1726 passed; **SDK** SDK pytest 350 passed; **FEU** frontend unit 311 passed; **E2E** Playwright 55 passed; **BS** bundle scan 0 findings; **CC** clean-clone run (§7); **PR** route probes (§5); **BM** benchmark (§6). Every named test below exists and passed in those runs (79 backend/SDK names checked against the JUnit output; E2E titles from the run log). ★ critical, ⛔ blocker.

| ID | Class | Locked definition (short) | Implementation | Automated tests (examples) | Run | Result | Limitation |
|---|---|---|---|---|---|---|---|
| T01 | ★⛔ | Valid protobuf → `200` + `ExportTraceServiceResponse` after COMMIT; rows stored | `app/api/otel.py`, `services/ingestion/` | `test_t01_valid_protobuf_returns_export_response_after_commit`, `test_t01_s2_empty_request_is_200_and_writes_nothing`, `test_t01_s3_standard_exporter_round_trip_and_reexport` | BE, CC, BM | **PASS** | binary-protobuf subset of OTLP only (R15) |
| T02 | ★ | 3-span fixture: children reference root; tree reconstructs | ingestion, `api/traces.py`, trace tree | `test_t02_parent_child_tree_reconstructs`; E2E T46 | BE, E2E | **PASS** | — |
| T03 | ★ | `service.name` from resource; multiple resourceSpans/scopes; missing → `unknown_service` | `services/ingestion` attrmap | `test_t03_missing_service_name_is_unknown_service`, `test_t03_s1_multiple_resource_spans_and_scopes`, `test_t03_s2_alias_precedence_is_persisted` | BE | **PASS** | — |
| T04 | ★⛔ | Same payload twice → counts unchanged, `200` both times | idempotent upserts | `test_t04_same_payload_twice_leaves_counts_unchanged`, `test_concurrent_identical_exports_store_exactly_once` | BE, BM (re-ingest of 135 batches, counts unchanged) | **PASS** | conflict counters per process (R23) |
| T05 | ★ | Missing / unknown / revoked key → identical `401`, nothing stored, key not logged | `core/security.py`, `api/otel.py`, CLI | `test_t05_t44_missing_unknown_revoked_keys_get_identical_401_and_nothing_logged`, `test_create_list_revoke_cycle` | BE | **PASS** | — |
| T06 | ★ | Corrupt protobuf → `400` + binary `google.rpc.Status`/`BadRequest`, no values echoed, nothing stored | `api/otel.py` | `test_t06_corrupt_protobuf_is_400_with_bad_request_and_nothing_stored`, `test_t06_s3_json_request_gets_json_encoded_status_415` (+ S1–S6) | BE | **PASS** | — |
| T07 | | gzip decoded; corrupt gzip → `400 INVALID_GZIP` | `api/otel.py` | `test_t07_gzip_request_is_decoded`, `test_t07_corrupt_gzip_is_400_invalid_gzip` | BE | **PASS** | — |
| T08 | ★ | Wire > 4 MiB, decompressed > 16 MiB, > 2048 spans → `413` | `api/otel.py` | `test_t08_s1_wire_body_over_4_mib_is_413`, `test_t08_s2_gzip_bomb_over_16_mib_decompressed_is_413`, `test_t08_s3_2049_spans_is_413_and_2048_is_accepted` | BE | **PASS** | — |
| T09 | ★ | CLIENT span `chat {model}` with `gen_ai.*` + provenance | `sdk/src/llm_insight_sdk/` (mock, OpenAI adapter) | `test_successful_call_span_shape_and_usage` (+ live-backend tests) | SDK | **PASS** | OpenAI adapter verified against offline fakes only; no real call |
| T10 | ★ | LLM span is a child of the demo FastAPI root span | `demo_app/web.py`, SDK context | `test_t10_request_span_is_the_root_of_every_inference_span`, `test_demo_web_live` | SDK, CC (5 traces) | **PASS** | — |
| T11 | ★ | Provider usage → `gen_ai.usage.*`, `usage_source=provider_reported` | SDK adapter | `test_successful_call_span_shape_and_usage` | SDK | **PASS** | offline fixtures |
| T12 | ★ | 429 / timeout → span `ERROR`, `error.type`, attempt recorded; handled error | SDK adapter | `test_rate_limit_then_success_is_two_physical_attempts`, `test_timeout_is_classified_retried_and_raised` | SDK | **PASS** | — |
| T13 | ★ | Backend down: app request succeeds, no exception, bounded latency | SDK telemetry | `test_backend_down_does_not_affect_the_call`, `test_telemetry_outage_never_breaks_the_application`, `test_backend_recovery_after_an_outage` | SDK | **PASS** | best effort, no persistent queue (R26) |
| T14 | | Same `call_id`, attempts 1..n, one usage row per attempt | SDK + ledger | `test_rate_limit_then_success_is_two_physical_attempts`, `test_each_physical_attempt_is_priced_on_its_own`, `test_retry_attempts_are_separate_usage_rows_and_reexport_adds_nothing` | SDK, BE | **PASS** | — |
| T15 | ★ | No API key / auth header in any exported attribute | SDK guards | `test_no_content_key_header_or_error_text_leaves_the_process`, `test_no_secret_reaches_stored_attributes_via_metadata` | SDK | **PASS** | heuristic value checks (R08, R25) |
| T16 | ★ | No message content exported; backend drops it if sent (counted) | SDK + ingest allowlist | `test_t16_content_and_credentials_are_never_persisted_and_are_counted` (+ SDK adapter tests) | BE, SDK | **PASS** | — |
| T17 | ★⛔ | Provider-reported values win over any estimate | `services/pricing`, usage rules | `test_usage_rules_decide_before_any_price` (estimates never priced), `test_usage_problems_are_never_priced`, `test_verified_price_gives_the_hand_computed_cost` | BE | **PASS** | — |
| T18 | ★ | Missing usage → NULL tokens, `cost_status=unknown` + reason, never 0 | usage rules, pricing | `test_missing_tokens_stay_null_with_unknown_source`, `test_missing_usage_is_absent_never_estimated`, `test_usage_problems_are_never_priced` | BE, SDK, CC ("Total unknown") | **PASS** | — |
| T19 | ★⛔ | Hand-computed `Decimal` cost equals the stored value | `services/pricing` | `test_t19_hand_computed_cost_gpt_5_4_mini_snapshot` (+ 2 more), `test_verified_price_gives_the_hand_computed_cost` | BE | **PASS** | estimates, not invoices |
| T20 | ★⛔ | Cached tokens priced once, not added to input | pricing | `test_t20_cached_tokens_are_priced_once_and_not_added_to_input` | BE | **PASS** | — |
| T21 | ★ | Reasoning tokens not added to output | pricing | `test_t21_reasoning_tokens_are_part_of_output_and_not_added_again` | BE | **PASS** | — |
| T22 | ★ | `price_version` + `model_price_id` per usage row; price change never rewrites history | pricing, ledger | `test_a_new_price_version_never_rewrites_stored_costs`, `test_verified_price_gives_the_hand_computed_cost` | BE | **PASS** | catalogue can go stale (R28) |
| T23 | ★⛔ | Re-export → one usage row, cost unchanged | `UNIQUE(project_id, canonical_call_key)` | `test_t23_s1_double_instrumentation_same_response_id_is_one_usage_row`, `test_t23_s2_key_source_precedence_is_persisted`, `test_concurrent_double_instrumentation_keeps_one_usage_row` | BE, BM | **PASS** | failed attempts from two instrumentations not deduplicated (R17) |
| T24 | ★ | SQL `percentile_cont` = NumPy linear; `n` returned | `services/metrics` | `test_t24_inference_and_trace_latency_percentiles`, `test_t24_percentiles_match_the_linear_method_on_a_larger_sample` | BE | **PASS** | — |
| T25 | ★ | Cursor pagination stable; `limit` max enforced | `api/traces.py` | `test_t25_cursor_pages_are_newest_first_complete_and_disjoint`, `test_t25_limit_default_is_50_and_maximum_200_is_accepted` (+ 2 more) | BE | **PASS** | — |
| T26 | ★ | ns epoch → UTC `TIMESTAMPTZ`; ISO-8601 `Z`; ns preserved | ingestion, API | `test_t26_ns_epoch_stored_losslessly_and_as_utc_timestamptz`, `test_t26_api_returns_iso_utc_z_with_all_nanoseconds` | BE | **PASS** | — |
| T27 | ★ | Missing parent stored and returned as orphan | ingestion, API, tree | `test_t27_orphan_span_is_stored_without_inventing_a_parent`, `test_t27_orphan_is_returned_as_orphan_until_its_parent_arrives`; E2E orphan tree | BE, E2E | **PASS** | — |
| T28 | ★ | Other project's trace → `404`; list never mixes projects | API project scope | `test_t28_other_projects_trace_is_an_indistinguishable_404`, `test_t28_list_never_mixes_projects_even_with_identical_trace_ids`; E2E project-isolation tests (4B, 4D) | BE, E2E | **PASS** | one admin token reads every project (R45) |
| T29 | ★ | No data → `n=0`, null values, `200` | metrics | `test_t29_empty_project_returns_n_zero_and_nulls`; FEU "accepts an empty window with nulls (T29)" | BE, FEU | **PASS** | — |
| T30 | ★ | Mid-batch failure → nothing persisted; DB down → `503` + Retry-After; ready `503` | ingest transaction, health | `test_t30_s1_db_error_after_partial_insert_rolls_back_then_retry_succeeds`, `test_t30_database_unreachable_is_503_with_retry_after`, `test_ready_503_when_database_missing_on_real_server` | BE | **PASS** | — |
| T31 | | upgrade / downgrade / upgrade; schema = `ARCHITECTURE.md` §4 | Alembic 0001–0005 | `test_upgrade_downgrade_upgrade_cycle`, `test_schema_matches_architecture_contract`, `test_models_and_migration_have_no_drift`; `alembic check` | BE, CC (fresh DB → `0005`) | **PASS** | — |
| T32 | ★ | M02/M04 counts match; trace vs inference counts separate | metrics | `test_t32_trace_and_inference_counts_are_separate_and_exact` | BE | **PASS** | — |
| T33 | ★ | Valid enum → `201`; invalid `issue_type` → `422`; duplicate open → `409` | `api/reviews.py` | `test_t33_every_issue_type_creates_an_open_pending_review`, `test_t33_invalid_issue_type_is_422_without_echo`, `test_t33_duplicate_open_review_is_409_and_closing_allows_a_new_one`; E2E review loop | BE, E2E, CC | **PASS** | names self-declared (R55) |
| T34 | ★⛔ | Missing / false `approved` → `422`, no row | `api/datasets.py` | `test_t34_no_approval_no_test_case`; E2E 4B loop | BE, E2E, CC (save without the tick: nothing stored) | **PASS** | — |
| T35 | ★⛔ | E-mail / phone / key-like input masked before insert; `redaction_applied=true` | `services/redaction.py` | `test_t35_input_is_masked_before_insert`, `test_t35_clean_input_still_records_redaction_applied` (+ unit `test_redaction.py`) | BE, E2E, CC (`redaction_applied` true in PostgreSQL) | **PASS** | heuristic, not a privacy guarantee (R56) |
| T36 | ★ | Valid output passes JSON Schema + field rule | `services/evaluation` | `test_t36_valid_output_passes_the_json_schema_rule`, `test_t36_valid_output_passes_the_field_rule` | BE | **PASS** | restricted schema subset (R61) |
| T37 | ★ | Schema violation fails with `evaluation_detail` | `services/evaluation` | `test_t37_schema_violation_fails_with_evaluation_detail` | BE, CC (case 13 under v1: `schema_violation: …` in PostgreSQL) | **PASS** | — |
| T38 | | Both versions on identical case ids | `services/experiments` | `test_t38_both_versions_run_on_identical_case_ids_in_order`, `test_the_human_approved_case_itself_is_run_under_both_versions`; E2E 4D loop (PostgreSQL check) | BE, E2E, CC (13 identical ids in PostgreSQL) | **PASS** | — |
| T39 | ★ | Each result `real`/`synthetic`; mock → synthetic | experiment service + trigger, dashboard | `test_t39_every_mock_result_is_synthetic`; FEU 4D decoder tests | BE, FEU, E2E, CC (26/26 `synthetic`) | **PASS** | mock-only results (R62) |
| T40 | | No expected output → `passed=NULL`, outside the denominator | evaluation, report | `test_t40_missing_expected_output_is_not_evaluated_for_every_rule`, `test_t40_not_evaluated_results_leave_the_pass_rate_denominator` | BE, CC (7/12 and 5/11 with 1 and 2 not evaluated) | **PASS** | — |
| T41 | ★ | `<script>` in a model/service name renders as text | React escaping, `react/no-danger` | E2E "T41: hostile labels ingested through /v1/traces render as text in list and detail"; FEU "escapes hostile service names and prompt versions (T41)" | E2E, FEU | **PASS** | `style-src 'unsafe-inline'` (R38) |
| T42 | ★⛔ | Build output / bundle / HTML contain no admin token | server-only config, `check-bundle.mjs` | `npm run check:bundle`; E2E "no credential reaches the browser (T42, live half)", "… on the trace pages (T42 regression)"; static unit test | BS (632 files, 0), E2E, CC (bundle 0; 0 token in 96 responses) | **PASS** | — |
| T43 | ★ | Burst → `429` + Retry-After (ingest, auth-failure, API buckets) | `core/ratelimit.py`, `api/admin.py` | `test_phase5a_rate_limit.py` (6); E2E `phase5a.spec.ts` (2) | BE, E2E | **PASS** | per-process buckets (R09) |
| T44 | ★ | No key / header value in logs after bad-auth requests | logging rules | `test_t05_t44_missing_unknown_revoked_keys_get_identical_401_and_nothing_logged`, `test_t44_success_path_logs_no_key_and_no_body` | BE; probe and clone logs scanned (0 secret values) | **PASS** | — |
| T45 | ★ | Seeded DB data visible in the list; screenshot | trace list page | E2E "T45: the trace list shows the seeded database traces exactly as the API returns them" | E2E, CC (`5b-02-traces.png`) | **PASS** | — |
| T46 | ★⛔ | Trace list → detail shows root → child tree | trace detail page | E2E "T46: list → detail shows the root → child span tree, and back restores the list" | E2E, CC (`5b-03-trace-detail.png`) | **PASS** | — |
| T47 | | Clean clone → `docker compose up --build` → init → demo works | compose `app` profile, dev scripts | (manual acceptance run) | CC (§7) | **PASS** | ingest needs a host backend (R43) and host venvs (R73) |
| T48 | ★ | SYNTHETIC/MIXED/UNKNOWN badges; mixed and unknown never counted as real | provenance derivation, badges | `test_t48_s1_mixed_trace_and_t48_s2_real_without_response_id`, `test_mixed_and_unknown_are_never_counted_as_real`; E2E "synthetic telemetry is labeled and never presented as real (T48)", "T48: synthetic, mixed and unknown provenance are labeled and never shown as real" | BE, FEU, E2E, CC (SYNTHETIC banner, 26 synthetic / 0 real) | **PASS** | provenance client-asserted (R16) |

**Verdict (42 critical): 42 PASS, 0 FAIL, 0 SKIP. The 10 blockers (T01, T04, T17, T19, T20, T23, T34, T35, T42, T46) pass. The 6 non-critical scenarios (T07, T14, T31, T38, T40, T47) pass against their documented criteria.** No definition was changed. T33–T40, T43, T47 and T48 were re-checked specifically: in the suites, in the E2E loop against PostgreSQL, and on the clean clone (T33–T35, T37–T40, T47, T48).

## 4. Security audit (Step 3)

Tools ran from a separate throwaway venv (`C:/Users/PC/AppData/Local/Temp/llmi5b/audit`, not a project dependency): `pip-audit` 2.10.1 (uploaded 2026-06-10) and `bandit` 1.9.4 (2026-02-25), both months older than the two-week rule requires; full tool freeze recorded (32 packages).

### 4.1 Dependency audit

| Command | Result before | Result after the fix |
|---|---|---|
| `pip-audit -r backend/requirements.lock --no-deps --disable-pip -s pypi` / `-s osv` | 1 advisory in `certifi` 2026.7.22 (exit 1) | **No known vulnerabilities** (exit 0) |
| `pip-audit -r sdk/requirements-dev.lock --no-deps --disable-pip -s pypi` / `-s osv` | the same advisory (exit 1) | **No known vulnerabilities** (exit 0) |
| `pip-audit --path backend/.venv/Lib/site-packages` (and `sdk/.venv`) | `certifi` + `pip` 24.2 (6 advisories) | `pip` 24.2 only (6 advisories; R75) |
| `npm audit` / `npm audit --omit=dev` (frontend) | 0 vulnerabilities | 0 vulnerabilities (final run) |

### 4.2 Findings and classification

| # | Finding | Severity (tool → assessed) | Affected | Local exploitability | Evidence | Resolution |
|---|---|---|---|---|---|---|
| F1 | `certifi` trusts `emSign ECC Root CA - G3` (compromised intermediates; PYSEC-2026-4320 / GHSA-6q48-6c7w-9gw2) | advisory → **Medium** | both locks, backend image; used by `requests` (OTLP exporter), `httpx`/`httpcore` | Low: mock mode makes no outbound TLS; the SDK exports to loopback `http` | `pip-audit` (PyPI and OSV) | **Fixed (R74):** `certifi` 2026.10.19, security exception to the two-week rule (uploaded 2026-10-20); wheel SHA-256 `42d35340…670e1b` matches PyPI, installed with `--require-hashes`; diff against 2026.7.22: version string and CA bundle only, exactly that root removed. Venvs equal their locks (51 / 48 entries), `pip check` clean, image rebuilt in the clean clone (`certifi 2026.10.19` inside the container) |
| F2 | `pip` 24.2 in the venvs and the base image: PYSEC-2026-1795/-1796/-2875/-2876/-196/-3721 (archive, path and entry-point handling; fixed in 25.3–26.2) | advisories → **Low** here | venv bootstrap and `python:3.12.6-slim-bookworm`; not in any lock | needs a malicious package or index; the project installs exact PyPI pins only; not imported at runtime | `pip-audit --path` | **Accepted (R75)**, remediation before any release beyond local use |
| F3 | Not covered by `pip-audit`: the CPython 3.12.6 patch level and the base image's OS packages (no container scanner ran); locks pin versions without hashes | **Gap** | toolchain | — | — | **Accepted (R75)**; listed as a release prerequisite (§11) |
| F4 | bandit B613: raw bidi-override / zero-width characters in seven test files | High → **Low** (tests only, inside string literals) | `backend/tests` (5), `sdk/tests` (2) | none at runtime; a review-safety (Trojan Source) issue | bandit; full scan of tracked files for format characters | **Fixed (R78):** `\uXXXX` escapes, constants identical, tests pass, B613 0 |
| F5 | bandit B202 `zipfile.extractall` in `scripts/dev/install_node.py` | High → **false positive** | dev tool | — | code: every member is checked to stay inside the target first; the archive's SHA-256 is checked against `SHASUMS256.txt`; the tar branch uses `filter="data"` | no change |
| F6 | bandit B310 `urlopen` (`demo_app/web.py`, `install_node.py`, `scripts/qa/backend_probe.py`) | Medium → **false positive** | — | — | fixed `http://127.0.0.1` / `https://nodejs.org` URLs, never user input | no change |
| F7 | bandit B608 f-string SQL in `scripts/qa/benchmark.py` and 20 test sites | Medium → **false positive** | QA tool, tests | — | constant table names only | no change |
| F8 | bandit Low: B105 (a hypothesis text, a `CHANGE_ME` placeholder check), B404/B603/B607 (fixed-argv `git`, `docker`, `taskkill` calls), B101 (asserts in the benchmark tool and tests), B104/B311 in tests | Low → **accepted** | — | — | — | no change |
| F9 | Secret scan pattern hits (URL user-info) | → **false positive** | 15 locations: `backend/tests/redaction.py`, `backend/tests/unit/test_config.py`, `backend/tests/unit/test_output_redaction.py`, the SDK copies, `docs/SECURITY.md`, `docs/reports/phase-2c.md` | — | each is a placeholder (`user:password`, `CHANGE_ME`), an f-string template or a dummy password to port 1 / `.invalid` (R33 redaction tests) | no change |
| F10 | A dispatched Server Action with a malformed argument list throws a `TypeError` (`500` with digest) | **Low** | dashboard actions | same-origin only; nothing stored, nothing echoed | route probe log | **Accepted (R77)** |
| F11 | Backend serves `/openapi.json`, `/docs`, `/redoc` without a token | **Info** | backend | discloses the route list to local processes only; every `/api/v1` route needs the token | backend probe | accepted under the loopback trust model (R39) |
| F12 | The backend has no `Host` check (a valid token with `Host: evil.example` → `200`) | **Info** | backend | a DNS-rebinding page cannot send the Bearer token or the ingest key | backend probe | by design: loopback bind + credentials |

Bandit totals, first-party production code (`backend/app`, `backend/alembic`, `sdk/src`, `demo_app`, `scripts`; 14,148 lines): High 1 (F5), Medium 4 (F6, F7), Low 38 (F8); `backend/app` and `sdk/src` have one Low false positive between them (B105 on a diagnostics text). Tests (16,067 lines): High 0 after F4, Medium 25, Low 2,764 (2,737 are `assert`). No `nosec` suppressions exist in the code.

### 4.3 Secret scans

- **Browser bundle (T42):** `npm run check:bundle` after the final build: 632 files (100.6 MiB) scanned, 23 in the browser bundle, the token value and 5 secret variable names searched, **0 findings** (also 0 on the clean clone's build: 444 files).
- **Full Git history** (`scripts/qa/secret_scan.py`): 7 local secret values from `.env` and `frontend/.env.local` (6 variables: admin token, API token in both files, pepper, database password, database URL; plus the URL's password part; values never printed) plus 10 credential patterns (OpenAI, GitHub, AWS, Slack, Google, private keys, JWT, ingest keys, long `Bearer` values, URL user-info) over **every blob reachable from every ref, every commit and tag message, and the working files to be committed**. Final run before the commit: 769 blobs, 26 commit and tag messages, 452 working files; **0 secret-value hits**; 35 pattern hits, all URL user-info at the 15 F9 locations, none in a Phase 5B file. **Control run:** a throwaway repository with a planted synthetic value (committed, then deleted from HEAD) and an OpenAI-shaped key: all detected, in history and in the working tree.
- `.env`, `frontend/.env.local` are git-ignored and untracked; the clone's `.env` stayed inside the clone. Logs of every server started in this phase (dashboards :3005, :3006, :3001 clone; backends :8000, :8009, :8010, :8012) and the demo outputs were scanned: 0 secret values, 0 ingest keys, 0 `Bearer` values.

### 4.4 Privacy, authentication, isolation, XSS and CSRF regression (re-run in §8)

| Area | Tests (all passed) |
|---|---|
| Metadata-only ingest, allowlist, value checks | T15, T16, `test_ingest_metadata_privacy.py`, `test_ingest_free_text.py`, `test_normalization_hardening.py`, SDK `test_guards.py` |
| Human-input redaction | T35, `test_redaction.py`, `test_output_redaction.py` (test output never prints a local secret) |
| API authentication | `test_missing_or_invalid_credentials_get_one_identical_401`, `test_writes_authenticate_before_any_validation`; T43 route list (every `/api/v1` router declares `require_admin`); backend probe (§5.2) |
| Project isolation | T28; E2E "project isolation: another project's trace cannot be reviewed or converted from here"; E2E "real mode, cross-origin, method and project-isolation attempts are refused and store nothing" |
| XSS | T41 (E2E and unit), `react/no-danger` lint, static test |
| CSRF / write boundary | E2E "CSRF: cross-site, origin-less and header-less POSTs are refused before any action runs"; E2E "a genuine experiment Server Action POST runs only on its write page"; request-guard unit tests; route probe (§5.1) |

## 5. Server Action route safety (Step 4; R68, R69, R71)

### 5.1 Dashboard (production build, raw sockets)

`scripts/qa/route_probe.py` against `next start` (production build) on 127.0.0.1:3005. Each request is a `POST` with the real `submitDataset` action id and an empty argument list, so a dispatched action refuses (R77) and nothing can be written; dispatch is visible as `Content-Type: text/x-component`. The request target goes on the wire unmodified.

- **Targets (78):** the four write pages; read-only pages; `/favicon.ico`, `/icon.svg`, `/robots.txt`; `/_next/static`, `/_next/static/`, `/_next/static/x`, `/_next/staticx`, `/_next/static-x`, dot-segment, percent-encoded (`%2e%2e`, `%2f`, `%73tatic`, `%5fnext`), backslash, double-slash, case and `;` variants; `/_next/image`, `/_next/image/`, `/_next/image/x`, `/_next/imagex`, `/_next/images`, `/_next/image?url=/datasets`, `/_next/image/../datasets`, `%2f`, `.rsc`, case variants; `/_next`, `/_next/data/…`, `/_next/webpack-hmr`, `/_next/postponed/resume/…`, `/__nextjs_original-stack-frame`; write-page normalization variants (`/datasets/`, `//datasets`, `/./datasets`, `/x/../datasets`, `/%64atasets`, `/DATASETS`, `/datasets.rsc`, `/datasets?_rsc=1`, `/datasets%2f`, `/datasets;x`, `/datasets%00`, …); absolute-form targets.
- **Host/Origin variants (17):** same-origin; no `Origin`; cross-origin; `Origin: null`; look-alike origin; other loopback name; same origin with `Sec-Fetch-Site: cross-site`; foreign `Host`; look-alike host; trailing-dot host; user-info host; `127.1`; `0.0.0.0`; upper-case host; duplicate `Host`; missing `Host`; `X-Forwarded-Host`. Plus `PUT`/`PATCH`/`DELETE`/`OPTIONS` on three paths and `GET` with `X-HTTP-Method-Override: POST`.
- **Result (1339 requests, exit 0):** 40 dispatches, **all on a write page or a target Next.js normalizes to one** (`/./datasets`, `/x/../datasets`, `/datasets.rsc`, `/datasets?_rsc=1`, `/experiments/new.rsc`, `/_next/data/x/datasets.json`), and only under effectively same-origin headers (same-origin, upper-case loopback host, `X-Forwarded-Host`, or a duplicate `Host` whose first value Node keeps). For every such target the proxy refused the same request without `Origin` (`403`, all other origin variants `403`) and with a foreign `Host` (`421`), proving the proxy treated it as the write page. **No other path dispatched.** `/favicon.ico`, `/_next/static`, `/_next/static/../datasets`, `/_next/image/x`, `/_next/image/../datasets` and `/_next/postponed/resume/datasets`: `405` (`421` with a foreign Host); `/_next/static/x` and `/_next/static/..%2fdatasets`: the static handler's `404`, no dispatch; `/_next/image`: the optimizer's `400`; backslash and absolute-form targets: `308`; missing `Host`: `400`; other methods: never dispatched. Positive control (same-origin POST to `/datasets`) dispatched. Dataset and experiment counts in both dev projects: 0 before and after.
- **Live replay with real writes:** the Phase 4D/5A E2E tests ("a genuine experiment Server Action POST runs only on its write page", R68/R71 look-alike paths, "nothing stored" asserted first) passed in the final E2E run.
- ADR-22, ADR-26 and ADR-27 are preserved: page paths are UI entry points, not authorization; the method allowlist and the four write pages are unchanged; no new authentication was introduced.

### 5.2 Backend

`scripts/qa/backend_probe.py` against a throwaway backend on 127.0.0.1:8009 (dev database, rate-limit buckets raised for this process only, so auth failures could not lock out the developer's backend): the 19 registered routes (16 `/api/v1` + health + ingest) in 17 spellings each (trailing and double slashes, dot segments, upper case, `%2f`, `;`, `%00`, percent-encoded letters, backslashes, absolute form) × 5 methods without credentials, and every write route's spellings with the token and an empty body. **1389 requests, 0 violations:** without credentials only `/health/live` and `/health/ready` (public by design) answered `2xx`; everything else `401`/`404`/`405`/`307`/`415`; with the token no write spelling answered `2xx` (`422`/`404`/`307`/`415`), and nothing was stored. Percent-encoded spellings that decode to a route reach `require_admin` (`401`). F11 and F12 are recorded above.

## 6. Benchmark (Step 5)

**Workload (master plan §I, `TEST_MATRIX.md`):** re-ingest 100–1000-span fixtures and measure trace-open time on a 10k-span dataset; report N, hardware, method, p50 and p95. **No target was set, assumed or claimed.**

**Method.** `backend/.venv/Scripts/python -I scripts/qa/benchmark.py --repo <repo> --db llmi_bench5b_test --out <dir>` (raw results: `docs/reports/phase-5b-benchmark.json`).

- A test-owned database (`llmi_bench5b_test`, created and dropped by `createdb`/`dropdb` in the compose PostgreSQL; refused unless the name ends in `_test`), migrated to head, own project and key; a separate backend (`python -m app`, one worker) on 127.0.0.1:8012 with the rate-limit buckets raised for this process only (otherwise the limiter, not the system, would be measured); a production dashboard on 127.0.0.1:3006 reading through it. The developer database and backend were not used.
- SYNTHETIC spans only: 10-span traces (1 SERVER root + 9 CLIENT `chat` inference spans with provider-reported usage, call id, attempt, provenance `synthetic`), uncompressed binary protobuf.
- **A. Ingest:** per batch size (100, 500, 1000 spans), per run: 3 warm-up batches discarded, 15 fresh batches measured, then the same 15 payloads re-sent (re-ingest, the idempotent duplicate path) with table counts checked unchanged; 3 runs.
- **B. Trace open on exactly 10,000 spans** (1 × 2000, 1 × 1000, 10 × 100 and 600 × 10 spans; 612 traces, 9,388 usage rows): API trace detail per trace size, diagnostics, trace list and overview (5 warm-up + 50 measured per run, 3 runs); the dashboard's server-rendered pages for the same traces (3 warm-up + 20 measured per run, 3 runs), full body read.
- Client wall time per request on one persistent HTTP/1.1 connection (`time.perf_counter_ns`, send → last byte); p50/p95 by linear interpolation (the NumPy `linear` definition of T24). Client, servers and the PostgreSQL VM share the machine; requests are sequential.

**A. Ingest** (p50 = median of the three per-run p50s; ranges across runs):

| Batch | Wire bytes | Mode | N | p50 ms | p50 range | p95 range | Spans/s at p50 |
|---|---|---|---|---|---|---|---|
| 100 spans | 40,539 | fresh | 15 × 3 | 112.1 | 93.3–112.2 | 106.4–125.9 | ≈ 890–1,070 |
| 100 spans | | re-ingest | 15 × 3 | 83.3 | 82.1–101.5 | 91.7–126.6 | |
| 500 spans | 202,459 | fresh | 15 × 3 | 415.1 | 411.0–432.9 | 453.5–471.1 | ≈ 1,150–1,220 |
| 500 spans | | re-ingest | 15 × 3 | 368.3 | 359.9–370.5 | 396.1–452.0 | |
| 1000 spans | 404,859 | fresh | 15 × 3 | 889.7 | 885.4–910.8 | 1,015.8–1,090.1 | ≈ 1,100–1,130 |
| 1000 spans | | re-ingest | 15 × 3 | 854.3 | 826.4–855.2 | 947.1–1,079.7 | |

Every request answered `200`; after every re-ingest the `traces`, `spans` and `usage_records` counts were unchanged (9 of 9 checks; T04, T23). Final ingest database: 8,640 traces, 86,400 spans, 77,760 usage rows.

**B. Trace open on the 10k-span dataset:**

| Request | N | p50 ms | p50 range | p95 range | Response bytes |
|---|---|---|---|---|---|
| API trace detail, 10 spans | 50 × 3 | 18.4 | 18.4–18.9 | 22.3–27.0 | 16,520 |
| API trace detail, 100 spans | 50 × 3 | 27.3 | 27.2–27.3 | 31.6–38.2 | 167,199 |
| API trace detail, 1000 spans | 50 × 3 | 106.0 | 104.9–110.8 | 158.3–161.8 | 1,674,808 |
| API trace detail, 2000 spans (the per-trace cap) | 50 × 3 | 183.7 | 183.2–185.4 | 242.4–252.0 | 3,349,811 |
| API diagnostics, 2000 spans | 50 × 3 | 125.7 | 122.2–130.4 | 182.6–188.9 | 23,968 |
| API trace list, `limit=50` | 50 × 3 | 21.2 | 20.9–21.3 | 25.0–27.3 | 35,731 |
| API overview | 50 × 3 | 121.3 | 116.5–124.1 | 130.3–156.1 | 10,078 |
| Dashboard trace page, 10 spans | 20 × 3 | 46.2 | 45.2–53.5 | 58.0–85.2 | 265,425 |
| Dashboard trace page, 100 spans | 20 × 3 | 142.5 | 141.8–149.4 | 159.4–176.8 | 1,908,615 |
| Dashboard trace page, 1000 spans | 20 × 3 | 1,028.3 | 1,026.2–1,043.7 | 1,077.0–1,134.0 | 14,506,586 |
| Dashboard trace page, 2000 spans | 20 × 3 | 1,831.2 | 1,830.7–1,854.1 | 1,906.2–2,003.2 | 26,448,076 |
| Dashboard trace list | 20 × 3 | 52.3 | 52.2–52.7 | 59.0–61.8 | 523,382 |
| Dashboard overview | 20 × 3 | 140.6 | 138.5–141.6 | 148.8–153.3 | 206,108 |

**Observations and known bottlenecks.**

- Ingest is about 1.1 k spans/s on one worker, roughly linear in batch size (≈ 0.9 ms per span). Re-ingest costs 74–96 % of a fresh ingest (89–96 % for 500- and 1000-span batches): a duplicate is decoded, validated and normalized in full before the database's conflict handling discards it.
- API trace detail grows linearly with spans (≈ 0.09 ms and ≈ 1.7 KB per span); the 2000-span cap bounds it.
- **The dashboard trace page dominates on large traces (R76):** about 13 KB of HTML per span (26 MB, about 1.8 s at 2000 spans) against 3.3 MB / 0.18 s of API JSON. A 10-span trace renders in about 46 ms; the demo produces 13 spans in 5 traces.
- The overview (about 120 ms) aggregates the whole 31-day window on every request.
- **Query limits in force** (`SECURITY.md` §3): ingest 4 MiB wire, 16 MiB decompressed, 2048 spans per request; trace detail ≤ 2000 spans and 2000 usage rows; list `limit` ≤ 200; date window ≤ 31 days; rate limits 20/s (ingest), 10/s (API), 30 auth failures/min per process (raised only on the benchmark's own backend).
- **Limitations of these numbers:** one laptop, client and server on the same machine, PostgreSQL in a WSL2 VM with default settings, Windows selector event loop (R34), sequential requests (no concurrency or saturation test), small datasets, no browser parse or paint time, no network. They describe this machine only and are **not** a scalability or production-capacity claim. No persistent queue exists, so no delivery guarantee under backend outage is claimed (R26).

## 7. Clean-clone acceptance (Step 7; T47)

The Phase 5A commit is not on GitHub (no push is allowed), so the isolated clone was made from the local repository: `git clone <repo> C:/Users/PC/AppData/Local/Temp/llmi5b/c` → `fe259e7`, 0 changes, no `.env`, venvs, `node_modules` or `.tools`; the Phase 5B working-tree changes were applied (`git diff --binary HEAD` + the new files), giving the exact tree of this commit. Ports were relocated because the developer stack holds the defaults (documented in `DEMO_SCRIPT.md`); the compose project name `llminsight-5b` kept the developer's containers and volume out of reach.

| Step | Command (from the clone, Git Bash) | Result |
|---|---|---|
| Environment | `python -I scripts/dev/init_env.py`; relocate `POSTGRES_HOST_PORT`/URL 55433, `LLMI_PORT` 8010, OTLP endpoint and `LLMI_API_BASE_URL` 8010, `LLMI_FRONTEND_HOST_PORT=3011` | `.env` created (values not shown), git-ignored; both compose configs valid |
| Compose | `docker compose -p llminsight-5b --profile app up -d --build --wait` | exit 0; postgres, backend, frontend healthy; published on `127.0.0.1:55433` and `127.0.0.1:3011` only; both images built from this tree |
| Migration and init (container) | `alembic current`; `/health/ready`; `pip check`; `python -m app.cli create-key --project demo`; `python -m app.cli seed-prices` | `0005 (head)`; ready; `certifi 2026.10.19`, no broken requirements; project 1 (key captured into the clone's `.env`, never shown); 6 prices inserted |
| Host tools (README steps 2–3) | backend and SDK venvs from the locks, `pip install --no-deps -e backend`, `pip install --no-deps ./sdk`; `alembic upgrade head`, `alembic check` | `pip check` clean in both; head, no drift |
| Host backend (R43) | `backend/.venv/Scripts/python -m app` | ready on 127.0.0.1:8010, same database |
| Synthetic telemetry | `set -a; . ./.env; set +a`; `sdk/.venv/Scripts/python -m demo_app.web --drive` | 5 traces, 13 spans exported and delivered, provenance `synthetic`; no key in the output |
| Golden dataset | `seed_golden_dataset.py --project 1 --approved-by phase5b-clean-clone --api http://127.0.0.1:8010` | dataset 1, 12 cases (G01–G12) |
| Browser demo (Edge, headless, on the compose dashboard :3011; driver checks each step against the API) | overview → traces → failing trace and diagnostics → review → decision → test case refused without approval, then approved → mock A/B → results → case detail | all steps passed (second attempt: the first stopped at a wait in my driver after storing a review on another trace; that review stayed in the clone's throwaway database) |
| Host dashboard (README step 4) | `install_node.py` (fresh download, SHA-256 checked), `init_frontend_env.py --project-id 1`, `npm ci`, `npm run build`, `npm run check:bundle`, `npm start` | exit 0 each; bundle scan 0 findings; `/`, `/traces`, `/traces/{id}`, `/experiments/1`, `/datasets`: `200`, no error state |

**Persisted results, read directly from the clone's PostgreSQL:**

- `test_cases` 13: `dataset_id` 1, `origin_trace_id` `f0f8af6e48060ca3b505cda813454f6c`, `origin_review_id` 2, `approved_by` `phase5b-clean-clone`, `redaction_applied` true. `failure_reviews` 2: the same trace, `schema_violation`, `closed`, `hypothesis_supported`.
- `experiments` 1: dataset 1, `mock`, `golden-prompt-v1` vs `golden-prompt-v2`, `gpt-5-mini`, finished.
- `experiment_results`: 13 rows per version, 13 distinct case ids per version, **the same case-id set under both versions** (`true`); provenance `synthetic` only (26/26); v1 7 passed, 1 not evaluated; v2 5 passed, 2 not evaluated.
- **The human-approved case 13 under both versions:** `golden-prompt-v1` `passed = false` (`schema_violation: …`), `golden-prompt-v2` `passed = true`.
- The results page shows the same (`5b-07-experiment-results.png`): SYNTHETIC banner, "26 synthetic, 0 real", 7/12 vs 5/11 with denominators, no better version named (G12 timed out under v2), cost not compared (v2 "Total unknown"). The case page names "Trace f0f8af6e…, review #2" (`5b-08-case-detail.png`). No token in 96 page responses, no console errors.

Screenshots (`docs/reports/phase-5b-screenshots/`): `5b-01-overview`, `5b-02-traces`, `5b-03-trace-detail`, `5b-04-review-decided`, `5b-05-test-case-approved`, `5b-06-experiment-setup`, `5b-07-experiment-results`, `5b-08-case-detail`.

**Teardown (test-owned resources only):** the clone's host backend and dashboard processes stopped; `docker compose -p llminsight-5b --profile app down -v --rmi local` removed exactly its containers, network, volume `llminsight-5b_pgdata` and the images `llminsight-5b-backend` / `llminsight-5b-frontend`; the benchmark database `llmi_bench5b_test` was dropped. The developer stack (`llminsight-postgres-1`, `llminsight_pgdata`, dev images) was untouched; the dev backend still answered `ready`.

## 8. Complete regression (Step 8; after the last code change)

| Command (from) | Outcome | Exit |
|---|---|---|
| `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` (backend, real PostgreSQL) | **1726 passed**, 0 failed, 0 skipped (298 s; 1 warning: R20 Starlette `httpx` deprecation) | 0 |
| `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` (sdk) | **350 passed**, 0 failed, 0 skipped (81 s) | 0 |
| `npm test` (frontend) | **311 passed**, 0 failed, 0 skipped, 0 cancelled | 0 |
| `npx playwright test` (frontend, real backend, Edge) | **55 passed**, 0 failed, 0 skipped, 0 flaky (2.3 min); teardown `remaining: []` | 0 |
| `npm run typecheck` / `npm run lint` | clean / 0 warnings | 0 / 0 |
| `npm run build` / `npm run check:bundle` | built / 632 files, 23 in the browser bundle, 0 findings | 0 / 0 |
| `ruff check .`, `ruff format --check .` (backend; sdk) | all passed; 140 / 29 files formatted | 0 |
| `ruff check`, `ruff format --check` on `scripts/dev/seed_trace_fixtures.py`, `scripts/dev/e2e_fixtures.py`, `scripts/qa` | all passed; 6 files formatted | 0 |
| `python -m mypy src`; `python -m mypy --config-file pyproject.toml ../demo_app` (sdk) | no issues (8 / 3 files) | 0 |
| `pip check` (backend; sdk) | no broken requirements | 0 |
| `alembic current` / `alembic check` (backend) | `0005 (head)` / no new upgrade operations; migration cycle in the backend suite (T31) | 0 |
| `docker compose config --quiet`; `docker compose --profile app config --quiet` | valid | 0 / 0 |
| Docker image build | both `app` images built from this tree in the clean clone (`up --build`, §7); not rebuilt in the developer's project, to keep its images | 0 |
| `pip-audit` (both locks × PyPI and OSV) | no known vulnerabilities | 0 |
| `pip-audit --path` (both venvs) | `pip` 24.2 only (R75) | 1 (accepted, §4.2 F2) |
| `bandit` (production code; tests) | findings classified in §4.2; no unresolved true positive | 1 (bandit exits 1 on any finding) |
| `npm audit`; `npm audit --omit=dev` | 0 vulnerabilities | 0 / 0 |
| Route probes (`route_probe.py`; `backend_probe.py`) | 0 violations (1339; 1389 requests) | 0 / 0 |
| History and tree secret scan (`secret_scan.py`) | 769 blobs, 26 messages, 452 files: 0 secret-value hits; 35 pattern hits = F9 fixtures only | 1 (pattern hits, triaged) |
| `python -I scripts/verify_phase0.py --quiet`; `git diff --check` | recorded in `PHASE_STATUS.md` (final checks) | — |

## 9. Remaining limitations (R66, R67 and the MVP boundary)

- **R66** (an experiment run is a synchronous request; after a lost response a resubmission creates a second, independent run, no idempotency key): retained as an accepted local MVP limitation. Durable idempotency (a stored client key with a unique constraint, or a two-step create/execute) is required before any real or paid mode.
- **R67** (4 concurrent runs and 15 s per run are per backend process and reset on restart): unchanged. A multi-worker or distributed deployment needs a new concurrency design and a superseding ADR.
- **R68, R69, R71:** resolved earlier and re-verified here (§5): no Server Action runs outside its write pages or without same-origin headers.
- **The MVP boundary:**
  - **Real-provider integration is unverified.** The OpenAI adapter is tested offline only, and no real OpenAI call has ever been made.
  - **Real-provider mode is disabled.** The backend refuses `mode: "real"` (`409`), has no real executor, and does not install `openai`.
  - **Estimated costs are not provider invoices.** They come from provider-reported (here synthetic) usage and the verified catalogue. Unknown stays unknown.
  - **Reviewer and approver identities are self-declared** labels (R55). There is no login.
  - **Heuristic redaction is not a privacy guarantee** (R08, R56). Test inputs must be synthetic.
  - **The MVP is authorized for local use only.** Public or multi-user deployment is prohibited until real authentication, TLS, per-project authorization, a distributed rate limiter, audit logging and a privacy review exist (`SECURITY.md` §7).

## 10. Technical debt and risks

| ID | State after 5B |
|---|---|
| R74 | New, resolved: `certifi` compromised-intermediate root (F1) |
| R75 | New, accepted: `pip` 24.2 in venvs and image, unaudited interpreter and OS packages, unhashed locks |
| R76 | New, accepted: large trace pages (≈ 13 KB HTML per span) |
| R77 | New, accepted: malformed Server Action arguments → `500` |
| R78 | New, resolved: bidi / zero-width characters in test sources |
| R66, R67 | Accepted, unchanged |
| R68, R69, R71 | Resolved; re-verified |
| R09, R23, R34, R43, R73 | Unchanged (per-process limiter and counters; Windows selector loop; host ingest; clean-clone host tools) |

Other technical debt, unchanged: no persistent telemetry queue (R26); price catalogue maintenance (R28, R29); Starlette test-client deprecation (R20); ESLint 9 end of support (R36); the CSP `style-src 'unsafe-inline'` (R38); the re-ingest path does full processing before discarding a duplicate (§6).

## 11. MVP release prerequisites (for the Phase 5 final GitHub release)

1. Architectural review of Phase 5A and Phase 5B (this report).
2. A push of `main` and a green GitHub Actions run on the Phase 5B commit (Linux CI of `fe259e7` and of this commit has not run; nothing was pushed).
3. The annotated `phase-5` tag, only on a commit whose CI passed.
4. The phase prompt's `docs/FINAL_DELIVERY_REPORT.md` and the short project pitch, if the review still wants them in addition to this report and the README.
5. For any use beyond the local machine (not a blocker for the local MVP): R75 remediation (current `pip`, current Python 3.12 patch release, image scan, hash-pinned locks) and every item of `SECURITY.md` §7.

**No release blocker for the local MVP remains:** the 42 critical tests pass, no security finding is open above Low, the clean-clone demo works, and every limitation is documented.

## 12. Files

- **Changed:** `backend/requirements.lock` and `sdk/requirements-dev.lock` (`certifi`); `backend/tests/integration/test_ingest_free_text.py`, `backend/tests/unit/test_case_rules.py`, `backend/tests/unit/test_metadata_privacy.py`, `backend/tests/unit/test_normalization_hardening.py`, `backend/tests/unit/test_safe_regex.py`, `sdk/tests/unit/test_config.py`, `sdk/tests/unit/test_guards.py` (escapes); `docs/ARCHITECTURE.md`, `docs/DEMO_SCRIPT.md`, `docs/PHASE_STATUS.md`, `docs/SECURITY.md`, `docs/SOURCES_AND_RISKS.md`, `docs/TEST_MATRIX.md`.
- **Added:** `README.md`; `scripts/qa/{secret_scan,route_probe,backend_probe,benchmark}.py`; `docs/reports/phase-5b.md`; `docs/reports/phase-5b-benchmark.json`; `docs/reports/phase-5b-screenshots/5b-0{1..8}-*.png`.
- **Not changed:** production backend, SDK and dashboard code; schema and migrations; ADRs; CI workflow; compose and Dockerfiles; pricing.

## 13. Verdict

**PASS for the Phase 5B scope**, on the evidence above:

- All 48 scenarios were executed and passed, with 0 FAIL and 0 SKIP among the 42 critical tests.
- The security audit found two real issues (R74, R78), both fixed and regression-tested. Every other finding is classified, and none is open above Low.
- Route safety was re-verified, with no bypass.
- The benchmark was measured and documented, with no invented targets.
- The clean-clone demo was reproduced end to end from the documented commands.

The MVP is ready for local delivery, pending the architectural review. No `phase-5` tag, no push.
