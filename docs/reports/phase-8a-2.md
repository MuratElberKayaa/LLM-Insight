# Phase 8A-2 — Groq HTTP adapter (offline)

**Date:** 2026-11-09 UTC. **Status: implemented offline and committed locally, not pushed. Offline verification and the architectural review (GO for commit and CI submission preparation, 2026-11-09) are complete, including the Content-Type correction. The checkpoint is NOT closed:** the six PostgreSQL tests and the seven CI jobs are outstanding.

**Checkpoint 8A-3 remains NO-GO. Real Groq and OpenRouter requests remain NO-GO.**

Provider API requests: **0**. No credential was read, printed or created. No `.env`, PostgreSQL, Docker, deployment or billing interaction. One local commit, made after the review; no push, PR, merge, reset, rebase, clean or stash. OpenRouter was not implemented and Experiments are unchanged.

Evidence labels: **EXECUTED** (run on Windows 11, Python 3.12.6), **COLLECTED, NOT RUN**, **NOT RUN**, **DOCS** (official pages read 2026-11-09).

---

## 1. Repository

| Item | Value |
|---|---|
| Branch | `feature/phase-8a-groq-adapter` |
| HEAD | `d10e16b`, the merge of PR #4 (8A-1) |
| 8A-1 CI | Run 42612935755 on `d10e16b`: all seven jobs successful. Read from the public GitHub API; job conclusions only |
| User-owned files | `LLM_INSIGHT_HANDOFF.md`, `docs/OPUS_PHASE7_PROMPTS.md`, `docs/PHASE7_LIVE_DEMO_PLAN.md`: untracked, hashes unchanged |

## 2. Provider contract (DOCS)

Only documentation pages were read: API reference, errors, reasoning and rate limits on `console.groq.com/docs`. Nothing on `api.groq.com` was fetched.

| Topic | Documented | Consequence |
|---|---|---|
| Endpoint and auth | `POST https://api.groq.com/openai/v1/chat/completions`, `Authorization: Bearer` | Pinned as a constant |
| Output limit | `max_completion_tokens`; `max_tokens` deprecated | The former is sent |
| Response | `id`, `object` = `chat.completion`, `model`, `choices`, `usage`, `service_tier`, `x_groq` | `object` and `model` are required by the parser |
| Finish reasons | **Not enumerated.** Only `stop` is shown | Other values follow the 8A-0 table; unknown ones fail |
| Usage details | Timing fields only. `prompt_tokens_details` and `completion_tokens_details` are **not documented** for this endpoint | Mapped only if present |
| Errors | `{error: {message, type}}`; 400, 401, 403, 404, 413, 422, 424, 429, 498, 499, 500, 502, 503 | `message` is never read |
| 200 with an error body | **Not documented** | Handled as a failure anyway |

## 3. What was built

`GroqTextAdapter` and `ObservedGroqClient` implement the unchanged `TextGenerationAdapter`. ADR-42 and ADR-43 record the decisions.

- **Request:** exactly `model`, one user message, `max_completion_tokens` (1 to 1024), `stream: false`, `include_reasoning: false`. Tools, streaming, response formats, sampling settings, service tiers and every extension are refused before sending. `groq/compound*` is refused.
- **HTTP:** direct `httpx` from the existing lock. No custom URL, environment routing, redirect, retry or continuation. Connect 5 s, write 5 s, read 20 s, pool 1 s, 30 s total deadline, 1 MiB response limit, 64 KiB error body limit.
- **Content-Type:** a 200 body is read only with exactly one header holding `application/json` under RFC 9110 syntax. The type and subtype are case-insensitive, parameters must be well formed, and a `charset`, if present, must appear once and be UTF-8. Non-2xx responses are classified by status alone.
- **Telemetry:** one CLIENT span on `llminsight.attrmap.v1`, with no new attribute and no schema change.
- **Cost:** always `unknown`/`price_missing` (ADR-40).
- **Offline lock:** `provenance="real"` always raises while `REAL_EXECUTION_ENABLED` is false, and `synthetic` needs a mock transport. There is no CLI or runner.

**Differences from `reports/phase-8a-0.md`, for review:**

