# Phase 6 — Final quality assurance, security review and CI release readiness

**Date:** 2026-11-01 (UTC). **Branch:** `feature/phase-6c-gemini-free` at `5f77c94` (audited tree); not pushed, not merged, no `phase-6` tag. **Scope:** audit and verification only. No product code, test, lockfile, CI workflow or contract was changed. **No Gemini or OpenAI request was sent and no provider key was read.** The live trace was only read. The user's `.env.example` change was preserved and left out of every commit.

**Verdict: BLOCKED — release gates unresolved** (§15). Every local gate except the full `npm audit` passes, on the working tree and on a clean clone. The existing mandatory CI frontend job is expected to fail at `npm audit` (R102), and the remote Linux CI has not run.

## 1. Git baseline

| Item | Found (inspected, not assumed) |
|---|---|
| Git root | `C:/Users/PC/Desktop/LLM_Insight_Claude_Code_MVP_Paketi` (own repository; the home-directory repository was not touched) |
| Branch / HEAD | `feature/phase-6c-gemini-free` / `5f77c9485a7f44d4a623c73ff76a2bd15a661c78` (= the known security-closure commit) |
| Commits ahead of `main` | 6: `b3e5b07` 6A, `5b51b57` 6B preflight, `0190bf4` 6B, `580d457` 6C-A, `e109846` 6C-C, `5f77c94` security closure |
| `main` / `origin/main` | both `1a57d65` (Phase 5 release); no remote-tracking branch for the Phase 6 branches (no fetch was made) |
| Tags | `phase-0` … `phase-5`; no `phase-6` |
| Working tree | ` M .env.example` only: the user's change, eight live-run values, no key (unchanged since `reports/phase-6c-c-security-closure.md` §4). Nothing staged, nothing untracked, no stash |
| Runtime found | `llminsight-postgres-1` healthy on `127.0.0.1:55432`; the user's backend on `127.0.0.1:8010` (PID started 2026-10-28 20:43, see §5); unrelated `ppe-split-*` containers (`127.0.0.1:8000`). None of them was stopped or changed |

## 2. Architectural acceptance (ADR-28 … ADR-38)

Each control was checked in code and by its named tests, which are all part of the passing suites in §6.

| ADR | Control verified in code | Tests (passing) |
|---|---|---|
| 28 Responses API on `attrmap.v1` | `ObservedOpenAIClient.create_response`; `store=False` forced; one attempt, retry only an unambiguous 429 | `test_openai_responses.py`, `test_live_responses.py` |
| 29 Fail-closed guard | `evaluate_policy`: consent `openai-paid-calls`, allowlists, Decimal reservation | `test_live_guard.py`, `test_live_runner.py` |
| 30 / 36 Runners | `demo_app.live_openai` / `demo_app.live_gemini`: dry run unless `--execute`; no browser surface | `test_live_runner.py`, `test_gemini_runner.py` |
| 31 Hardening | `RESPONSES_ALLOWED` allowlist; `client_env_override` | `test_live_guard.py`, `test_openai_responses.py` |
| 32 OpenAI endpoint integrity | `live_client_problems`; `DefaultHttpxClient(trust_env=False, follow_redirects=False)` | `test_live_endpoint.py` |
| 33 Gemini adapter | `ObservedGeminiClient.generate_content`; `automatic_continuation=False`; usage rules; outcomes | `test_gemini_adapter.py`, `test_live_gemini.py` |
| 34 Gemini endpoint integrity | `live_gemini_client_problems`; pinned `https://generativelanguage.googleapis.com/` `v1beta`; `follow_redirects=False`; checked again before the send | `test_gemini_endpoint.py` |
| 35 Free Tier guard / unknown cost | `evaluate_free_tier_policy` (consent, attestation ≤ 1 day, `free_tier_attestation_stale`); backend: no stored price applies to `gcp.gemini` | `test_gemini_live_guard.py`, backend `test_pricing.py` |
| 37 `provider` filter | `PROVIDER_PATTERN`; one `EXISTS` on the usage row (provider and model must match the same call); `filters.provider` echo | `test_phase6c_provider_filter.py`, plan tests; clone check §11 |
| 38 Controlled refresh | `RefreshController`, `MIN_INTERVAL_MS` 15 s, off by default, hidden-tab pause, `Retry-After` cooldown, backoff, stop on fatal failures, same-view stale label | `phase6c.test.tsx`, `phase6c.spec.ts` (7 E2E tests) |

