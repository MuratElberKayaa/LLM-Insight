# Phase 8A-3 — Groq Free Plan guard and endpoint boundary (offline)

**Date:** 2026-11-09 UTC, corrected 2026-11-10 UTC. **Status: implemented and verified offline. The pre-commit architecture review found a fail-open (§12). Its correction (F1, F2) is applied and verified in §13, and was approved for one local commit (§14). Committed locally, not pushed.**

§1 to §11 describe the code before the correction. Their totals were produced on that code and are kept as the record of that run. §13 holds the results on the corrected code.

**The provider execution path remains disabled. Real Groq and OpenRouter requests remain NO-GO.**

Provider API requests: **0**. No credential was read, printed or created. No `.env` was opened: the backend tests ran from a scratch copy that holds none. No PostgreSQL, Docker, deployment or billing interaction. No commit, push, PR, merge, reset, rebase, clean or stash. OpenRouter was not implemented and Experiments are unchanged. Network use was limited to PyPI (two throwaway environments from the existing locks) and one read of job conclusions from GitHub's public API.

Evidence labels: **EXECUTED** (run on Windows 11, Python 3.12.6), **COLLECTED, NOT RUN**, **NOT RUN**.

---

## 1. Repository

| Item | Value |
|---|---|
| Branch | `feature/phase-8a3-free-plan-guard` |
| HEAD | `eece127`, the merge of PR #5 (8A-2) |
| 8A-2 CI | Run 42675227030 on `eece127`, `main`: all seven jobs successful. Read from the public GitHub API; job conclusions only |
| ADR registry | ADR-43 was the last entry, so this checkpoint records **ADR-44**, the number `reports/phase-8a-0.md` §16.3 reserved |
| User-owned files | `LLM_INSIGHT_HANDOFF.md`, `docs/OPUS_PHASE7_PROMPTS.md`, `docs/PHASE7_LIVE_DEMO_PLAN.md`: still untracked, not opened or written |

## 2. What was built

`llm_insight_sdk.groq_live` holds the policy, the endpoint boundary and `FreePlanSession`, which is the send gate of a real `ObservedGroqClient`. ADR-44 records the decisions.

**Why execution stays disabled.** Two independent locks, each needing its own reviewed code change:

| Lock | Shipped value | Effect |
|---|---|---|
| `groq_adapter.REAL_EXECUTION_ENABLED` | `False` | A real client cannot be built |
| `FREE_PLAN_EVIDENCE_VERIFIED_AT`, `FREE_PLAN_MODELS` | `None`, empty | The policy refuses in every environment |

**No model is listed, because none has been verified.** Blockers B1 and B2 are open, and a guessed allowlist would have been fabricated evidence. The allowlist belongs to the evidence gate.

**The send sequence.** Each attempted send runs these in order, after the request is built. Only the span's creation follows the last step.

1. The adapter's own client check and its pinned-request check.
2. The full policy, re-read against the clock and the environment.
3. The endpoint boundary (§3).
4. The body that will actually be sent, parsed back and compared with the exact allowed shape. The model and limits are taken from it, not from the caller's arguments.
5. The session and process slot, taken under the session lock.

Any refusal raises `LiveRefusal`, sends nothing and creates no span.

**Objectives.**

| # | Objective | Where |
|---|---|---|
| 1 | Fail-closed Free Plan policy | `evaluate_free_plan_policy` |
| 2 | HTTPS boundary before every send | `send_boundary_problems`, called from `authorize_send` |
| 3 | Consent, attestation, evidence, model, limits | Policy; §4 lists each refusal |
| 4 | One request, concurrency one, zero retries, no fallback, no custom endpoint, no paid mode | Hard caps; one-model rule; no parameter or setting exists for the rest |
| 5 | Unsafe client, proxy, redirect, mock under real, invalid TLS, expired evidence | §3 and §4 |
| 6 | Zero provider calls on every refusal | Tests assert 0 requests on the fake transport, and 0 attempts in the ledger |
| 7 | `not_sent` versus `possibly_sent` | §5 |
| 8 | ADR | ADR-44 |

**Reuse.** `_Reader`, `Refusal`, `LiveRefusal` and `input_token_bound` are reused from `live`. No generic provider abstraction was added: only one provider is authorized, so its shape would be a guess. `gemini_live.py` is unchanged.

**Adapter changes, all additive to the real path.** The synthetic path behaves as before, confirmed by the 350 adapter tests.

| Change | Why |
|---|---|
| `send_gate` keyword; mandatory and exactly `FreePlanSession` under `real`; refused under `synthetic` | A real client cannot exist without the guard, and a synthetic result cannot look guarded |
| The gate is called after the request is built, and told when transmission starts and when a response arrives | §5 |
| `LiveRefusal` passes through `GroqTextAdapter` | **Defect found while building this:** it would have become a generic `error` outcome, hiding that nothing was sent |
| `GroqCompletion.service_tier_withheld` | An unreadable tier was indistinguishable from an absent one, so it could not halt |

**Differences from `reports/phase-8a-0.md`, for review:**

| 8A-0 proposal | Built | Why |
|---|---|---|
| Module `free_chat_live.py`, shared with OpenRouter | `groq_live.py`, Groq only | OpenRouter is not authorized here |
| A prefix check refuses a swapped key | Known foreign prefixes are refused; no Groq prefix is required | Groq's format is undocumented (R110) |
| Timeout at most 30 s | Fixed at 30 s; any other stated value is refused | The adapter's deadline is fixed, so another value would be silently ignored |
| Exit codes and the dry run | Not built | They belong to the runner checkpoint |

**Files (10, excluding the three user-owned files):**
- New: `sdk/src/llm_insight_sdk/groq_live.py`, `sdk/tests/unit/test_groq_live_guard.py`, `docs/reports/phase-8a-3.md`.
- Modified: `sdk/src/llm_insight_sdk/groq_adapter.py`, `sdk/tests/unit/test_groq_adapter.py` (one test now passes the required gate, §7), `.env.example`, `docs/ADR.md`, `docs/SECURITY.md`, `docs/SOURCES_AND_RISKS.md`, `docs/PHASE_STATUS.md`.

Unchanged: `backend/`, migrations, lock files, CI, `demo_app`, the frontend, and the Gemini and OpenAI adapters. No `.env` file was touched.

## 3. Endpoint boundary

`send_boundary_problems` runs the existing `live_groq_client_problems` first, then adds what that check does not cover.

| Added check | Why it was missing |
|---|---|
| TLS trusts exactly the pinned `certifi` bundle | The existing check confirms verification is on, but not which roots are trusted, so a private CA would have passed |
| TLS 1.2 or newer | Not checked |
| No TLS key logging, and `SSLKEYLOGFILE` refused | **Found in the stdlib source:** `ssl.create_default_context` honours it even with `trust_env=False`, so `build_groq_http_client` would have written session keys to that file. A test confirms the boundary catches it |
| No proxy, Unix socket, local address or socket option on the pool | A direct-looking `HTTPTransport` can still route elsewhere |
| Default network backend, one connection, no keep-alive, HTTP/1.1 only | Not checked |
| Exactly `POST` to the pinned URL, the exact header set, one `Authorization`, the pinned `Host`, only the bounded timeouts | Re-verified at the last moment, on the request object itself |