| 8A-0 proposal | Built | Why |
|---|---|---|
| Shared Groq and OpenRouter profiles, `chat_completions_adapter.py`, extra `free-chat` | Groq only, `groq_adapter.py`, extra `groq` | OpenRouter is not authorized here |
| A different response model halts in the guard | The adapter itself returns `failed` with `groq.response.model_mismatch` | The brief forbids silently selecting another model |
| 200 with error uses the provider's code as `error.type` | Constant `groq.response.error` | Keeps provider-controlled text out of `error.type` |
| Real path built with the guard | Real provenance locked in code | The guard does not exist yet |

**Files (16, excluding the three user-owned files):**
- New: `sdk/src/llm_insight_sdk/groq_adapter.py`, `sdk/tests/groq_fixtures.py`, `sdk/tests/unit/test_groq_adapter.py`, `sdk/tests/integration/test_live_groq.py`, `backend/tests/unit/test_groq_contract.py`, `docs/fixtures/groq_client_span.v1.json`, `docs/reports/phase-8a-2.md`.
- Modified: `sdk/src/llm_insight_sdk/providers.py` (docstring only), `sdk/pyproject.toml` (extra `groq`), `docs/ADR.md`, `docs/ARCHITECTURE.md`, `docs/PHASE_STATUS.md`, `docs/SECURITY.md`, `docs/SOURCES_AND_RISKS.md`, `docs/TELEMETRY_CONTRACT.md`, `docs/fixtures/README.md`.

Unchanged: `backend/app`, migrations, lock files, CI, `demo_app`, the frontend, and the Gemini and OpenAI adapters.

## 4. PASS (EXECUTED)

Backend and Python tests never used a database or `.env`.

Every result in this section was produced after the final code change. The earlier 1428-pass regression covered the code before the Content-Type correction and is superseded.

**Full SDK regression:** a new virtual environment was built from `sdk/requirements-dev.lock` (`pip check` clean), and a copy of `sdk/` was installed non-editable with `--no-deps`, as CI does. All 13 installed modules are AST-identical to the repository. Provider key variables were removed from the test environment.

Command, from `sdk`: `python -m pytest -q -p no:cacheprovider --no-header --tb=short`.

**Result: 1589 tests, 1508 passed, 0 failed, 0 errors, 81 skipped** (491 s).

| Module group | Passed |
|---|---|
| Groq adapter (`test_groq_adapter`) | 350 |
| Gemini: adapter 75, endpoint 41, live guard 66, runner 37, content runner 183, preview 46, scenarios 93 | 541 |
| Gemini content integration (the offline part of `test_live_gemini_content`) | 34 |
| OpenAI: adapter 58, responses 59, live endpoint 52, live guard 80, live runner 43 | 292 |
| Instrumentation 22, telemetry 27, config 74, demo web 13 | 136 |
| Privacy: guards 145, redaction 3 | 148 |
| `test_install`, non-editable | 7 |
| **Total** | **1508** |

**Other gates:**

| Check | Command (from the named directory) | Result |
|---|---|---|
| Backend unit, including 29 Groq contract tests | `backend`: `python -m pytest tests/unit -q -rs -p no:cacheprovider` | 1202 passed, 0 failed, 0 skipped |
| Groq contract and pricing | `backend`: `pytest tests/unit/test_groq_contract.py tests/unit/test_pricing.py -q` | 140 passed |
| Backend Ruff | `backend`: `ruff check .`, `ruff format --check .` | PASS, 143 files |
| SDK Ruff | `sdk`: `ruff check .`, `ruff format --check .` | PASS, 52 files |
| mypy strict | `sdk`: `mypy src`, `mypy --config-file pyproject.toml ../demo_app` | PASS, 13 and 9 files |
| QA self-tests | root: `python -I -m unittest discover -s scripts/qa/tests` | 214 ran, OK, 15 skipped |
| Bandit gate | Bandit raw scan, then `bandit_baseline_check.py evaluate` | PASS; High 1, Medium 5, Low 38, 44 of 44; baseline unchanged |
| History baseline | root: `secret_history_gate.py validate-baseline` | PASS, 38 entries |
| Local secret scan | `secret_scan.py` logic, no env file, each hit matched to the baseline | 41 hits, all reviewed, 0 unexplained, none `groq_key` |
| Changed and new files | All detectors, excluding the user-owned files | 16 files, 0 matches |
| Doc check | root: `python -I scripts/verify_phase0.py` | 828 PASS, 0 FAIL |
| Whitespace | `git diff --check`, and a scan of new files | Clean |