Cross-cutting checks (all pass):
- **Metadata only:** no `gen_ai.input/output.messages`, prompt or completion attribute exists in `sdk/src`; only `gen_ai.prompt.name`/`.version` labels. The stored live span carries metadata only (§5).
- **OTLP ingest unchanged since Phase 5:** `git diff phase-5 HEAD` touches no migration, ingest service, `/v1/traces` handler or ORM model. The backend runtime diff is six files (ADR-35 pricing rule; ADR-37 read filters), +82/−30.
- **Telemetry contract:** one additive note line (`openai.api.type`).
- **No provider SDK in the backend:** its only `google.*` imports are `google.protobuf` / `google.rpc` for OTLP.
- **Loopback only:** backend `bind_host` validator (`LOOPBACK_HOSTS`); dashboard `LLMI_API_BASE_URL` loopback check; `next start --hostname 127.0.0.1`; compose ports `127.0.0.1:` only. No `NEXT_PUBLIC_` variable exists (one comment says so).

**Verdict:** the implementation matches ADR-28…38. No accepted contract was changed.

## 3. Phase 5 regression evidence

- The Phase 5 suites are contained in the current ones, and all pass: backend 1765 (Phase 5: 1726), SDK 817 (350), frontend unit 349 (311), E2E 62 (55). No test file was deleted since `phase-5`.
- Eleven pre-existing test files changed. Every changed assertion follows an accepted ADR-37/38 behaviour: the new `filters.provider` echo, the "Real (asserted)" label, the model combobox, hidden tab = pause, the new filter reader, provider now supported. One validation test was tightened (`model: "x y<"` is now refused). No security assertion was removed.
- Bandit fingerprints at tag `phase-5` equal five of the six current High/Medium findings (§9).

**Verdict:** Phase 5 behaviour is preserved.

## 4. Phase 6 implemented features

- OpenAI Responses adapter with fail-closed guard and endpoint integrity (6A/6B, ADR-28…32).
- Gemini Developer API adapter, Free Tier guard with operator attestation and unknown cost, and Gemini runner (6C-A, ADR-33…36).
- Exact provider filter on `/api/v1/traces`, `/overview`, `/models/compare` (ADR-37).
- Controlled dashboard refresh, provenance labels "Real (asserted)" / "Synthetic" / "Mixed" / "Unknown", latest-trace summary (6C-C, ADR-38).
- `pyasn1` 0.6.5 (R101).

No feature was added in this phase.

## 5. Real Gemini live trace evidence (read-only)

**The user's `:8010` backend runs pre-6C-C code.** Its process started 2026-10-28 20:43, before `e109846` (2026-11-01). Its OpenAPI schema has no `provider` parameter, and FastAPI ignores unknown query parameters, so `provider=openai` returned the Gemini trace there. This is a stale process, not a code defect. ADR-37's echo check makes a current dashboard refuse such an aggregate rather than show it as filtered. The process was left running.

To verify the committed code, this session started it read-only on `127.0.0.1:8030` against the same dev database (the startup path creates an engine only and writes nothing). It made 42 `GET` requests, then was stopped. A dashboard built from the same tree ran on `:3011`, pointed at `:8030`, and was also stopped afterwards.

| Check | Result |
|---|---|
| Project | `gemini-live`, id 148 |
| Trace `bec4f18a3f9a29375a61290dfc62798d` | 2 spans: root `demo.live-gemini` INTERNAL (`df46e2cef674d806`) → child `generate_content gemini-3.1-flash-lite` CLIENT (`fc5a45c8fd910b5c`, parent correct) |
| Provider / model | `gcp.gemini` / requested and response `gemini-3.1-flash-lite`; `server.address generativelanguage.googleapis.com` |
| Usage | input 18 / output 1, `provider_reported`, finish `STOP` |
| Status / cost | trace `ok`; `known_cost_usd null`, `unknown_cost_calls 1`, `price_missing`, `cost_complete false` |
| Provenance | `real`, `provenance_verified false` |
| Isolation | same trace with `project=1` → 404 |
| Filters (current code) | `gcp.gemini` + model → 1; `provider=openai` → 0 |
| Date window | 2026-10-29 window → 1; a 2020 window → 0 |
| Overview / Models | 1 trace, `real: 1`, error rate 0, p50 6751.5 ms (low sample); one `gcp.gemini` group with unknown cost |
| Dashboard (SSR HTML on `:3011`) | Trace Explorer lists the trace with "Real (asserted)"; detail shows both spans and `price_missing`; Overview and Models show `gcp.gemini`; the `provider=openai` view says the backend "returned no traces" |
| Database (read-only SQL, `BEGIN READ ONLY`) | exactly **one** `provenance = 'real'` usage row exists (this call); no real OpenAI row; no leftover `e2e-*` project |

