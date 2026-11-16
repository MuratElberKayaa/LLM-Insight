# Phase 8H-CI — CI repair after commit 3472800

**Date:** 2026-11-16/17 (Europe/Istanbul).

**Trigger:** the user pushed `3472800` ("release: current project version") to
`release/groq-first-observed-request`. CI run 43933570995 failed in three jobs:
- `python`: SDK lint, format and strict types;
- `security-python`: pip-audit gate and Bandit gate;
- `frontend`: full npm audit policy.

`e2e`, `docs`, `image-build` and `security-history` passed.

**Approach:** each failure was reproduced locally with the same command CI uses, then fixed at the cause.
- 0 provider requests were made.
- No ADR changed.
- The Gemini execution switch is re-locked (`gemini_live.REAL_EXECUTION_ENABLED = False`) on the user's decision of 2026-11-17 (§7). It had been committed as `True` in `3472800`.

## 1. SDK lint, format and strict types (`python` job)

| Check | Before | Cause | Fix |
|---|---|---|---|
| `ruff format --check` | 3 files would be reformatted | `tests/unit/test_groq_adapter.py`, `test_live_batch.py`, `test_prompt_ab_wire.py` | `ruff format` on those three files (layout only) |
| `mypy demo_app` | 42 errors in 3 files | see below | typing fixes, no suppressions |

**Most of the 42 errors had one cause.** `dict.get()` returns `Any | None`, and `require(isinstance(x, dict) and …, code)` does not narrow the type for mypy.

The fix makes the check visible to mypy as a plain `if not isinstance(...): raise Refused(code)`, followed by the remaining `require(...)`. Each refusal keeps the same code, the same order and the same short-circuit behaviour. Fixed this way:
- `spec` in `stopped_batch_state` and `execute_recovery`;
- `recovery_spec`;
- `items` and `token` in `live_prompt_ab.py`;
- the `powershell` lookup.

**Other fixes:**
- **`smoke_file(path)`:** a new helper that narrows the optional smoke path and raises the same `smoke_result_required` refusal.
- **Imports:** `input_token_bound` and `compose_input` are imported from their defining modules (`llm_insight_sdk.live`, `demo_app.comparison_scenarios`), not through modules that re-export them. They are the same functions.
- **Annotations:** missing return annotations were added (`run_child -> tuple[int, Any]`, `execute -> dict[str, Any]`), along with `jobs: list[dict[str, Any]]` and `registry: types.ModuleType`.

**Side effect, by design:** `live_batch.code_fingerprint()` hashes `demo_app/*.py` and `sdk/src`. A plan prepared before this change and not yet executed will refuse with `*_code_changed`, so it must be planned and approved again. This is the existing fail-closed behaviour.

**Result:**
- `ruff check` clean;
- `ruff format --check` reports 70 files already formatted;
- `mypy src` and `mypy demo_app` report no issues.

## 2. pip-audit gate (`security-python` job)

**Finding:** CVE-2026-126360 / GHSA-mf5v-w3w3-f6cg in `opentelemetry-api` 1.45.1. `traceparent` parsing is quadratic in time, which allows a denial of service. It was reported by all four runs (backend and SDK lock × PyPI and OSV). The fix is in 1.45.2.

**Fix:** the OTel family moves together, because `opentelemetry-sdk` pins `opentelemetry-api` and `opentelemetry-semantic-conventions` exactly:
- 1.45.1 → 1.45.2: `opentelemetry-api`, `-sdk`, `-proto`, `-exporter-otlp-proto-common`, `-exporter-otlp-proto-http`;
- 0.66b1 → 0.66b2: `opentelemetry-semantic-conventions`, `-exporter-http-transport`, `-exporter-otlp-common`.

**Two-week rule:** each version was first uploaded on 2026-10-29 (PyPI JSON API, read 2026-11-16), 18 days before the change. CR-09 therefore holds, and no exception is needed.

**Files changed:**
- **Pins:** `backend/pyproject.toml`, `sdk/pyproject.toml`, `backend/requirements.lock`, `sdk/requirements-dev.lock`.
- **Test expectations:** `sdk/tests/unit/test_install.py`, plus `telemetry.sdk.version` in `test_instrumentation.py` and `tests/integration/test_live_backend.py`. These assert the real SDK resource.
- **Unchanged:** the backend's `test_metadata_privacy.py` value is a synthetic input.
- **Docs and comments:** the current-pin statements in ARCHITECTURE §6 (with an install note), TELEMETRY_CONTRACT, the SDK README and the SDK source comments. The new risk is recorded as R142 (Resolved).
- **Historical reports:** unchanged.