**Security and contract review:**

| Requirement | Evidence | Result |
|---|---|---|
| Keys, prompts and responses stay out of telemetry and logs | 29 named leakage tests covering spans, exported protobuf bytes, DEBUG logs, `repr`, `asdict` and exceptions, including a refused Content-Type; mutations 34, 35, 36, 44, 45 and 50 caught | PASS, offline |
| Groq price applicability stays fail-closed | Listed, zero and conflicting catalogue rows (`test_pricing`, `test_groq_contract`), including the exact contract span | PASS, offline |
| `attrmap.v1` unchanged | Shared fixture tested on both sides, 0 dropped attributes; no backend application change | PASS, offline |
| ADR-42 and ADR-43 describe the implementation | Each stated request field, outcome, bound and boundary condition was compared with `groq_adapter.py`; ADR-43 gained the Content-Type rule | Accurate |
| No real execution path | `REAL_EXECUTION_ENABLED` is false, no key variable is named in the adapter, no Groq reference in `demo_app`, no OpenRouter reference in the adapter | Confirmed |

**Test coverage against the original brief:**

| Required | Where | State |
|---|---|---|
| 200 valid, malformed and error bodies; 400, 401, 403, 404, 429, 5xx | Adapter suite | PASS |
| Timeout, connection failure, cancellation, no retry | Adapter suite, including real loopback sockets | PASS |
| Invalid usage and model identifiers | Adapter suite | PASS |
| Truncated and oversized responses, redirects | Adapter suite, including real loopback sockets | PASS |
| Unexpected fields, content and key leakage, exported protobuf | Adapter suite | PASS |
| Endpoint, routing and TLS configuration | Adapter suite | PASS |
| Content-Type validation | 80 new tests, listed in §5.1 | PASS |
| CLIENT span, parent correlation, allowlist | Adapter suite with the OTLP stub | PASS |
| SDK and backend contract compatibility | Shared fixture, tested on both sides | PASS |
| Groq never priced, including a matching catalogue row | Backend contract tests | PASS |
| Overview, Traces and Model Comparison | `test_live_groq.py` | **NOT RUN** (§7) |

## 5. Mutation verification

**Result: 50 of 50 caught. 0 survived, 0 invalid, 0 unfinished.** This combines three runs. Each mutation was caught by a test failing against the scratch install. A further 17 supplementary mutations, reported separately and not counted in the 50, were also all caught.

| Run | Mutations | Result |
|---|---|---|
| First (15:46, interrupted by machine standby at about 16:21) | 1, 3–11, 13–18, 20–34 | 31 caught. Evidence preserved |
| Second, after the two test corrections | 12, 36–50 | 16 caught. Evidence preserved |
| Third, on the final code with the Content-Type correction | 2, 19, 35 | 3 caught |
| Supplementary, second run | S1: TLS hostname half removed | Caught |
| Supplementary, third run | C1–C16, against the new parser | 16 caught |

The third run's control passed 350 of 350 tests on the final code, and the scratch install was restored and AST-identical afterwards.

### 5.1 Effect of the Content-Type correction on earlier evidence

The change was measured by comparing every top-level definition and method with a copy saved before the edit:

| Finding | Detail |
|---|---|
| Changed | `ObservedGroqClient._exchange` (one condition) and the module docstring |
| Added | `is_json_content_type`, `_has_json_content_type`, `_unquote` and six constants |
| Unchanged | 97 definitions |
| Mutation patterns | All apply exactly once to the final source, except 19, whose target line was the one replaced |

**Rerun:** 19, because its code was replaced, and 2 and 35, because they sit in the one changed function.

**Not rerun:** the other 47. Their code is identical, and the tests that caught them were not edited. Every fixture still uses `application/json`, so those tests follow the same path as before, as the 350-test control confirms.

**Content-Type tests (80 new, all passing):**