Nothing was regenerated, modified or deleted.

## 6. Full test results (this run, exact commands)

Working tree = HEAD + the user's `.env.example`. Clone = committed HEAD only (§11).

| Gate | Command | Working tree | Clone |
|---|---|---|---|
| Backend tests (PostgreSQL) | `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` (backend) | **PASS** 1765 passed, 0 failed, 0 skipped (340.6 s; 1 warning, R20) | **PASS** 1765 / 0 / 0 (312.4 s) |
| SDK tests | same (sdk) | **PASS** 817 / 0 / 0 (280.6 s) | **PASS** 817 / 0 / 0 (313.4 s) |
| Frontend unit | `npm test` | **PASS** 349 / 0 / 0 | **PASS** 349 / 0 / 0 |
| Playwright E2E | `playwright test` (Edge) | **PASS** 62 / 0 / 0 (2.2 min), second run; first run 58 passed / 4 failed, see below | **PASS** 62 / 0 / 0 (2.2 min) |
| TypeScript strict | `npm run typecheck` | **PASS** | **PASS** |
| ESLint | `npm run lint` (`--max-warnings 0`) | **PASS** | **PASS** |
| Next.js build | `npm run build` | **PASS** | **PASS** |
| Bundle secret scan (T42) | `npm run check:bundle` | **PASS** 632 files, 23 in the browser bundle, 0 findings | **PASS** 444 files, 23, 0 findings |
| Ruff + format | backend (141 files), dev scripts (2), sdk (44), demo_app (6) | **PASS** | **PASS** (backend, sdk) |
| Strict mypy | `mypy src` (12 files), `mypy --config-file pyproject.toml ../demo_app` (6) | **PASS** | **PASS** |
| `pip check` | backend, sdk | **PASS** | **PASS** |
| Alembic | `alembic current` / `alembic check` | **PASS** `0005 (head)` / no new operations | **PASS** (fresh `upgrade head`) |
| Compose | `docker compose config --quiet`, `--profile app` | **PASS** | **PASS** (`-p llmiqa6`) |
| Doc verifier | `python -I scripts/verify_phase0.py --quiet` | **FAIL** 797 PASS / 1 FAIL: only the user's `.env.example` (`LLMI_LIVE_MAX_*_TOKENS` not placeholders) | **PASS** 797 / 0 (also on `git archive HEAD`) |
| `pip-audit` lockfiles | `-r {backend/requirements.lock, sdk/requirements-dev.lock} --no-deps --disable-pip -s {pypi,osv}` | **PASS** 4 × exit 0, no known vulnerabilities | — |
| Installed packages | `pip-audit --path <venv>/Lib/site-packages -s {pypi,osv}` | **WARN** exit 1: only `pip` 24.2, 12 advisories in each venv (R75, accepted) | — |
| `npm audit` | `npm audit` | **FAIL** exit 1: 16 moderate = 1 advisory (R102) | **FAIL** exit 1 (same) |
| `npm audit --omit=dev` | | **PASS** exit 0, 0 vulnerabilities | **PASS** |
| Bandit | `bandit -r backend/app sdk/src demo_app scripts` (1.9.4) | **WARN** exit 1: High 1 / Medium 5 / Low 38, all classified, none exploitable (§9) | — |
| History secret scan | `python -I scripts/qa/secret_scan.py --repo . --env .env --env frontend/.env.local` | **PASS (values) / WARN (patterns)** exit 1: 954 blobs, 35 messages, 498 files, 9 local values: **0 value hits**; 40 `url_userinfo` hits, all at the Phase 5B F9 fixture locations (same 40 as at 6C-C) | — |
| Backend route probe | `python -I scripts/qa/backend_probe.py 8040 .env <out>` (throwaway clone backend) | — | **PASS** 19 routes, 1,389 requests, `violations: []`; unauthenticated 2xx only on `/health/*`; token + foreign `Host` → 200 (documented: boundary = loopback + token) |
| Whitespace | `git diff --check`, `--cached` | **PASS** | — |