Redirects are never followed. A 3xx response is reported, halts the session and causes no second request.

## 4. Refusals

Every row was tested on its own, and asserted to make 0 provider calls.

| Area | Refused |
|---|---|
| Execution | Lock off; no evidence; clock without a timezone |
| Mode | Provider mode not `real`; Groq mode unset, `paid`, `developer`, wrong case |
| Consent | Missing; arbitrary; the OpenAI, Gemini or OpenRouter phrase; wrong case |
| Key | Missing, blank, OpenAI, OpenRouter, Gemini or LLM Insight shape, invalid characters. Never quoted |
| Attestation | Plan missing, `developer`, `paid`, `unknown`, wrong case. Date missing, not a date, basic format, ISO week, impossible, non-ASCII digits, future, two days old |
| Evidence | Missing; over 14 days; dated in the future |
| Provider, model | Other or second provider; empty, unlisted, compound, wrong-case or suffixed model; two models; a listed model other than the configured one |
| Caps | Requests, input, output, retries, concurrency and timeout outside their allowed values |
| Environment | Each of 13 base-URL, proxy, CA-file and key-log variables. `NO_PROXY` alone is allowed |
| Client | Mock transport, proxy, mount, redirects, `trust_env`, base URL, timeouts, auth, event hook, custom header, TLS off, unverified TLS, other roots, old TLS, key logging, HTTP/2, pool and keep-alive limits, transport retries, Unix socket, local address, socket option, replaced backend, wrong type |
| Request | Plain HTTP, host, port, path, query, user info, method, extra or missing header, two `Authorization` headers, `Host` override, longer timeout, SNI override |
| Body | 18 shapes: not JSON, extra field, `service_tier`, streaming, reasoning, several messages, system role, content parts and others. Limits read from the body |
| Timing | Evidence or attestation going stale, consent or key removed, a proxy added or the plan changed after the client was built; the client changed while the request is built |
| Gate | Absent, lookalike, subclass, removed after construction, carried by synthetic provenance |
| Repeat | After success, after each failure, after a timeout, after a proven-unsent failure, from a second session, concurrently |

The tests use the real client shape and replace only `httpx.HTTPTransport.handle_request`, so the real boundary code runs and there is no test seam in production code.

## 5. Request state

| State | Meaning | When |
|---|---|---|
| `not_sent` | **Proven** unsent | Refused by the guard; interrupted before the send began; `httpx.ConnectError`, `ConnectTimeout` or `PoolTimeout` |
| `possibly_sent` | Ambiguous | `ReadTimeout`, `WriteTimeout`, `ReadError`, `WriteError`, `RemoteProtocolError`, any other failure, or an interruption during the send |
| `sent` | A response arrived | Any status, including a failure while reading the body |

The connect-phase rule was read from the audited `httpcore` 1.0.9 source: those errors are raised while connecting and before the first request byte. Other versions fail `groq_library_problems`. **No state allows a second request**, so the label changes reporting only.

**Post-call halts:** a service tier other than `on_demand` or null, or one that cannot be read; a response naming another model; `blocked_api_access` or `402`; a 3xx; reported usage above the request's bounds. A halt only records the problem. It cannot prevent or undo the request already made.

## 6. PASS (EXECUTED)

Every result in this section was produced on the final code.

**Full SDK regression:** a new virtual environment built from `sdk/requirements-dev.lock` (`pip check` clean), with a copy of `sdk/` installed non-editable with `--no-deps`, as CI does. All 14 installed modules are AST-identical to the repository. Provider key variables were removed from the test environment.

Command, from `sdk`: `python -m pytest -q -p no:cacheprovider --no-header --tb=short -rf`, with `LLMI_BACKEND_PYTHON` pointing at a path that does not exist.

**Result: 1894 tests, 1813 passed, 0 failed, 0 errors, 81 skipped** (608 s).

| Module group | Passed |
|---|---|
| Groq guard (`test_groq_live_guard`, new) | 305 |
| All previously existing SDK tests, including 350 Groq adapter tests | 1508 |
| **Total** | **1813** |

**Other gates:**

| Check | Command (from the named directory) | Result |
|---|---|---|
| Backend unit | scratch copy of `backend`: `python -m pytest tests/unit -q -rs -p no:cacheprovider` | 1202 passed, 0 failed, 0 skipped |
| Backend privacy and cost | scratch copy: `pytest tests/unit/test_groq_contract.py tests/unit/test_pricing.py tests/unit/test_metadata_privacy.py tests/unit/test_redaction.py -q` | 475 passed |
| Backend Ruff | `ruff check .`, `ruff format --check .` | PASS, 143 files |
| SDK Ruff | `sdk`: `ruff check .`, `ruff format --check .` | PASS, 54 files |
| mypy strict | `sdk`: `mypy src`, `mypy --config-file pyproject.toml ../demo_app` | PASS, 14 and 9 files |
| QA self-tests | root: `python -I -m unittest discover -s scripts/qa/tests` | 214 ran, OK, 15 skipped |
| Bandit gate | Bandit 1.9.4 raw scan over the CI targets, then `bandit_baseline_check.py evaluate` | PASS; High 1, Medium 5, Low 38, 44 of 44; baseline unchanged |
| History baseline | root: `secret_history_gate.py validate-baseline` | PASS, 38 entries |
| Changed and new files | The repository's detectors, excluding the user-owned files | 1 match: the existing `.env.example` `url_userinfo` placeholder, identical at `HEAD` and allowlisted. 0 reportable |
| Doc check | root: `python -I scripts/verify_phase0.py` | 828 PASS, 0 FAIL |
| Whitespace | `git diff --check` | Clean |

## 7. Mutation verification

**Result: 112 of 112 caught. 0 survived after the test additions, 0 invalid, 0 unfinished.** Each mutant was a private copy of the installed package placed first on `PYTHONPATH`, so neither the repository nor the clean install was modified.

| Run | Mutations | Result |
|---|---|---|
| First | All 112 | 109 caught, 3 survived |
| Second, after three tests were added | B09, S04, S07 | 3 caught |

**The three survivors:**

| # | Mutation | Why it survived | Test added |
|---|---|---|---|
| B09 | Proxy on a plain pool not checked | No test set one; `HTTPTransport(proxy=)` produces a different pool type, refused elsewhere | `test_a_proxy_on_a_plain_pool_is_a_boundary_problem` |
| S04 | Repeat-send state check removed | The process slot also refuses, hiding the session's own limit | `test_the_session_limit_holds_without_the_process_slot` |
| S07 | Reservation outside the session lock | A thread race that rarely fires | `test_the_slot_is_taken_while_the_session_is_locked` |

The first run also exposed one weakness before it started: the routing-variable test took its parameters from the list being tested. An explicit list was added, and it catches P10, P11 and P12.

**Mutation testing shows that these chosen faults are detected. It does not prove the absence of others.** The logs are in the session's scratch directory, so the table below is the durable record.