| Tests | Count | What they prove |
|---|---|---|
| Accepted media types | 15 | `application/json`, any case, charset forms, quoted values, empty and other parameters |
| Rejected media types | 39 | `application/x-not-json`, `text/plain`, `json` in a parameter or quotes, `+json` types, malformed syntax, other charsets, repeated charset, Unicode case-folding, over-long values, empty |
| Pathological input | 1 | Rejected quickly |
| Adapter, misleading type | 4 | A valid body is stopped with nothing parsed |
| Adapter, valid type | 3 | The same body reaches the parser |
| Checked before the body | 1 | No chunk is read |
| Leakage on refusal | 1 | The body stays out of spans, logs and outcome |
| Non-2xx with any type | 15 | Status classification is preserved; error text is not read |
| Several headers | 1 | Refused |

**Supplementary mutations:**

| # | Mutation | Caught by test |
|---|---|---|
| S1 | TLS hostname half removed | `unsafe_tls_is_the_only_live_boundary_problem[required-certificate-no-hostname-check]` |
| C1 | Substring check restored (the original defect) | `any_other_media_type_is_rejected[x-not-json]` |
| C2 | Media type case-sensitive | `the_valid_body_used_below_completes_with_a_json_content_type[APPLICATION/JSON]` |
| C3 | ASCII flag removed | `any_other_media_type_is_rejected[unicode-case-folding-s]` |
| C4 | Prefix match instead of full match | `any_other_media_type_is_rejected[json-prefix]` |
| C5 | Length bound removed | `any_other_media_type_is_rejected[too-long]` |
| C6 | Charset not enforced | `any_other_media_type_is_rejected[latin1]` |
| C7 | Repeated charset accepted | `any_other_media_type_is_rejected[repeated-charset]` |
| C8 | Charset value case-sensitive | `a_json_media_type_is_accepted[charset-upper-no-space]` |
| C9 | Quoted charset not unquoted | `a_json_media_type_is_accepted[quoted-charset-mixed-name]` |
| C10 | Charset name case-sensitive | `any_other_media_type_is_rejected[latin1-mixed-name]` |
| C11 | Several headers accepted | `several_content_type_headers_are_refused` |
| C12 | Missing header accepted | `valid_json_with_no_content_type_is_refused` |
| C13 | Body read before the check | `the_content_type_is_checked_before_the_body_is_read` |
| C14 | Parameter syntax not validated | `any_other_media_type_is_rejected[json-prefix]` |
| C15 | `+json` suffix accepted | `any_other_media_type_is_rejected[json-patch]` |
| C16 | Content-Type enforced on error responses | `an_unreadable_error_body_still_reports_the_status[step0]` |

### 5.2 The 50 mutations

**Why the first 31 and the next 16 stayed valid after the test corrections:** the log's `N passed, 1 failed` shows which test caught each, and none of those tests was edited. The test corrections removed one parameter and added new tests, and no fixture changed.

**The corrected tests:**
- **Mutation 12 (TLS):** four unsafe contexts, including `CERT_OPTIONAL` with hostname checking on. The tests assert TLS is the only boundary problem, and that construction is refused with no connection attempted. They inspect client configuration, because the check reads a real transport's TLS context; no connection is opened.
- **Mutation 19 (Content-Type):** an otherwise valid completion with a wrong, empty or absent Content-Type must be refused with nothing parsed. A control shows the same body completes with `application/json`. In the third run it was reapplied by removing the new guard.

Mutation 12 was reapplied to the formatted source with the same effect: it disables only the `verify_mode` half.