**E2E environment note.** E2E ran against a disposable `llminsight_e2e_qa_test` database on the dev server (created, migrated, dropped), not the dev database. Backends ran on `:8020`, `:8001` and `:8002`. Teardown removed only the run's projects (`remaining: []`).

The first run reported 58 passed / 4 failed (three `phase4d` mock A/B tests and the Decimal-cost trace test). Cause: the disposable database had no price catalogue. `python -m app.cli seed-prices` is a documented setup step (README) that this session had skipped. After seeding, all 62 passed, with no code change.

Observation (not fixed; a test-harness precondition): the global setup does not assert seeded prices, so a missing catalogue fails late as a `TypeError`.

Tools: `pip-audit` 2.10.1 and `bandit` 1.9.4 from the Phase 5B throwaway venv (not a project dependency); Node 22.23.4 / npm 10.9.9 from `.tools/`; Python 3.12.6.

## 7. Python dependency audit results

- **R101 resolved and re-verified:** `importlib.metadata.version("pyasn1") == pyasn1.__version__ == "0.6.5"` in `sdk/.venv`. The clean-clone install from the lockfile also gives `pyasn1 0.6.5`.
- Both lockfiles are clean on PyPI and OSV.
- The installed-package audit finds only the toolchain `pip` 24.2 (12 advisories: PYSEC-2026-1795/-1796/-196/-2875/-2876/-3721, i.e. CVE-2025-8869, CVE-2026-1703, -8643, -3219, -6357, -13346; fixed in 25.3–26.2). These are install-time issues; `pip` is not imported at runtime (R75).
- The R75 text said "six" advisories; corrected to twelve in the register.
- **Lockfile integrity:**
  - Committed lockfiles are unchanged on this branch since the security closure.
  - Clean-clone installs from `requirements.lock` / `requirements-dev.lock` pass `pip check`.
  - Python locks are pinned without hashes (R75).
  - `npm ci` installs exactly `package-lock.json` (406 packages); the `overrides` block was not changed.

## 8. R102 analysis and the CI blocker

**Advisory (GitHub API, read 2026-11-01):**

| Field | Value |
|---|---|
| ID | GHSA-g3hv-75wg-m249 / CVE-2026-118764 |
| Severity | medium (npm: moderate); CVSS 4.0 6.9, `AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:N/VA:L/SC:N/SI:N/SA:N` |
| Published | 2026-10-30; not withdrawn |
| Affected / patched | `minimatch` < 11.0.1 / 11.0.1 |

**Exposure:**
- `npm audit`: exit 1, 16 moderate (1 root advisory plus 15 dependents).
- `npm audit --omit=dev`: exit 0, 0 vulnerabilities.
- `npm ls minimatch --omit=dev`: empty.
- `npm ls minimatch` shows the chains:
  - `minimatch@3.1.5` via `eslint@9.39.5` (direct and via `@eslint/config-array`, `@eslint/eslintrc`) and via `eslint-config-next@16.4.3` → `eslint-plugin-{import,jsx-a11y,react}`;
  - `minimatch@10.2.6` via `typescript-eslint@8.71.1` → `@typescript-eslint/typescript-estree`.
- Every chain is development-only. No production dependency is affected.

**Upstream fixes (npm registry, 2026-11-01):** there is no compatible fix.
- No `minimatch` 3.1.x or 10.2.x release after the advisory; 11.0.0–11.0.3 were released 2026-10-27/28.
- `eslint@10.14.0` (latest) still depends on `minimatch ^10.2.6`.
- `typescript-eslint@8.72.1` / `typescript-estree@8.72.1` (latest) still depend on `^10.2.2`.
- `eslint-config-next@16.4.3`, `eslint-plugin-import@2.32.0`, `-react@7.37.5` and `-jsx-a11y@6.10.2` are already latest and pin `^3.1.2`.
- `@eslint/eslintrc@3.3.7` (latest) pins `^3.1.5`.
- An ESLint 10 major upgrade would therefore not close the advisory.
- npm's suggested fix is a semver-major downgrade (`eslint@4.0.0`).
- A `minimatch@11` override would violate four declared ranges, cross two majors of a changed API and break the two-week rule.
- **None of these was applied.**

