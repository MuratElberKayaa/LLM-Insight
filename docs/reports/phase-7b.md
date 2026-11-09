# Phase 7B — Gemini content demo: offline implementation

**Date:** 2026-11-09 UTC. **Scope:** the sibling CLI runner, three synthetic scenarios, the shared security path, ADR-39, and offline tests. **Provider requests in this phase: 0.** No push, PR, merge, rebase, reset or tag. No Docker command, no database access, no `.env` change, no change to the Free Tier evidence date.

Evidence labels used below: **SOURCE VERIFIED** (read in this checkout), **LOCALLY EXECUTED** (run in this session on Windows 11, Python 3.12.6), **NOT RUN**, **BLOCKED**.

---

## 1. Git baseline and 7A checkpoint

| Item | Value |
|---|---|
| Repository root | `C:/Users/PC/LLM-Insight-Development` |
| Branch | `feature/phase-7-gemini-demo` |
| Base | `435030648240ee54af78622a0d4d30737a84ba0b`, equal to GitHub `main` when checked |
| 7A checkpoint | `7397a142f063ef3f23957dd1e13ca6b54dd9ef13`, `docs: record Phase 7A Gemini demo design`, two files only |
| Author and committer | The configured Git identity; no co-author trailer |

The two 7A documents were validated first: `verify_phase0.py` 828 PASS / 0 FAIL, `git diff --check` clean, user-file hashes equal to the 7A report.

## 2. Test environment

| Item | Value |
|---|---|
| Interpreter | `sdk/.venv/Scripts/python.exe`, Python 3.12.6, created from `C:\Python312\python.exe`; git-ignored |
| Install | `pip install -r sdk/requirements-dev.lock`, then `pip install --no-deps -e .` |
| SDK import origin | `sdk/src/llm_insight_sdk/__init__.py` (this checkout) |
| SDK version | 0.2.0 |
| `pip check` | No broken requirements |
| Audited libraries | `google-genai` 2.30.1, `httpx` 0.28.1, `httpcore` 1.0.9 |

Two tool environments were built in the session scratchpad, outside the repository: a non-editable SDK install from a copy of `sdk/` (to run the tests that assert a non-editable install, as CI does), and the Bandit tool from the hash-checked `security/tools/requirements-audit.lock`. On Windows, `pip check` in the Bandit tool environment reports `colorama` missing, because that lock is resolved for the Linux CI runner. It does not affect the JSON report.

## 3. What was built

### 3.1 Files

| File | Change |
|---|---|
| `demo_app/live_gemini_content.py` | New: CLI, JSON document, exit codes, links, readiness |
| `demo_app/gemini_scenarios.py` | New: registry, composition, registry check, validators |
| `demo_app/content_preview.py` | New: answer extraction, escaping, bounds, terminal rule |
| `demo_app/live_gemini.py` | `run_single_request` extracted; four helpers renamed public; smoke behaviour unchanged |
| `sdk/tests/unit/test_gemini_scenarios.py` | New, 93 tests |
| `sdk/tests/unit/test_gemini_content_preview.py` | New, 46 tests |
| `sdk/tests/unit/test_gemini_content_runner.py` | New, 183 tests |
| `docs/ADR.md` | ADR-39 appended |
| `docs/SECURITY.md` §6 | Two paths; preview boundary |
| `docs/SOURCES_AND_RISKS.md` | R106 to R109 registered |
| `README.md`, `sdk/README.md` | Usage |
| `docs/PHASE_STATUS.md`, this report | Status and evidence |

Unchanged, confirmed with `git status` on these paths: `sdk/src/**`, `demo_app/live_common.py`, `backend/**`, `frontend/**`, `security/**`, `.github/**`, the lock files, `.env.example`, and the existing Gemini test files.

### 3.2 Commands

```text
sdk/.venv/Scripts/python -m demo_app.live_gemini_content --scenario <id>                     # dry run
sdk/.venv/Scripts/python -m demo_app.live_gemini_content --scenario <id> --execute           # one request, if allowed
sdk/.venv/Scripts/python -m demo_app.live_gemini_content --scenario <id> --execute --preview # plus local preview
```

Scenario IDs: `support-ticket-classification`, `json-data-interpretation`, `short-text-summary`. Other options: `--model`, `--dashboard-url`. There is no input-file, prompt, token, temperature, retry, run-all or fallback option. An abbreviated, repeated or unknown option is a JSON refusal with exit 2, and the value is not echoed. `--help` is the one case where stdout is not a JSON line.