| # | Mutation | Status | Run | Caught by test |
|---|---|---|---|---|
| 1 | Retry after a failure | Caught | First | `an_http_failure_is_an_error_outcome_and_is_never_retried[400-invalid_request]` |
| 2 | Redirects followed | Caught | Third | `a_redirect_is_refused_and_the_key_does_not_travel[301]` |
| 3 | Real provenance enabled | Caught | First | `real_provenance_is_refused_at_this_checkpoint` |
| 4 | Synthetic accepts a real transport | Caught | First | `synthetic_provenance_cannot_use_a_real_transport` |
| 5 | No re-check before send | Caught | First | `the_client_is_checked_again_immediately_before_every_send` |
| 6 | Pinned request check removed | Caught | First | `a_request_that_is_not_the_pinned_one_is_never_sent` |
| 7 | Base URL check removed | Caught | First | `every_routing_misconfiguration_is_refused[base-url]` |
| 8 | `trust_env` check removed | Caught | First | `every_routing_misconfiguration_is_refused[trust-env]` |
| 9 | Mount check removed | Caught | First | `every_routing_misconfiguration_is_refused[mount]` |
| 10 | Event hook check removed | Caught | First | `every_routing_misconfiguration_is_refused[event-hook]` |
| 11 | Transport retries check removed | Caught | First | `transport_retries_are_a_live_boundary_problem` |
| 12 | TLS check removed | Caught | Final | `unsafe_tls_is_the_only_live_boundary_problem[optional-certificate-with-hostname-check]` |
| 13 | Timeout check removed | Caught | First | `each_live_boundary_condition_alone_refuses[timeout]` |
| 14 | Per-request timeout removed | Caught | First | `every_request_carries_the_bounded_timeouts` |
| 15 | Oversized stream allowed | Caught | First | `an_oversized_stream_stops_being_read_at_the_limit` |
| 16 | Declared length ignored | Caught | First | `a_declared_oversized_body_is_refused_without_reading_it` |
| 17 | Compressed body accepted | Caught | First | `a_compressed_body_is_refused_without_decoding_it` |
| 18 | Deadline removed | Caught | First | `a_slowly_dripped_body_hits_the_total_deadline` |
| 19 | Content type ignored | Caught | Third | `valid_json_with_a_wrong_content_type_is_refused[text/html]` |
| 20 | Multi-model breakdown ignored | Caught | First | `unusable_detail_counts_make_the_whole_usage_unknown[extra5]` |
| 21 | Total mismatch ignored | Caught | First | `usage_is_read_or_left_unknown[usage6]` |
| 22 | Missing prompt counts as 0 | Caught | First | `usage_is_read_or_left_unknown[usage3]` |
| 23 | Subset ceiling removed | Caught | First | `unusable_detail_counts_make_the_whole_usage_unknown[extra0]` |
| 24 | Boolean accepted as a count | Caught | First | `usage_is_read_or_left_unknown[usage9]` |
| 25 | Model mismatch ignored | Caught | First | `a_different_response_model_is_a_failure_and_stays_visible[stop]` |
| 26 | Error object in a 200 ignored | Caught | First | `an_error_object_in_a_200_is_a_failure_even_with_choices` |
| 27 | `object` field ignored | Caught | First | `an_unusable_200_is_malformed_with_no_invented_metadata[stream-object]` |
| 28 | Missing model tolerated | Caught | First | `an_unusable_200_is_malformed_with_no_invented_metadata[no-model]` |
| 29 | Several choices accepted | Caught | First | `an_unusable_200_is_malformed_with_no_invented_metadata[two-choices]` |
| 30 | Empty text is completed | Caught | First | `a_returned_response_maps_to_its_outcome[step3]` |
| 31 | Length is completed | Caught | First | `a_returned_response_maps_to_its_outcome[step0]` |
| 32 | Content filter is completed | Caught | First | `a_returned_response_maps_to_its_outcome[step2]` |
| 33 | Tool call accepted | Caught | First | `a_tool_call_is_not_an_answer` |
| 34 | Provider message kept as reason | Caught | First | `an_http_failure_is_an_error_outcome_and_is_never_retried[400-invalid_request]` |
| 35 | httpx exception chained | Caught | Third | `the_key_and_prompt_are_absent_from_every_exception_detail` |
| 36 | JSON error chained | Caught | Final | `a_malformed_body_is_not_kept_on_the_exception` |
| 37 | Unsupported parameters ignored | Caught | Final | `every_unsupported_parameter_is_refused_before_sending[tools]` |
| 38 | Model format not checked | Caught | Final | `an_invalid_or_implicit_model_is_refused[openai/gpt-oss-20b:free]` |
| 39 | Compound models allowed | Caught | Final | `an_invalid_or_implicit_model_is_refused[groq/compound]` |
| 40 | Output limit not bounded | Caught | Final | `an_invalid_output_limit_is_refused[0]` |
| 41 | Input size not bounded | Caught | Final | `invalid_input_is_refused[too-long]` |
| 42 | `stream` sent true | Caught | Final | `exactly_one_minimal_request_reaches_the_pinned_endpoint` |
| 43 | Reasoning requested | Caught | Final | `exactly_one_minimal_request_reaches_the_pinned_endpoint` |
| 44 | Key on the HTTP client | Caught | Final | `a_completed_call_emits_exactly_the_contract_span` |
| 45 | Completion `repr` shows text | Caught | Final | `content_and_the_key_never_leave_the_outcome_boundary[completed]` |
| 46 | Response ID not validated | Caught | Final | `a_missing_or_invalid_response_id_is_never_invented[]` |
| 47 | Withheld ID not counted | Caught | Final | `a_credential_shaped_response_id_is_withheld_and_counted` |
| 48 | 429 category wrong | Caught | Final | `an_http_failure_is_an_error_outcome_and_is_never_retried[429-rate_limited]` |
| 49 | Cost attribute exported | Caught | Final | `a_completed_call_emits_exactly_the_contract_span` |
| 50 | Output text exported | Caught | Final | `a_completed_call_emits_exactly_the_contract_span` |

