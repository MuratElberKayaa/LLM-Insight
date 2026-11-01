# Phase 6C-C — Security exception closure and final regression verification

**Date:** 2026-11-01 (UTC). **Branch:** `feature/phase-6c-gemini-free`, from `e109846` (Phase 6C-C); not pushed, not merged, no tag. **Review decisions applied:** ADR-33…38 accepted at the design level; R101 security exception approved (`pyasn1` 0.6.5 before the two-week period); R102 accepted conditionally for local-only development if dev-only is confirmed; no `npm audit fix --force`; the user's `.env.example` preserved. **No Gemini or OpenAI request was sent, no provider key was read, the live trace was only read.**

## 1. Initial state

| Item | Found |
|---|---|
| Git root / branch / HEAD | `C:/Users/PC/Desktop/LLM_Insight_Claude_Code_MVP_Paketi` / `feature/phase-6c-gemini-free` / `e109846fd39e137afc938217a3717019599efc53` |
| Working tree | ` M .env.example` (user-owned, live-run settings, no key); nothing staged, nothing untracked |
| Tags | `phase-0` … `phase-5` (unchanged) |
| Runtime | `llminsight-postgres-1` healthy; the user's backend on `127.0.0.1:8010` ready; unrelated `ppe-split-*` containers running (port 8000). None was stopped or changed; this session's backend for E2E ran on `127.0.0.1:8020` and was stopped afterwards |

## 2. R101 — `pyasn1` (resolved)

**Where:** only `sdk/requirements-dev.lock` and `sdk/.venv` (`google-genai` → `google-auth` 2.62.0 → `pyasn1_modules` 0.4.2 → `pyasn1`). The backend lock and venv do not contain it; CI installs the SDK lock as is (no workflow change); no Docker image includes the SDK.

**Advisories (OSV / GitHub, published 2026-10-30; all fixed in 0.6.5, severity High, CVSS 3.1 7.5 `AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H`):**

| Advisory | CVE | Affected | Issue |
|---|---|---|---|
| GHSA-2qm8-x737-gmw5 | CVE-2026-119196 | < 0.6.5 | quadratic CPU in constructed BIT STRING decoding |
| GHSA-9m75-vc3g-4x39 | CVE-2026-119195 | < 0.6.5 | quadratic time in constructed string and indefinite-length ANY decoding |
| GHSA-fm9c-5rcf-2q5p | CVE-2026-119194 | 0.3.3 – < 0.6.5 | quadratic CPU in schemaless BER/CER/DER container decoding |
| GHSA-mm57-4v3p-rchx | CVE-2026-119197 | < 0.6.5 | quadratic complexity in binary REAL decoding |

The Phase 6C-C report rated R101 "Low": that was the **contextual** exposure (the Gemini runner authenticates with an API key and refuses Google credentials and Vertex AI, ADR-34, so no attacker-supplied ASN.1 is parsed), not the advisory severity, which is High. The risk register now states both.

**Patched release evidence (PyPI JSON API, 2026-11-01):** `pyasn1` 0.6.5, not yanked, latest release, `requires_python >=3.8`; `pyasn1-0.6.5-py3-none-any.whl` uploaded 2026-10-27T06:32:48Z, SHA-256 `4017b0fce125096b8573b39c989fdb1a4b19cf761e683398184550fea0e66587`; sdist SHA-256 `6b11616a6bcf3a40e635f082bb232fa57b5e0dd7f517bd7ddd69ec3794508a47`. The downloaded wheel's SHA-256 equals PyPI's. OSV lists no vulnerability for 0.6.5. **Static diff against 0.6.4** (both wheels read as zip data, nothing executed): same file list; changes only in the version string, `WHEEL`/`METADATA` and `pyasn1/codec/ber/decoder.py` (+74 / −48): the BIT STRING / OCTET STRING fragment assembly now appends to one `bytearray` instead of re-prepending the whole value per fragment (the quadratic pattern of the advisories); no new import, no network, subprocess or `eval`. **Compatibility:** `pyasn1_modules` 0.4.2 requires `pyasn1>=0.6.1,<0.7.0`; `google-auth` 2.62.0 requires `pyasn1-modules>=0.2.1`; pure-Python wheel.