| # | Mutation | Status | Run | Caught by test |
|---|---|---|---|---|
| P01 | consent not required | Caught | First | `test_an_api_key_alone_enables_nothing` |
| P02 | any consent accepted | Caught | First | `test_each_setting_alone_refuses[consent-other]` |
| P03 | provider mode not required | Caught | First | `test_an_api_key_alone_enables_nothing` |
| P04 | Groq mode not required | Caught | First | `test_an_api_key_alone_enables_nothing` |
| P05 | execution lock not in the policy | Caught | First | `test_as_shipped_a_perfect_environment_is_refused` |
| P06 | key presence not required | Caught | First | `test_each_setting_alone_refuses[key-unset]` |
| P07 | foreign key prefix allowed | Caught | First | `test_each_setting_alone_refuses[key-openai-shape]` |
| P08 | key shape not checked | Caught | First | `test_each_setting_alone_refuses[key-with-space]` |
| P09 | routing variables ignored | Caught | First | `test_each_routing_variable_refuses[GROQ_BASE_URL]` |
| P10 | SSLKEYLOGFILE not refused | Caught | First | `test_the_required_routing_variables_are_all_listed` |
| P11 | HTTPS proxy variable not refused | Caught | First | `test_the_required_routing_variables_are_all_listed` |
| P12 | GROQ_BASE_URL not refused | Caught | First | `test_the_required_routing_variables_are_all_listed` |
| P13 | missing plan accepted | Caught | First | `test_each_setting_alone_refuses[plan-unset]` |
| P14 | non-free plan accepted | Caught | First | `test_each_setting_alone_refuses[plan-developer]` |
| P15 | plan compared case-insensitively | Caught | First | `test_each_setting_alone_refuses[plan-case]` |
| P16 | strict date shape removed | Caught | First | `test_each_setting_alone_refuses[date-basic-format]` |
| P17 | future attestation accepted | Caught | First | `test_each_setting_alone_refuses[date-future]` |
| P18 | stale attestation accepted | Caught | First | `test_each_setting_alone_refuses[date-two-days-old]` |
| P19 | attestation window widened | Caught | First | `test_each_setting_alone_refuses[date-two-days-old]` |
| P20 | attestation date not in UTC | Caught | First | `test_the_attestation_date_is_compared_in_utc` |
| P21 | missing evidence accepted | Caught | First | `test_as_shipped_a_perfect_environment_is_refused` |
| P22 | future evidence accepted | Caught | First | `test_the_evidence_window[future]` |
| P23 | stale evidence accepted | Caught | First | `test_the_evidence_window[stale]` |
| P24 | evidence window widened | Caught | First | `test_the_evidence_window[stale]` |
| P25 | two-model list accepted | Caught | First | `test_two_models_are_refused_so_no_fallback_list_can_exist` |
| P26 | unverified model accepted | Caught | First | `test_as_shipped_a_perfect_environment_is_refused` |
| P27 | other provider accepted | Caught | First | `test_each_setting_alone_refuses[provider-other]` |
| P28 | naive clock accepted | Caught | First | `test_a_clock_without_a_timezone_is_refused` |
| P29 | request cap raised | Caught | First | `test_each_setting_alone_refuses[requests-two]` |
| P30 | input cap raised | Caught | First | `test_each_setting_alone_refuses[input-over]` |
| P31 | output floor lowered | Caught | First | `test_each_setting_alone_refuses[output-under]` |
| P32 | one retry allowed | Caught | First | `test_each_setting_alone_refuses[retries-one]` |
| P33 | concurrency two allowed | Caught | First | `test_each_setting_alone_refuses[concurrency-two]` |
| P34 | timeout range widened | Caught | First | `test_a_complete_environment_is_allowed` |
| P35 | evidence shipped as released | Caught | First | `test_as_shipped_nothing_is_released` |
| P36 | a model shipped on the allowlist | Caught | First | `test_as_shipped_nothing_is_released` |
| P37 | execution lock shipped enabled | Caught | First | `test_as_shipped_nothing_is_released` |
| B01 | existing client check skipped | Caught | First | `test_each_client_violation_is_a_boundary_problem[mock-transport]` |
| B02 | connection checks skipped | Caught | First | `test_each_client_violation_is_a_boundary_problem[http2]` |
| B03 | TLS extras skipped | Caught | First | `test_each_client_violation_is_a_boundary_problem[tls-other-roots]` |
| B04 | request checks skipped | Caught | First | `test_each_request_violation_is_a_boundary_problem[plain-http]` |
| B05 | trust store not compared | Caught | First | `test_each_client_violation_is_a_boundary_problem[tls-other-roots]` |
| B06 | old TLS accepted | Caught | First | `test_each_client_violation_is_a_boundary_problem[tls-old-version]` |
| B07 | key logging accepted | Caught | First | `test_tls_key_logging_is_a_boundary_problem` |
| B08 | non-SSL context accepted | Caught | First | `test_a_context_that_is_not_ssl_is_a_boundary_problem` |
| B09 | pool proxy not checked | Caught | Second | `test_a_proxy_on_a_plain_pool_is_a_boundary_problem` |
| B10 | Unix socket not checked | Caught | First | `test_each_client_violation_is_a_boundary_problem[uds]` |
| B11 | local address not checked | Caught | First | `test_each_client_violation_is_a_boundary_problem[local]` |
| B12 | socket options not checked | Caught | First | `test_each_client_violation_is_a_boundary_problem[socket-option]` |
| B13 | network backend not checked | Caught | First | `test_a_replaced_network_backend_is_a_boundary_problem` |
| B14 | connection count not checked | Caught | First | `test_each_client_violation_is_a_boundary_problem[pool]` |
| B15 | keep-alive not checked | Caught | First | `test_each_client_violation_is_a_boundary_problem[keepalive]` |
| B16 | HTTP version not checked | Caught | First | `test_each_client_violation_is_a_boundary_problem[http2]` |
| B17 | URL not compared | Caught | First | `test_each_request_violation_is_a_boundary_problem[plain-http]` |
| B18 | method not compared | Caught | First | `test_each_request_violation_is_a_boundary_problem[method]` |
| B19 | header set not compared | Caught | First | `test_each_request_violation_is_a_boundary_problem[extra-header]` |
| B20 | duplicate authorization allowed | Caught | First | `test_each_request_violation_is_a_boundary_problem[two-authorizations]` |
| B21 | Host header not compared | Caught | First | `test_each_request_violation_is_a_boundary_problem[host-header]` |
| B22 | request extensions not compared | Caught | First | `test_each_request_violation_is_a_boundary_problem[longer-timeout]` |
| Q01 | body keys not exact | Caught | First | `test_any_body_other_than_the_pinned_request_is_refused[extra-field]` |
| Q02 | streaming body accepted | Caught | First | `test_any_body_other_than_the_pinned_request_is_refused[streaming]` |
| Q03 | reasoning body accepted | Caught | First | `test_any_body_other_than_the_pinned_request_is_refused[reasoning]` |
| Q04 | several messages accepted | Caught | First | `test_any_body_other_than_the_pinned_request_is_refused[no-message]` |
| Q05 | other role accepted | Caught | First | `test_any_body_other_than_the_pinned_request_is_refused[system-role]` |
| Q06 | message fields not exact | Caught | First | `test_any_body_other_than_the_pinned_request_is_refused[message-no-content]` |
| Q07 | boolean output limit accepted | Caught | First | `test_any_body_other_than_the_pinned_request_is_refused[limit-bool]` |
| Q08 | body model not checked | Caught | First | `test_the_bounds_are_read_from_the_body[other-model]` |
| Q09 | configured model not required | Caught | First | `test_a_listed_model_other_than_the_configured_one_is_refused` |
| Q10 | output limit not checked | Caught | First | `test_the_bounds_are_read_from_the_body[output-low]` |
| Q11 | input bound not checked | Caught | First | `test_the_bounds_are_read_from_the_body[input-one-byte-over]` |
| S01 | boundary refusal ignored | Caught | First | `test_each_request_violation_makes_no_provider_call[plain-http]` |
| S02 | policy not re-read before the send | Caught | First | `test_a_refusal_leaves_the_slot_free` |
| S03 | process slot not taken | Caught | First | `test_a_second_session_in_the_process_is_refused` |
| S04 | repeat send allowed | Caught | Second | `test_the_session_limit_holds_without_the_process_slot` |
| S05 | in-flight not refused | Caught | First | `test_a_concurrent_send_is_refused_while_one_is_in_flight` |
| S06 | halted session not refused | Caught | First | `test_a_redirect_is_not_followed_and_halts_the_session[301]` |
| S07 | reservation outside the lock | Caught | Second | `test_the_slot_is_taken_while_the_session_is_locked` |
| S08 | permit reusable | Caught | First | `test_a_permit_allows_exactly_one_transmission` |
| S09 | attempt not counted | Caught | First | `test_one_authorized_request_reaches_the_pinned_endpoint_once` |
| S10 | send start not recorded | Caught | First | `test_a_failure_is_never_retried_and_its_state_is_recorded[read-timeout]` |
| S11 | connect failure erases a response | Caught | First | `test_a_late_connect_failure_cannot_erase_a_received_response` |
| S12 | connect failure not recorded | Caught | First | `test_a_failure_is_never_retried_and_its_state_is_recorded[connect]` |
| S13 | response without a send claims sent | Caught | First | `test_a_response_before_a_transmission_does_not_claim_sent` |
| S14 | finish not idempotent | Caught | First | `test_finishing_twice_changes_nothing` |
| S15 | finish leaves the session in flight | Caught | First | `test_a_second_send_is_refused_after_a_completed_one` |
| S16 | halt not recorded | Caught | First | `test_a_redirect_is_not_followed_and_halts_the_session[301]` |
| H01 | blocked access not a halt | Caught | First | `test_a_post_call_indicator_halts_and_never_resends[blocked-400]` |
| H02 | 402 not a halt | Caught | First | `test_a_post_call_indicator_halts_and_never_resends[payment-required]` |
| H03 | redirect not a halt | Caught | First | `test_a_redirect_is_not_followed_and_halts_the_session[301]` |
| H04 | paid service tier not a halt | Caught | First | `test_a_post_call_indicator_halts_and_never_resends[tier-flex]` |
| H05 | unreadable service tier not a halt | Caught | First | `test_a_post_call_indicator_halts_and_never_resends[tier-not-text]` |
| H06 | model mismatch not a halt | Caught | First | `test_a_post_call_indicator_halts_and_never_resends[model-mismatch]` |
| H07 | input usage not bounded | Caught | First | `test_a_post_call_indicator_halts_and_never_resends[input-over-bound]` |
| H08 | output usage not bounded | Caught | First | `test_a_post_call_indicator_halts_and_never_resends[output-over-request]` |
| H09 | output bound uses the policy ceiling | Caught | First | `test_a_post_call_indicator_halts_and_never_resends[output-over-request]` |
| A01 | gate never asked | Caught | First | `test_each_setting_refused_at_the_send_makes_no_provider_call[provider-mode-unset]` |
| A02 | real provenance needs no gate | Caught | First | `test_real_provenance_without_a_gate_is_refused` |
| A03 | gate type not exact | Caught | First | `test_a_subclassed_gate_is_refused` |
| A04 | gate allowed on synthetic | Caught | First | `test_synthetic_provenance_cannot_carry_the_gate` |
| A05 | gate not re-checked at the send | Caught | First | `test_a_gate_removed_after_construction_stops_the_send` |
| A06 | refusal becomes an error outcome | Caught | First | `test_each_setting_refused_at_the_send_makes_no_provider_call[provider-mode-unset]` |
| A07 | send start not reported | Caught | First | `test_one_authorized_request_reaches_the_pinned_endpoint_once` |
| A08 | response not reported | Caught | First | `test_one_authorized_request_reaches_the_pinned_endpoint_once` |
| A09 | connect timeout not reported | Caught | First | `test_a_failure_is_never_retried_and_its_state_is_recorded[connect-timeout]` |
| A10 | connect error not reported | Caught | First | `test_a_failure_is_never_retried_and_its_state_is_recorded[connect]` |
| A11 | read error treated as unsent | Caught | First | `test_a_failure_is_never_retried_and_its_state_is_recorded[read]` |
| A12 | read timeout treated as unsent | Caught | First | `test_a_failure_is_never_retried_and_its_state_is_recorded[read-timeout]` |
| A13 | pool timeout treated as possibly sent | Caught | First | `test_a_failure_is_never_retried_and_its_state_is_recorded[pool-timeout]` |
| A14 | exception does not finish the permit | Caught | First | `test_a_failure_is_never_retried_and_its_state_is_recorded[http-400]` |
| A15 | completion does not finish the permit | Caught | First | `test_a_second_send_is_refused_after_a_completed_one` |
| A16 | a settling failure escapes | Caught | First | `test_a_failure_while_settling_cannot_change_the_outcome` |
| A17 | unreadable service tier not flagged | Caught | First | `test_a_post_call_indicator_halts_and_never_resends[tier-not-text]` |