New fact: `npm ci` warns that `eslint@9.39.5` is deprecated ("no longer supported"). `eslint-config-next@16.4.3` accepts `eslint >=9.0.0`. Recorded in R102 as a future upgrade item; it does not change the advisory analysis.

**CI consequence (read from `.github/workflows/ci.yml`):**
- The `frontend` job runs `npm audit` (line 127, default audit level = any severity) immediately after `npm ci`.
- Its exit 1 fails the job, and GitHub Actions then **skips** `typecheck`, `lint`, `npm test`, `build` and `check:bundle`. A red job would also hide any later frontend regression.
- The `docs` and `python` jobs are unaffected; the doc verifier passes on the committed tree.
- The workflow triggers on pushes to `main` and on pull requests, so a PR from this branch or a merge to `main` is expected to fail.
- **The workflow was not changed** (§14 is a proposal).

**Verdict:** R102 remains conditionally accepted for local-only development, and its dev-only condition is re-verified. As a CI gate it is an **unresolved release blocker**.

## 9. Bandit reconciliation (0/0 vs 1 High / 5 Medium)

**Cause of the difference: scan scope only.**
- Same tool (bandit 1.9.4, Python 3.12.6), same configuration (none: no `.bandit`, no `[tool.bandit]`, no `-s`/`-x`/`-l` options, no `# nosec` in the code) and same output format.
- The Phase 6C-C report's "0 High, 0 Medium" covered **only its seven changed files** (2,249 LOC: `api/{traces,overview}.py`, `repositories/{traces,metrics}.py`, `services/metrics.py`, `scripts/dev/{e2e_fixtures,seed_trace_fixtures}.py`). Re-run now: 0 / 0 / Low 2.
- The security-closure "1 High / 5 Medium" was the **whole first-party tree** `backend/app sdk/src demo_app scripts` (15,617 LOC). Re-run now: identical, High 1 / Medium 5 / Low 38.
- Adding `backend/alembic` (16,764 LOC) changes nothing.
- The Phase 6A/6B/6C-A reports also scanned changed files only (1 Medium B310, 0 High).

**Fingerprints:** SHA-256 of file + rule + offending source line, so they are independent of line numbers. A scan of `git archive phase-5` (13,001 LOC) gives High 1 / Medium 4 / Low 38, with the **same fingerprints** for five findings; the sixth is the Phase 6A probe. None of the files with these findings changed since `phase-5` except `demo_app/live_common.py`, which is new in 6C-A.

| # | File:line | Rule | Severity / confidence | Code path | Exploitability | Prior acceptance | False-positive evidence | Fix |
|---|---|---|---|---|---|---|---|---|
| 1 | `scripts/dev/install_node.py:77` | B202 `zipfile.extractall` | High / High | dev-only installer of Node into `.tools/` | none found | Phase 5B F5 | Download from fixed `https://nodejs.org/dist/v22.23.4/` (`BASE_URL` constant). The archive's SHA-256 is checked against `SHASUMS256.txt` before extraction. `safe_extract` resolves **every** member and refuses any outside the target. `zipfile` creates no symlinks; the tar branch uses `filter="data"`. fp `945c03dea0c0` | no |
| 2 | `scripts/dev/install_node.py:53` | B310 `urlopen` | Medium / High | same installer | none | 5B F6 | Only called with `BASE_URL + "SHASUMS256.txt"` / `BASE_URL + archive`: fixed https URL, no user input. fp `f3c379faef32` | no |
| 3 | `demo_app/web.py:297` | B310 | Medium / High | demo driver `call()` | none | 5B F6 | Only caller `web.py:304` passes `running.url`, built as `http://127.0.0.1:{bound port}` of the demo's own server. fp `d572f779c1b8` | no |
| 4 | `scripts/qa/backend_probe.py:26` | B310 | Medium / High | QA probe | none | 5B F6 | `http://127.0.0.1:{int(argv[1])}/openapi.json`: `int()` port, fixed loopback host and scheme. fp `26557e2296ab` | no |
| 5 | `scripts/qa/benchmark.py:307` | B608 f-string SQL | Medium / Medium | QA benchmark, test-owned DB | none | 5B F7 | The table name comes from the literal tuple `("traces", "spans", "usage_records")`; no external value. fp `884bc2e15a66` | no |
| 6 | `demo_app/live_common.py:32` | B310 | Medium / High | live runners' readiness probe (empty OTLP POST) | none | Phase 6A (then `live_openai.py`), moved unchanged in 6C-A | `config.endpoint` is validated by `LLMInsightConfig._validate_endpoint`: `http`/`https` only (no `file:`/custom schemes, the B310 concern), host required, plain `http` only for loopback. fp `085fb004b875` | no |