**Behaviour checked in the 1.45.2 source:**
- the exporter still reads `OTEL_EXPORTER_OTLP_TRACES_TIMEOUT` as seconds (`_resolve_timeout`, `float(raw)`);
- `BatchSpanProcessor` still stores `export_timeout_millis` without enforcing it.

The SDK README statements therefore remain true.

**Venvs:** reinstalled from the locks with `--no-deps`. `pip check` is clean in both. The SDK is installed non-editable, as CI does; `test_install.py` requires the site-packages copy.

**Result:** pip-audit gate PASS on all four runs:
- backend: 51 pinned packages, 0 vulnerabilities;
- SDK: 60 pinned packages, 0 vulnerabilities.

## 3. Bandit gate (`security-python` job)

**Findings:** six findings new since the Phase 6 baseline, all in the Phase 8 live-comparison scripts. Each was reviewed against its code.

| ID | File (line at review) | Test | Class | Review |
|---|---|---|---|---|
| BL-46 | `demo_app/live_batch.py` (19) | B404 Low | F8 | `import subprocess` for the runner below |
| BL-47 | `demo_app/live_batch.py` (352) | B603 Low | F8 | Argv list `sys.executable -m demo_app.live_compare --provider … --scenario … --dashboard-url …`, no shell. Provider and scenario come from `MODELS` and `scenarios.SCENARIOS` (refused otherwise, `unknown_selection`), through a plan whose SHA-256 the approval binds |
| BL-48 | `demo_app/live_batch.py` (833) | B603 Low | F8 | PowerShell found by `shutil.which`. Fixed script text with the JSON-encoded venv path and the own PID; no shell, 10 s timeout |
| BL-49 | `demo_app/live_prompt_ab.py` (16) | B404 Low | F8 | `import subprocess` for the runner below |
| BL-50 | `demo_app/live_prompt_ab.py` (144) | B603 Low | F8 | Same fixed-module argv as BL-47. Suite, provider and scenario come from `spec_for` (registered constants); no shell |
| BL-51 | `scripts/import_completed_ab.py` (40) | B310 Medium | F6 | `urlopen` only on `http://127.0.0.1:<port>/api/v1/imported-experiments?project=<id>`, with `--port` and `--project` parsed as `type=int`. No `file:` or custom scheme is reachable |

**Process:**
- The entries come from the checker's own `candidates` output for the clean report, plus a hand classification.
- Existing ids are unchanged; the new ids are BL-46…BL-51.
- The baseline totals are now 50 (High 1, Medium 6, Low 43).
- The SECURITY.md §8 register was regenerated with the checker's `register` command, and the F6/F8 descriptions name the Phase 8 cases.

**Checker change:** `ORIGINS` gains `phase-8`. The checker previously accepted only release tags `phase-1…6` or `UNVERIFIED HISTORICAL ORIGIN`, and neither would be true here. `phase-8` means the untagged Phase 8 live-comparison work, first committed in `3472800`.

**Result:**
- `validate-baseline`: valid, 50 entries;
- gate: PASS, 0 problems;
- QA self-tests: 256 OK (15 skipped);
- Bandit gate self-tests with real Bandit 1.9.4: 34 OK.

## 4. npm audit policy (`frontend` job)

**At CI time:** `R102 … dependency chain changed (gone: eslint-config-next)`. Nothing in `package.json` or `package-lock.json` had changed (`npm ls`: `eslint-config-next` 16.4.3 → `typescript-eslint` 8.71.1 (override) → `typescript-estree` 8.71.1 → `minimatch` 10.2.6).

npm's advisory data moved that day:
- `typescript-eslint` 8.73.0, published 2026-11-16T17:06Z and depending on `minimatch ^11.0.1`, made npm report `fixAvailable` and drop `eslint-config-next` from the dependent set for a while;
- a later run listed it again.

With the committed policy, the R102 chain matches again (21:00Z run). **The policy file is unchanged.** A narrowing edit was tried and reverted when the report changed back.

**New, after the push:** GHSA-mfr8-8qww-v76h, published 2026-11-16T18:51Z, moderate. It is in `next` `>=16.3.0 <=16.4.4`: "use cache" combined with `unstable_cache()` can disclose cached data across root params. The fix is in 16.4.5.
- **Exposure:** `next` is a production dependency, so the gate rejects it with no exception possible, which is correct.
- **Our use:** the code base has no `'use cache'`, `unstable_cache` or `cacheComponents` (grep of `app`, `components`, `lib`, `next.config.*`), so the vulnerable path is not used.
- **Fix version:** `next` / `eslint-config-next` / `@next/env` 16.4.5 were first published 2026-11-03T18:01Z. They meet the two-week rule from **2026-11-17T18:01Z**.
- **Status:** **blocked: user decision** (§6).