**Security exception (two-week rule, CR-09):** approved by the architectural review on 2026-11-01. 0.6.5 was uploaded 2026-10-27 and would satisfy the rule only from 2026-11-10. Rationale: four published High DoS advisories with a fixed patch release whose diff is confined to the vulnerable decoder; the same procedure as R74 (`certifi`, Phase 5B). Recorded in the lock header, `ARCHITECTURE.md` §6 and `SOURCES_AND_RISKS.md` R101.

**Change:** `sdk/requirements-dev.lock`: `pyasn1==0.6.4` → `pyasn1==0.6.5` plus a three-line dated header note. Nothing else was upgraded (`google-genai`, OpenTelemetry, Python and every other pin unchanged). **Install** (project workflow, hash-checked): `sdk/.venv/Scripts/python -m pip install --no-deps --require-hashes --no-index --find-links <verified wheel dir> -r <file with pyasn1==0.6.5 --hash=sha256:4017b0fc…>` → "Successfully installed pyasn1-0.6.5" (0.6.4 uninstalled). Effective version: `importlib.metadata.version("pyasn1") == pyasn1.__version__ == "0.6.5"`. `pip freeze` equals the lock (60 pins); `pip check`: no broken requirements.

| Verification | Result |
|---|---|
| `pip-audit -r sdk/requirements-dev.lock --no-deps --disable-pip -s pypi` / `-s osv` | **PASS** — no known vulnerabilities (exit 0 / 0); before: 4 `pyasn1` findings, exit 1 |
| `pip-audit -r backend/requirements.lock --no-deps --disable-pip -s pypi` / `-s osv` | **PASS** — no known vulnerabilities (exit 0 / 0) |
| `pip-audit --path sdk/.venv/Lib/site-packages -s pypi\|osv` | **WARN** — `pyasn1` absent; only `pip` 24.2 (12 advisories, R75, accepted in Phase 5) |
| `pip-audit --path backend/.venv/Lib/site-packages -s pypi\|osv` | **WARN** — only `pip` 24.2 (R75, accepted) |
| Gemini adapter, endpoint, guard and runner tests (`tests/unit/test_gemini_*.py`) | **PASS** — 219 passed |
| OpenAI adapter, Responses, live guard/endpoint/runner tests (`test_openai*.py`, `test_live_*.py`) | **PASS** — 292 passed |
| SDK integration tests (live backend, network blocked) | **PASS** — 50 passed |
| Full SDK suite | **PASS** — 817 passed, 0 failed, 0 skipped |

**R101 verdict: RESOLVED** — the fixed version is pinned and installed, and the lockfile scans pass on both vulnerability services; the remaining installed-package finding is the separately accepted R75.

## 3. R102 — frontend `npm audit` (accepted conditionally, dev-only confirmed)

`npm audit`: exit 1, 16 moderate, 0 high/critical; `npm audit --omit=dev`: exit 0, **0 vulnerabilities**; `npm ls minimatch --omit=dev`: empty. The 16 findings are **one advisory** and its dependents, not 16 root causes:

| Field | Value |
|---|---|
| Advisory | GHSA-g3hv-75wg-m249 / CVE-2026-118764 (published 2026-10-30), "minimatch has potential denial of service via globparts amplification": input-controlled growth of glob parts in `firstPhasePreProcess()` when `optimizationLevel >= 2`, then quadratic processing |
| Severity | moderate (CVSS 4.0 6.9, `AV:N/AC:L/…/VA:L`) |
| Vulnerable | `minimatch` < 11.0.1; installed 3.1.5 and 10.2.6 |
| Chains (all transitive, all dev) | `eslint@9.39.5` → `minimatch@3.1.5` (also via `@eslint/config-array@0.21.2`, `@eslint/eslintrc@3.3.7`); `eslint-config-next@16.4.3` → `eslint-plugin-import@2.32.0` / `eslint-plugin-jsx-a11y@6.10.2` / `eslint-plugin-react@7.37.5` → `minimatch@3.1.5`; `eslint-config-next` → `typescript-eslint@8.71.1` → `@typescript-eslint/typescript-estree@8.71.1` → `minimatch@10.2.6`. Direct dependencies involved: `eslint`, `eslint-config-next` (both devDependencies) |
| The other 14 entries | `@eslint-community/eslint-utils`, `@eslint/config-array`, `@eslint/eslintrc`, `@next/eslint-plugin-next`, `@typescript-eslint/{eslint-plugin,parser,type-utils,typescript-estree,utils}`, `eslint-plugin-{import,jsx-a11y,react}`, `typescript-eslint` and the two direct ones: flagged only because they depend on a vulnerable `minimatch` |
| Patched version | 11.0.1 (2026-10-27); latest 11.0.3 (2026-10-28). No 3.1.x or 10.2.x release after the advisory |
| Runtime exposure | **None found**: not in the production dependency tree, no `minimatch` code in `.next/static` or `.next/server`; ESLint matches the repository's own configuration globs, never patterns from users or telemetry |

**Compatible fix: none.** npm's suggested fixes are semver-major downgrades (`eslint@4.0.0`, `eslint-config-next@0.2.4`). Forcing `minimatch` 11 through `overrides` would violate the declared ranges (`eslint` → `^3.1.5`, `typescript-estree` → `^10.2.2`), cross two major versions of a changed API (3.x CommonJS default export), add a new package (`@isaacs/nonrecursive-glob-match`) and break the two-week rule; it would be an unverified override. **Nothing was changed:** no `npm audit fix`, no override, no ESLint change, no rule disabled, auditing not weakened. **Consequence for the review:** CI runs a plain `npm audit` (`.github/workflows/ci.yml:127`), so the frontend CI job fails until `eslint`, `eslint-config-next` and `typescript-eslint` release on `minimatch` ≥ 11.0.1; that step was deliberately left as it is.

**R102 verdict: ACCEPTED CONDITIONALLY** (local-only development), the review's condition (dev tooling only) verified; open until upstream releases a compatible fix.

## 4. `.env.example` review (proposal only; the file was not modified, staged or committed)

Inspected by key name and non-secret value only. The user's change sets eight lines that the committed template leaves empty: `LLMI_LIVE_CONSENT="gemini-free-tier-single-call"`, `LLMI_LIVE_PROVIDERS="real"`, `LLMI_LIVE_MAX_REQUESTS="1"`, `LLMI_LIVE_MAX_INPUT_TOKENS="256"`, `LLMI_LIVE_MAX_OUTPUT_TOKENS="256"`, `LLMI_GEMINI_MODE="free-tier"`, `LLMI_GEMINI_BILLING_TIER="free"`, `LLMI_GEMINI_TIER_CHECKED_ON="2026-10-28"`. Secret-bearing keys are untouched by it (`GEMINI_API_KEY` and `OPENAI_API_KEY` empty; the others are the template's committed placeholders).

- **`LLMI_LIVE_PROVIDERS="real"` is invalid:** the Gemini guard accepts only `gcp.gemini` and the OpenAI guard only `openai` (`provider_not_allowed`).
- **No accidental live execution:** neither runner loads `.env` or `.env.example` (they read the process environment); `scripts/dev/init_env.py` copies the template into a new `.env` once. Evaluating both guards offline on the edited template's values (pure functions, no client, nothing sent): Gemini refused (`api_key_missing`, `provider_mode_not_real`, `provider_not_allowed`, `setting_missing`), OpenAI refused (five reasons). Even with real mode, a model and a placeholder key added, `provider_not_allowed` still refuses; with `gcp.gemini` as well, today's date refuses `free_tier_attestation_stale` (attestation of 2026-10-28).
- **Why not "correct" it to `gcp.gemini`:** in a committed template that would remove the one remaining closed gate and pre-arm six others; the template must stay fail-closed (Phase 6C-A) and operator values belong in the operator's own shell (`reports/phase-6c-a.md` §10, which already sets `LLMI_LIVE_PROVIDERS='gcp.gemini'` there). The verifier also fails on the edited copy (`LLMI_LIVE_MAX_*_TOKENS` are not placeholders).