Operator settings beyond the smoke runner: `LLMI_LIVE_MAX_OUTPUT_TOKENS` must be at least the scenario's cap (512, 768, 512), `LLMI_LIVE_MAX_INPUT_TOKENS` at least its bound (665, 721, 675), and `LLMI_DASHBOARD_PROJECT_ID` must be a valid project ID. The environment can cause a refusal; it can never change the request.

### 3.3 Scenarios

Text, composition and SHA-256 are those of `reports/phase-7a.md` §3, measured again by test and in the CLI dry runs.

| ID | Version | Input bytes | Bound | Output cap | SHA-256 prefix |
|---|---|---|---|---|---|
| `support-ticket-classification` | 1 | 601 | 665 | 512 | `18d3061e26e4` |
| `json-data-interpretation` | 1 | 657 | 721 | 768 | `1e9f1ba31ae5` |
| `short-text-summary` | 1 | 611 | 675 | 512 | `740179ed9944` |

The bound is UTF-8 bytes + 64. It is a safe upper bound, not a tokenizer count. The SHA-256 identifies the local text and does not prove provider receipt. Beyond the 7A design, the pin is also held in the registry, so a changed text is refused at run time before any client exists, not only caught by a test.

### 3.4 Shared security path

`live_gemini.run_single_request` holds the reservation, client construction, ADR-34 checks, one adapter call under a root span, settlement, flush and shutdown. Both runners call it. The new runner contains no `genai.Client(`, `httpx.Client(`, `FreeTierSession(`, `ObservedGeminiClient(` or `GeminiTextAdapter(`; a test asserts this.

The function returns a result and hands back an aborting exception after settling and closing. The smoke runner re-raises it, so its behaviour on Ctrl-C (no JSON line) is unchanged. A send that never began now settles zero attempts instead of one; the session is local to that call, so nothing outside it can observe the difference.

### 3.5 Corrections from the 7A findings

| Finding | Result |
|---|---|
| Dry-run readiness | `execution_allowed` requires both `policy_eligible` and `execution_ready`; backend-not-ready is listed as a refusal. Scenario limits are evaluated early, so a dry run cannot say ready for a request the session would refuse |
| Explicit execution | `allow_abbrev=False`; only the complete `--execute` flag sends |
| Telemetry | Export outcome uses flush, shutdown and the counters, so an earlier failed export is not hidden by a successful flush |
| KeyboardInterrupt | One sanitized JSON line. `possibly_sent` with `provider_request_sent: null` once the send may have begun; `not_sent` with exit 2 before that. An interrupt during the flush keeps the known provider outcome, with export `unknown` |
| Project links | Confirmed in `backend/app/api/traces.py` that `project` is required (1 to BIGINT max). New runner's `api_path` and `dashboard_url` both carry it. Missing or invalid ID is the refusal `dashboard_project_invalid`. The shared `live_common.dashboard_links` is unchanged, as is the smoke runner's `api_path` |
| Stale install | `sdk_checkout_match` compares six audited modules byte for byte; `false` refuses, `unknown` (no source tree) does not |

### 3.6 Exit codes and output contract

| Exit | Meaning |
|---|---|
| 0 | Dry run printed, or provider `completed`, validation `passed` and export `confirmed` |
| 1 | Provider failed, blocked the answer, or the call was interrupted |
| 2 | Refused before a provider send |
| 3 | Provider `completed` or `incomplete`, but validation did not pass or export is not confirmed |

Missing or inconsistent usage does not change the exit code: `tokens` is `null`, `usage_check` says why, and cost stays `unknown` with `billed_amount_usd: null`.

Stdout is one JSON line, schema `llminsight.demo.gemini_content.v1`:

| Key | Content |
|---|---|
| `schema`, `mode` | `dry_run`, `execute` or `invalid_arguments` |
| `provider_request_sent`, `provider_request_state` | `true` / `false` / `null`; `not_sent` / `sent` / `possibly_sent` |
| `policy_eligible`, `execution_ready`, `execution_allowed`, `refusals[]` | Eligibility, readiness, and coded refusals |
| `scenario` | ID, version, fixture ID, prompt name and version, `input_sha256`, byte count, bound, output cap |
| `plan` | The smoke plan with the scenario's cap and bound |
| `environment` | `backend{ready,reason}`, `sdk_checkout_match` |
| `provider` | `outcome`, reasons, category, provider, request and response model, attempts, duration, provenance |
| `usage` | `tokens`, `provider_usage`, `usage_check`, `cost` |
| `session` | Requests started, limit, physical attempts, halted |
| `validation` | `status`, `reason`, `rules[{rule,passed}]`, `fence_stripped` |
| `export` | `outcome` (`confirmed` / `failed` / `partial` / `unknown`), `flushed`, `shutdown_ok`, three span counters |
| `preview` | `requested`, `shown`, `output_shown`, `reason`, sizes, `truncated`, part counts |
| `correlation` | `trace_id`, `project_id`, `project_id_source`, `dashboard_url`, `api_path` |