## 8. FAIL

**None** in the final runs. Earlier failures during this checkpoint:

| Failure | Cause | Fix |
|---|---|---|
| Client built with `http2=True` failed | Test defect: needs the optional `h2` package | Set on the transport, which is what the guard inspects |
| `host` and `port` request cases reported 2 problems | Test expectation: changing the URL also changes `Host` | The test asserts at least one problem |
| `missing-header` case was not refused | Test defect: `httpx` refills `Accept` from client defaults | A header it does not supply is removed instead |
| `test_the_groq_extra_is_exactly_pinned` in `sdk/.venv` | The local editable install predates the extra. Passes in the clean install | No change |
| Ruff S108, formatting | A `/tmp` literal in a test | Replaced; formatted |
| 5 backend tests in the first scratch copy | The copy lacked `.env.example`, `scripts/` and `sdk/src` | Files added to the copy; 1202 passed |

**Production defects found and corrected while building this:** `LiveRefusal` swallowed by `GroqTextAdapter`, and an unreadable service tier being indistinguishable from an absent one (§2).

**One existing test was edited.** `test_with_the_lock_lifted_a_changed_client_is_refused_before_the_send` now passes `send_gate=FreePlanSession({})`, because a real client now requires one. Its assertion is unchanged.

## 9. SKIP and NOT RUN

