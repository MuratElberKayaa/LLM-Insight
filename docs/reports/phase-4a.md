# Phase 4A — Evidence-Based AI Failure Diagnostics

- **Date:** 2026-10-25 (UTC; the machine's local clock is UTC+3).
- **Instruction (user, Phase 4A):** build the first evidence-based diagnostic capability on metadata already stored by the platform; separate observed facts from possible explanations and unsupported claims; stop after Phase 4A.
- **Delivered:** a deterministic, metadata-only diagnostics engine (`backend/app/services/diagnostics/`), the read-only endpoint `GET /api/v1/traces/{trace_id}/diagnostics` (`backend/app/api/diagnostics.py`), contract `llminsight.diagnostics.v1` with ruleset `llminsight.diagnostics.rules.v1` (`ARCHITECTURE.md` §5.6), and unit, PostgreSQL integration and live SDK end-to-end tests.
- **Not done (by instruction):** Phase 4B/4C (Failure Review, Trace-to-Testset, datasets, golden-set evaluation, experiments, Optimization Lab), any frontend workflow, LLM-as-judge, real OpenAI calls, public deployment, tags, pushes, history rewrites.
- **Untouched:** the home-directory repository, global Git/Node configuration, credentials, the `llminsight-postgres-1` container and its volume, the dev database content (no seeding, no migration), the frontend, the SDK source, dependencies and lock files, the CI workflow.

## 1. Baseline verification (before any change)

| Check | Result |
|---|---|
| Git root | `C:/Users/PC/Desktop/LLM_Insight_Claude_Code_MVP_Paketi` (own repository) |
| Branch / HEAD / tree | `main`, `e5a631c` (Phase 3 closure), clean |
| `phase-3` tag | annotated (`git cat-file -t` → `tag`), peels to `e5a631c` = HEAD; remote `refs/tags/phase-3` = tag object `55d2f4e` → `e5a631c` |
| Remote | `origin` = `https://github.com/MuratElberKayaa/LLM-Insight`; `git ls-remote` `refs/heads/main` = `e5a631c` |
| Latest GitHub Actions run | `40211917319` on `e5a631c`: `completed`, `success` (public API, read-only) |
| PostgreSQL | `llminsight-postgres-1` healthy on `127.0.0.1:55432`; dev DB at `0002 (head)` |
| Backend `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` | **1104 passed**, 0 failed, 0 skipped (98 s), exit 0 |
| SDK same command | **343 passed**, 0 failed, 0 skipped (77 s), exit 0 |
| Frontend `npm run typecheck` / `npm test` | exit 0 / **237 pass**, 0 fail, 0 skipped |

The backend and dashboard dev servers were not running; the suites start their own backend on disposable `*_test` databases.

## 2. Contract review and mapping to the locked Phase 4

Read: `CLAUDE.md`, `MVP_MASTER_PLAN.md` (§A, §H Phase 4, §I), `CLAUDE_PHASE_PROMPTS.md` §5, `PRD.md`, `ADR.md`, `ARCHITECTURE.md` (§1, §4.4–4.12, §5–5.5), `SECURITY.md`, `TELEMETRY_CONTRACT.md`, `METRICS_CATALOG.md`, `TEST_MATRIX.md`, `PHASE_STATUS.md`, `SOURCES_AND_RISKS.md`, `reports/phase-3-final.md`, plus the trace read API, its repository, the ingest usage/attribute mapping and the SDK's error classification.

**Exact locked acceptance requirements of T33–T40 (`TEST_MATRIX.md`, unchanged):**

| ID | Scenario | Expected outcome |
|---|---|---|
| T33★ | Failure review create | Valid enum values → `201`; invalid `issue_type` → `422`; duplicate open → `409` |
| T34★⛔ | No approval no test case | Missing or false `approved` → `422`, no row |
| T35★⛔ | Redaction before case save | Email / phone / key-like input masked before insert; `redaction_applied=true` |
| T36★ | Golden JSON pass | Valid output passes the JSON Schema + field rule |
| T37★ | Golden JSON fail | Schema violation fails with `evaluation_detail` |
| T38 | Prompt A/B same dataset | Both versions run on identical case IDs |
| T39★ | Evaluation provenance labels | Each result carries `real`/`synthetic`; mock → synthetic |
| T40 | Missing expected output handling | `passed=NULL` ("not evaluated"), excluded from the pass-rate denominator |

**Mapping.** Phase 4A (automatic, metadata-only diagnostics) implements **none** of T33–T40. They test the human Failure Review (`failure_reviews`, CR-03), the human-approved Trace-to-Testset, the deterministic golden-set engine and the A/B experiment: the locked Phase 4 deliverables, which need the §4.8–4.12 migration. All eight stay `NOT RUN`, and no acceptance criterion was reinterpreted.

**Conflict analysis (stop condition of the instruction).** No conflict with the approved architecture was found, so implementation proceeded within these constraints:

| Locked rule | Phase 4A position |
|---|---|
| ADR-10 / PRD §5: no automatic root-cause claim; a cause is a hypothesis | Every hypothesis is `verified: false`, phrased "Possible explanation (not verified)", with `not_established` lines; `root_cause` is listed as not assessed |
| PRD §3: in Failure Review *a human* records evidence and hypothesis | The diagnostics write nothing to `failure_reviews` and use a separate category vocabulary; they are input for the human review (PRD §3 note added) |
| ADR-19 / ADR-07: no content | Only stored, allowlisted metadata is read; no prompt is reconstructed; no content field exists to read |
| ADR-21: column contract binding, migration only with an ADR note | No table, column, index or migration; results derived on request |
| ADR-01/02: modular monolith, one PostgreSQL | One new service package and one router in the backend process |
| CR-06 / §5.1: admin token, project scope, indistinguishable `404`, no `403` | Same `require_admin`, same statements, same `404` (byte-identical to the detail's) |
| §5 API table is the contract | Endpoint added to the table and specified in §5.6 as an additive amendment by this instruction (the precedent is `/api/v1/projects`, Phase 3C) |
| No LLM-as-judge (ADR-10, PRD P2) | No model is called; `method.language_model_used: false` |

**Scope note for the architectural review:** the master plan names this phase "Diagnose" through the human Failure Review only. Automatic diagnostics are scope added by this instruction. They are not a P0 item and do not advance the Phase 5 gate (0 FAIL on the 42 critical tests, including T33–T37, T39).

## 3. Diagnostic contract `llminsight.diagnostics.v1`

The full schema is in `ARCHITECTURE.md` §5.6. The five parts the instruction requires map onto per-finding fields:

| Required part | Field(s) | Rule |
|---|---|---|
| 1. Observed facts | `observed_facts[{name, value}]`, `established[]` | Values computed from stored rows; `established` restates only what the rows show |
| 2. Supporting evidence | `evidence[]` (span references with the stored fields used), `evidence_total`, `subject` | Never empty: no finding without evidence |
| 3. Diagnostic hypothesis | `hypothesis{code, statement, error_interpretation, verified: false}` | Unverified possible explanation; no scores |
| 4. Evidence limitations | `limitations[{code, text}]`, `data_complete`, `not_established[]`, trace-level `coverage` | Incompleteness is explicit, per finding and per trace |
| 5. Investigation steps | `investigation_steps[]` | Constant, rule-specific steps |

What triggered a finding: `category` + `trigger` (the rule's condition text). Which spans support it: `evidence[].span_id` (+ `subject`). Whether data is incomplete: `data_complete`, `coverage.complete`, limitations. What is and is not established: `established` / `not_established`. Stable machine identifiers: `category`, `group`, `hypothesis.code`, `error_interpretation.code`, limitation `code`, finding `id` (`<category>:<trace|call:<call_id>|span:<span_id>>`).

**Vocabulary rules (enforced by tests):** all prose is a catalogue constant; no stored value is interpolated into a sentence; no "confidence", "probability" or "score"; no wording that implies a model reasoned (`likely`, `we believe`, `the AI`, …); every response lists `not_assessed` categories with reasons; `method` states `deterministic_rules`, `language_model_used: false`, `content_used: false`.

## 4. Rule catalogue and evidence mapping

Every input below was verified to exist in the stored schema (`ARCHITECTURE.md` §4.4–4.6) and to be written by the ingest (`TELEMETRY_CONTRACT.md` §3) before the rule was implemented.

| # | Category (group) | Stored inputs | Trigger | Evidence | Hypothesis code |
|---|---|---|---|---|---|
| 1 | `inference_call_failed_after_retries` (reliability) | `spans.status`, `error_type`, attrs `gen_ai.operation.name`, `llminsight.call_id`, `llminsight.attempt` | ≥ 2 attempts with one call ID, all ERROR | every attempt | `persistent_failure_across_attempts` + error interpretation |
| 2 | `inference_call_failed` (reliability) | same | the call's only stored attempt is ERROR; or an ERROR attempt without a usable call ID (limitation `no_call_id`) | the attempt | `failed_single_attempt` + error interpretation |
| 3 | `inference_call_recovered_after_retry` (reliability) | same + trace status | a call with an ERROR attempt whose highest-numbered attempt is not ERROR; facts `trace_status`, `other_error_spans` | every attempt | `transient_failure_recovered` + error interpretation |
| 4 | `non_inference_span_error` (reliability) | `spans.status`, `error_type`, `parent_span_id`, `http.response.status_code` | a non-inference span is ERROR; variant by failed inference attempts in its stored subtree | the span + failed inference descendants | `related_to_failed_inference_below` / `outside_instrumented_inference` (no error interpretation: a server's own 500 is not a provider status) |
| 5 | `application_validation_failed` (application_signal) | `llminsight.validation.passed` (bool), `llminsight.validation.rule` | `passed` is boolean `false` | the span | `application_check_failed` |
| 6 | `inference_output_truncated` (provider_signal) | `gen_ai.provider.name`, `gen_ai.response.finish_reasons`, `gen_ai.request.max_tokens` | provider `openai`, finish reason `length` | the attempts | `output_cut_at_token_limit` |
| 7 | `inference_content_filtered` (provider_signal) | same | provider `openai`, finish reason `content_filter` | the attempts | `provider_content_filter_applied` |
| 8 | `token_usage_missing` (usage_and_cost) | `usage_records.input_tokens`/`output_tokens`, span status, operation | non-ERROR attempt with NULL input, or NULL output (not embeddings) | the attempts | `usage_not_reported_by_instrumentation` |
| 9 | `usage_ledger_row_missing` (usage_and_cost) | presence of the span's usage row, `gen_ai.response.id`, provider | inference span without a usage row (only when usage rows are not truncated) | the spans; fact `duplicate_identity_in_trace` | `call_identity_already_recorded` |
| 10 | `usage_inconsistent` (usage_and_cost) | `cost_unknown_reason`, token subsets | reason `usage_inconsistent`; facts recompute which subset exceeds its total | the attempts | `instrumentation_reported_inconsistent_usage` |
| 11 | `cost_unknown_price_missing` (usage_and_cost) | `cost_unknown_reason`, models, `openai.response.service_tier`, `server.address` | reason `price_missing` | the attempts | `no_matching_stored_price` |
| 12 | `orphan_spans` (trace_structure) | `parent_span_id`, parent stored (over all stored spans) | a parent ID that is not stored | the orphans | `parent_not_received` |
| 13 | `multiple_root_spans` (trace_structure) | `parent_span_id` | ≥ 2 spans without parent | the roots | `several_entry_points` |
| 14 | `no_root_span` (trace_structure) | same | no root **and** the analysis covered every stored span | orphans + cycle members | `root_not_received` |
| 15 | `parent_cycle` (trace_structure) | parent links among analysed spans | a link cycle | cycle members | `client_assigned_inconsistent_ids` |
| 16 | `retry_sequence_incomplete` (metadata_integrity) | call ID, attempt numbers | attempt numbers are not 1..n without gaps (linear-time gap scan) | every attempt | `attempts_not_stored` |
| 17 | `attempt_metadata_inconsistent` (metadata_integrity) | call ID, attempt, status | duplicate attempt number, unusable number, failure after success, attempt without call ID, unusable call ID | the attempts | `call_metadata_not_sdk_shaped` |
| 18 | `attributes_dropped_at_ingest` (metadata_integrity) | `server_dropped_attributes_count` | count > 0; other findings on such spans get `evidence_attributes_dropped` and `data_complete: false` | the spans | `attributes_not_allowlisted_or_invalid` |

**`error.type` interpretation (failed inference attempts only, exact match):** `429`, `openai.RateLimitError` → `rate_limit_status_recorded`; `5xx`, `openai.InternalServerError` → `server_error_status_recorded` (says explicitly that no provider outage is established); other `4xx` → `client_error_status_recorded`; `timeout`, `openai.APITimeoutError` → `timeout_recorded`; `openai.APIConnectionError` → `connection_error_recorded`; several classes → `mixed_error_classes_recorded`; anything else → `error_type_not_interpreted`; absent → `error_type_not_recorded`. The identifiers were verified in the installed `openai` 3.28.0 source and the SDK's own classifier (`SOURCES_AND_RISKS.md`, Phase 4A sources). There is no substring or fuzzy matching.

**Candidates evaluated and not implemented:** elevated latency (§6); provider outage (§6); recovery of failed attempts without a call ID (impossible to group without the ID, R51); finish reasons of other providers (vocabulary not verified for them).

## 5. False-positive and false-negative limits

| Rule | Can fire wrongly when | Can miss when |
|---|---|---|
| 1–3 retries | an instrumentation re-uses one call ID for distinct calls, or mislabels status (client-asserted, R49) | spans have no call ID (R51); later attempts are not exported yet (`export_may_be_incomplete`); the analysis was truncated (marked) |
| 1–3 interpretation | a client labels an unrelated failure `429`/`5xx` (R50) | another instrumentation uses identifiers outside the table (shown as not interpreted) |
| 3 recovered | the final attempt is UNSET although the application then failed (the root is reported separately) | a failure follows a success in one call ID (reported as inconsistent metadata instead) |
| 4 non-inference error | an expected, handled error is still marked ERROR by the instrumentation | the failing span was dropped or is unexported |
| 5 validation | the application's check is wrong | the application records no validation attributes |
| 6–7 finish reasons | — (provider-reported value) | providers other than `openai`; finish reasons dropped at ingest |
| 8 missing tokens | the provider legitimately returns no usage for a successful call | failed attempts are excluded by design (usage there is normally absent); embeddings output is not evaluated |
| 9 ledger row | — (a stored fact) | the colliding row in another trace is not identified (`single_trace_scope`); suppressed when usage rows are truncated |
| 10–11 cost | — (stored ingest reason) | none: the stored reason is the trigger |
| 12–15 structure | clock-independent; a parent in a rejected batch looks missing (that is the point) | spans beyond the 2000-span bound (`no_root_span` suppressed, others marked incomplete); cycles through unanalysed spans |
| 16–17 metadata | an instrumentation with another numbering scheme | values dropped at ingest look absent (rule 18 flags the drop) |
| 18 dropped | — (a stored count) | the names of dropped attributes are never stored |

## 6. Unsupported diagnostic categories (returned as `not_assessed` in every response)

| Category | Reason |
|---|---|
| `elevated_latency` | No approved comparison basis: the contracts define no latency objective or threshold (threshold alerts M21 are P1), and a percentile of peer attempts would flag a fixed share by construction. Durations are facts only (R52) |
| `semantic_quality` | Correctness, relevance and hallucination are not observable in metadata; no content is stored (ADR-19) and no evaluation result is linked to a trace |
| `prompt_quality` | Prompt content is never stored and is not reconstructed |
| `provider_outage` | One trace cannot establish a provider-wide incident; no provider status data is stored |
| `root_cause` | Requires human review (Failure Review, ADR-10) |

## 7. Privacy and provenance behaviour

- **Inputs:** stored, allowlisted rows only (`traces`, `spans`, `usage_records`). Prompts, completions, system instructions, tool definitions, prompt variables, exception messages and status messages are never stored (ADR-19, R21) and so cannot be read. Nothing is reconstructed.
- **Outputs:** a fixed set of validated fields; never span names, raw attribute objects, response-ID values or `cost_usd`. Proven by injecting content sentinels into attributes (including always-denied keys), exception and status messages, and identity attributes: absent from the response and the DEBUG log (unit, integration and live tests).
- **Untrusted metadata:** every attribute read is type-checked (`bool` is not a count; non-object attributes are empty; unhashable operation names are not inference; malformed finish reasons and validation flags are ignored); values are never interpolated into text; bounded loops (the attempt-gap scan is linear even for attempt 2^31−1).
- **Provenance:** `trace.provenance` with `provenance_verified: false` on the trace and every evidence item. A per-finding limitation names the provenance of its inference evidence: `synthetic_telemetry` ("describe test or mock traffic, not observed behaviour of a real provider"), `provenance_reported_real` (not cryptographically verified), `provenance_unknown`, `provenance_mixed`. SYNTHETIC traffic is never presented as real.
- **Access:** unchanged admin model; authentication before validation and before any query; project-scoped statements; the `/api/v1` bucket; read-only snapshot; `Cache-Control: no-store`; no CORS.

## 8. Tests and results

### 8.1 New tests (diagnostic test groups `D01`–`D20`; these are not matrix IDs)

| Group | What is proven | Where (positive + counterexample) |
|---|---|---|
| D01 | Auth before validation and lookup (missing / wrong / scheme → identical `401`), `/api/v1` rate limit | integration |
| D02 | Malformed trace IDs and projects → `422` naming the field, no echo; other methods → `405` | integration |
| D03 | Cross-project, unknown trace and unknown project → one byte-identical `404` (identical to the detail's); results scoped with identical trace IDs in two projects | integration |
| D04 | Single failed attempt; failed attempt without call ID cannot be grouped | unit, integration, live |
| D05 | Multiple failed attempts reported as one call with every attempt; not reported as recovered | unit, integration, live |
| D06 | Retry then success is `recovered`, stays distinct from the stored trace status `error` (R27), `other_error_spans` | unit, integration, live |
| D07 | `error.type` interpretation exact-match only (16 cases); no provider text for uninterpreted or missing types; a 5xx never establishes an outage | unit |
| D08 | Non-inference errors: related vs outside inference; handled provider error invents no root error; unhandled one does | unit, live |
| D09 | Missing usage is unknown, not zero (storage, detail API, diagnostics); failed-attempt usage is expected; embeddings | unit, integration |
| D10 | Unknown cost is not zero: `price_missing` reported, no `cost_usd` rendered anywhere | unit, integration, live |
| D11 | Inconsistent usage from ingest; double instrumentation → one ledger row, missing row explained | unit, integration |
| D12 | Orphans, multiple roots, no root (complete analysis only), cycles; orphan finding disappears when the parent arrives | unit, integration |
| D13 | Retry metadata integrity: gaps, duplicates, failure after success, attempt without call ID, huge attempt numbers (bounded time and output) | unit |
| D14 | Malformed client metadata: 9 bad attempt values, 6 bad call IDs, 5 non-object attribute forms, 8 operation names, 6 finish-reason forms, 5 validation flags; via ingest: dropped and counted, then reported | unit, integration |
| D15 | Application validation and OpenAI finish reasons, with counterexamples (other provider, `stop`, non-boolean flags) | unit, integration |
| D16 | Truncated analysis → every finding incomplete; caps (100 findings, 25 evidence) with totals; absence rules suppressed | unit, integration |
| D17 | Determinism: 25 shuffles give byte-identical JSON; ingest order and re-export do not change the bytes; idempotent reads; inputs not mutated; reads never write | unit, integration, live |
| D18 | Efficiency: 4 statements for a 2-span and a 61-span trace (no N+1); read-only transaction; DB outage → `503` | integration |
| D19 | No leakage (content sentinels, fake key, e-mail, span names, raw attributes, tokens); every sentence is a catalogue constant; unverified hypotheses without scores; no model-reasoning wording; no semantic category; synthetic never shown as real | unit, integration, live |
| D20 | End to end: SDK → OTLP/HTTP protobuf → live `python -m app` → PostgreSQL → read API → diagnostics; every evidence field compared with its stored column, summary recomputed by SQL, roles equal the detail API | live (7 tests) |

Files: `backend/tests/unit/test_diagnostics_engine.py` (108 tests with parametrization), `backend/tests/integration/test_diagnostics_api.py` (35), `sdk/tests/integration/test_diagnostics_live.py` (7).

**Mutation check (10/10 killed):** each mutation applied to `engine.py`, both backend test files run, the file restored byte for byte (SHA-256 compared):

| Mutation | Killed by |
|---|---|
| M1 `no_root_span` ignores coverage | `test_no_root_is_reported_only_when_the_whole_trace_was_analysed` |
| M2 substring `error.type` matching | `test_error_type_interpretation_is_exact_match_only[RateLimit-…]` |
| M3 failed attempts count as missing usage | `test_recovered_call_stays_distinct_from_the_trace_level_error` |
| M4 recovery ignores the final attempt | `test_failure_after_success_is_inconsistent_metadata_not_a_recovery` |
| M5 no provenance limitation | `test_synthetic_provenance_is_never_presented_as_real` |
| M6 error type interpolated into the hypothesis | `test_every_sentence_is_a_catalogue_constant` |
| M7 raw attributes echoed in evidence | `test_unlisted_attribute_values_never_reach_the_output` |
| M8 findings unordered | `test_findings_follow_catalogue_order_then_subject` |
| M9 truncation keeps `data_complete` | `test_truncated_analysis_marks_every_finding_incomplete` |
| M10 ledger-row rule without the truncation guard | `test_usage_row_rule_is_suppressed_when_usage_rows_were_truncated` |

The first M5 attempt was itself faulty (the mutated expression fell back to the original code) and "survived"; the corrected mutation was killed. Recorded so the 10/10 is not overstated.

### 8.2 Regression (Windows 11, Python 3.12.6, Node 22.23.4; after the last code change)

| # | Command (directory) | Result | Exit |
|---|---|---|---|
| 1 | `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` (backend) | **1247 passed, 0 failed, 0 skipped**, 1 warning (R20), 180 s: 1104 baseline + 108 unit + 35 integration | 0 |
| 2 | same (sdk; unit + live backend) | **350 passed, 0 failed, 0 skipped**, 77 s: 343 baseline + 7 new live tests | 0 |
| 3 | `ruff check .` / `ruff format --check .` / `pip check` (backend) | passed / 95 files formatted / no broken requirements | 0 |
| 4 | `alembic current` / `alembic check` (backend, dev DB) | `0002 (head)` / no new upgrade operations | 0 |
| 5 | ad-hoc `mypy --strict` (SDK venv mypy 2.4.0, `--ignore-missing-imports`) on `app/services/diagnostics/` + `app/api/diagnostics.py` | no issues in 4 source files | 0 |
| 6 | `ruff check .`, `ruff format --check .`, `mypy src`, `mypy --config-file pyproject.toml ../demo_app`, `pip check` (sdk) | passed; 29 files formatted; strict mypy no issues (8 and 3 files); clean | 0 |
| 7 | `npm run typecheck` / `npm run lint` / `npm test` (frontend) | 0 errors / 0 problems / **237 pass, 0 fail, 0 skipped** | 0 |
| 8 | `docker compose config --quiet`; `docker compose --profile app config --quiet` (root) | valid | 0 / 0 |
| 9 | `python -I scripts/verify_phase0.py --quiet` (root) | `SUMMARY: 729 PASS, 0 FAIL` (after these documentation changes) | 0 |
| 10 | `git diff --check` (root) | no output | 0 |
| 11 | Secret and privacy scan of every new or changed file and the diff (scratch script; values never printed) | 7 local secret values (admin/dashboard token, pepper, DB URL and password): 0 hits; key/JWT/Bearer patterns: 0 hits; no added `NEXT_PUBLIC_` line; `.env` / `frontend/.env.local` untracked | 0 |
| 12 | Mutation check (§8.1) | 10/10 killed, `engine.py` restored (SHA-256 identical) | — |
| 13 | Read-only dev-DB smoke: dev `python -m app` on :8000, diagnostics for **every** stored trace (2 projects, 28 SYNTHETIC traces incl. the Phase 3B orphan, cycle, hostile-label and 771-span traces), each read twice and compared with the detail API | 28 × `200`, 0 nondeterministic, 0 evidence mismatches, 0 incomplete; categories: price_missing 19, recovered 4, failed 4, token_usage_missing 1, non-inference error 1, orphan 1, cycle 1, dropped attributes 1. Server log: 0 tracebacks (13 × `429` from the unthrottled first attempt of the script: the `/api/v1` limiter working). Dev DB afterwards identical to the Phase 3 final counts (projects 2, keys 5 with 0 active, traces 28, spans 846, usage 43, prices 6, `0002`) | 0 |

**Failures met on the way, and their root causes (none left open):**
- The first full backend run gave **1 failed, 1246 passed**: `tests/unit/test_health.py::test_routes_are_health_otlp_ingest_and_read_api` pins the exact public route set and did not yet list the new route. Every phase extends this inventory deliberately, so the new path was added to it (the assertion stays exact). Then 1247 passed.
- The documentation verifier's secret scan flagged two synthetic `sk-…` probes written as literals in the new tests. They are now built at runtime (`"sk-" + …`), as the existing privacy tests do.
- A test helper passed a keyword twice (fixed in the helper). The first M5 mutation was ineffective (§8.1).

**Not run, with reason:**
- **Playwright E2E (44):** not affected. No frontend file changed, and the dashboard calls none of the new paths (its backend path allowlist is unchanged); the backend change is one additive router. The last run is the Phase 3 final (44/44). No UI result is claimed for Phase 4A.
- **Frontend build and bundle scan:** no frontend file changed; typecheck, lint and unit tests were re-run as the relevant regression.
- **Compose image build:** no Dockerfile, compose service or dependency changed; `docker compose config --quiet` (both profiles) was validated.

### 8.3 Test matrix

**No status changed.** T33–T40 stay `NOT RUN` (§2). T18, T27 and T28 keep `PASS (phase-1c/2b)`: the diagnostics apply their rules to a new response, which is recorded here and not as a status change. No Phase 4B/4C item is marked complete.

## 9. Frontend scope boundary and integration requirements (later subphase)

Phase 4A's acceptance does not require a UI, and none was built. Requirements recorded for a later Trace Detail integration:
1. Add `traces/<32 hex>/diagnostics` to the server-side path allowlist (`frontend/lib/server/backend.ts`), built only from the already-validated trace ID.
2. Decode against a strict `llminsight.diagnostics.v1` schema; refuse unknown `contract_version` values; keep `verified: false` and `provenance_verified: false` visible.
3. Present each finding as facts → evidence (links to the waterfall's span rows) → "Possible explanation (not verified)" → limitations → steps; never render a hypothesis as a status or root cause; show `data_complete: false` and `coverage.complete: false` prominently; show `not_assessed`.
4. Render all values as text only (T41 rules); never style or rank findings by an invented severity score.
5. A failed diagnostics request degrades only its own section of the Trace Detail page.
6. Browser evidence (Playwright + screenshot) before any UI PASS claim.

## 10. Architecture and ADR compliance

ADR-01 (monolith; the SDK still never imports the backend; the live test talks HTTP only), ADR-02 (PostgreSQL only), ADR-04 (traces only), ADR-07/19 (metadata only), ADR-08 (local admin API), ADR-10 (no LLM judge; hypotheses only), ADR-11 (unknown never 0), ADR-13 (inference detection by `gen_ai.operation.name` only), ADR-21 (no schema change), CR-06 (one admin token), CR-08 (existing rate limiter). No ADR is superseded. The OTLP ingest, the attribute mapping (`llminsight.attrmap.v1`), pricing and the existing read endpoints are unchanged.

## 11. Known risks

R49 (false positives/negatives from client-asserted metadata), R50 (limited `error.type` table), R51 (no grouping without call ID), R52 (no latency diagnostic), R53 (no dashboard view yet); recorded in `SOURCES_AND_RISKS.md`. Existing: R27 (recovered call still makes the trace `error`; now explained by finding 3), R39, R42, R48. The strict mypy run on the new backend modules is ad hoc: mypy is not part of the backend toolchain, so it used the SDK venv's pinned mypy 2.4.0 with `--ignore-missing-imports` (SQLAlchemy types are not resolved there).

## 12. Phase 4B prerequisites

- Architectural review of this report, including the scope note in §2.
- Decide whether a human Failure Review may be **pre-filled** from a diagnostic finding. If it may, the copied evidence stays labelled as engine output and the human still writes `issue_type`, `evidence` and `hypothesis`. Note that the CR-03 `issue_type` enum (`retrieval_missing`, …) is semantic, while diagnostic categories are metadata-level. There is no 1:1 mapping, and none should be invented.
- The Phase 4 migration for `ARCHITECTURE.md` §4.8–4.12 (with T31 re-run), then T33–T35 with redaction; `reviews` in the trace detail stops being `[]`.
- Decide whether T48 re-verification covers the review and testset views (Phase 3 final §8).
- Keep real-provider runs out unless explicitly approved with a budget.

## 13. Files changed

- **New:** `backend/app/services/diagnostics/{__init__,catalog,engine}.py`, `backend/app/api/diagnostics.py`, `backend/tests/unit/test_diagnostics_engine.py`, `backend/tests/integration/test_diagnostics_api.py`, `sdk/tests/integration/test_diagnostics_live.py`, `docs/reports/phase-4a.md`.
- **Changed:** `backend/app/main.py` (router registration), `backend/tests/unit/test_health.py` (route inventory: the new path), `docs/ARCHITECTURE.md` (§1, §5 table, §5.6), `docs/SECURITY.md` (§1, §3), `docs/PRD.md` (§3 note), `docs/TEST_MATRIX.md` (Phase 4A note; no status change), `docs/SOURCES_AND_RISKS.md` (Phase 4A sources, R49–R53), `docs/PHASE_STATUS.md`.
