# Phase 8A-6 — Multi-provider dashboard E2E verification (offline)

**Date:** 2026-11-11 UTC. **Status: implemented; locally verified where no running backend or database is needed; approved for one local commit (§11). In the first CI run of PR #9 all 72 browser tests passed, but the E2E lifecycle gate still expected four projects; its correction is a second local commit, not pushed (§12). The 10 new browser tests have not run locally; the checkpoint closes when the CI retry is green.**

Provider API requests: **0**. No credential, billing account, `.env`, `.env.example`, `frontend/.env.local`, PostgreSQL server, database, volume, Docker container or deployment was touched. Every check ran from a copy holding no `.env`, or from a frontend copy with a dummy, non-secret `.env.local`. `REAL_EXECUTION_ENABLED` is still `False`, `FREE_PLAN_MODELS` is empty and `FREE_PLAN_EVIDENCE_VERIFIED_AT` is `None`. No production code, dependency, migration, Docker or CI file changed. The three user-owned untracked files were not opened, copied or changed.

Base: `feature/phase-8a6-comparison-dashboard` at `3424644` (= `main` = `origin/main`, the merge of PR #8).

**This is synthetic dashboard evidence, not real-provider evidence.** Every inference span in the comparison project is a SYNTHETIC fixture labelled `synthetic`. The tests show how the dashboard renders stored comparison telemetry. They say nothing about a real Groq, Gemini, OpenRouter or OpenAI response (R115, R120, R124).

Numbering: `reports/phase-8a-0.md` §16.4 calls this checkpoint "8A-7 Dashboard"; its numbering was shifted by one when 8A-5 became the integration checkpoint. The scope is the same: `scripts/dev/seed_trace_fixtures.py` and a new spec under `frontend/tests/e2e/`, with no product code change.

---

## 1. Files changed

| File | Change |
|---|---|
| `scripts/dev/seed_trace_fixtures.py` | New fixture set `--set phase8a6` (`phase8a6_set`), constants for the pinned scenario identities and sentinels, docstring |
| `scripts/dev/e2e_fixtures.py` | One more run-owned project, `e2e-<run>-comparison`, seeded with `phase8a6`; docstring |
| `frontend/tests/setup/e2e-global-setup.ts` | Exposes `LLMI_E2E_COMPARISON_PROJECT` to the tests |
| `frontend/tests/e2e/e2e-env.ts` | `comparisonProject()`, read lazily (see §2.3) |
| `frontend/tests/e2e/phase8a6.spec.ts` | **New**: 10 browser tests |
| `backend/tests/unit/test_e2e_comparison_fixture.py` | **New**: 8 offline tests of the fixture through the real ingest rules |
| `docs/reports/phase-8a-6.md` | **New**: this report |
| `docs/PHASE_STATUS.md` | 8A-5 closure, 8A-6 lines, two command rows |
| `docs/SOURCES_AND_RISKS.md` | R131 extended; new R132 |

Not changed: `frontend/app`, `frontend/components`, `frontend/lib`, `backend/app`, `backend/alembic`, `sdk/src`, `demo_app`, `.github/workflows`, `scripts/qa/e2e_gate.sh`, `scripts/qa/e2e_check.py`, `playwright.config.ts`, lock files and `.env.example`.

## 2. Fixture design and project lifecycle

### 2.1 The comparison set (`--set phase8a6`)

One project, eight traces, fifteen spans, seven inference attempts. They are posted through the real `POST /v1/traces` as OTLP/HTTP protobuf, with timestamps a few minutes before now (UTC). Ids are derived from the run.

| Service name | Root | Inference span | Prompt version | Tokens in / out | Cost |
|---|---|---|---|---|---|
| `cmp8a6.openai` | `POST /v1/answer` (SERVER) | `openai`, `gpt-5.4-mini` → `gpt-5.4-mini-2026-03-17`, standard tier, `api.openai.com` | `v1` | 1,200 / 300 (cached 1,000, reasoning 200) | computed from the verified catalogue: 0.0015750000 |
| `cmp8a6.gemini` | `POST /v1/answer` (SERVER) | `gcp.gemini`, `gemini-3.1-flash-lite` | `v1` | 18 / 6 | unknown, `price_missing` (ADR-35) |
| `cmp8a6.groq.ticket-triage` | `demo.live-compare` (INTERNAL) | `groq`, `openai/gpt-oss-20b`, the 17 keys of `docs/fixtures/groq_client_span.v1.json` | `cmp-v1.ticket-triage` | 20 / 32 | unknown, `price_missing` (ADR-40) |
| `cmp8a6.groq.json-aggregate` | `demo.live-compare` | same | `cmp-v1.json-aggregate` | 24 / 40 | unknown, `price_missing` |
| `cmp8a6.groq.release-summary` | `demo.live-compare` | same | `cmp-v1.release-summary` | 30 / 64 | unknown, `price_missing` |
| `cmp8a6.openrouter-synthetic` | `synthetic.openrouter-compat` | `openrouter`, `synthetic/compat-only:free`, `openrouter.ai` | `cmp-v1.ticket-triage` | 20 / 32 | unknown, `price_missing` (ADR-40) |
| `cmp8a6.refused-root-only` | `demo.live-compare` alone | none (R131) | none | none | none |
| `<img …>cmp8a6.adversarial` | `<script>…</script>` | hostile provider and model, two content attributes, a reasoning attribute and a key-shaped response id, all dropped by the server; no usage | none | unknown | unknown, `usage_missing` |

- **Identities.** The three Groq scenarios use the exact names and versions of the Phase 8A-4 registry (`demo_app/comparison_scenarios.py`, ADR-45). The backend unit test reads them from that file's source and compares them, so a drift fails. Token counts differ per scenario, so a merged row would be visible.
- **Provenance.** Every inference span claims `synthetic`; nothing claims `real`. No span claims to have reached a provider.
- **OpenRouter.** A SYNTHETIC compatibility representation only. No OpenRouter adapter exists, the model name says so, and its root is deliberately not the runner's `demo.live-compare`.
- **R131.** The runner's root span alone, as a send-time refusal leaves it. It is made by the fixture; the runner is never executed and its one-request-per-process slot is never bypassed.
- **Prices.** No price row is added. The OpenAI cost uses the verified catalogue that the CI `e2e` job seeds (`app.cli seed-prices`). Append-only price history is untouched.
- **Adversarial values.** The key-shaped value is assembled at runtime (`"gsk" + "_" + …`), never written as a literal. The content and reasoning sentinels are plain marker strings.

### 2.2 Lifecycle

The existing run-owned mechanism is extended by one role and reused unchanged otherwise:
- **Setup.** `e2e_fixtures.py setup` creates `e2e-<run>-comparison` beside the other four projects. It refuses a name that already exists, mints one ingest key in memory, sends `--set phase8a6` through the seeder, revokes the key, and writes the project id and name to the state file. A failed setup deletes everything this run created.
- **Teardown.** `teardown` deletes the comparison project only when its id **and** name still match the run; `owned()` covers the new role through `names(run)`. The cascade removes its traces, spans and usage rows. No price row, schema object or other project is touched.
- **Isolation.** The comparison data never enters the legacy projects. The existing specs keep their exact datasets.

### 2.3 CI listing constraint

The CI gate first lists the suite (`e2e_gate.sh test`: `playwright test --list`) with only the four existing project ids set. `e2e-env.ts` throws at import when a required id is missing. The comparison id is therefore read lazily, through `comparisonProject()` in the spec's `beforeAll`, which the listing never runs. The listing step itself needs no change. The local listing with exactly the gate's four variables shows 72 tests, 10 of them new, and 0 errors (§5). *(Corrected in §12: this section first said `e2e_check.py` and its self-tests needed no change. They did: the gate pinned four run-owned projects and at least 62 tests.)*

## 3. E2E test matrix (`frontend/tests/e2e/phase8a6.spec.ts`)

| # | Test | Requirement |
|---|---|---|
| 1 | Traces: the comparison project lists exactly its SYNTHETIC set, as the backend returns it | B; A (no foreign telemetry); synthetic never verified |
| 2 | Traces: provider and model are exact backend filters; `prompt_version` is not a Traces filter (R130) | B, R130 |
| 3–5 | Trace detail, one per Groq scenario: `demo.live-compare` root, CLIENT span, prompt identity, finish reason, unknown cost | B |
| 6 | Overview: counts, tokens and cost completeness match the backend; unknown cost is never $0; synthetic is never verified | A |
| 7 | Models / Compare: one row per provider, model and scenario; unknown cost, low sample, no ranking; filters; R132 | C |
| 8 | R131: a root-only trace counts as a trace but adds no attempt, tokens, cost or Compare row | D |
| 9 | Isolation and authentication | E |
| 10 | Adversarial trace: hostile labels render as text; dropped content, reasoning and key never reach the browser | E |

The tests are independent (no serial mode), do not use `test.skip`, and pace their direct API reads at 150 ms, under the shared `/api/v1` rate limit, as the existing suites do.

## 4. UI/API contract assertions

Each page value is compared with a direct authenticated backend read for the window the page echoes (`data-from` / `data-to`), plus the fixed values the fixture defines.

**A. Overview**
- **Counts:**
  - 8 traces and 7 inference attempts;
  - traces by provenance: real 0, synthetic 7, mixed 0, unknown 1;
  - attempts by provenance: real 0, synthetic 7, unknown 0.
- **Tokens:** 1,312 in and 474 out, with "Not reported: input by 1, output by 1 of 7 attempts".
- **Cost:**
  - known cost 0.0015750000 shown as `formatUsd`, `data-state="known"`;
  - "Incomplete: 6 of 7 unpriced";
  - "Priced 1 of 7 attempts · unknown cost 6";
  - API reasons: `price_missing` 5, `usage_missing` 1.
- **Provenance:** notice `contains_synthetic`, containing "not verified", Real badge 0; `provenance_verified: false`.
- **Provider filters:**
  - `?provider=groq`: 3 traces, 3 attempts, 74 / 136 tokens, cost "Unknown: no call priced" with `data-state="unknown"`, "Incomplete: 3 of 3 unpriced";
  - Gemini and OpenRouter: 1 attempt each, unknown cost;
  - no `$0` value in the cost tile or the group table.

**B. Traces**
- **List:**
  - exactly the eight service names; the order and ids are the backend's;
  - each row's provenance badge, status, prompt version and cost state;
  - Groq rows show `groq · openai/gpt-oss-20b ×1` and their own tokens;
  - the root-only row has no cost and no model cell;
  - no `Real (asserted)` badge and no `$0`.
- **Filters:**
  - provider options are exactly the observed values (`gcp.gemini`, `groq`, `openai`, `openrouter`, `unknown`), with the "1 attempt reported no model" note;
  - provider, provider + model and model-only filters each return the backend's rows.
- **R130:** `?prompt_version=` shows the unfiltered list, and the backend returns the same ids with and without the parameter.
- **Detail, per Groq scenario:**
  - API: 2 spans (root `demo.live-compare` INTERNAL; the CLIENT span its child); 0 dropped attributes; the scenario's prompt name and version; finish reasons `["stop"]`; one usage row with the scenario's tokens; `unknown` / `price_missing`, no price version.
  - UI: a two-level tree with those span ids; facts for prompt version, OK status, 2 spans, "1 attempts", tokens and unknown cost; provenance notice `synthetic`; one attempt row with "no verified price for this model".
  - CLIENT span details: name `chat openai/gpt-oss-20b`; "Status UNSET (no error recorded)"; span cost "Unknown: no verified price for this model"; finish reason and prompt identity in the stored attributes; "Dropped by server 0".
  - Root span: "Not an inference span: no usage row."

**C. Models / Compare**
- **Groups:**
  - 7 groups: OpenAI, Gemini, three Groq, OpenRouter, and `unknown` (the adversarial span);
  - the three Groq groups carry exactly the three scenario versions and their own tokens;
  - the OpenRouter group (`cmp-v1.ticket-triage`) is a row of its own beside Groq's ticket-triage row;
  - OpenAI shows `data-exact="0.0015750000"`, complete; every other row is `data-cost-state="unknown"` with no `$`;
  - every group has n = 1 with `low_sample` p95 and p99, rendered as "low sample for p95" and "(low sample)".
- **Notices:**
  - "SYNTHETIC data only";
  - "no group is presented as the best", "▲ marks a p95 from fewer than 20 samples", "Unknown cost is never counted as $0";
  - no "best model", "winner", "recommended" or "ranked first".
- **Filters:** `provider=groq` (3 rows), `provider=groq` + model (3), `provider=openrouter` (1), `model=gpt-5.4-mini` (1, priced).
- **R132:** the backend's `prompt_version=cmp-v1.json-aggregate` returns one group, but the page with `?prompt_version=` still shows all 7.

**D. R131**
- **API:** the root-only trace has 1 span (`demo.live-compare`, INTERNAL, no `gen_ai.*`), 0 calls, no usage rows, no prompt version, provenance `unknown`.
- **List:** no cost and no model cell.
- **Detail:** 1 tree item, "0 attempts", no cost state, "no inference attempt in this trace.", no attempts table, provenance notice `unknown`.
- **Aggregates:** Overview traces 8 against attempts 7, the root-only trace being the difference; cost n 7 (computed 1 + unknown 6); Compare attempts sum 7, no group with 0 attempts; the only group without a prompt version is the adversarial `unknown` one.

## 5. Local results

| Check | Command (from) | Result |
|---|---|---|
| Frontend typecheck | `npm run typecheck` (frontend copy, Node 22.23.4) | **PASS** |
| ESLint | `npm run lint` (`--max-warnings 0`) | **PASS** |
| Frontend unit tests | `npm test` | **353 passed, 0 failed, 0 skipped** |
| Audit-policy self-test | `npm run test:audit-policy` | **88 passed, 0 failed** |
| Production build | `npm run build` | **PASS** |
| Bundle secret scan | `npm run check:bundle` | **PASS**: 444 files (23 in the browser bundle), 0 findings |
| Playwright listing, as the CI gate (four project ids only) | `npx playwright test --list --reporter=json` | **72 tests in 8 files; `phase8a6.spec.ts` 10; 0 errors** |
| Backend Ruff | `ruff check .` / `ruff format --check .` (backend copy without `.env`) | **PASS** |
| Dev scripts Ruff (root, ruff defaults, as CI) | `ruff check` / `ruff format --check` on both scripts | **PASS** |
| New fixture test | `pytest tests/unit/test_e2e_comparison_fixture.py` | **8 passed** |
| Backend unit suite | `pytest tests/unit` | **1216 passed** (1208 before + 8) |
| Backend privacy and cost subset | `test_groq_contract`, `test_comparison_contract`, `test_pricing`, `test_metadata_privacy`, `test_redaction`, the new test | **489 passed** |
| Fixture mutants vs the new test (private copies, after a passing control) | §5.1 | **8 of 8 caught** |
| Bandit gate | Bandit 1.9.4 over `backend/app sdk/src demo_app scripts` | **PASS**: 44 = 44, 0 problems |
| QA self-tests | `python -I -m unittest discover -s scripts/qa/tests` | **214 ran, OK, 15 skipped** (as before) |
| Credential detectors, doc check, history gate | §6 | §6 |
| SDK suite, strict mypy | | **Not rerun: no SDK or `demo_app` file changed** |
| **Browser E2E, `phase8a6.spec.ts`** | `npx playwright test` | **NOT RUN LOCALLY (10 tests)** |
| Browser E2E, the other 62 tests with the fifth project present | | **NOT RUN LOCALLY** |

Local tooling: the pinned Node 22.23.4 was installed into the scratchpad by a copy of `scripts/dev/install_node.py`, with its SHA-256 checked. The locked frontend dependencies (406 packages) were installed by `npm ci` in a scratch copy of `frontend/`. Nothing was installed in the repository or globally.

**Why the browser tests did not run.** `playwright.config.ts` needs the backend on `127.0.0.1:8000`, its PostgreSQL and `frontend/.env.local` with the server token. Locally these are the developer's persistent services and secrets, which this checkpoint may not use. **Listing is not passing.** Until the `e2e` job runs them, the browser assertions of §4 are checked only against the component and API source and the offline fixture test, not by execution.

### 5.1 Fixture mutation results

Each mutant changes `seed_trace_fixtures.py` in a private copy:

| Mutant | Caught by |
|---|---|
| F1 a scenario's prompt version altered | `test_the_scenario_identities_are_the_phase_8a4_registry` |
| F2 key-shaped value not sent | `test_the_adversarial_span_drops_six_values_and_keeps_its_labels_as_text` |
| F3 root-only trace gets an inference span | `test_the_set_is_eight_synthetic_traces_that_ingest_without_violation` |
| F4 OpenRouter labelled as Groq | `test_compare_keys_keep_seven_groups_and_three_groq_scenarios` |
| F5 OpenAI service tier removed (would become `price_missing`) | `test_usage_and_cost_follow_the_ledger_and_pricing_rules` |
| F6 Groq `server.port` dropped | `test_each_groq_scenario_is_the_adapter_span_under_a_live_compare_root` |
| F7 a Groq span claims `real` | `test_the_set_is_eight_synthetic_traces_that_ingest_without_violation` |
| F8 Groq root renamed | `test_each_groq_scenario_is_the_adapter_span_under_a_live_compare_root` |

## 6. Security and privacy evidence

- **Project isolation (test 9, CI):**
  - a comparison trace under the foreign, main or providers project returns 404 with the body of a missing trace;
  - the foreign project's list holds none of the comparison ids;
  - its Overview for `groq` shows 0 traces and 0 attempts;
  - its Compare has no Groq, OpenRouter or `cmp-v1.*` group;
  - the dashboard shows "trace not found" for the comparison trace under the foreign project;
  - its Traces HTML holds no comparison id, and its Models page shows no comparison text.
  - Test 1 shows the comparison project holds only its own eight traces.
- **Authentication:** the read API without the bearer token returns 401.
- **Credentials:** no browser response, direct API response or browser storage contains the server token, `LLMI_API_TOKEN`, `LLMI_ADMIN_TOKEN` or a `Bearer` value. The production bundle scan found 0 secret values (local).
- **Content, reasoning and key (test 10, CI; offline proof in the backend unit test, local):** the adversarial span's two content attributes, reasoning attribute and key-shaped response id are sent and then dropped. The server counts 6 dropped values, including the hostile provider and model. None of the four values appears in any browser response, the four direct API reads, the stored span attributes or the rendered pages. The offline test also checks that the values were really in the request bytes, so the drop is tested.
- **Hostile labels:**
  - the service name and both span names keep their markup and render as text;
  - the hostile provider and model are dropped, and shown as `unknown` / "not reported";
  - no dialog opens and `window.__llmiXss` stays undefined on the list, detail, Models and Overview pages;
  - no `img` or `script` element exists in `main` or the header.
- **CSP and console:** test 10 fails on any console error or warning, page error, CSP violation or hydration warning across those pages.
- **Synthetic labelling:**
  - no `real` claim anywhere;
  - `provenance_verified` is false in every list item and detail;
  - no "verified real" or "provenance verified" text;
  - Compare shows "SYNTHETIC data only" and Overview "Window includes synthetic telemetry".
- **Repository checks:**
  - credential detectors over the changed and new files: **0 matches**;
  - doc check: **828 PASS, 0 FAIL**;
  - `git diff --check`: clean;
  - history secret gate in a throwaway clone with this working tree committed there and a PR-style merge ref: **PASS, 39 = 39, 0 findings in the new blobs**; baseline unchanged.

## 7. Remaining risks

| Risk | State after 8A-6 |
|---|---|
| R123 | Open: the Gemini security findings; Gemini comparison runs stay refused. Not touched |
| R125 | Open: the SDK cannot verify the comparison project. The dashboard isolation tests cover only the read side |
| R126 | Open: single samples. Compare shows n = 1 low-sample flags and no ranking (test 7) |
| R127 | Open: the `.env.example` Groq comment, required before the first real request. `.env.example` unchanged |
| R128 | Mitigated, unchanged |
| R129 | Unchanged: the 8A-5 integration rows labelled `real`. The 8A-6 fixture labels everything `synthetic` and adds no `real` row |
| R130 | Open, now also shown in the browser: the Traces list ignores `prompt_version` (test 2) |
| R131 | Open; the dashboard behaviour is now covered with a SYNTHETIC root-only trace (test 8, CI only) |
| R132 (new) | Open: no dashboard page sends `prompt_version` (§8) |
| R115, R119–R122, R124 | Unchanged: no real provider response has been observed |

## 8. New findings

- **R132, a UI limitation and not a defect.** `GET /api/v1/overview` and `GET /api/v1/models/compare` accept `prompt_version`, but no dashboard page forwards it: `lib/model-query.ts` and `lib/scope-filters.ts` handle `provider` and `model` only. A `?prompt_version=` in a dashboard URL is ignored silently, without the "ignored" notice an invalid value of a known filter gets. Nothing in the UI claims the filter exists, and the three scenarios stay apart as separate Compare rows. Test 7 pins the behaviour, so adding the filter later would have to update it deliberately. No production change was made.
- **R130 confirmed end to end** (test 2): the backend returns the same list with and without `prompt_version`.
- **CI listing constraint** (§2.3): a new required project id read at import would break the gate's `--list` step. Reading it lazily avoids any CI change.
- **No dashboard defect was found** in reading the components against the API, or in the offline fixture checks. The browser has not yet run these tests, so this is not yet execution evidence.

## 9. Required GitHub CI checks

- **`e2e`:**
  - every discovered test runs and passes (`e2e_check.py report`): 72 tests including the 10 of `phase8a6.spec.ts`, 0 skipped, 0 flaky;
  - the setup creates five run-owned projects;
  - the teardown removes them;
  - the cleanup leaves no resource.
- **`python`:** with `LLMI_TEST_REQUIRE_DB=1`, backend 1904 + 8 = **1912** expected; the dev-scripts Ruff step passes on both changed scripts.
- **The other five jobs:** `docs` (doc check), `frontend` (typecheck, lint, unit, build, bundle scan, audit policy), `security` (Bandit 44 = 44), `security-history` (39 = 39) and `image-build`, unchanged and expected green.

## 10. Recommendation

**Ready for architectural review, then one commit and CI submission.** Test and fixture code plus documentation only. The checkpoint closes only when the `e2e` job runs the 10 new browser tests, and the whole suite, green on the real stack. Real Groq, Gemini, OpenRouter and OpenAI requests stay **NO-GO**.

```text
PHASE / DATE: 8A-6 multi-provider dashboard E2E verification / 2026-11-11 UTC
BRANCH: feature/phase-8a6-comparison-dashboard on 3424644, uncommitted, not pushed
PROVIDER REQUESTS / DOCKER / DATABASE / .ENV / .ENV.LOCAL: 0 / none / none touched / not read / not read
PRODUCTION CODE: unchanged. EXECUTION LOCKS: disabled as shipped
NEW TESTS: browser 10 (NOT RUN LOCALLY, CI e2e); backend offline 8 (passed)
FRONTEND: typecheck, lint, build, bundle scan PASS; unit 353 passed; listing 72 tests, 0 errors
BACKEND UNIT: 1216 passed. PRIVACY/COST: 489 passed. BANDIT: 44 = 44
MUTATIONS: 8 of 8 fixture mutants caught
NEW RISK: R132 (no dashboard prompt-version control)
CI: NOT RUN on this branch. COMMIT: not authorized, not made. 8A-7: not started
```

## 11. Architectural approval (2026-11-11)

The reviewer accepted the implementation and verification of §1 to §10: **conditional GO for one local commit.** The checkpoint stays open until GitHub CI runs the 10 new browser tests, and the whole suite, green.

The final check before staging confirmed:
- the branch is on `3424644`, and exactly the nine files of §1 differ;
- the comparison project uses the existing run-owned lifecycle; a failed setup deletes only what this run created, matched by id and name;
- the seeder posts to a loopback endpoint only, and every trace is SYNTHETIC;
- the fixture contract (8 traces, 7 attempts, 1,312 / 474 tokens, 5 `price_missing`, 1 `usage_missing`, 6 dropped values, the three 8A-4 prompt versions as separate groups) is pinned by `test_e2e_comparison_fixture.py`, which passed;
- the OpenAI price comes from the existing catalogue, and no price row is written;
- the comparison project id is read at runtime, and the CI-style listing shows 0 errors;
- no production, dependency, migration, CI, guard, allowlist or credential file changed;
- the three user-owned files are unchanged.

The only changes after §10 are documentation (this section, the status line and `PHASE_STATUS.md`), so no test was rerun; the doc check and `git diff --cached --check` were. §10 records the state before this approval. The commit is not pushed; its SHA is reported to the owner and not recorded in the commit itself.

## 12. PR #9 CI failure: the E2E lifecycle gate (2026-11-11)

**Failure.** As reported by the reviewer from GitHub Actions run `42985427705` on PR #9 (`7f393fc`): all 72 Playwright tests passed, including the 10 of `phase8a6.spec.ts`. The global teardown reported `deleted: [1, 2, 3, 4, 5]` and `remaining: []`. The `e2e` job still failed after the tests, because `scripts/qa/e2e_check.py` expected `OWNED_PROJECTS = 4`. The other six jobs passed. This session did not read that run.

**Cause: a gap in this phase's implementation.** §2.3 checked the gate's listing step but missed the two constants in `e2e_check.py` that pin the suite: four run-owned projects in the teardown report, and at least 62 discovered tests. No product code or E2E test was at fault.

**Correction**, authorized as a narrowly scoped QA gate fix:
- `e2e_check.py`: `OWNED_PROJECTS = 5` and `MIN_TESTS = 72`.
- The teardown check is tightened, not relaxed. `deleted` must also hold distinct positive integer ids, so a repeated id or a non-integer value no longer counts as a deleted project. All earlier conditions stay: exactly one setup and one teardown line, the same run id, `remaining == []`, and no `kept`.
- `scripts/qa/tests/test_e2e_check.py` (55 → 65 tests):
  - the defaults are now five projects;
  - new cases: exactly five distinct ids pass; four, six or none fail; repeated, string, float, bool, zero, negative and null ids fail; a remaining, missing or mistyped `remaining` fails; wrong, missing, upper-case or truncated run ids fail;
  - malformed, non-object and twice-reported teardown lines fail; a twice-reported or malformed setup line fails;
  - the minimum is pinned at 72;
  - a drift guard reads `e2e_fixtures.names()` from source and requires its role count to equal `OWNED_PROJECTS`, with every role printed by the Playwright global setup.
- `scripts/qa/tests/test_e2e_gate.sh`: the fake Playwright run reports 72 tests and five projects. The "too few" boundaries move to 71. Three new end-to-end gate cases: four deleted, six deleted, a repeated id.

No E2E test, fixture, teardown, workflow, production code, dependency or credential changed.

**Local verification:**

| Check | Result |
|---|---|
| `python -I -m unittest discover -s scripts/qa/tests -p test_e2e_check.py` | **65 passed** |
| `bash scripts/qa/tests/test_e2e_gate.sh` (offline fakes) | **102 passed, 0 failed** (99 before; the unchanged tree passes 99 of 99) |
| Gate mutants against the self-tests, in private copies, after a passing control | **11 of 11 caught**: count back to 4, raised to 6, distinct, integer, positive and bool checks removed, `remaining`, run and `kept` checks removed, minimum lowered to 62, a second teardown report accepted |
| The new self-tests against the gate as committed in `7f393fc` | they fail (6 tests), as they must |
| Ruff format and E/F/W lint on the two Python files | formatted; no new finding compared with the base |

**Required in CI:** the `e2e` job green with the same 72 tests and five projects deleted; the `security` job's QA self-tests green; all seven jobs green.