**SKIP (81, all with one reason):** the backend interpreter was deliberately absent, so these tests stop before any `.env` read or database connection. **None is a pass.** QA self-tests also report 15 skipped, unchanged from 8A-2.

**NOT RUN:**

| Item | State |
|---|---|
| Any real Groq or OpenRouter request | **NOT RUN**, by instruction |
| Real TLS to `api.groq.com`; real connect-phase failures | **NOT RUN.** The transport call is faked. Certificates, `httpcore` connection errors on a real network and real response shapes are unexercised |
| All seven GitHub CI jobs on this code | **NOT RUN.** Nothing is committed or pushed |
| PostgreSQL and backend integration, including the 6 tests in `test_live_groq.py` | **COLLECTED, NOT RUN locally.** They passed in the `python` job of run 42675227030 only insofar as that job succeeded; only job conclusions were read. This change does not touch ingest, storage or the synthetic path, but they have not been re-run on it |
| Full history gate on a clean clone | NOT RUN. Changed files were checked by the detectors |
| pip-audit | NOT RUN; no dependency changed. `httpcore` is newly imported directly, and was already locked and audited as a dependency of `httpx` |
| Frontend and E2E | NOT RUN; no frontend file changed |

## 10. Risks and remaining real-request blockers

**New risks** (full text in `SOURCES_AND_RISKS.md`):

| Item | Note |
|---|---|
| R119 | An attested Free Plan account may be paid. The halts only detect. **The account's real plan is the actual boundary** |
| R120 | `not_sent` after a connect failure rests on audited source and fake transports |
| R121 | The boundary reads private `httpx` and `httpcore` attributes; pinned. Since the §13 correction, a missing, unreadable, added or changed transport or pool attribute refuses. Method replacement in the same process is not detected |
| R122 | The one-request limit is per process. A new process can send again, so the runner must not loop |
| R123 | **Two Gemini findings, confirmed and not fixed:** its attestation accepts an ISO week date such as `2026-W45-1`, and its real client honours `SSLKEYLOGFILE` unchecked. **A separately reviewed security fix is required before any additional real Gemini call** |
| R124 | `on_demand` or null as the Free Plan service tier is unconfirmed against a real response |

**Other limits:** the key the runner passes is not compared with the key whose presence the policy checked, which the runner checkpoint must bind. Between the final check and the send, only span creation runs, and a span cannot be withdrawn, so a check there would misreport a request that never left.

**Before any real Groq request, all of these are required:**

| # | Blocker | Clears when |
|---|---|---|
| 1 | `REAL_EXECUTION_ENABLED` is false | A reviewed code change |
| 2 | No evidence and no allowlisted model | The evidence gate releases them |
| 3 | B1: Groq Free Plan limits and model availability unknown | The owner reads the account Limits page and records it |
| 4 | B2: Groq documentation contradicts itself | Re-read at the evidence gate |
| 5 | B5: reasoning accounting undocumented | First-call comparison |
| 6 | B7: telemetry against live responses unproven; R115, R124 and the Content-Type of real responses | The first authorized call |
| 7 | No runner, dry run, exit codes or key binding | The runner checkpoint |
| 8 | Real-transport path never exercised | The first authorized call |
| 9 | Explicit approval | The owner authorizes each request separately |

R102 (npm advisory, deadline 2026-11-30 UTC) and R105 remain open. The Gemini Free Tier evidence guard expires 2026-11-11 13:00 UTC and was not changed. Phase 7D is not complete. The "Last updated" line of `PHASE_STATUS.md` still describes 7C as pending, as noted in 8A-1.

## 11. Recommendation

**Checkpoint 8A-4 (scenarios and runner, offline): conditional GO**, on three conditions: architectural review of ADR-44, the owner's commit and push of this work, and all seven CI jobs green on it.

**Real Groq and OpenRouter requests: NO-GO.** See §10.

```text
PHASE / DATE: 8A-3 / 2026-11-09 UTC
BRANCH: feature/phase-8a3-free-plan-guard on eece127, uncommitted, not pushed
PROVIDER REQUESTS / DOCKER / DATABASE / .ENV: 0 / none / none / not read
EXECUTION PATH: disabled (lock false, no evidence, no allowlisted model)
SDK CLEAN INSTALL, FINAL CODE: 1894 tests, 1813 passed, 0 failed, 81 skipped (database-backed)
BACKEND UNIT: 1202 passed. QA: 214 OK, 15 skipped. DOC CHECK: 828 PASS, 0 FAIL
RUFF / MYPY / BANDIT GATE: PASS / PASS / PASS, baseline unchanged
MUTATIONS: 112 of 112 caught across two runs; 0 survived, 0 invalid, 0 unfinished
NOT RUN: real requests, real TLS, PostgreSQL on this code, 7 CI jobs on this code
8A-4: conditional GO, offline only. REAL REQUESTS: NO-GO
```

## 12. Pre-commit architecture review (2026-11-10): commit held

The reviewer issued a conditional GO for one local commit, to be made only if a final architecture verification found no inconsistency. It found one, so **nothing was staged or committed**. No production or test code changed after §1 to §11 were written, and those totals still describe this code. PostgreSQL and all seven CI jobs remain NOT RUN on it.

| Check | Result | Basis |
|---|---|---|
| `REAL_EXECUTION_ENABLED` false, not settable from the environment | PASS | A constant with no environment read. Refused with `real_execution_disabled` under a fully configured environment that also set variables of that name |
| Free Plan allowlist empty | PASS | `FREE_PLAN_MODELS` is `{}` and the evidence date is `None` |
| No real Groq or OpenRouter execution possible | PASS | `_check_http_client` refuses `real`. No module outside the SDK imports the Groq adapter. No OpenRouter adapter exists |
| Reservation, concurrency, retry and redirect protections | PASS | Source read; §4 tests |
| Endpoint, TLS and proxy protections fail closed | **FAIL** | F1 |
| `not_sent` only where provably unsent | PASS | `httpcore` 1.0.9 re-read: `ConnectError` and `ConnectTimeout` are mapped only in `connect_tcp`, `connect_unix_socket` and `start_tls`. A server disconnect raises `RemoteProtocolError`, which stays `possibly_sent` |
| Ambiguous outcomes stay `possibly_sent` | PASS | Set before the send; only the three connect-phase errors lower it, and only before a response |
| Gemini, OpenAI and the Groq synthetic path preserved | PASS | No Gemini or OpenAI file in the diff. The synthetic path passes no gate, and its tests are unchanged |
| No credential or content in telemetry, logs or exceptions | PASS | Refusals name settings and rules only. No new span attribute. The body parser's `JSONDecodeError` does not outlive it |
| Documentation matches the implementation | **FAIL** | F2 |