The 38 Low findings are the Phase 5B F8 classes: B101 asserts ×13, B603 ×12, B607 ×7, B404 ×4 (fixed-argv `git`/`docker`/`taskkill`/seeder calls) and B105 ×2 (a hypothesis text, a `CHANGE_ME` placeholder check).

**Verdict:** no new High or Medium finding; no exploitable issue; nothing requires a fix. Bandit still exits 1 on any finding, so the gate is reported as WARN (classified), not PASS. Bandit's configuration was not touched.

## 10. Security risk register review

| Risk | Status | Evidence this run |
|---|---|---|
| R75 `pip` 24.2 toolchain, unhashed locks, unscanned image/OS | Accepted (local) | 12 advisories (text corrected from six); not imported at runtime |
| R80 Phase 2B Chat adapter retries ambiguous failures | Accepted (compat.) | Live runners use Responses / Gemini paths with one attempt; no paid run is enabled |
| R85, R86, R87 OpenAI env headers, deny-list, transport | Resolved (ADR-31/32) | Guard and endpoint tests pass (in SDK 817) |
| R88 OS trust store (OpenAI) / R96 pinned `certifi` (Gemini) | Accepted (local) | Unchanged; `CERT_REQUIRED` + hostname checks tested |
| R89 Gemini finish reasons not interpreted | Accepted | Stored verbatim (§5: `STOP`) |
| R90 Free Tier is an operator attestation | Accepted, Med if misused | Committed `.env.example` is fail-closed (clone check); the user's edited copy still refuses (`provider_not_allowed`, stale attestation; security-closure §4). No call was made |
| R91 Free Tier data use / R92 model lifecycle | Accepted | Fixed SYNTHETIC sentence only; one real call ever (§5) |
| R93, R94 Gemini continuation / redirect key forwarding | Resolved (ADR-33/34) | `test_gemini_adapter.py`, `test_gemini_endpoint.py` pass |
| R95 `responseId` uniqueness | Accepted | Unchanged |
| R97–R100 stale display, cooldown, read budget, top-200 suggestions | Accepted (local) | E2E `phase6c.spec.ts` passes on working tree and clone |
| R101 `pyasn1` | **Resolved** | §7 |
| R102 `minimatch` dev-only | **Accepted conditionally (local); CI blocker** | §8 |
| R103 (new) compose project name fixed to `llminsight` | Accepted (operational) | A second checkout on the same machine, without `-p`, would address the dev stack's container and volume; the clone used `-p llmiqa6` and port 55433 |
| R104 (new) CI security coverage | Open (review item) | CI runs no `pip-audit`, Bandit or history secret scan, and its `npm audit` step masks the later frontend steps (§8, §14) |

Further checks:

**Provider calls and secrets**
- **No unauthorized provider call:** SDK provider tests use fake transports, and the live-backend integration tests block every non-loopback connection (`block_network`). The only real usage row in the dev database is the 2026-10-29 Gemini call.
- **No secret exposure:**
  - 0 secret-value hits in history and files; bundle scan 0.
  - Keys were never read; the admin token was read from `.env` into process memory only.
  - The clone's ingest key was written only to the clone's temp folder, never printed, and deleted with it.

**Data handling**
- **No prompt/completion persistence:** see §2 and §5.

**Response error handling**
- Responses failure, unexpected status and malformed bodies, plus Gemini blocked, failed and empty outcomes, are covered by the passing adapter tests.
- API errors use the uniform envelope, and an invalid `provider` is not echoed (§11).

**Access and exposure**
- **Localhost and dashboard:** see §2. The probe found no unauthenticated data route.
- **Per-project isolation:**
  - Cross-project trace read → 404, absent project → 404.
  - The E2E foreign- and hostile-project tests pass.
  - `test_phase6c_provider_filter.py` isolation cases pass.

