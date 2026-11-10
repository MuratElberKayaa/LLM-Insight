# Phase 8A-4 — Comparison scenarios and runner (offline)

**Date:** 2026-11-10 UTC. **Status: implemented and verified offline; approved for one local commit (§12). Committed locally, not pushed. Real execution stays disabled.**

Provider API requests: **0**. No credential, billing account, `.env`, database, Docker container or deployment was touched. No commit, push, PR or merge. `REAL_EXECUTION_ENABLED` is still `False`, `FREE_PLAN_MODELS` is still empty and `FREE_PLAN_EVIDENCE_VERIFIED_AT` is still `None`. The three user-owned untracked files were not opened, copied or changed. Gemini code is unchanged (R123 stays open). OpenRouter is not implemented.

Base: `feature/phase-8a4-comparison-runner` at `d32f3df` (= `main` = `origin/main`, the merge of PR #6).

---

## 1. Scope

The authorized scope is checkpoint 8A-4 of `reports/phase-8a-0.md` §16.4 (§11's 8A-5): comparison scenarios and the runner. The instruction's required work maps to this checkpoint as follows.

| # | Required work | Result |
|---|---|---|
| 1 | Read ADR-40 to ADR-44, 8A-0, 8A-3, adapters and guards | Done; §2 records what shaped the design |
| 2 | Deterministic, versioned synthetic scenarios with separate identity and prompt version | `demo_app/comparison_scenarios.py`, §3 |
| 3 | Runner with fake-provider tests, dry-run default, explicit single-request authorization | `demo_app/live_compare.py`, §4, §7 |
| 4 | Reuse the Groq guard and endpoint protections | The unchanged `GroqTextAdapter`, `build_groq_http_client`, `live_groq_client_problems` and `FreePlanSession`; nothing bypassed or loosened |
| 5 | One request, no retries, no fallbacks, no paid routing, metadata-only telemetry | §4.3, tested |
| 6 | Bind the validated credential to the client that sends | §4.4: one environment copy in the runner, and a new check in the guard's send gate |
| 7 | Separate comparison project; how traces appear in Overview, Traces, Compare | §5 |
| 8 | Preserve the Gemini runner; no real Experiments | No Gemini or Experiments file changed; Gemini is refused by the comparison runner |
| 9 | Preconditions for the first real Groq request | §9 |
| 10 | Report, architecture and status documents | This report; ADR-45; `SECURITY.md` §6; `SOURCES_AND_RISKS.md` R125–R128; `PHASE_STATUS.md`; both READMEs |

## 2. What the existing code required

- **Only Groq can run.** Checkpoint 8A-2 built a Groq-only adapter (ADR-42), not the two-profile adapter drafted in 8A-0 §5. No OpenRouter adapter exists, so the runner cannot route anything to OpenRouter. "OpenRouter must not route to paid alternatives" is met by refusing OpenRouter outright.
- **Gemini is blocked.** R123 requires a reviewed fix before any further real Gemini call, so the comparison runner does not call `live_gemini.run_single_request`. It refuses `gemini` before reading any setting. ADR-36 and ADR-39 keep exactly two Gemini entry points.
- **Grouping.** `/models/compare` groups by provider, requested model, response model and the span's `gen_ai.prompt.version` (`backend/app/repositories/metrics.py`). The Gemini demo uses `v1` for all three of its scenarios (finding F2), so each comparison scenario needs its own version.
- **Credential gap.** ADR-44's gate validated `GROQ_API_KEY` from its environment at the send, while the request carried the key given to `ObservedGroqClient` at construction. Nothing checked that they were the same. §4.4 closes this.
- **Process slot.** The guard's one-request slot is never released, so a second execution in the same process is refused by design.

## 3. Scenarios

`demo_app/comparison_scenarios.py` is a closed registry in code. The texts are exactly the drafts of 8A-0 §6: every composed input equals the draft's size and its SHA-256 prefix and suffix.

| ID | `prompt_name` | `prompt_version` | Bytes | Token bound | SHA-256 | Output cap |
|---|---|---|---|---|---|---|
| `cmp-ticket-triage` | `cmp.ticket-triage` | `cmp-v1.ticket-triage` | 621 | 685 | `5d9040f3a67d37e5edaf4dab90a9a59ff236061c729c7b62dcd0a39812c267ff` | 1024 |
| `cmp-json-aggregate` | `cmp.json-aggregate` | `cmp-v1.json-aggregate` | 661 | 725 | `89684d9402c71ad6681fbd77ad6199473612a5859da9580fa18dd537bcb31a37` | 1024 |
| `cmp-release-summary` | `cmp.release-summary` | `cmp-v1.release-summary` | 595 | 659 | `815eef603170295620d27a787ff3e8345384cc43e56b016bb4d6976ee135ea47` | 1024 |

- **Identity:** `prompt_version` is derived from the scenario ID and version (`cmp-v<version>.<id>`), so a text change with a version bump gets a new Compare row. A registry check refuses duplicate names or versions. No comparison name, version or ID equals a Gemini demo one.
- **Before anything else:** `check_scenario` refuses a changed text (pin), a different exact size, an invalid ID, name or version, a control character, an email, phone-like digit run, URL, credential prefix or empty fixture, a malformed or ambiguous data fixture (a tie for the highest-error day), and any output cap other than the uniform 1024. It runs before any policy read or client.
- **Validators** return rule codes only: ticket (JSON object, exact keys, enums, reason length, expected category `login`), aggregate (total 1000, day `Wed`, rate 3.0 ± 0.05, observation length), summary (at most 280 characters and two sentences, no Markdown, mentions `import`). They reuse the Gemini demo's parsing helpers, so a rule means the same thing in both; `gemini_scenarios.py` is unchanged.
- **Caps:** input bound at most 1024 and output 16 to 1024, the same values as both guards (tested).

## 4. Runner

`python -m demo_app.live_compare --provider <name> --scenario <id> [--execute] [--dashboard-url <loopback origin>]`

### 4.1 Order of checks

1. Command line: `allow_abbrev=False`, each option at most once, no `--model`, retry or other option; an invalid line reads nothing and echoes no value.
2. Provider registry: `groq` only. `gemini` → `provider_blocked`, `openrouter` → `provider_not_implemented`, unknown → `provider_unknown`. Nothing in the environment has been read at this point (tested with a recording mapping).
3. Scenario registry check (§3).
4. One read-only copy of the environment (§4.4).
5. Policy: `groq_live.evaluate_free_plan_policy` (every refusal), `groq_library_problems`, and the scenario's input bound and output cap against the policy's limits.
6. Readiness: telemetry configuration, backend probe (empty OTLP request), installed SDK against the checkout (seven audited modules), `LLMI_DASHBOARD_PROJECT_ID`, loopback dashboard URL.
7. Dry run: prints the plan and stops (exit 0). Execute: any refusal → exit 2, nothing built.
8. Execute: the audited client from `build_groq_http_client`, checked by `live_groq_client_problems`; telemetry; `ObservedGroqClient(provenance="real", send_gate=FreePlanSession)`; one `generate_text` under root span `demo.live-compare`; the guard's snapshot; flush and shutdown; the client closed.

### 4.2 As shipped

Every execution is refused twice: the policy returns `real_execution_disabled`, `free_plan_evidence_missing` and `model_not_free_plan_verified`, and `ObservedGroqClient` refuses `provenance="real"` while the lock is false. The dry run is still useful: it reports the scenario, the fixed constraints, the readiness and every refusal, with `execution_ready: true` when the local stack is ready.

### 4.3 One request, nothing more

- The runner calls the adapter once. A test counts the guard's `authorize_send` calls: exactly one for a completed answer, a 429, a 503 and a timeout. The adapter makes one physical attempt, and the guard's session and process slot refuse any second one.
- A second execution in the same process is refused (`request_limit_reached`), including after a proven-unsent connect failure.
- The model comes from the policy alone, which requires exactly one allowlisted model. There is no model or provider fallback, and no paid mode.
- `incomplete` (`length`) is reported and never continued (exit 3).
- Halts (`service_tier_not_free_plan`, `response_model_mismatch`, `paid_plan_indicator` for 402 and `blocked_api_access`, `usage_exceeded_bound`) set `free_plan_violation` and exit 3. They never cause a request.
- Interrupted during the send: `possibly_sent`, `provider_request_sent: null`, exit 1, no resend. Interrupted before: `interrupted_before_send`, exit 2.
- The request state is the guard's own record (`FreePlanSession.snapshot()`); anything unreadable is `possibly_sent`, never `not_sent`.

### 4.4 Credential binding

- **Runner:** the run copies the environment once (`MappingProxyType(dict(env))`) and uses that copy for the policy, the telemetry configuration, the client's key and the send gate. The client receives `groq_live.api_key_value(copy)`, the exact reading the policy validates (surrounding whitespace removed). A test changes the caller's environment after the run starts (another key and a proxy variable): the request still carries the validated key and the guard sees the validated values.
- **Guard (`groq_live.FreePlanSession.authorize_send`), new check:** one copy of the session's environment is read for the whole authorization, and after the endpoint boundary the gate refuses with `credential_not_bound` unless the request has exactly one `Authorization` header equal to `Bearer` and that copy's validated key, compared with `hmac.compare_digest`. Refused: another key, a key changed between construction and send, a truncated or extended key, two spaces, a lowercase or other scheme, no scheme, non-ASCII. The refusal names the variable, never a value, and leaves the slot free.
- **Why in the guard as well:** a check only in the runner would not protect another caller of the adapter. The change adds a check and removes none. The guard's own test fixture sent `Authorization: Bearer x` with a different validated key, which this check now refuses; that one fixture constant was changed to the validated key (the only edited line of an existing test, §6).

### 4.5 Output

One JSON line, schema `llminsight.demo.compare.v1`: mode, request state, eligibility and readiness, refusals (codes and fixed messages), provider selection, scenario metadata, fixed constraints, plan, environment, provider outcome, provider-reported usage, cost (`unknown`, `billed_amount_usd: null`, `list_price_estimate_usd: null`, `ledger_cost_status: unknown (price_missing)`), the guard's session record, `free_plan_violation`, validation rule codes, export outcome, comparison project and grouping key, correlation links. Never: the key, the project key, the input, the output, reasoning or provider error text (tested on every run of the test helper, on logs at DEBUG and on the exported protobuf bytes). Importing the runner loads neither `google.genai` nor `openai` (tested in a subprocess).

Exit codes: 0 dry run, or completed, validated and export confirmed; 1 failed, blocked, error, or interrupted after the send may have begun; 2 refused before a send; 3 answered but not validated or export not confirmed, an incomplete answer, or a Free Plan violation halt.

## 5. Comparison project and the dashboard

- **Project (D5):** create it with `python -m app.cli create-key --project llm-insight-compare`, which prints the project ID and the ingest key once. Run with that key in `OTEL_EXPORTER_OTLP_TRACES_HEADERS` and its ID in `LLMI_DASHBOARD_PROJECT_ID`. The SDK cannot verify the key's project, so the output says `operator_env_unverified` (R125).
- **What is exported** (tested against the local OTLP stub): one trace with an INTERNAL root span `demo.live-compare` carrying no `gen_ai.*` attribute, and one CLIENT span `chat <model>` whose attribute keys are exactly those of the shared fixture `docs/fixtures/groq_client_span.v1.json`: provider `groq`, the request and response model, `gen_ai.prompt.name` `cmp.<scenario>`, `gen_ai.prompt.version` `cmp-v1.<scenario>`, `gen_ai.request.max_tokens` 1024, `llminsight.provenance` `real`, `server.address` `api.groq.com`.
- **On the backend** (new `backend/tests/unit/test_comparison_contract.py`, real ingest rules, no database): the prompt identity and `real` provenance are kept under the unchanged `llminsight.attrmap.v1` with no dropped attribute; a catalogue row for `groq` at a listed or zero rate never prices the call (`unknown` / `price_missing`); the three scenarios give three distinct Compare keys, none with the Gemini demo's `v1`.
- **Expected views**, for the comparison project after one completed request:
  - **Overview:** one trace, one inference call, provider `groq`, tokens as reported, cost unknown with `cost_complete: false`.
  - **Traces:** the trace, filterable by provider `groq`, model and prompt version; its detail shows the root and the CLIENT span with status and finish reason.
  - **Models / Compare:** one row (`groq`, model, model, `cmp-v1.<scenario>`) with n = 1, flagged low-sample; a run of another scenario adds its own row.
- **Not verified here:** storage in PostgreSQL and the read API responses for these spans. That is the integration checkpoint (8A-5), which runs in CI only.

## 6. Changes

| File | Change |
|---|---|
| `demo_app/comparison_scenarios.py` | New: registry, pins, checks, validators |
| `demo_app/live_compare.py` | New: the runner |
| `sdk/src/llm_insight_sdk/groq_live.py` | `api_key_value`; `_credential_bound`; `authorize_send` reads one environment copy and refuses `credential_not_bound`; docstring |
| `sdk/tests/unit/test_comparison_scenarios.py` | New |
| `sdk/tests/unit/test_compare_runner.py` | New |
| `sdk/tests/unit/test_groq_live_guard.py` | Appended: credential-binding tests. Edited: the request fixture's `Authorization` value now carries the validated key (one line) |
| `backend/tests/unit/test_comparison_contract.py` | New |
| `docs/ADR.md` | ADR-45; §13 introduction |
| `docs/SECURITY.md` §6 | Comparison runner bullet |
| `docs/SOURCES_AND_RISKS.md` | R122 note; R125–R128 |
| `docs/PHASE_STATUS.md` | 8A-3 CI line corrected; 8A-4 lines |
| `README.md`, `sdk/README.md` | Runner settings and usage |

No dependency, lock, migration, backend application code, frontend, workflow, Gemini or `demo_app` Gemini file changed. `.env.example` was deliberately **not** changed, although its Groq comment ("No runner exists") is now outdated: a new blob of that file makes its reviewed `CHANGE_ME` placeholder a new history-gate occurrence, which needs its own reviewed baseline entry (R127).

## 7. Tests and results

### 7.1 New tests

| File | Tests | Covers |
|---|---|---|
| `sdk/tests/unit/test_comparison_scenarios.py` | 56 | Pins equal to the 8A-0 drafts, exact sizes, shared caps equal to both guards, identity (distinct, derived, apart from Gemini, accepted by the telemetry rules), every tamper refused, fixture shape and ties, every validator rule, no answer text in a result |
| `sdk/tests/unit/test_compare_runner.py` | 73 | As shipped (dry run reports the locks; execute builds no client); released dry run; one completed request per scenario with the exact request body and header; exported trace and attribute keys; failures and their request state (429, 500, 401, timeout, protocol error, connect error, connect timeout, empty, blocked) with one request and no retry; incomplete; failed validation; unconfirmed export; five halts; second execution refused; clock and client re-checked at the send; credential binding; interruption during and before the send; client construction failures; providers refused before any read; command line; readiness and limits; checkout and library audit; one guard call; fixed constraints; logs, stdout and exported bytes free of keys and content; no other provider library imported |
| `sdk/tests/unit/test_groq_live_guard.py` (appended) | 15 | `credential_not_bound` for another key, a key changed after construction, seven malformed `Authorization` values and non-ASCII; whitespace read as the policy reads it; `api_key_value`; the helper never binds an empty key and never raises |
| `backend/tests/unit/test_comparison_contract.py` | 6 | §5 on the backend's real ingest and pricing rules |

All are offline. The SDK tests block every non-loopback connection; the provider is the SYNTHETIC `FakeGroq` behind the real audited client.

### 7.2 Results

Commands ran on Windows from this working tree; the SDK suite on a clean, non-editable install.

| Check | Command | Result |
|---|---|---|
| SDK clean install | Venv from `sdk/requirements-dev.lock`, `pip install --no-deps --force-reinstall` of a copy of `sdk/`; `pip check`; AST comparison with the checkout | No broken requirements; 14 of 14 modules AST-identical; not editable |
| SDK full regression | `python -m pytest -q` in `sdk/`, no `PYTHONPATH`, API-key variables removed | **2200 tests: 2119 passed, 0 failed, 81 skipped** (database-backed, `LLMI_TEST_REQUIRE_DB` unset) |
| Backend unit | Scratch copy without `.env` or user files: `python -m pytest tests/unit -q` | **1208 passed**, 0 failed, 0 skipped (1202 before, plus 6 new) |
| Backend privacy and cost | Scratch copy: `test_groq_contract`, `test_comparison_contract`, `test_pricing`, `test_metadata_privacy`, `test_redaction` | **481 passed** |
| Ruff | SDK `ruff check .` and `ruff format --check .`; `demo_app` with the SDK config; backend `ruff check .` and `format --check .` | PASS (SDK 56 files, `demo_app` 11, backend 144) |
| mypy strict | `mypy src`; `mypy --config-file pyproject.toml ../demo_app` | PASS (14 and 11 files) |
| Bandit gate | Bandit 1.9.4 over `backend/app sdk/src demo_app scripts`; `bandit_baseline_check.py validate-baseline` and `evaluate` | PASS: 44 findings = 44 entries (1 / 5 / 38), 0 problems, baseline unchanged |
| QA self-tests | `python -I -m unittest discover -s scripts/qa/tests` | 214 ran, OK, 15 skipped |
| Credential detectors | `secret_scan` detectors over the 13 changed and new files | 0 matches |
| History secret gate | Throwaway clone holding this working tree as a commit, with a PR-style merge ref: self-tests, `validate-baseline`, `coverage`, `scan`, `evaluate` | PASS: 39 findings = 39 entries, 0 problems, coverage COMPLETE; 0 findings in the 14 new or changed blobs; baseline unchanged |
| Doc check and whitespace | `python -I scripts/verify_phase0.py`; `git diff --check` | 828 PASS, 0 FAIL; clean |
| PostgreSQL, read API, dashboard E2E, CI | | **NOT RUN** (CI only; 8A-5 and 8A-6) |
| Real provider request | | **NOT RUN** (forbidden) |

### 7.3 Mutation testing

Each mutant is a private copy of `sdk/`, `demo_app/`, `docs/` and `.env.example`, imported first, so the runner's checkout check compares the mutated code with itself. A control run of the unmutated copy must pass first (611 passed).

**New mutants: 48 of 48 caught** on the final code.

| Group | Mutants | Examples of the fault |
|---|---|---|
| Runner, what is sent (R01–R12) | 12 | Refused provider allowed; scenario check skipped; dry run executes; refusals ignored at execute; a retry after an error; a second request after any outcome; send gate not attached; session given the caller's live environment; no environment copy; key read without the policy's stripping; unknown request state read as `not_sent`; send-time refusal hidden |
| Runner, what is reported (R13–R19, R29) | 8 | Halt does not change the exit code; validation or export ignored for success; cost reported as 0 or `computed`; incomplete answer validated; retries reported as allowed; interrupted send reported as success |
| Runner, readiness and command line (R20–R28) | 9 | Links without the project; non-loopback dashboard; project ID, backend, checkout or library audit ignored; abbreviated or repeated options accepted; scenario limits not checked early |
| Scenarios (C01–C12) | 12 | Pin, size, prompt-version, safe-form, ID-shape or uniform-cap checks removed; wrong expected values; wider tolerance; ties accepted; duplicates allowed; oversized answer parsed |
| Guard binding (G01–G07) | 7 | Check removed; only the scheme compared; prefix or case-insensitive match; key read without stripping; empty key bound; check after the slot is taken |

The first run caught 44 of 48. The four survivors were real gaps and were fixed before the second run:
- **R29:** the runner returned 3 for "halted without an outcome", which cannot happen (a halt needs a response or a provider error). The dead branch was removed and R29 retargeted to the remaining line (interrupted send reported as success).
- **C08:** the duplicate-identity test used a clashing `prompt_version`, which the per-entry rule already refuses. A test with a clashing `prompt_name`, which only the registry check catches, was added.
- **C12:** the ID-shape and `version >= 1` rules were each masked by another rule. Two tests isolate them.
- **G04:** the empty-key and non-ASCII conditions of the binding helper cannot be reached through `authorize_send`, because the policy refuses such keys first. Two direct tests of the helper were added; with the mutant, an empty key bound `Bearer `.

**Earlier mutants: 142 of 142 caught** on the final code. All 142 mutants of 8A-3 (96 on `groq_live.py`, 46 on `groq_adapter.py`) were rerun with the 8A-3 harness against the reinstalled package, because the guard and its test fixture changed. 141 were caught in one run. S02 ("policy not re-read before the send") no longer matched, because `authorize_send` now evaluates its environment copy; it was retargeted to the same fault on the new line and caught. Earlier results are not used as evidence for this code.

## 8. Risks

| ID | State after 8A-4 |
|---|---|
| R119 | Unchanged: the plan is an operator attestation; the account's real plan is the boundary |
| R121 | Unchanged: method replacement in the same process is not detected |
| R122 | Unchanged limit; the runner is now shown to ask the guard once per process |
| R123 | **Open**: Gemini comparison runs are refused until its fix is reviewed |
| R115, R120, R124 | Unchanged: real response shapes, a real TLS failure and the Free Plan service tier are untested |
| R125 (new) | The comparison project is an operator statement |
| R126 (new) | Single samples are not a ranking |
| R127 (new) | `.env.example` Groq comment outdated until a reviewed change with its baseline entry |
| R128 (new) | The checkout match covers seven modules, not `config.py` |

## 9. Preconditions for the first real Groq request

All must hold, in this order; none is met today.

1. **Evidence gate (8A-7, blockers B1, B2, B5, B7):** the operator reads, on the day, the Groq console **Limits** page of the account to be used and records the plan, the model's request and token limits and the date; and the official models, deprecations and rate-limit pages, confirming the model is production, not deprecated and available on the Free Plan. Recorded in a report with URLs and UTC time.
2. **Reviewed release change:** `FREE_PLAN_MODELS` gets exactly that model with its sources, `FREE_PLAN_EVIDENCE_VERIFIED_AT` the reading time (valid 14 days), and `REAL_EXECUTION_ENABLED = True`, in one reviewed commit with its CI green.
3. **Integration evidence:** the 8A-5 PostgreSQL and read-API tests and the 8A-6 dashboard tests green in CI, or an explicit waiver.
4. **Comparison project** created, its key and ID used (§5).
5. **Environment** in the operator's own shell only: `LLMI_PROVIDER_MODE=real`, `LLMI_GROQ_MODE=free-plan`, `LLMI_LIVE_CONSENT=groq-free-plan-single-call`, `LLMI_GROQ_PLAN=free`, `LLMI_GROQ_PLAN_CHECKED_ON=<today, UTC>`, `LLMI_LIVE_PROVIDERS=groq`, `LLMI_LIVE_MODELS=<the one model>`, `LLMI_LIVE_MAX_REQUESTS=1`, `LLMI_LIVE_MAX_INPUT_TOKENS=1024`, `LLMI_LIVE_MAX_OUTPUT_TOKENS=1024`, `GROQ_API_KEY`, telemetry variables; none of the routing, proxy, CA or key-log variables.
6. **Dry run reviewed:** `execution_allowed: true`, no refusal, the plan's model, caps and dates as expected.
7. **Explicit approval** of exactly one `--execute` for one named scenario.

## 10. First-call authorization proposal (not executed)

| Item | Proposal |
|---|---|
| Provider | Groq, `POST https://api.groq.com/openai/v1/chat/completions` |
| Model | Candidate `openai/gpt-oss-20b` (8A-0 §3: production, not deprecated, read 2026-11-09). **Its Free Plan availability is UNVERIFIED** and must be confirmed at the evidence gate; if not confirmed, no model is proposed |
| Free Plan evidence | **None yet.** Required: the account's console Limits page and the official pages, read by the operator within 14 days before the call (precondition 1). No date can be stated today |
| Scenario | `cmp-ticket-triage`: 621 bytes, input bound 685, SHA-256 `5d9040f3…12c267ff`; fixed SYNTHETIC text only |
| Output bound | `max_completion_tokens` 1024; `include_reasoning: false`; whether reasoning counts against the limit is unverified (B5) |
| Requests and retries | 1 request, 1 physical attempt, 0 retries, concurrency 1; no model or provider fallback; one execution per process |
| Endpoint and routing | Direct HTTPS to the pinned host, TLS against the pinned `certifi` bundle, TLS 1.2+, no key log, HTTP/1.1, one connection, no keep-alive, no proxy, Unix socket, redirect, base-URL override or mock transport; exact headers and body; credential bound to the validated key |
| Expected output | One JSON line: `provider_request_state: sent`, outcome `completed`, validation rules, provider-reported tokens, cost `unknown`, `free_plan_violation: null`, export `confirmed`, trace ID and project-scoped links; exit 0 |
| Expected telemetry | One trace in the comparison project: root `demo.live-compare`, one CLIENT span `chat openai/gpt-oss-20b`, provider `groq`, `cmp-v1.ticket-triage`, provenance `real`, usage `provider_reported`, cost `unknown` / `price_missing` |
| Dashboard verification | Overview (one trace, cost unknown), Traces (filter provider `groq`), trace detail (two spans), Models / Compare (one low-sample row); then the Groq console's usage view shows one request |
| Abort before the send | Any dry-run refusal; a backend that is not ready; a checkout mismatch; any change to the planned model, caps or dates |
| Stop after the call | Any exit other than 0; `possibly_sent`; any `free_plan_violation`; a 401, 402, 403, 429 or `blocked_api_access`; a service tier other than `on_demand` or null; a response model other than requested; usage above the bounds. **No re-run**: report and wait for review |
| Billing risks | If the account is not on the Free Plan (R119), Groq bills the one request at its published per-token price for that model, for at most 685 input and 1024 output tokens. Groq offers spend limits only on paid plans and no plan API. The halts detect but cannot prevent a charge. The 14-day evidence and one-day attestation reduce, not remove, this risk |

**Recommendation:** do not execute until preconditions 1 to 7 hold; the next checkpoint is 8A-5 (integration), then 8A-6 (dashboard), then the 8A-7 evidence gate.

## 11. Summary

**Phase 8A-4 readiness: ready for architectural review.** The scenarios and the runner are implemented and verified offline. Two points need the reviewer's decision: the guard change of §4.4 (a check added to the reviewed 8A-3 send gate, and one edited test fixture line), and leaving `.env.example` unchanged (R127). Real Groq, OpenRouter and Gemini requests stay **NO-GO**.

```text
PHASE / DATE: 8A-4 comparison scenarios and runner / 2026-11-10 UTC
BRANCH: feature/phase-8a4-comparison-runner on d32f3df, uncommitted, not pushed
PROVIDER REQUESTS / DOCKER / DATABASE / .ENV: 0 / none / none / not read
EXECUTION PATH: disabled (lock false, no evidence, empty allowlist); gemini and openrouter refused
SDK CLEAN INSTALL: 2200 tests, 2119 passed, 0 failed, 81 skipped (database-backed)
BACKEND UNIT: 1208 passed. PRIVACY/COST: 481 passed. QA: 214 OK, 15 skipped
RUFF / MYPY / BANDIT GATE / DETECTORS: PASS / PASS / PASS, baseline unchanged / 0 matches
HISTORY GATE: PASS, 39 = 39, baseline unchanged. DOC CHECK: 828 PASS, 0 FAIL
MUTATIONS: 48 of 48 new, 142 of 142 earlier, on the final code
NOT RUN: real requests, real TLS, PostgreSQL, read API, dashboard E2E, 7 CI jobs on this code
COMMIT: not authorized, not made. 8A-5: not started
```

## 12. Architectural approval (2026-11-10)

The reviewer accepted the implementation and verification of §1 to §11: **conditional GO for one local commit only.** Both open decisions were approved:
1. The additive credential-binding check in the shared Groq guard (§4.4). ADR-44 now records it in its own decision text, beside ADR-45.
2. Deferring the inaccurate `.env.example` comment under R127, **provided it is corrected before the first real request**.

The final check before staging confirmed: the binding check fails closed and refuses before any send, with no credential value in a message, log, exception or span; both locks and the empty allowlist unchanged; `gemini` and `openrouter` refused; R123 and R127 open; no `.env.example` or dependency change; the history baseline valid at 39 of 39; the three user-owned files unchanged. The only changes after §11 are documentation (this section, the status line, ADR-44's decision text and `PHASE_STATUS.md`), so no test was rerun; the doc check, `git diff --check` and the history gate were. §11 records the state before this approval. The commit is not pushed; its SHA is reported to the owner and not recorded in the commit itself.