**F1: the endpoint boundary fails open on a lookalike pool.** `live_groq_client_problems` accepts any pool whose class is *named* `ConnectionPool`. `send_boundary_problems` then runs its pool and TLS checks only `if type(pool) is httpcore.ConnectionPool`, and has no `else`. A different class with that name therefore passes with every check added here skipped: pinned roots, TLS version, key logging, proxy, Unix socket, network backend, connection limits and HTTP/1.1. That object's `handle_request` would receive the request, including its `Authorization` header. The probe (sockets blocked, no key, `httpx` 0.28.1, `httpcore` 1.0.9) reported no new problem for this case. Reaching it needs code in the same process to replace the private `_transport._pool`, and the execution lock makes it unreachable today. The guard does treat this class of tampering as in scope (a replaced backend and a lookalike gate are both refused). No test covered it, so the 112 mutations could not reveal it. The Gemini check uses the same name-only test, but no later check depends on it there.

**F2: three statements overstate the private-attribute protection.** R121 says a changed or missing attribute "is reported as a problem and refuses"; ADR-44's Consequences and the R121 row of §10 say the same in short form. Seven of the eleven pool attributes do refuse when deleted. The four in `_POOL_UNSET_ATTRIBUTES` (`_proxy`, `_uds`, `_local_address`, `_socket_options`) are read with a `None` default, so a missing one passes. The pinned `httpcore` version makes this unreachable through a library change. These statements are left as written until the F1 decision, because the fix would make them true.

**Proposed fix, not applied (it changes code whose evidence was accepted):** add an `else` in `send_boundary_problems` that reports a pool that is not exactly `httpcore.ConnectionPool`; treat a missing `_POOL_UNSET_ATTRIBUTES` entry as a problem; add a test for each; extend the mutation set; re-run the SDK clean-install regression, Ruff, mypy, Bandit, the detectors and the documentation check.

**R123 confirmed by the same probe, and not fixed.** `2026-W45-1` was accepted as 2026-11-02 and refused as stale a week later. With `SSLKEYLOGFILE` set, the context of the client built by `_default_client` had key logging on, and `live_gemini_client_problems` reported nothing. A separately reviewed security fix is required before any additional real Gemini call.

**Outcome:** the correction of F1 and F2 was authorized on 2026-11-10 without commit authorization. It is §13.

## 13. F1/F2 correction (2026-11-10)

**Scope as authorized:** F1 and F2 only, with no commit, push, PR or merge. Provider requests: **0**. No credential, `.env`, PostgreSQL data or Docker container was touched. `REAL_EXECUTION_ENABLED` stays false, the Free Plan allowlist stays empty, and the evidence date stays `None`. Gemini code is unchanged and R123 stays open. The three user-owned files are still untracked, and they were neither opened nor copied. Network use was limited to PyPI, to build one throwaway environment from `sdk/requirements-dev.lock`.

### 13.1 Code changes

**`groq_adapter.py`**

| Change | Closes |
|---|---|
| New `transport_problems(transport)`. The transport must be exactly `httpx.HTTPTransport` and hold only `_pool` as an instance attribute. Then `_pool_problems` runs on its pool | F1: the transport layer, including an instance-level `handle_request` override |
| New `_pool_problems(pool)`. The pool must be exactly `httpcore.ConnectionPool`, compared by identity with `type(pool) is`, never by name or `isinstance`. Its instance attributes must equal `_POOL_INSTANCE_ATTRIBUTES`, the 15 that the audited 1.0.9 constructor sets. `_connections` and `_requests` must be empty lists. Retries must be exactly the integer 0, and TLS verification must be on. **Every check runs whatever the others found** | F1 |
| `_POOL_UNSET_ATTRIBUTES` moved here from `groq_live`. Each of the four is read with the `_MISSING` sentinel, so a missing or unreadable one is its own problem and is never treated as `None`. A present value other than `None` is refused, including `False`, `0`, `""` and empty containers | F2 |
| `_attr` catches any `Exception` from a getter and returns `_MISSING`, so an unreadable object is a refusal and its error text goes nowhere. New `_is_exactly` rejects `bool` where an integer is required. New `_instance_attributes` returns `None` for an object whose attributes cannot be listed | F1, F2 |
| `live_groq_client_problems` calls `transport_problems`. The `else` branch that skipped the retry and TLS checks on an unexpected pool is gone | F1 |

**`groq_live.py`**

| Change | Closes |
|---|---|
| `send_boundary_problems` calls `transport_problems` itself, so the send does not depend on the client check. `_connection_problems` and `_tls_problems` now run unconditionally on whatever object is in `_pool`. The fall-through `if type(pool) is httpcore.ConnectionPool:` is gone. Repeated problems are merged in order | F1 |
| `_connection_problems` reads through `_attr` and compares the two limits with `_is_exactly` | F1 (a pool with `True` or `False` limits) |
| `_tls_problems` requires `type(tls) is ssl.SSLContext`. A subclass could answer the root, version and key-log checks with any value it chose | F1: the requirement that a monkey-patched pool must not skip the CA-root or key-log checks. The rule is applied to the pool's `_ssl_context` |

**Unchanged by design:**
- Construction still checks only ADR-43's shape plus the new transport and pool checks. The roots, TLS version, key logging, network backend, connection limits and HTTP version are checked at the send, before any byte is written. Moving them into construction would break `test_unsafe_tls_is_the_only_live_boundary_problem`, an 8A-2 test that locks ADR-43's construction behaviour, so it was not done.
- No existing test was edited.

### 13.2 Regression tests (`test_groq_live_guard.py`, 162 new cases)

Every refusal test runs on both layers separately. One run uses `live_groq_client_problems` directly. The other uses `send_boundary_problems` with `live_groq_client_problems` replaced by a function returning `[]`, so neither layer can pass on the other's behalf.