**Not safe for public deployment.** No user authentication (one admin bearer token), no Host-header check, a local-only threat model. Every accepted local-only risk above must be re-assessed before any non-local use.

## 11. Clean-clone evidence

- **Tree:** `git clone --no-hardlinks` of this repository into `%TEMP%\llmi6qa\repo`. A short path was used because `node_modules` paths would exceed Windows' limit under the scratchpad. HEAD was `5f77c94`, status clean, and `.env.example` equal to the committed fail-closed template; the user's modification was not included.
- **Isolated resources:**
  - Throwaway `.env` from `init_env.py`, with alternate ports (`POSTGRES_HOST_PORT=55433`, `LLMI_PORT=8040`).
  - Compose project `-p llmiqa6`: container `llmiqa6-postgres-1`, volume `llmiqa6_pgdata`.
  - `frontend/.env.local` from `init_frontend_env.py`.
  - Node 22.23.4 binary reused from `.tools/` (same SHA-checked version).

| Step | Result |
|---|---|
| Dependency install (CI commands) | backend: `pip install -r requirements.lock`, `-e .`, `pip check` **PASS**. sdk: `requirements-dev.lock`, `.`, `pip check` **PASS** (`pyasn1 0.6.5`). frontend: `npm ci --no-audit --no-fund` **PASS** (406 packages; ESLint 9 deprecation warning) |
| Database | `alembic upgrade head` → `0005 (head)`; `alembic check` no operations; `seed-prices` inserted 6 (catalogue `openai-2026-10-24`) |
| Gates | Ruff, format, strict mypy, doc verifier 797/0, both compose configs: **PASS**. Suites: backend 1765, SDK 817, unit 349, E2E 62, all passed with 0 failed and 0 skipped. typecheck, lint, build, bundle scan **PASS**. `npm audit` **FAIL** (R102), `--omit=dev` **PASS** |
| Backend starts | `python -m app` on `127.0.0.1:8040`, `/health/ready` → `ready` |
| Dashboard builds and loads | `npm run build`, then 62 E2E tests against `next start` instances on `:3001`–`:3005` |
| Synthetic ingestion | E2E fixtures through the real `POST /v1/traces`. SDK demo A–D (`python -m demo_app`, mock): 12 spans exported, `delivered: true`, provenance `synthetic`. E is the designed export-failure case (rejected key → 401; unreachable backend): the app answers, nothing is delivered. `demo_app.web --drive`: 13 spans exported (mock and offline fake-OpenAI) |
| Provider/model filtering | project 5: all 9; `llminsight.mock` 8; `openai` 2; `gcp.gemini` 0; `openai` + `gpt-5.4-mini` 2; `openai` + `mock-chat-small` 0 (same-call rule); `OPENAI` 0 (exact); `bad:val` → 422 `fields: ["provider"]`, value not echoed; overview echoes `filters.provider = "openai"`; no token → 401 |
| Auto-refresh | `phase6c.spec.ts`: off by default, nothing at 29 s / one at 31 s, 429 cooldown for timer and button, backoff, stop on wrong token, stale same view |
| Mock evaluation flows | `seed_golden_dataset.py --run-experiment --model gpt-5-mini`: 12 cases, mock experiment 4 (v1: 7 passed / 4 failed / 1 not evaluated; v2: 4 / 6 / 2). The E2E `phase4d` flow (failed trace → review → approved case → golden dataset → mock A/B, checked against PostgreSQL) passed |
| Cleanup | backend stopped; `docker compose -p llmiqa6 down -v` removed only `llmiqa6-postgres-1`, its network and `llmiqa6_pgdata`; clone folder deleted. The dev container, the `llminsight_pgdata` volume and the user's `:8010` backend were untouched |

## 12. Operational limitations

- **Local only:** single admin token, loopback binds, no public deployment, no Host check. Not production-ready.
- **Asserted provenance:** `real` is asserted, never verified (ADR-21); Gemini cost is always unknown; the Free Tier is an operator attestation (R90).
- **Stale dev process:** the user's `:8010` backend runs pre-6C-C code. It should be restarted to serve the provider filter; the dashboard refuses its unfiltered aggregates.
- **E2E prerequisites:** Edge and free ports `3001`–`3005`, `8001`, `8002` are needed; the price catalogue must be seeded (§6). Playwright E2E is a local gate, not in CI.
- **Second checkout:** compose needs `-p <name>` and another host port on a machine that already runs the dev stack (R103).
- **Working-tree doc verifier:** it fails while the user's `.env.example` is modified. The committed tree passes.