`confirmed` means the local backend acknowledged every ended span. It is not proof of storage; that is Phase 7C evidence.

### 3.7 Preview

Opt-in by `--preview`, written only to stderr, and refused before any send unless stderr is a real terminal and `CI` / `GITHUB_ACTIONS` are unset. Input is limited to 1200 characters; output to 2000 characters and 40 lines. Control, bidi, zero-width, tag, surrogate and line-separator characters appear as visible escapes, and an escape is never cut. Text is read part by part from `candidates[0].content.parts`; `response.text` is not used and the response is never serialized. Thought parts are always excluded. A function call or other non-text part withholds the answer. A blocked, failed, errored or interrupted outcome shows nothing. **Terminal scrollback, screen recording and capture tools can still keep what is shown (R106).**

### 3.8 Differences from the 7A design

All are additions or stricter behaviour required by the Phase 7B instruction:

- `provider_request_sent` is `null` when the send may have begun.
- Added `policy_eligible`, `execution_ready`, `preview.output_shown`, `input_sha256_scope` and `input_bound_note`.
- A missing or invalid project ID is a refusal.
- The registry holds the pinned hash as well as the tests.
- A refused `--model` is removed from the plan so it is not echoed.

## 4. Verification results (this session)

### 4.1 Tests, LOCALLY EXECUTED

Commands run from `sdk/`, with `PYTHONDONTWRITEBYTECODE=1`, using `-p no:cacheprovider`.