Mutation logs are in the session's scratch directory, not in the repository, so this table is the durable record.

Rows marked "Final" are the second run.

**Scratch restoration:** after the third run, the scratch adapter is AST-identical to the repository. No mutation touched the repository. **Mutation testing shows that these chosen faults are detected. It does not prove the absence of others.**

## 6. FAIL

**None** in the final runs. Earlier failures:

| Failure | Cause | Fix |
|---|---|---|
| 7 failures, 6 errors on the first test run | Test defects: a fixture call, a wrong counter name, over-long test IDs, an eagerly decoding mock, proxy variables reaching the OTLP exporter | Tests corrected |
| Mutations 12 and 19 survived | Test weaknesses (§5) | Tests corrected |
| `verify_phase0` 8 failures | Documentation named unregistered ADRs and reserved field names | Wording corrected; the checker was not changed |
| Ruff S105 on a new constant, and one over-long test input | A name containing "token"; a test string one character too long | Constant renamed, with no suppression; test corrected |

**Production defect found and corrected:** the Content-Type guard was a substring test for `json`, so `application/x-not-json` passed it. Found during verification and replaced at the review's direction. No other production defect was found.

## 7. SKIP and NOT RUN

**SKIP (81, all with one reason):** the backend interpreter was deliberately absent, so these tests stop before any `.env` read or database connection. **None is a pass.**

| Test module | Skipped |
|---|---|
| **`test_live_groq` (new in 8A-2)** | **6** |
| `test_live_gemini_content` (database part) | 25 |
| `test_openai_live` | 11 |
| `test_live_backend` | 10 |
| `test_demo_web_live` | 8 |
| `test_diagnostics_live` | 7 |
| `test_live_gemini` | 7 |
| `test_live_responses` | 7 |

QA self-tests also report 15 skipped, unchanged from 8A-1.

**NOT RUN:**

| Item | State |
|---|---|
| **Six PostgreSQL integration tests** in `sdk/tests/integration/test_live_groq.py` | **COLLECTED, NOT RUN. Not a pass.** 6 collected; 6 skip with no backend; 6 error with `LLMI_TEST_REQUIRE_DB=1`, so they cannot skip silently in CI. Their expectations have never been executed and could fail on first contact |
| **All seven GitHub CI jobs** on this code | **NOT RUN.** The code is committed locally and not pushed |
| Backend integration | NOT RUN; no backend application file changed |
| Full history gate on a clean clone | NOT RUN before the local commit. New files were checked by the detectors |
| pip-audit | NOT RUN; no dependency changed |
| Frontend and E2E | NOT RUN; no frontend file changed |
| Any real Groq request | NOT RUN, by instruction |