`npm audit --omit=dev` reports this one moderate finding.

## 5. Tests and checks run

| Command | Result |
|---|---|
| `ruff check`, `ruff format --check` (SDK, tracked + new files) | clean; 70 files already formatted |
| `mypy src`; `mypy --config-file pyproject.toml ../demo_app` | no issues (16 / 14 files) |
| SDK tests touching the changed demo files (`test_live_batch`, `test_prompt_ab*`, `test_compare_*`, `test_groq_adapter`) | 530 passed |
| `pip_audit_gate.py evaluate` (2 locks × PyPI, OSV) | PASS ×4 |
| `bandit_baseline_check.py validate-baseline` / `evaluate` | valid (50) / PASS |
| QA self-tests; Bandit self-tests with Bandit 1.9.4 | 256 OK (15 skipped); 34 OK |
| `audit-policy.mjs` on the full npm audit | FAIL: `next` GHSA-mfr8-8qww-v76h only (§4) |
| `python -I scripts/verify_phase0.py` | 865 PASS, 0 FAIL |
| Full backend suite (`pytest -q`, local PostgreSQL) | 1953 passed, 1 failed: `test_server_resilience.py::…[reset-after-connect]`; that file then passed 4/4 on its own. It passed in the three earlier full runs, so it is intermittent; the root cause was not investigated |
| Full SDK suite (`pytest -q -rfs`) | **2724 passed**, 0 failed, 0 skipped. A first run gave 215 failures with `sdk_checkout_match: False`: the non-editable SDK had been installed before the source comments in §2 changed. The suite passed after reinstalling it with `pip install --no-deps --force-reinstall ./sdk` |

## 6. Open items

1. **`next` 16.4.5 (blocks the `frontend` job).** The user chose to adopt it without an exception, once it meets the two-week rule at **2026-11-17T18:01Z** (21:01 Europe/Istanbul). At the time of this report (2026-11-16T23:23Z) it was not yet eligible, so it is not in this change. Then `next`, `eslint-config-next` and `@next/env` move together; run `npm ci`, the unit tests, the build, the bundle check and the audit policy. Until then the `frontend` job keeps failing on GHSA-mfr8-8qww-v76h.
2. **Gemini execution switch:** resolved, re-locked (§7).
3. **R102 deadline 2026-11-30.** `typescript-eslint` 8.73.0 (`minimatch ^11.0.1`) becomes eligible on 2026-11-30T17:06Z. Raising the eight `@typescript-eslint` overrides then would remove R102.

## 7. Gemini execution switch re-locked (user decision, 2026-11-17)

`3472800` shipped `gemini_live.REAL_EXECUTION_ENABLED = True` from the Phase 8F operator-approved local validation, whose own code comment said "Re-lock after validation". The validation is finished, so the switch is back to the ADR-50 shipped state.

| File | Change |
|---|---|
| `sdk/src/llm_insight_sdk/gemini_live.py` | `REAL_EXECUTION_ENABLED: bool = False`; module docstring back to "false as shipped" |
| `sdk/tests/unit/test_gemini_execution_gate.py` | The Phase 8F release edits reversed with `git apply -R --include=… docs/patches/phase-8f-execution-proposed.patch` (applied cleanly): `test_the_switch_ships_false`, the source check for exactly `REAL_EXECUTION_ENABLED: bool = False`, and no `switch_off` fixture |

**Unchanged on purpose:** ADR-50 keeps the evidence and the switch as separate reviewed changes, so these stay:
- `FREE_TIER_VERIFIED_AT` 2026-11-12T13:06:58Z (its 14-day window ends 2026-11-26);
- the 30 s default timeout;
- the Groq switch (`True`, released by Phase 8G patch B) and the OpenRouter switch (`False`).

A later Gemini run therefore needs a new reviewed release of the switch, as ADR-50 requires.

**Checks:**
- SDK reinstalled non-editable (`pip install --no-deps --force-reinstall ./sdk`);
- `ruff check` / `ruff format --check` on the SDK's tracked files clean (70 files);
- `mypy src` (16 files) and `mypy demo_app` (14 files) clean;
- full SDK suite (`pytest -q -rfs`), PostgreSQL stopped: **2612 passed, 0 failed, 112 skipped**. Every skip was a live-backend integration test (`live backend unavailable`);
- PostgreSQL started, then `LLMI_TEST_REQUIRE_DB=1 pytest -q tests/integration`, so a missing backend fails instead of skipping: **153 passed**, including the 112 skipped above;
- together: all 2724 SDK tests pass with the switch re-locked;
- `verify_phase0`: 865 PASS, 0 FAIL.