| Suite | Pass | Fail | Skip |
|---|---|---|---|
| `tests/unit/test_gemini_scenarios.py` | 93 | 0 | 0 |
| `tests/unit/test_gemini_content_preview.py` | 46 | 0 | 0 |
| `tests/unit/test_gemini_content_runner.py` | 183 | 0 | 0 |
| `tests/unit/test_gemini_runner.py` (smoke, unmodified) | 37 | 0 | 0 |
| `tests/unit/test_gemini_adapter.py` | 75 | 0 | 0 |
| `tests/unit/test_gemini_endpoint.py` | 41 | 0 | 0 |
| `tests/unit/test_gemini_live_guard.py` | 66 | 0 | 0 |
| **All of `tests/unit`, non-editable SDK (as CI installs it)** | **1089** | **0** | **0** |
| All of `tests/unit`, editable SDK (this phase's environment) | 1086 | 3 | 0 |

The 3 editable-install failures are all in `tests/unit/test_install.py`, and each asserts a non-editable install. The same 3 failed before any code changed (baseline: 764 passed, 3 failed, 767 total). All three pass in the non-editable environment. They are a consequence of the requested editable install, not a regression.

Every new test uses a fake provider transport and `block_network`, which fails any non-loopback connection attempt.

### 4.2 Real command line, LOCALLY EXECUTED

Five dry-run or refused invocations of `python -m demo_app.live_gemini_content` in an empty environment, with no key and no telemetry settings, and without `--execute`:

| Arguments | Exit | Stdout lines | Stderr bytes | Request state | Note |
|---|---|---|---|---|---|
| each of the three scenarios | 0 | 1 | 0 | `not_sent` | `execution_allowed: false`, 13 refusals, hash matches pin, `sdk_checkout_match: true` |
| `--scenario short-text-summary --exec` | 2 | 1 | 0 | `not_sent` | `argument_invalid` |
| `--scenario no-such-scenario` | 2 | 1 | 0 | `not_sent` | `scenario_unknown` |

### 4.3 Mutation check, LOCALLY EXECUTED

Seventeen deliberate defects, each applied to a scratch copy: escaping removed, thought filter removed, preview allowed off a terminal, resend on provider error, shutdown result ignored, checkout check bypassed, readiness ignored, abbreviations allowed, interrupted reported as not sent, `api_path` without project, flush hiding earlier failures, exit 3 removed, policy cap replacing the scenario cap, invalid project accepted, registry widened, pinned hash unchecked, duplicate keys accepted. **17 of 17 caught.** The control run passed 322 tests. The check ran before the final one-line JSON key rename in §4.4, which touches none of the mutated code.

### 4.4 Static and security gates, LOCALLY EXECUTED

| Check | Result |
|---|---|
| `ruff check .` and `ruff format --check .` from `sdk/` | PASS, 47 files formatted |
| `ruff check ../demo_app` and `ruff format --check ../demo_app` | PASS, 9 files formatted |
| `python -m mypy src` | PASS, 12 source files |
| `python -m mypy --config-file pyproject.toml ../demo_app` | PASS, 9 source files |
| `bandit -r backend/app sdk/src demo_app scripts -f json` (1.9.4), then `bandit_baseline_check.py validate-baseline` and `evaluate` | First final run **FAIL**: one new B105 on the JSON key `input_token_bound_note`. Cause fixed by renaming the key. Final run **PASS**, High 1 / Medium 5 / Low 38, baseline 44 of 44 |
| `security/**` | Unchanged; baseline not regenerated |
| `python -I -B scripts/verify_phase0.py --quiet` | PASS, see §4.5 |
| `git diff --check` | PASS |
| User-file SHA-256 | Equal to the 7A report, see §4.5 |

### 4.5 Final checkpoint before the commit

Run after this report was written, immediately before the commit: `verify_phase0.py`, `git diff --check`, the repository's secret detectors on the changed and new files, and the three user-file hashes. Results are in the reviewer's final report.

### 4.6 NOT RUN and BLOCKED

| Item | State | Reason |
|---|---|---|
| Real Gemini request | NOT RUN | Forbidden in this phase. `--execute` was not run against the real client |
| `sdk/tests/integration`, real PostgreSQL, real backend | NOT RUN | Needs a test-owned database; Phase 7C |
| Backend and frontend suites | NOT RUN | No backend or frontend file changed |
| GitHub CI on these commits | NOT RUN | Nothing pushed. All results above are from Windows; CI runs Linux |
| pip-audit, history secret gate | NOT RUN | No dependency change; the history gate covers pushed history in CI |
| Provider or account checks | NOT RUN | Operator preflight, later |

Nothing was BLOCKED.

## 5. Risks

| ID | State |
|---|---|
| R102 | **OPEN, NOT REMEDIATED**; review deadline 2026-11-30 UTC. Untouched |
| R105 | **OPEN, NOT ACCEPTED**. Untouched |
| R104 | Resolved by merged PR #2 and successful `main` CI, as stated in the Phase 7B instruction. Its documentation closure remains a separate task |
| R106 to R109 | Registered in this phase |
| Free Tier evidence | The guard refuses after **2026-11-11 13:00 UTC**. Timestamp not changed. Refusal after that date is expected fail-closed behaviour. Refreshing official evidence and private account verification belong to the operator preflight |
| Thinking tokens | They count against the output cap and cannot be disabled, so a scenario can end `incomplete` (exit 3). Unknown until a real run |
| Smoke runner limits | Retained by design: `api_path` without `project`, `--exec` abbreviation accepted, no JSON on Ctrl-C |

Everything here is offline: no claim is made about real provider behaviour, about the answers real prompts would produce, or about storage in PostgreSQL.

## 6. Handoff

```text
PHASE / DATE: 7B, offline implementation / 2026-11-09 UTC
BASE BRANCH / WORKTREE: feature/phase-7-gemini-demo, 7A checkpoint 7397a14 on base 4350306 / C:/Users/PC/LLM-Insight-Development
USER FILES ALREADY PRESENT: LLM_INSIGHT_HANDOFF.md, docs/PHASE7_LIVE_DEMO_PLAN.md, docs/OPUS_PHASE7_PROMPTS.md (untracked, untouched)
IMPLEMENTED: demo_app/{live_gemini_content,gemini_scenarios,content_preview}.py; run_single_request in live_gemini.py; ADR-39; three unit test files
PROVIDER REQUEST COUNT: 0
DATABASE / DOCKER ACTIONS: none
ENVIRONMENT: sdk/.venv (editable, from lock, git-ignored). test_install.py needs a non-editable SDK
NEXT PHASE: 7C, only on a new instruction. Prerequisites below
```

### Phase 7C prerequisites

1. A separate instruction starting 7C.
2. Test-owned PostgreSQL: CI, or a separately isolated instance. Never the developer's existing database without separate authorization.
3. A backend environment for the integration harness (`backend/.venv` is absent here).
4. Evidence matrix: `reports/phase-7a.md` §9. Add a case for the project-scoped `api_path` against the real read API.
5. A real call remains out of scope for 7C and needs its own explicit authorization for one request after the operator preflight.
