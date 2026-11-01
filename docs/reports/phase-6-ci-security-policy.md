# Phase 6 — CI security policy (R102 exception) and release blocker closure

**Date:** 2026-11-01 (UTC). **Branch:** `feature/phase-6c-gemini-free`; not pushed, not merged, no tag.

**Decision applied:** the architectural review accepted the Phase 6 final QA for local scope and approved R102 as a **temporary, dev-only** exception for GHSA-g3hv-75wg-m249 (`minimatch`) in the verified ESLint chain, review deadline 2026-11-30 UTC. Every other vulnerability must keep failing the gate, and the full audit must stay visible.

**No Gemini or OpenAI request was made. No provider key or secret value was read or printed. The developer database and the live trace were not modified.**

## 1. Initial Git state

| Item | Value |
|---|---|
| Git root | `C:/Users/PC/Desktop/LLM_Insight_Claude_Code_MVP_Paketi` (this project, not the home repository) |
| Branch / HEAD | `feature/phase-6c-gemini-free` at `3584085` (Phase 6 final QA), on `5f77c94` → `e109846` → `580d457` → `0190bf4` |
| Working tree | only ` M .env.example` (the user's change, 8 keys; blob `22d7ec4e5aaf`); nothing staged, nothing untracked |
| Tags | `phase-0` … `phase-5`; no `phase-6` |
| Remote | only `origin/main`; the branch is not pushed |

## 2. R102 baseline, re-verified on the current tree

Commands run in `frontend/` (Node 22.23.4, npm 10.9.9 from `.tools/`):

| Command | Exit | Result |
|---|---|---|
| `npm audit --json` | 1 | 16 findings, all `moderate`; `auditReportVersion` 2; 480 dependencies (62 prod, 381 dev) |
| `npm audit --omit=dev` | 0 | `found 0 vulnerabilities` |
| `npm ls minimatch` | 0 | `minimatch@3.1.5` (under `eslint`, `@eslint/config-array`, `@eslint/eslintrc`, `eslint-plugin-import`, `-jsx-a11y`, `-react`) and `minimatch@10.2.6` (under `@typescript-eslint/typescript-estree` via `typescript-eslint`) |

- **One advisory.** All 16 findings resolve to a single advisory: GHSA-g3hv-75wg-m249 / CVE-2026-118764, "minimatch has potential denial of service via globparts amplification" (npm source 1244012, CWE-405, `<11.0.1`, first patched 11.0.1; GitHub advisory published 2026-10-30, not withdrawn).
- **Only `minimatch` carries the advisory.** The other 15 findings are npm's transitive ("meta") entries, whose `via` names another finding instead of an advisory.
- **Dev-only.** All 18 vulnerable nodes are `dev: true` in `package-lock.json` (lockfile v3).
- **No compatible upstream fix (checked against the registry on 2026-11-01):**
  - `minimatch` is now at 11.0.3, but every consumer still requires 3.x or 10.x: `eslint` 10.14.0 `^10.2.6`, `@eslint/config-array` 0.23.5 `^10.2.4`, `@eslint/eslintrc` 3.3.7 `^3.1.5`, `@typescript-eslint/typescript-estree` 8.72.1 `^10.2.2`, `eslint-plugin-import` 2.32.0, `-react` 7.37.5 and `-jsx-a11y` 6.10.2 `^3.1.2`.
  - The latest ESLint 9 is 9.39.5, the installed version.
  - Normal remediation is therefore not possible yet.
- **Nothing was changed in the dependency tree.** No `npm audit fix --force`, no ESLint downgrade, no override.

## 3. Approved exception policy

Recorded in `frontend/security/npm-audit-policy.json` (schema version 1). It contains exactly one exception:

| Field | Value |
|---|---|
| Risk ID | R102 |
| Advisory | GHSA-g3hv-75wg-m249 (`https://github.com/advisories/GHSA-g3hv-75wg-m249`), CVE-2026-118764, npm source 1244012, CWE-405 |
| Package / vulnerable range | `minimatch` `<11.0.1` |
| Installed vulnerable copies | `node_modules/minimatch` 3.1.5; `node_modules/@typescript-eslint/typescript-estree/node_modules/minimatch` 10.2.6 |
| Verified chain | 15 dependent packages with their exact node paths and direct/transitive status. Direct: `eslint`, `eslint-config-next`. Transitive: `@eslint-community/eslint-utils` (2 copies), `@eslint/config-array`, `@eslint/eslintrc`, `@next/eslint-plugin-next`, `@typescript-eslint/{eslint-plugin, parser, type-utils, typescript-estree, utils}`, `eslint-plugin-import`, `-jsx-a11y`, `-react`, `typescript-eslint` |
| Scope / severity | `dev` / `moderate` |
| Justification | ESLint lints the repository's own files with the repository's own config globs and never matches untrusted patterns. Production audit 0, no `minimatch` in production. The only patched line is the new major 11.x, outside every consumer's range, and npm's own "fix" downgrades to `eslint` 4.0.0 / `eslint-config-next` 0.2.4 |
| Approved | 2026-11-01, by the architectural review of `phase-6-final-qa.md` |
| Review deadline | 2026-11-30 (valid through the end of that UTC day; fails from 2026-12-01T00:00:00Z) |
| Required remediation | upstream releases of `eslint`, `eslint-config-next`, `typescript-eslint` and the ESLint plugins on `minimatch` ≥ 11.0.1, then delete the exception. No automatic renewal |
| Blocking conditions | listed in the file and enforced as in §4 |

**This is a policy-based acceptance of one known vulnerability, not a clean `npm audit`.** R102 stays **unresolved**. The full report keeps listing all 16 findings on every CI run, and the gate prints "the full npm audit is NOT clean".

## 4. Policy evaluator

`frontend/scripts/audit-policy.mjs` follows the conventions of `check-bundle.mjs`: Node standard library only, no new dependency, 340 lines.

**Interface:**
- CLI: `node scripts/audit-policy.mjs --report FILE --npm-exit-code N`.
- Exit code: 0 for PASS / CONDITIONAL PASS, 1 for FAIL.
- Fixed for the CLI: the policy path, the lockfile path and the clock. There is no flag to move the date or point at another policy. Tests call the exported `evaluate()` / `run()` instead.

**What it checks (any violation means FAIL):**

1. **Execution and report integrity.**
   - The report must be a JSON object with `auditReportVersion` 2 and no `error`/`message` keys. A real npm registry failure prints `{"message": "request to … failed", "error": {…}}` and **also exits 1**, so the exit status alone never decides.
   - `metadata.vulnerabilities` must have every severity count. The counts must add up, match the entries per severity and match the total. `metadata.dependencies.total` must be greater than 0; otherwise the report is treated as truncated or incomplete.
   - npm's exit status must be 0 with no findings or 1 with findings. Any other combination or value fails.
2. **Advisory resolution.**
   - Each finding's `via` items are either references to other findings, which must exist and must not be the finding itself, or advisory objects. An advisory object needs a GitHub advisory URL, an integer source, `dependency` equal to the finding, a name, a severity and a range.
   - Findings are resolved through the references to their underlying GHSA IDs. Cycles are allowed: `eslint` ↔ `@eslint-community/eslint-utils` reference each other in the real report.
   - A finding that resolves to no advisory is rejected as unknown.
3. **Approval.** Every resolved advisory must be an exception in a valid policy. There is no blanket rule by package name (a second advisory on `minimatch` fails) or by dev status (a new dev-only advisory fails).
4. **No production exposure.** Every node of every finding must exist in `package-lock.json` with `dev: true`. `devOptional`, a missing flag or a missing entry fails.
5. **Exact R102 scope.**
   - The set of findings that reach the advisory must equal `minimatch` plus the 15 recorded dependents. A new package, a gone package, a changed node path or a changed direct/transitive status all fail. Narrowing also needs a review.
   - `minimatch` must carry exactly this advisory, with the approved source, name, severity, range, CWE list and title. Its finding range must match, and it must not become direct.
   - The vulnerable copies must be exactly the two recorded node paths, at the recorded lockfile versions (3.1.5, 10.2.6).
   - Every finding must have severity `moderate`. Escalation and de-escalation both fail.
   - No dependent may carry the advisory itself.
6. **Policy validity.**
   - Unknown or missing keys fail, so there is no hidden switch such as `allowAll`.
   - Field types are checked, the GHSA ID must match the URL and duplicates fail. `scope` must be `dev`: production dependencies cannot take an exception.
   - Dates must be real `YYYY-MM-DD` dates. `approvedOn` must not be in the future, and the review window must be 0–31 days.
   - When the deadline has passed, the gate fails, even on a clean audit.
   - With an invalid policy, no exception applies.

**Deliberately not pinned:**
- npm's `effects` and `fixAvailable` fields. Two real `npm audit --json` runs on the same unchanged tree, an hour apart, differed in exactly these fields (fixtures `full-r102-2026-11-01.json` and `-run2.json`). The first run even claimed `fixAvailable: true` for `eslint-plugin-import`, `eslint-plugin-react` and two `@typescript-eslint` packages although no compatible fix exists. Pinning them would make the gate flaky; trusting `fixAvailable` (proposal §14 D.6) would be wrong. The chain itself is pinned through the `via` graph and the node paths.
- Versions of the dependent packages, so a patch release of `eslint` on the same chain does not need a policy edit. The vulnerable `minimatch` copies **are** version-pinned.
- The advisory's CVSS score: npm currently reports 0 with no vector, while GitHub rates it 6.9 (CVSS 4.0). The severity label is pinned instead.

**Output:**
- Every finding is printed with severity, direct flag, resolved advisories, node paths and either "accepted by policy" or "REJECTED: <reasons>".
- Then come the notices (for example, an exception that no longer appears: "remove the exception"), the errors and one verdict line.
- On GitHub Actions it also emits a `::warning` (conditional pass), `::error` (fail) or `::notice` (clean) annotation.

## 5. CI workflow changes (`.github/workflows/ci.yml`, frontend job only)

| Step | Before | After |
|---|---|---|
| A. Install | `npm ci --no-audit --no-fund` | unchanged |
| B. Production audit | — | `npm audit --omit=dev --audit-level=info` (mandatory, no exceptions) |
| C. Full audit + policy | `npm audit` (failed on R102, skipped D–H) | `npm run test:audit-policy` (self-tests), then `npm audit --json` to `$RUNNER_TEMP`, the complete JSON printed in a log group, then `node scripts/audit-policy.mjs --report … --npm-exit-code "$status"` |
| D–H | typecheck, lint, `npm test`, build, `check:bundle` | unchanged, still after B and C, unconditional |

- **`--audit-level=info` is required.** npm's default audit level is `low` (`npm-audit-report/lib/index.js:21`: `options.auditLevel || 'low'`). A plain `npm audit --omit=dev` would therefore exit 0 on an `info` advisory. With `info`, any production advisory of any severity fails. (Proposal §14 A had suggested `low`; corrected here.)
- **`status=0; npm audit --json > … || status=$?` captures the exit status rather than hiding it.** The evaluator accepts only a 0 or 1 that agrees with the report. Any other exit status, malformed or empty output, or an npm error object fails the step. There is no `continue-on-error`, no `|| true`, and no step is disabled.
- **An accepted exception changes nothing for D–H.** They run only after B and C pass, exactly as before, and a failure in B or C still fails the job.
- **Evidence:** the full report and the per-finding classification are in the job log. No artifact action was added, so there is no new third-party action to pin under CR-09.
- **Scope of the edit:**
  - The header comment gained a note on the policy.
  - The `docs` and `python` jobs, the triggers, the permissions and the frontend job settings are byte-equal after YAML parsing; the only removed step is `run: npm audit`.
  - Two scripts were added to `frontend/package.json`: `test:audit-policy` and `audit:policy`.

## 6. Production audit result

`npm audit --omit=dev --audit-level=info` exit 0, `found 0 vulnerabilities`, both on the working tree and on the isolated committed-tree export (§10). **PASS.**

## 7. Full audit result and the accepted warning

The CI step C was emulated exactly (Git Bash, `bash -e`, the same four commands, a local `RUNNER_TEMP`):
- `npm audit --json` exit status 1, 16 findings.
- Evaluator exit 0 and this verdict line:

```
CONDITIONAL PASS: the full npm audit is NOT clean; all 16 finding(s) are accepted dev-only exceptions: R102 GHSA-g3hv-75wg-m249 (review by 2026-11-30 UTC).
```

All 16 findings print "accepted by policy" and resolve to GHSA-g3hv-75wg-m249. **The full audit is not clean.** It reports R102 as unresolved, and the gate passes only by policy. Two real runs were classified the same way.

## 8. Fail-closed policy tests

`npm run test:audit-policy` runs `frontend/tests/scripts/audit-policy.test.mjs` on Node's built-in test runner, offline. Inputs:
- three reports captured from real npm runs on 2026-11-01: full, full run 2, and the registry error;
- the clean production report;
- synthetic mutations of these.

**Result: 59 tests, 17 suites, 59 passed, 0 failed, 0 skipped.**

| Required case | Covered by (suite) | Verdict asserted |
|---|---|---|
| 1 No vulnerability | 1 (also: unused-exception notice) | PASS |
| 2 Only approved R102 dev-only findings | 2 | CONDITIONAL PASS, "NOT clean" line |
| 3 New unapproved dev-only advisory | 3 (new package; second advisory on `minimatch`; new advisory on `eslint`) | FAIL |
| 4 Production vulnerability | 4 (non-dev / `devOptional` / missing lock node; prod advisory; `scope: prod`; no lockfile) | FAIL |
| 5 Unknown advisory | 5 (no or non-GHSA URL; reference cycle without advisory) | FAIL |
| 6 Expired exception | 6 (valid at 2026-11-30T23:59:59Z; fails at 2026-12-01T00:00:00Z, also on a clean audit) | FAIL |
| 7 Malformed audit JSON | 7 (invalid JSON file, non-object, CLI exit 1) | FAIL |
| 8 Registry/network error | 8 (real ECONNREFUSED output, npm error object, contradicting or invalid exit status, missing `--npm-exit-code`, GitHub `::error` annotation) | FAIL |
| 9 Wrong dependency chain | 9 (moved copy, new dependent, direct flag flips, dependent carrying the advisory) | FAIL |
| 10 Changed R102 scope | 10 (range, installed version, narrowed chain, CWE, source, title) | FAIL |
| 11 Changed or escalated severity | 11 (high, critical, low; one dependent escalated) | FAIL |
| 12 Missing exception configuration | 12 (no policy, unreadable file, each required field removed, unknown keys, invalid dates and windows, inconsistent ID, duplicates, malformed chain) | FAIL |
| 13 Malformed/transitive mappings | 13 (dangling, self and non-string references, empty `via`, wrong `dependency`, missing fields, name mismatch; cycle resolution) | FAIL |
| 14 Empty or incomplete report | 14 (empty file, `{}`, missing metadata or counts, zero dependencies, truncated entries) | FAIL |
| 15 Approved + unapproved mixed | 15 (only the new finding is rejected; the 16 stay listed) | FAIL |
| 16 Current real report | 16 (real report + real lockfile + real policy; policy equals the report's chain; run-2 variance; CLI exit 0 before the deadline, 1 after) | CONDITIONAL PASS |

- **Input mutation sweep:** every field of the approved advisory (8 mutations), every one of the 16 findings × {severity, extra node, direct flag, removal} (64), and the dev flag of each of the 18 vulnerable nodes. All fail.
- **Code mutation check:** 25 mutants of the evaluator, run outside the suite and restored byte-for-byte afterwards (SHA-256 checked). Each mutant disables one safety check: dev check, expiry, expiry off by one day, approval lookup, npm error detection, exit-status check, unknown-advisory check, chain set, both severity checks, range, copies, installed version, direct flag, chain nodes, dangling references, metadata count, unknown keys, `scope: prod`, unlimited window, use of an invalid policy, optional lockfile, source, CWE, and a CLI that always exits 0. **25 of 25 were caught.**

## 9. Full regression

Working tree = HEAD + this change + the user's `.env.example`; synthetic data only.

| Gate | Command | Result |
|---|---|---|
| Policy tests | `npm run test:audit-policy` | **PASS** 59 / 0 / 0 |
| Production audit | `npm audit --omit=dev --audit-level=info` | **PASS** exit 0, 0 vulnerabilities |
| Full audit + policy | CI step C (§7) | **CONDITIONAL PASS** (R102 only; plain `npm audit` still exit 1, 16 moderate) |
| Backend tests | `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` (disposable `*_test` databases) | **PASS** 1765 passed, 0 failed, 0 skipped (216.4 s; 1 warning, R20) |
| SDK tests | same (sdk) | **PASS** 817 passed, 0 failed, 0 skipped (264.4 s) |
| Frontend unit | `npm test` | **PASS** 349 / 0 / 0 |
| Playwright E2E | `playwright test` (Edge), backend `:8020` on the disposable `llminsight_e2e_qa_test`, prices seeded | **PASS** 62 passed, 0 failed, 0 skipped (2.6 min); teardown `remaining: []`; database dropped afterwards. A first attempt started no test: the web server found the system Node 22.11.0 because of a malformed `PATH` entry in the QA shell (environment error, no test ran); re-run with the pinned Node 22.23.4 |
| TypeScript strict | `npm run typecheck` | **PASS** |
| ESLint | `npm run lint` (`--max-warnings 0`; covers the new `.mjs` files, checked with a probe file that failed as expected) | **PASS** |
| Build | `npm run build` | **PASS** |
| Bundle secret scan (T42) | `npm run check:bundle` | **PASS** 632 files, 23 in the browser bundle, 0 findings |
| Ruff + format | backend, dev scripts, sdk (incl. `demo_app`) | **PASS** |
| Strict mypy | `mypy src`, `mypy --config-file pyproject.toml ../demo_app` | **PASS** |
| `pip check` | backend, sdk | **PASS** |
| Alembic | `alembic current` / `alembic check` | **PASS** `0005 (head)` / no new operations |
| Compose | `docker compose config --quiet`, `--profile app` | **PASS** |
| `pip-audit` lockfiles | both locks × PyPI, OSV (`--no-deps --disable-pip`) | **PASS** 4 × exit 0 |
| Installed packages | `pip-audit --path <venv>/Lib/site-packages` | **WARN** exit 1: only `pip` 24.2, 12 advisories per venv (R75, accepted; unchanged) |
| Doc verifier | `python -I scripts/verify_phase0.py --quiet` | Working tree: **FAIL** 797 PASS / 1 FAIL, only the user's `.env.example` (unchanged cause, §12). Committed tree (index export, §10): **PASS** 797 / 0 |
| Bandit 1.9.4 | `bandit -r backend/app sdk/src demo_app scripts` | **WARN** exit 1: High 1 / Medium 5 / Low 38, `nosec` 0. The same six fingerprints as the final QA, all classified, none exploitable (`phase-6-final-qa.md` §9). The new code is JavaScript, outside Bandit's scope |
| History secret scan | `python -I scripts/qa/secret_scan.py --repo . --env .env --env frontend/.env.local` | **PASS (values) / WARN (patterns)** exit 1: 957 blobs, 36 messages, 506 files, 9 local values: **0 value hits**; 40 `url_userinfo` hits, all at the known Phase 5B fixture locations, none in the new files |
| Whitespace | `git diff --check` | **PASS** |

## 10. Local CI-equivalent run on the committed tree

**Tree.** `git checkout-index -a` of the staged change, i.e. exactly the content of this phase's commit, exported to `%TEMP%\llmi6ci` (507 files).
- `.env.example` in the export is the committed fail-closed template (blob `71789f6dd7e3`, equal to `HEAD`), not the user's edit.
- Throwaway `.env` and `frontend/.env.local` were generated there with `init_env.py` / `init_frontend_env.py --project-id 1`, as in CI. Node 22.23.4 / npm 10.9.9.
- The `frontend` job's steps ran in their CI order, with step C run as its literal commands under `bash -e`:

| CI step | Command | Result |
|---|---|---|
| docs job | `python -I scripts/verify_phase0.py --quiet` | **PASS** 797 / 0 |
| A | `npm ci --no-audit --no-fund` | **PASS** (406 packages; the known `eslint@9.39.5` deprecation warning) |
| B | `npm audit --omit=dev --audit-level=info` | **PASS** `found 0 vulnerabilities` |
| C | `npm run test:audit-policy` | **PASS** 59 / 0 / 0 |
| C | `npm audit --json` → evaluator | npm exit status 1 (16 findings); evaluator exit 0: **CONDITIONAL PASS**, 16 × "accepted by policy", "the full npm audit is NOT clean" |
| D | `npm run typecheck` | **PASS** |
| E | `npm run lint` | **PASS** |
| F | `npm test` | **PASS** 349 / 0 / 0 |
| G | `npm run build` | **PASS** |
| H | `npm run check:bundle` | **PASS** 444 files, 23 in the browser bundle, 0 findings |

**Verdict: the local CI-equivalent `frontend` and `docs` jobs pass on the committed tree.** The `python` job's commands passed on the working tree (§9); Python code is unchanged since the final QA clean clone. The export was deleted afterwards.

## 11. Outstanding accepted risks

- **R102:** accepted as a temporary dev-only exception and unresolved until upstream moves to `minimatch` ≥ 11.0.1. The gate fails by itself from 2026-12-01T00:00:00Z. That needs a new review, not an automatic renewal.
- **R104:** the npm part is resolved. `pip-audit`, Bandit, the history secret scan and the Playwright E2E remain local gates, not CI gates (open review item).
- **`eslint@9.39.5` deprecation:** a separate future upgrade, which does not by itself fix R102 (ESLint 10.14.0 is also affected).
- **R75** (`pip` 24.2 toolchain) and the other accepted local-only risks are unchanged. None of them is accepted for public deployment.

## 12. `.env.example`

- **Not edited, restored, staged, committed or deleted.**
  - Its blob hash `22d7ec4e5aaf` is the same before and after this phase.
  - It is excluded from the commit; the release-verification tree in §10 is built from the git index, so it carries the committed template instead.
- **Proposed correction (not applied):** restore the committed fail-closed template, equivalent to `git restore .env.example`. As recorded in `phase-6c-c-security-closure.md` §4, keep per-run live settings in a git-ignored local file exported only in the operator's shell.
- The edited file has two problems:
  - It still fails the doc verifier on the working tree, because the `LLMI_LIVE_MAX_*_TOKENS` keys are not placeholders.
  - It sets `LLMI_LIVE_PROVIDERS` to a value that is not a provider ID.
- Both guards evaluated it offline as fail-closed.

## 13. Why Linux CI remains unverified

- The branch is not pushed, so no GitHub Actions run exists for it.
- Everything above ran on Windows 11 with the repository's pinned Node and Python. The Ubuntu 24.04 runner is not yet verified for:
  - the shell semantics of step C (`bash -e`, `$RUNNER_TEMP`);
  - the registry response seen from the runner;
  - the `python` job.
- These results are **unknown, not passed**.

## 14. Remaining Phase 6 release blockers

1. **Remote Linux CI:** a push and a green run of all three jobs on the release commit (requires approval; not done).
2. **Architectural review** of this policy implementation.
3. **R102 deadline:** the exception must be re-reviewed or removed by 2026-11-30 UTC.

Resolved by this phase: the local CI-equivalent `frontend` job is no longer expected to fail on R102. It now fails only on unapproved or changed findings.

## 15. Files changed

- `.github/workflows/ci.yml`: frontend audit steps B and C, header note.
- `frontend/package.json`: scripts `test:audit-policy`, `audit:policy`.
- `frontend/security/npm-audit-policy.json`: the R102 exception.
- `frontend/scripts/audit-policy.mjs`: the evaluator.
- `frontend/tests/scripts/audit-policy.test.mjs`: 59 tests.
- `frontend/tests/fixtures/npm-audit/`: `full-r102-2026-11-01.json`, `full-r102-2026-11-01-run2.json`, `omit-dev-clean-2026-11-01.json`, `registry-error-econnrefused.json`, all real npm output.
- `docs/reports/phase-6-ci-security-policy.md` (this report).
- `docs/SOURCES_AND_RISKS.md`: R102 and R104 updated.
- `docs/PHASE_STATUS.md`: Phase 6 CI security policy entry.