**Proposed correction (for the user to apply or not): restore the committed fail-closed values.**

```diff
-LLMI_LIVE_CONSENT="gemini-free-tier-single-call"
+LLMI_LIVE_CONSENT=
-LLMI_LIVE_PROVIDERS="real"
+LLMI_LIVE_PROVIDERS=
-LLMI_LIVE_MAX_REQUESTS="1"
-LLMI_LIVE_MAX_INPUT_TOKENS="256"
-LLMI_LIVE_MAX_OUTPUT_TOKENS="256"
+LLMI_LIVE_MAX_REQUESTS=
+LLMI_LIVE_MAX_INPUT_TOKENS=
+LLMI_LIVE_MAX_OUTPUT_TOKENS=
-LLMI_GEMINI_MODE="free-tier"
+LLMI_GEMINI_MODE=
-LLMI_GEMINI_BILLING_TIER="free"
-LLMI_GEMINI_TIER_CHECKED_ON="2026-10-28"
+LLMI_GEMINI_BILLING_TIER=
+LLMI_GEMINI_TIER_CHECKED_ON=
```

(Equivalent to `git restore .env.example`; if a per-run file is wanted, a git-ignored local file exported only in the operator's shell, with `LLMI_LIVE_PROVIDERS=gcp.gemini`.)

## 5. Live Gemini trace (read-only)

`GET` through the user's backend on `127.0.0.1:8010`: project `gemini-live` id 148; trace `bec4f18a3f9a29375a61290dfc62798d` with 2 spans (root `demo.live-gemini` INTERNAL → child `generate_content gemini-3.1-flash-lite` CLIENT, correct parent), provider `gcp.gemini`, requested and response model `gemini-3.1-flash-lite`, input 18 / output 1 (`provider_reported`), trace status `ok`, `cost_status = unknown` / `price_missing` / `cost_usd = null`, provenance `real` with `provenance_verified = false`. Identical to the Phase 6C-C read-back; nothing deleted, regenerated or altered.

## 6. Dashboard safety (Phase 6C-C behaviour re-verified)

| Property | Evidence (this run) |
|---|---|
| Optional `provider` filter; provider + model on one row; validation; project isolation | backend `test_phase6c_provider_filter.py` (in the 1765), plan variants; E2E provider/model test |
| Provenance filtering (Real (asserted) / Synthetic / Unknown, downgraded claim) | E2E provenance test; unit label tests |
| Auto-refresh off by default; intervals 30 s (first choice, `DEFAULT_INTERVAL_S`) / 1 min / 5 min; 15 s floor | unit (`MIN_INTERVAL_MS`, `backoffMs`), E2E (no request in 5 min while off; nothing at 29 s, one at 31 s). There is no single "on" switch: enabling means choosing an interval, 30 s being the first |
| Hidden-tab suspension, unmount cleanup, one timer | unit controller tests (fake clock) |
| 429 Retry-After cooldown for timer and button | unit; E2E (button "Retry in N s", no request for 5 min) |
| Failure backoff; stop on non-recoverable errors | unit; E2E (60 s then 120 s; wrong token stops after one attempt) |
| Same-view stale data, never cross-project | unit `showsStale`; E2E stale after 429; project switch test |
| Latest stored trace summary | unit; E2E Overview |
| No secret in the client bundle | `check:bundle` 0 findings; E2E T42 tests; static test that no client component imports `describeFailure` |

**Request amplification vs T43:** one render reads `/api/v1` 5 times on Overview and 3 times on Models and Traces (plus the unauthenticated health probe). At 30 s an Overview tab makes ≈ 0.17 req/s, about 1.7 % of the default refill (10 req/s, burst 20); every tab shares the bucket of 127.0.0.1, so roughly 60 Overview tabs at 30 s would reach the refill rate. Manual refresh is disabled while a render runs, a 429 pauses timer and button for its `Retry-After`, failures back off. The R99 text, which said "well under 1 %", was corrected. No WebSocket, SSE or provider integration exists or was added.

## 7. Quality gates (exact commands, this run)

| Gate | Result |
|---|---|
| `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` (backend, disposable `*_test` PostgreSQL databases) | **PASS** — 1765 passed, 0 failed, 0 skipped (286.8 s; 1 warning, R20) |
| same (sdk, `pyasn1` 0.6.5) | **PASS** — 817 passed, 0 failed, 0 skipped (285.8 s) |
| `npm test` | **PASS** — 349 passed, 0 failed, 0 skipped |
| `npx playwright test` (backend `127.0.0.1:8020`, run-owned projects) | **PASS** — 62 passed, 0 failed, 0 skipped (2.3 min); teardown `remaining: []` |
| `npm run typecheck` / `npm run lint` | **PASS** — exit 0 / 0 warnings |
| `npm run build` / `npm run check:bundle` | **PASS** — built / 632 files, 23 in the browser bundle, 0 findings |
| Ruff check + format (backend 141 files; sdk + demo_app 50 files) | **PASS** |
| `mypy src`, `mypy --config-file pyproject.toml ../demo_app` (strict) | **PASS** — 12 / 6 files, no issues |
| `pip check` (backend, sdk) | **PASS** |
| `alembic current` / `alembic check` | **PASS** — `0005 (head)` / no new upgrade operations |
| `docker compose config --quiet`, `--profile app` | **PASS** — exit 0 / 0 |
| `python -I scripts/verify_phase0.py` | committed tree: see §9; working tree: 1 FAIL from the user's `.env.example` only |
| `pip-audit` lockfiles (backend, sdk; PyPI and OSV) | **PASS** — 0 known vulnerabilities (4/4) |
| `pip-audit --path` installed packages (both venvs) | **WARN** — `pip` 24.2 only (R75, accepted) |
| `npm audit` | **WARN** — exit 1: 16 moderate = GHSA-g3hv-75wg-m249, dev-only (R102, accepted conditionally) |
| `npm audit --omit=dev` | **PASS** — 0 vulnerabilities |
| `bandit -r backend/app sdk/src demo_app scripts` (1.9.4) | **WARN** — High 1 (B202 `install_node.py`, Phase 5B F5 false positive), Medium 5 (B310 ×4 fixed loopback/nodejs.org URLs: three Phase 5B F6, plus `demo_app/live_common.py`'s readiness probe accepted in Phase 6A; B608 `benchmark.py` constant table names, Phase 5B F7); none new, none in a changed file |
| `python -I scripts/qa/secret_scan.py --repo . --env .env --env frontend/.env.local` | see §9 |
| `git diff --check` | see §9 |

Mandatory regressions all pass; the WARN rows are previously accepted findings, not new failures.

## 8. Files modified

`sdk/requirements-dev.lock` (pin + header note), `docs/ARCHITECTURE.md` (§6 note), `docs/SOURCES_AND_RISKS.md` (R101 resolved with the advisory severity corrected, R102 analysis and conditional acceptance, R99 read-budget figure corrected), `docs/PHASE_STATUS.md`, this report. `sdk/.venv` was updated locally (not versioned). `.env.example` untouched.

## 9. Commit-time checks

Results of the doc verifier on the staged tree, the secret scan and `git diff --check` are in the completion message for the commit (a report cannot contain its own commit id).

## 10. Accepted limitations and readiness

- Accepted: R75 (`pip` 24.2 in the venvs), R102 (dev-only `minimatch`, CI `npm audit` step red until upstream), R97–R100 (stale display, cooldown not across reloads, read budget, top-200 suggestions), R16/ADR-21 (provenance asserted), ADR-35 (Gemini cost unknown).
- The user's `.env.example` remains modified and outside every commit (§4 proposal).
- Linux CI not run (not pushed).
- **Readiness:** the dependency exception is closed and every mandatory regression passes; Phase 6C-C is ready for closure by the architectural review, for local use only. It is not production-ready (no authentication, local-only), provenance is not verified and Gemini cost is not a billed amount.