| Required | Tests |
|---|---|
| Genuine audited pool accepted | `test_the_audited_pool_is_accepted_by_both_layers`; `test_the_audited_attribute_list_is_the_installed_pool` (the installed 1.0.9 pool has exactly the 15 listed attributes) |
| Lookalike `ConnectionPool` rejected | `lookalike` case: a class of that name holding every genuine value. `test_identity_alone_refuses_a_faithful_substitute` asserts its **only** problem is identity |
| Subclass or substituted pool rejected | `subclass` and `spoofed-class` (its `__class__` and `isinstance` both claim the audited type); a genuine `httpcore.HTTPProxy`; `None`, a plain object and a number |
| Missing pool rejected | `test_a_missing_pool_is_refused_by_each_layer` |
| Substituted transport rejected | `test_a_substitute_transport_holding_the_audited_pool_is_refused`: a class named `HTTPTransport`, and an `httpx.HTTPTransport` subclass, each wrapping the genuine pool. Added after F30 survived (§13.4) |
| Monkey-patched pool rejected | An instance `handle_request`, an added attribute, a removed attribute, a seeded connection, a queued request, non-list state, `_retries=False`, `True` and `False` limits, an instance override on the transport, and a subclassed `SSLContext` |
| No check skipped on a wrong type | `test_no_other_check_is_skipped_because_the_pool_type_is_wrong`: one lookalike carrying a proxy, other roots, TLS 1.1, key logging, a replaced backend, two connections, keep-alive and HTTP/2 reports all nine problems. `test_retries_and_tls_are_still_checked_on_a_wrong_pool` |
| Each of the four attributes missing | `test_a_missing_pool_attribute_is_refused_by_each_layer`: 4 attributes × 2 layers |
| Each present as `None` | `test_a_pool_attribute_present_as_none_is_accepted`: 4 × 2 |
| Unexpected values | `test_an_unexpected_pool_attribute_value_is_refused_by_each_layer`: 7 values × 4 attributes × 2 layers. Unreadable attributes: 4 × 2 |
| Refused before any send | Through the gate for every substitute and every missing attribute, asserting 0 requests on the fake transport and an untouched session. **Without any fake transport:** a substitute swapped in after construction, or while the request is built, records whether its `handle_request` was reached. It never was |
| No leak | Every attribute read on the substitute raises an error holding the fake key, `Authorization: Bearer` and the prompt sentinel. The refusal, its `repr`, the session snapshot, the logs at DEBUG and the spans contain none of them, and the refusal has no `__cause__` or `__context__`. Tested for the client check and for the send check |
| Client changed after construction | Swapped pool or removed attribute after construction (refused by the adapter's re-check, `ConfigError`), and while the request is built (refused by the gate, `LiveRefusal`) |
| Zero retries and one request preserved | `test_a_retry_or_second_request_stays_impossible_on_the_audited_pool`, with all earlier limit tests unchanged |

**Red first:** before the fix, these tests ran against the unchanged installed package. 114 failed, including the lookalike, subclass, spoofed-class and missing-attribute paths at the gate and end to end. That is the F1 and F2 defect, now reproduced by tests.

### 13.3 Results on the corrected code (EXECUTED)

| Check | Command (from the named directory) | Result |
|---|---|---|
| SDK full regression, clean non-editable install | A new venv built from `sdk/requirements-dev.lock` (`pip check` clean), with a copy of `sdk/` installed using `--no-deps`. `direct_url.json` shows no editable install, and all 14 installed modules are AST-identical to the repository. From `sdk`: `python -m pytest -q -p no:cacheprovider --no-header --tb=short -rf`, with provider key variables removed and `LLMI_BACKEND_PYTHON` pointing at a missing path | **2056 tests: 1975 passed, 0 failed, 0 errors, 81 skipped** (640 s), run after the F30 test was added. The guard module has 467 tests (305 before, 162 added). **None of the 81 skips is a pass**: they are the database-backed tests, each with the single reason in §9 |
| Backend unit | Scratch copy of `backend` with no `.env`: `python -m pytest tests/unit -q -rs -p no:cacheprovider` | 1202 passed, 0 failed, 0 skipped |
| Backend privacy and cost | Scratch copy: `pytest tests/unit/test_groq_contract.py tests/unit/test_pricing.py tests/unit/test_metadata_privacy.py tests/unit/test_redaction.py -q` | 475 passed |
| Ruff | `sdk`: `ruff check .`, `ruff format --check .`; `backend`: the same | PASS, 54 and 143 files |
| mypy strict | `sdk`: `mypy src`, `mypy --config-file pyproject.toml ../demo_app` | PASS, 14 and 9 files |
| Bandit gate | Bandit 1.9.4 raw scan over `backend/app sdk/src demo_app scripts`, then `bandit_baseline_check.py validate-baseline` and `evaluate` with the four CI targets | PASS. High 1, Medium 5, Low 38, 44 of 44. **Baseline unchanged**: the new `except Exception: return` reads raise no finding |
| History baseline | `secret_history_gate.py validate-baseline` | PASS, 38 entries |
| Changed and new files | The repository's detectors over all 10 changed files, excluding the user-owned files | 1 match: the existing `.env.example` `url_userinfo` placeholder, identical at `HEAD`, 0 reportable |
| QA self-tests | root: `python -I -m unittest discover -s scripts/qa/tests` | 214 ran, OK, 15 skipped |
| Doc check | root: `python -I scripts/verify_phase0.py` | 828 PASS, 0 FAIL, on the final documents |
| Whitespace | `git diff --check` | Clean |

### 13.4 Mutation verification

**Result: 142 of 142 caught after one test addition. 0 survived, 0 invalid, 0 unfinished.** The method is as in §7: each mutant is a private copy of the newly installed package, first on `PYTHONPATH`, run against `test_groq_live_guard.py` and `test_groq_adapter.py`. Neither the repository nor the clean install was modified.

| Run | Mutations | Result |
|---|---|---|
| First, on the corrected code | All 142: the 112 of §7 and 30 new | 141 caught, 1 survived (F30) |
| Second, after one test was added | F30 | Caught |

**The earlier 112 are not carried over.** All 112 were rerun on the corrected code. The F1/F2 change affects them as follows:

| Effect on the earlier mutation | Mutations | Rerun |
|---|---|---|
| Pattern moved with the code and re-targeted, same fault | B02, B03, B08, B13 to B16 | Caught |
| Pattern re-targeted to the read loop: the tuple now also defines the audited attribute set, so deleting a name from it would be caught trivially and no longer model "not checked" | B09 to B12 | Caught |
| Same pattern, but the surrounding code changed: the send boundary now re-checks the transport and pool itself, and the TLS checks run on any pool | B01, B04 to B07, S01 | Caught. B01 is now caught by `[proxy]` instead of `[mock-transport]`, because the send boundary refuses a mock transport on its own |
| Code not changed, but rerun because `_attr` and the client check it reaches changed | P01 to P37, B17 to B22, Q01 to Q11, S02 to S16, H01 to H09, A01 to A17 | All caught |

**The 30 new mutations:**

| # | Mutation | Status | Caught by test |
|---|---|---|---|
| F01 | pool identity check removed | Caught | `test_an_unexpected_pool_is_refused_by_each_layer[client-validation-lookalike]` |
| F02 | pool identified by class name (**the original F1**) | Caught | `test_an_unexpected_pool_is_refused_by_each_layer[client-validation-lookalike]` |
| F03 | pool identified by `isinstance` | Caught | `test_an_unexpected_pool_is_refused_by_each_layer[client-validation-subclass]` |
| F04 | send boundary does not re-check the transport | Caught | `test_an_unexpected_pool_is_refused_by_each_layer[send-boundary-alone-lookalike]` |
| F05 | client validation does not check the transport | Caught | `test_mock_transport_under_real_provenance_makes_no_call` |
| F06 | send checks conditional on the pool type (**the original fail-open**) | Caught | `test_no_other_check_is_skipped_because_the_pool_type_is_wrong` |
| F07 | pool checks stop at a wrong type | Caught | `test_a_missing_pool_is_refused_by_each_layer[client-validation]` |
| F08 | pool structure not checked | Caught | `test_a_missing_pool_is_refused_by_each_layer[client-validation]` |
| F09 | attributes added to the pool allowed | Caught | `test_a_monkey_patched_audited_pool_is_refused_by_each_layer[client-validation-send]` |
| F10 | attributes removed from the pool allowed | Caught | `test_a_missing_pool_is_refused_by_each_layer[client-validation]` |
| F11 | transport instance not checked | Caught | `test_a_monkey_patched_transport_is_refused_by_each_layer[client-validation]` |
| F12 | missing attribute not reported | Caught | `test_a_missing_pool_attribute_is_refused_by_each_layer[client-validation-_proxy]` |
| F13 | missing attribute read as `None` (**the original F2**) | Caught | `test_a_missing_pool_attribute_is_refused_by_each_layer[client-validation-_proxy]` |
| F14 | present attribute value not checked | Caught | `test_each_client_violation_is_a_boundary_problem[uds]` |
| F15 | falsy attribute value accepted | Caught | `test_an_unexpected_pool_attribute_value_is_refused_by_each_layer[client-validation-false-_proxy]` |
| F16 | held connections not checked | Caught | `test_a_monkey_patched_audited_pool_is_refused_by_each_layer[client-validation-seeded-connection]` |
| F17 | held connections may be any empty value | Caught | `test_a_monkey_patched_audited_pool_is_refused_by_each_layer[client-validation-connections-none]` |
| F18 | a raising attribute not handled | Caught | `test_an_unreadable_pool_attribute_is_refused_by_each_layer[client-validation-_proxy]` |
| F19 | a pool whose attributes cannot be listed accepted | Caught | `test_a_missing_pool_is_refused_by_each_layer[client-validation]` |
| F20 | exact-integer helper accepts `bool` | Caught | `test_a_monkey_patched_audited_pool_is_refused_by_each_layer[client-validation-retries-bool]` |
| F21 | max connections compared loosely | Caught | `test_a_limit_must_be_exactly_an_integer[connections-bool]` |
| F22 | keep-alive compared loosely | Caught | `test_a_limit_must_be_exactly_an_integer[keepalive-bool]` |
| F23 | retries compared loosely | Caught | `test_a_monkey_patched_audited_pool_is_refused_by_each_layer[client-validation-retries-bool]` |
| F24 | TLS context subclass accepted | Caught | `test_a_subclassed_tls_context_is_refused` |
| F25 | repeated problems not merged | Caught | `test_a_proxy_on_a_plain_pool_is_a_boundary_problem` |
| F26 | retries not checked | Caught | `test_each_client_violation_is_a_boundary_problem[transport-retries]` |
| F27 | TLS verification not checked | Caught | `test_each_client_violation_is_a_boundary_problem[tls-off]` |
| F28 | audited attribute list wrong | Caught | `test_each_setting_refused_at_the_send_makes_no_provider_call[provider-mode-unset]` |
| F29 | pool not inspected at all | Caught | `test_each_client_violation_is_a_boundary_problem[tls-off]` |
| F30 | transport type not checked, only its attributes | Caught, second run | `test_a_substitute_transport_holding_the_audited_pool_is_refused[client-validation-lookalike]` |

**F30, the survivor.** With the type check removed, the instance-attribute check alone accepted any object holding only `_pool`. That covers a class named `HTTPTransport`, or an `httpx.HTTPTransport` subclass, wrapping the genuine pool with its own `handle_request`. No test had built one. The code was right, and the test was missing. `test_a_substitute_transport_holding_the_audited_pool_is_refused` was added: both layers, the gate, 0 requests on the fake transport, and the substitute never reached. It catches F30. The full SDK regression in §13.3 was rerun after the addition.

**Mutation testing shows that these chosen faults are detected. It does not prove the absence of others.** The harness and logs are in the session's scratch directory, so the tables are the durable record.

### 13.5 Unresolved risks

| Item | State |
|---|---|
| **Method replacement in the same process** (R121) | The boundary inspects configuration, not code. It does not detect a replaced method on the `httpx.Client` instance, such as `http.send`, or a replaced method on the `httpx` or `httpcore` classes. The existing tests themselves override `build_request` on the client instance. Doing this needs code already running in the process, which could equally read the key from the environment. Recorded in R121, not fixed: it is outside F1 and F2 |
| Construction checks less than the send | Other roots, TLS version, key logging, limits and HTTP version are refused only at the send, as before. No byte is written first |
| Real transport | Still NOT RUN: real TLS, real connect failures and real response shapes (R115, R120, R124) |
| PostgreSQL and the seven CI jobs | NOT RUN on this code. Nothing is committed |
| R123 (Gemini) | **Open, unchanged.** The Gemini check still identifies the pool by name. No later check depends on that, but the same hardening belongs in its separately reviewed fix |
| R119, R122 | Unchanged |

### 13.6 Recommendation

**Phase 8A-3 commit readiness: ready for the architectural review of this correction.** F1 and F2 are closed by code and tests. The faults that recreate them exactly are F02, F06 and F13 in §13.4, and all three are caught. Commit authorization remains the reviewer's decision, and none is assumed. After a commit, the owner pushes, and all seven CI jobs, including PostgreSQL, must pass on this code before 8A-4.

**Real Groq and OpenRouter requests: NO-GO** (§10).

```text
PHASE / DATE: 8A-3 F1/F2 correction / 2026-11-10 UTC
BRANCH: feature/phase-8a3-free-plan-guard on eece127, uncommitted, not pushed
PROVIDER REQUESTS / DOCKER / DATABASE / .ENV: 0 / none / none / not read
EXECUTION PATH: disabled (lock false, no evidence, empty allowlist)
SDK CLEAN INSTALL: 2056 tests, 1975 passed, 0 failed, 81 skipped (database-backed)
BACKEND UNIT: 1202 passed. PRIVACY/COST: 475 passed. QA: 214 OK, 15 skipped
RUFF / MYPY / BANDIT GATE / DETECTORS: PASS / PASS / PASS, baseline unchanged / 0 reportable
DOC CHECK: 828 PASS, 0 FAIL. WHITESPACE: git diff --check clean
MUTATIONS: 142 of 142 caught (112 rerun, 30 new) across two runs; 0 survived, 0 invalid
NOT RUN: real requests, real TLS, PostgreSQL on this code, 7 CI jobs on this code
COMMIT: not authorized, not made. 8A-4: not started
```

## 14. Final architectural approval (2026-11-10)

The reviewer accepted the evidence of §13 and approved F1, F2 and the four related hardening changes of §13.1: **GO for one local commit only.** The final check before staging found the code, the locks (`REAL_EXECUTION_ENABLED` false, empty allowlist, no evidence date), ADR-44, R121 and this report consistent, R123 still open, and no dependency lock, Gemini or `demo_app` file changed. §13.5 and §13.6 record the state before this approval.

The only change after §13 is this documentation status, so no test was rerun. `verify_phase0.py` and `git diff --cached --check` were run on the staged files. The ten Phase 8A-3 files were staged one by one and committed locally with the owner's Git identity, without a co-author trailer. The commit is not pushed. Its SHA is reported to the owner and is not recorded in the commit itself.

Still open: the push (by the owner), all seven CI jobs including PostgreSQL on this code, R121's same-process method replacement, R123, and real requests (NO-GO, §10). 8A-4 has not started.