The six test IDs, in `tests/integration/test_live_groq.py`:
- `test_a_groq_trace_reaches_postgresql_and_every_read_view`
- `test_outcomes_are_stored_once_with_unknown_cost[step0-ERROR-429-usage_missing]`
- `test_outcomes_are_stored_once_with_unknown_cost[step1-ERROR-timeout-usage_missing]`
- `test_outcomes_are_stored_once_with_unknown_cost[step2-ERROR-groq.response.malformed-usage_missing]`
- `test_outcomes_are_stored_once_with_unknown_cost[step3-UNSET-None-price_missing]`
- `test_outcomes_are_stored_once_with_unknown_cost[step4-UNSET-None-usage_missing]`

## 8. Unresolved risks and uncertainties

| Item | Note |
|---|---|
| Free Plan limits | The rate-limit page's visible table is for the Developer plan. Free limits and model availability are **unverified** (B1) |
| Rate limits | Not enforced by the adapter. A 429 is reported and never retried. The per-process budget belongs to the guard |
| R115, parser built from documentation | Requiring `object`, an exact response model and a matching total may be too strict for real responses. A mismatch fails closed instead of guessing |
| Content-Type of real responses | Groq does not document the header. A response using `+json`, the unregistered `charset=utf8` or another charset would be `malformed_response`. This is deliberate, and the first authorized real call must confirm the actual value |
| Reasoning accounting | Whether reasoning counts toward the completion count or the output limit is **undocumented** (B5). A reasoning model can return `length` with no text |
| `include_reasoning` | Sent on every request. Behaviour on a non-reasoning model is undocumented |
| Finish reasons | Only `stop` is documented. `length`, `content_filter` and `error` follow the 8A-0 design |
| Read views | Overview, Traces and Model Comparison depend on the six tests not yet run. The offline contract covers ingest only |
| Real transport | TLS and the real `api.groq.com` path have never been exercised. Loopback tests used plain HTTP |
| Debug logging | `httpx` logs the URL at INFO. Tests ran on a mock transport, so low-level `httpcore` logging was not exercised |
| Exposure through `provider_response` | The answer text is available to the calling application by design. It is hidden from `repr` and `asdict` |
| R116 | Diagnostics do not interpret Groq finish reasons |
| R110, R111 | Unchanged |
| Platform | All local results are Windows; CI is Linux |
| Header line | The "Last updated" line of `PHASE_STATUS.md` still describes 7C as pending, as noted in 8A-1 |

R102 (npm advisory, deadline 2026-11-30 UTC) and R105 remain open. The Gemini Free Tier evidence guard expires 2026-11-11 13:00 UTC and was not changed. Phase 7D is not complete.

## 9. Recommendation

**Architectural review: GO for commit and CI submission preparation (2026-11-09).** Next: push and CI by the owner, with all seven jobs green, including the six PostgreSQL tests.

**Checkpoint 8A-3 (guard and endpoint): NO-GO now.** Once those two steps are complete, the offline evidence supports a conditional GO, offline only.

**Real Groq and OpenRouter requests: NO-GO.** Blockers B1 to B7 of `reports/phase-8a-0.md` remain.

```text
PHASE / DATE: 8A-2 / 2026-11-09 UTC
BRANCH: feature/phase-8a-groq-adapter on d10e16b, committed locally, not pushed
PROVIDER REQUESTS / DOCKER / DATABASE / .ENV: 0 / none / none / not read
SDK CLEAN INSTALL, FINAL CODE: 1589 tests, 1508 passed, 0 failed, 81 skipped (database-backed)
BACKEND UNIT: 1202 passed. QA: 214 OK, 15 skipped. DOC CHECK: 828 PASS, 0 FAIL
MUTATIONS: 50 of 50 caught across three runs; 0 survived, 0 invalid, 0 unfinished
SUPPLEMENTARY MUTATIONS: 17 of 17 caught
NOT RUN: 6 PostgreSQL tests, 7 CI jobs
8A-3: NO-GO. REAL REQUESTS: NO-GO
```

## Sources (read 2026-11-09)

Groq: [API reference](https://console.groq.com/docs/api-reference), [errors](https://console.groq.com/docs/errors), [reasoning](https://console.groq.com/docs/reasoning.md), [rate limits](https://console.groq.com/docs/rate-limits). Several properties sit behind collapsed page elements and were not visible in the text read.