## 13. Remaining blockers

1. **R102 / CI:** the existing mandatory `frontend` job is expected to fail at `npm audit` and skip its later steps. No compatible dependency fix exists. Resolution needs either an upstream release on `minimatch` ≥ 11.0.1, or an approved CI security-policy change (§14).
2. **Remote Linux CI not run:** the branch is not pushed. Ubuntu-runner results (including the Linux `python` job) are unknown, not passed.
3. **Architectural review** of this report (and of the security closure, `PHASE_STATUS.md` open item).

Not blockers: Bandit (classified, no exploitable finding); the R75 `pip` toolchain advisories (accepted local-only); the working-tree verifier failure (user-owned file, outside every commit).

## 14. Proposed CI release policy (proposal only; not applied)

The workflow was **not** changed. The following needs architectural approval before any edit to `ci.yml`.

**A. Strict production dependency audit (blocking)**
- `npm audit --omit=dev --audit-level=low` fails the job on any production finding.
- `pip-audit -r backend/requirements.lock` and `-r sdk/requirements-dev.lock` (`--no-deps --disable-pip`, PyPI and OSV) fail on any finding.
- Run these in a separate `audit` job, so an audit failure no longer skips typecheck, lint, tests, build and the bundle scan.

**B. Full dependency audit reporting (always visible)**
- Run `npm audit --json` (all dependencies) on every run.
- Upload the JSON as an artifact and write a per-advisory summary (ID, severity, chain, dev/prod) to the job summary.
- Never suppress or filter the full list.

**C. Accepted dev-only exceptions (explicit, expiring)**

A committed exception file, for example `frontend/audit-exceptions.json`. Each entry holds:
- advisory ID (`GHSA-g3hv-75wg-m249`);
- package and allowed vulnerable versions (`minimatch` 3.1.5, 10.2.6);
- allowed parent chains (ESLint toolchain only);
- `dev_only: true`;
- the risk ID (R102);
- approver and approval date;
- an expiry date (proposal: 2026-12-01 or the first upstream fix, whichever comes first).

A small stdlib checker (no new dependency) compares the full audit JSON with the file.

**D. Explicit release failure criteria**

The `audit` job fails if any of these holds:
1. any production-dependency finding (npm or Python), of any severity;
2. any high or critical finding anywhere;
3. any dev finding not listed in C;
4. a listed advisory appears in the production tree or outside its allowed chains;
5. an exception has expired;
6. npm reports a non-major fix for a listed advisory (the exception must then be removed and the fix taken).

Separately, Bandit and the history secret scan stay classified local release gates (they exit 1 on any finding by design) unless a baseline mechanism is approved. A tagged release additionally requires a green remote run on the release commit.

Until such a policy is approved and green, the release is not CI-ready.

## 15. Final release readiness verdict

**BLOCKED — RELEASE GATES UNRESOLVED.**

- Locally, every functional, regression and security gate passes on the working tree and on a clean clone: backend 1765, SDK 817, frontend unit 349, E2E 62, all passed with 0 failed and 0 skipped.
- Phase 5 behaviour is preserved.
- ADR-28…38 are implemented as accepted.
- R101 is resolved.
- Bandit has no exploitable finding.
- The live Gemini trace is intact and correctly shown.

Release is blocked by (1) the mandatory CI `frontend` job, which is expected to fail on R102 with no compatible fix, pending upstream or an approved policy (§14), and (2) the absence of a remote Linux CI run. No push, merge or tag was made.

## 16. Files changed by this phase

- `docs/reports/phase-6-final-qa.md` (this report).
- `docs/PHASE_STATUS.md`: Phase 6 final QA entry.
- `docs/SOURCES_AND_RISKS.md`:
  - R75: advisory count corrected from six to twelve;
  - R102: re-verification of 2026-11-01, ESLint 9 deprecation, CI step consequence;
  - new rows R103, R104.

No code, test, lockfile, workflow, contract or ADR file was changed. `.env.example` was not staged.
