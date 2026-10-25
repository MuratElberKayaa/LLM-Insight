# Phase 4C — Safe Deterministic Evaluation and Synthetic A/B Experiments

- **Date:** 2026-10-26 (UTC; the machine's local clock is UTC+3).
- **Instruction (user, Phase 4C):** implement safe, repeatable deterministic evaluation and the locked MVP A/B workflow under two gates — **Gate A** (contract and data-integrity hardening: D1 rule contract, RE2, JSON Schema safety, D3/D4 review integrity, D5 database uniqueness, ADR for D6) and **Gate B** (experiment persistence, golden dataset, evaluation engine, mock A/B experiments, metrics, T36–T40); stop after Phase 4C.
- **Delivered:** Gate A and Gate B in full on the backend; the dashboard's test-case form and review card updated for Gate A. Gate B was started only after every Gate A check passed (§6).
- **Not done (by instruction or scope):** real OpenAI or any paid call, LLM-as-judge, public deployment, `phase-4` tag, push, Phase 5. No experiment page in the dashboard (not requested by the Phase 4C tasks; listed as a Phase 4 closure prerequisite, §12). D8 (decision actor history) deferred as approved.
- **Untouched:** the home-directory repository, global Git/Node configuration, credentials, the PostgreSQL container and volume (only the additive migrations `0004` and `0005` were applied to the dev DB, §7), the SDK source and lock, the frontend dependencies, the CI workflow, the price catalogue and every stored telemetry, usage and price row.

## 1. Baseline verification (before any change)

| Check | Result |
|---|---|
| Git root | `C:/Users/PC/Desktop/LLM_Insight_Claude_Code_MVP_Paketi` (own repository) |
| Branch / HEAD / tree | `main`, `7fd1528` (Phase 4B), clean; previous commit `888b188` (Phase 4A) |
| Tags | `phase-0` … `phase-3` unchanged at the end |
| PostgreSQL | `llminsight-postgres-1` healthy on `127.0.0.1:55432`; dev DB `0003 (head)`, PostgreSQL 17.11 |
| Dev DB human rows | `test_cases` **0**, `failure_reviews` 0, `test_datasets` 0; duplicate (dataset, review) pairs 0 — verified before changing rule validation (A1) and before creating the index (A5) |
| Recorded baseline | backend 1425, SDK 350, frontend unit 268, E2E 48 passed (Phase 4B report §6) |

## 2. Contract mapping and D1–D9 final dispositions

Read: `CLAUDE.md`, `MVP_MASTER_PLAN.md` (§C, §D1, §E, §F, §H Phase 4, §I), `PRD.md`, `ADR.md`, `ARCHITECTURE.md` (§4.8–4.13, §5, §5.7–5.8, §6–7), `SECURITY.md`, `TEST_MATRIX.md`, `PHASE_STATUS.md`, `SOURCES_AND_RISKS.md`, `reports/phase-4a.md`, `reports/phase-4b.md` (§2.4 D1–D9), migration `0003`, the review/dataset API and repositories, `case_rules.py`, `redaction.py`, `pricing.py`, the test infrastructure and the frontend form/decoders.

Locked elements used: §4.11–4.12 columns and checks (verbatim, migration `0005`); `UNIQUE(experiment_id, test_case_id, prompt_version)`; `passed NULL = not evaluated`; `evaluation_detail ≤ 1000`; the two ledger cost checks; `mode = mock ⇒ synthetic` (service rule, T39); `mode = real` needs `budget_usd` and `LLMI_ALLOW_REAL_LLM=true` (§5); ADR-10 (no LLM judge), ADR-11 (unknown cost never 0), ADR-12 (lineage), ADR-21 (migration procedure, stricter checks allowed without a column change).

| Decision | Final disposition (architectural review) | Implemented as |
|---|---|---|
| D1 rule payload | **Revised** | Versioned contract `llminsight.rule.v1` (ADR-23), §3 |
| D2 dataset endpoints | Accepted | unchanged |
| D3 review transitions | **Revised** | decision frozen once cited, text and references immutable (ADR-24), §5 |
| D4 conversion gate | **Revised** | supported → eligible; rejected → eligible with an independent expected output; pending/inconclusive → refused (ADR-24) |
| D5 one case per review | **Strengthened** | partial unique index + kept advisory lock (ADR-24) |
| D6 dashboard writes | Accepted, recorded | **ADR-22** supersedes the GET-only restriction for the four writes only |
| D7 body limits | Accepted | unchanged (now with exact numbers, §3) |
| D8 decision actor | **Deferred** | not implemented; no identity system added (R55) |
| D9 structured-field refusal | Accepted | unchanged; also applied to `Decimal` values |

ADR-25 records the Gate B experiment decisions (mock-only execution, identity, provenance, budget, report rules). No locked ADR was altered; the stop condition (a mandatory decision conflicting with a locked ADR) was not met.

## 3. Gate A — rule contract `llminsight.rule.v1` (A1)

```text
{"version": 1, "type": "json_schema", "schema": <draft 2020-12 schema: object | boolean>}
{"version": 1, "type": "field_match", "fields": ["<dotted.path>", ...]}      1-50 paths
{"version": 1, "type": "exact", "output_format": "text" | "json"}
{"version": 1, "type": "regex", "pattern": "<RE2>", "match": "full" | "search"}
```

- Stored inside the existing `rule` JSONB (the `rule->>'type'` check is unchanged). Every key is required, no other key is accepted; `version` must be the JSON integer `1` (not `true`, `1.0` or `"1"`). Unknown versions and the unversioned Phase 4B payload are refused on write (`422 ["rule"]`) and reported `rule_unsupported` (not evaluated) by the engine — never reinterpreted. The dev DB held 0 cases.
- **Output representation:** the raw output text, valid Unicode, ≤ 32 KiB UTF-8. Structured rules parse it with `app/core/exact_json.py`: non-integers are exact `Decimal`s, NaN/Infinity and duplicate keys are refused, unpaired surrogates are refused, number literals ≤ 64 characters with |exponent| ≤ 400; depth ≤ 32, ≤ 10,000 values.
- **Equality:** key order ignored; array order significant; booleans never numbers (`True == 1` is not used); numbers by exact value (`1 == 1.0 == 1E0`, `0.1 ≠ 0.1000000000000000000001`); strings by code point (no trimming, case folding, NFC/NFD normalization).
- **Missing expected output:** every type needs it; without it `passed = NULL` with `expected_output_missing` (T40). A present expected output must satisfy its own rule (refused on write otherwise).
- **Malformed output:** not JSON / duplicate keys / NaN → `false` `output_not_json`; non-object for `field_match` → `false` `output_not_object`; text that is not valid Unicode → `false` `output_invalid_text`; over a limit → `NULL` (`output_too_large`, `output_limits_exceeded`, `evaluation_limit_exceeded`).
- **Regex target:** the raw output text; `full` = the whole text must match (`fullmatch`), `search` = a match anywhere; inline flags only.
- **Exact numbers end to end:** request bodies are parsed with `Decimal` (`json_body.read_json`), assertions are written as exact JSON text cast to `jsonb`, read back as `jsonb::text`, returned with `ExactJSONResponse`. Integration test: `0.1000000000000000000001` and `1.10` survive POST, storage and GET; `1e999`, 70-digit literals, NaN and Infinity are refused. The dashboard form refuses decimals JavaScript would round (R64).

## 4. Gate A — RE2 regex safety (A2)

- **Pin:** `google-re2==1.1.20251105` (2025-11-05; cp312 wheels for win_amd64 and manylinux_2_28 x86_64; no runtime dependency). Installed wheels-only with the lock as constraint.
- **Verified behaviour (installed source and probes):** a parse error raises `re2.error`; there is **no fallback** to Python `re`. Refused: backreferences (`(a)\1`, `(?P=n)`), lookahead/lookbehind, possessive `a++`, atomic groups, `\Z`, repetition counts above 1000. By default RE2 **logs the rejected pattern to stderr**; `log_errors=False` is set (tested with `capfd`).
- **Bounds:** pattern 1–1000 characters without control/invisible characters; compiled program ≤ **1500** instructions; `max_mem` 2 MiB; `never_capture`; input ≤ 32 KiB. Compiled programs are cached by `re2`'s bounded LRU (128).
- **Measured workload (dev machine, Windows, Python 3.12.6):** classic catastrophic patterns (`(a+)+$`, `(a|aa)*b`, `(x+x+)+y`, `(.*a){12}`, …) on 32–64 KiB: **0.0–0.5 ms**. Worst case found — a program that exhausts the DFA cache and forces the NFA: 982 instructions **353 ms**, 1471 instructions **528 ms** on 32 KiB (and ~700 ms for ~1000 instructions on 64 KiB, which is why the caps are 32 KiB / 1500). Linear, bounded; tests assert < 1 s (pathological) and < 5 s (worst case at the caps) for CI.
- **No uncontrolled `re`:** user patterns reach only `safe_regex.compile_pattern`; a static test allows `re.compile` in evaluation/experiment code only with constant patterns; `re2` may be imported only by `safe_regex.py`.

## 5. Gate A — JSON Schema safety (A3) and review integrity (A4, A5)

**JSON Schema (`safe_schema.py`).** Pin `jsonschema==4.26.0` (+ `referencing` 0.37.0, `jsonschema-specifications` 2025.9.1, `rpds-py` 2026.9.1, `attrs` 26.1.0). Findings in the installed source that drove the design: the default registry retrieves unknown URIs with **`urlopen`**, and `check_schema` uses a format checker whose `regex` format calls **`re.compile`**. Therefore:
- one dialect, **draft 2020-12**; `$schema` absent or exactly its URI, root only;
- a **keyword allowlist applied to every nested schema** (walker follows only real subschema keywords, so a property *named* `pattern` or `$ref` stays data); refused with a fixed message: `pattern`, `patternProperties`, `format`, `content*`, `$id`, `$anchor`, `$dynamicRef`, `$dynamicAnchor`, `$recursive*`, `$vocabulary`, `definitions`, `dependencies`, `unevaluated*`, and any unknown keyword;
- `$ref` only `#/$defs/<name>` for an existing root definition; reference graph **acyclic**; ≤ 1000 subschemas, ≤ 100 definitions, ≤ 5000 subschemas once expanded (a 2^19 doubling chain is refused);
- meta-validation with a validator built from the bundled meta-schema, **`format_checker=None`**, and a registry `Registry().combine(bundled meta-schemas)` that **cannot retrieve**; `check_schema` and `RefResolver` are never used (static test);
- evaluation: `Decimal`-aware types (`integer` includes `1.0`, booleans are never numbers), exact `multipleOf` (fractions), `uniqueItems` by canonical forms (5000 objects without an O(n²) scan), ≤ 200,000 keyword applications per evaluation (`evaluation_limit_exceeded`), ≤ 21 reported errors; error *messages* (which quote the instance) are never used.
- Tests run with sockets, DNS and `urlopen` blocked (any attempt fails the test) and with `re.compile` patched to fail for schemas containing regex keywords at any depth; a validator built directly with a remote `$ref` (bypassing the checks) raises a refusal, never a request.

**Review integrity (ADR-24, migration `0004`, no column).**
- API: conversion needs `hypothesis_supported`, or `hypothesis_rejected` with an `expected_output` (`422 ["expected_output"]` otherwise); `pending` → `409 review_not_decided`, `inconclusive` → `409 review_not_eligible`. PATCH that would change or clear the decision of a cited review → `409 review_decision_frozen`, nothing written; status changes stay possible within §4.9.
- Database: trigger `failure_reviews_integrity` (every column except status, decision, `updated_at` immutable; decision frozen when cited) and trigger `test_cases_review_eligibility` (eligible decision, rejected ⇒ expected output, same trace, same project; review read `FOR SHARE`). Errors carry SQLSTATE 23000 and a constraint name that the API maps to the same `409`s.
- Partial unique index `uq_test_cases_dataset_id_origin_review_id` on `(dataset_id, origin_review_id) WHERE origin_review_id IS NOT NULL`; the advisory lock and friendly `409 duplicate_test_case` stay.
- The `0004` upgrade first counts duplicate pairs and ineligible existing cases and refuses (version stays `0003`, rows untouched) if any exist; downgrade drops index and triggers only.
- **Concurrency evidence:** (a) over real HTTP, ten rounds of a decision PATCH and a conversion released together by a barrier: every round ends either (PATCH 409, case 201, decision still supported) or (PATCH 200, case 409, no case) — never both; (b) with two raw psycopg connections bypassing the API: an uncommitted case insert blocks a decision UPDATE, which then fails `failure_reviews_decision_frozen`; an uncommitted decision change blocks a case insert, which then fails `test_cases_review_eligibility`; (c) eight simultaneous conversions with the API lock and duplicate check **disabled**: one `201`, seven `409 duplicate_test_case`, one row (the index alone).

**A6:** ADR-22 records the approved local Server Actions write architecture (§2; `ADR.md` §4).

## 6. Gate A verification (A7) — passed before Gate B started

| Check | Result |
|---|---|
| Backend full suite | **1643 passed**, 0 failed, 0 skipped (194 s), exit 0 |
| Focused suites | rule contract, RE2, schema/no-network, engine, exact JSON, static boundaries, integrity (API + raw SQL concurrency), migrations incl. `0004` lifecycle — all within the run above |
| Phase 4B regressions | `test_dataset_api.py`, `test_review_api.py` (T33–T35) pass on the Gate A code |
| Frontend | typecheck 0, lint 0, unit 274/274, build 0 |
| Phase 4B browser E2E | 4/4 passed on the real stack |
| Dev DB | `0003 → 0004`; every table's row count and content checksum identical; `alembic check` clean |
| Docs | `verify_phase0.py` 732 PASS, 0 FAIL |

## 7. Gate B — persistence, golden dataset, engine, experiments (B1–B5)

**B1 — migration `0005`** creates `experiments` and `experiment_results` column for column (the contract test parses §4.11–4.12), all checks, `UNIQUE(experiment_id, test_case_id, prompt_version)`, `INDEX(dataset_id)`. Triggers (ADR-25): `experiments_integrity` (only `finished_at` may change, once) and `experiment_results_integrity` (append-only; `prompt_version` ∈ the experiment's two versions; case in the experiment's dataset; mock ⇒ synthetic; nothing after `finished_at`). Downgrade refuses while any experiment exists. Tested: up/down/up cycle, `0004 → 0005` with reviews and cases byte-identical, refused downgrade with an experiment, every check and trigger, cascades from dataset and project. **Dev DB:** `0004 → 0005`, existing tables identical by checksum, two new empty tables, `alembic check` clean.

**B2 — golden dataset `golden-synthetic-v1`** (`backend/app/services/experiments/golden.py`): 12 SYNTHETIC cases, every input prefixed `SYNTHETIC Gnn:`, stable keys, scripted answers for `golden-prompt-v1` / `golden-prompt-v2` and the documented expected outcome:

| Key | Demonstrates | v1 | v2 |
|---|---|---|---|
| G01 | JSON Schema pass | pass | pass |
| G02 | missing required field | `schema_violation` | pass |
| G03 | prose instead of JSON | `output_not_json` | pass |
| G04 | number instead of enum string | pass | `schema_violation` |
| G05 | field match, extras ignored | pass | pass |
| G06 | value differs / `"1"` vs `1` | `field_mismatch` | `field_mismatch` |
| G07 | exact text (trailing period) | pass | `exact_mismatch` |
| G08 | exact JSON (key order, `3.0 == 3`, array order) | pass | `exact_mismatch` |
| G09 | RE2 full match | pass | `regex_no_match` |
| G10 | RE2 search with `(?i)` | pass | `regex_no_match` |
| G11 | no expected output (T40) | not evaluated | not evaluated |
| G12 | truncated JSON; simulated timeout | `output_not_json` | not evaluated (`provider_timeout`) |

Evidence: every input passes redaction unmasked, every assertion passes the D9 checks and contract v1, and the engine reproduces all 24 documented outcomes (unit and integration). `scripts/dev/seed_golden_dataset.py` loads it through the real API (loopback only, token from settings, never printed); verified live in run-owned projects that were then deleted (dev DB content identical afterwards); a second seed is refused `409`.

**B3 — engine** (`services/evaluation/engine.py`): pure and deterministic (20 repeated evaluations identical, inputs unmodified), stable codes, `NULL` preserved for not-evaluable cases, details `"<code>: <fixed text>"` ≤ 1000 characters without output or expected values (keys from the output shown as `*` or only counted; tested with e-mail-, key- and marker-shaped content). No LLM, similarity or invented score.

**B4 — mock A/B experiments** (`POST /api/v1/experiments`, `services/experiments/{mock_provider,runner}.py`): cases in id order under A then B (identical ids), one simulated attempt each (no retries; one row = one attempt), scripted SYNTHETIC answers keyed by (prompt version, input hash), unknown inputs → `mock_no_scripted_output` (no invented answer). `mode: real` → `422` without budget, else `409 real_llm_not_allowed` / `409 real_mode_unavailable`; nothing written. Budget: pre-check from estimates (`budget_unverifiable` / `budget_exceeded`, nothing written) and a run-time guard (`budget_exhausted`, tested with an underestimating provider). Cost: synthetic tokens at the stored price of (`openai`, model) in force at `started_at`, ledger rules, never 0. One transaction (storage failure → `503`, 0 rows). Model output never stored. Repeated runs give identical results; four concurrent runs give four complete, identical experiments.

**B5 — report** (`GET /api/v1/experiments/{id}`, `llminsight.experiment_report.v1`): per version passed/failed/not evaluated (+ by code), `pass_rate` with denominator `passed + failed`, p50/p95 with `n` and `p95_low_sample`, token sums with `n`, exact `known_cost_usd` (decimal string) with computed/unknown counts and `complete`; quality compared only on cases evaluated under both versions, no `higher_pass_rate` when any one-sided `NULL` exists; cost compared only when both versions are fully priced; latency per version only; constant limitations; `provenance` label and `simulated_provider: true`.

Live result on the real stack (golden dataset, model `gpt-5-mini`, catalogue price `openai-2026-10-24`; SYNTHETIC estimate, nothing spent): v1 **7 passed / 4 failed / 1 not evaluated**, pass rate 0.636364 (7/11), known cost `0.0004682500` (12/12 priced); v2 **4 / 6 / 2**, pass rate 0.4 (4/10), known cost `0.0004652500` (11/12 priced, 1 `usage_missing`); quality `partial` (10 paired, 1 excluded by design, 1 unpaired: G12 timeout) → no better version named; cost `incomplete` → no difference reported; case ids identical; 24/24 results `synthetic`.

## 8. T33–T40 (exact `TEST_MATRIX.md` criteria)

| ID | Status | Evidence |
|---|---|---|
| T33★ | PASS (phase-4b), re-run | `test_review_api.py` on the Gate A code |
| T34★⛔ | PASS (phase-4b), re-run | `test_dataset_api.py` (missing/false/null/string/number `approved` → `422`, 0 rows) |
| T35★⛔ | PASS (phase-4b), re-run | `test_dataset_api.py` (masking before insert, `redaction_applied = true`) |
| T36★ | **PASS (phase-4c)** | unit: valid output passes the JSON Schema rule and the field rule (`test_evaluation_engine.py`); integration: G01/G05 pass under both versions |
| T37★ | **PASS (phase-4c)** | unit: six schema violations fail with a `schema_violation: …` detail naming the keyword and path; integration: G02 v1 stored `passed = false` with `evaluation_detail` naming `required` |
| T38 | **PASS (phase-4c)** | integration: both prompt versions on the identical 12 case ids (database rows and `ids_identical_across_versions`); unit: A then B per case |
| T39★ | **PASS (phase-4c)** | integration: every one of 24 stored results `synthetic`, report label `synthetic`; trigger refuses `real` in a mock experiment |
| T40 | **PASS (phase-4c)** | unit: all four rule types (five variants) × three outputs → `passed = NULL` `expected_output_missing`; report excludes `NULL` from the denominator; integration: G11 `NULL` under both versions, v1 denominator 11 of 12 |

T31 re-run with `0004`/`0005` (status unchanged). T41/T42 re-verified by the full E2E and bundle scan (unchanged). **T48 not re-checked for Phase 4** (no experiment page, §12).

## 9. Final system validation (after the last code change; commands from the named directory)

| Command | Result |
|---|---|
| `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` (backend) | **1705 passed**, 0 failed, 0 skipped, exit 0 (1 pre-existing Starlette/httpx deprecation warning) — see §13 for the final re-run |
| same (sdk) | **350 passed**, 0 failed, 0 skipped (90 s), exit 0 |
| `npm run typecheck` / `npm run lint` (frontend) | exit 0 / exit 0 (0 warnings) |
| `npm test` (frontend) | **274 pass**, 0 fail, 0 skipped, exit 0 |
| `npm run build` / `npm run check:bundle` | exit 0 / 546 files scanned, findings 0 |
| `npm audit --omit=dev` | 0 vulnerabilities |
| `npx playwright test` (real backend on :8000, Edge) | **48 passed**, 0 failed (1.4 min); teardown deleted the run's projects, `remaining: []` |
| `ruff check .` / `ruff format --check .` (backend, sdk) | exit 0 / exit 0 |
| `python -m mypy src` and `--config pyproject.toml ../demo_app` (sdk, strict) | no issues (8 and 3 files) |
| `python -m pip check` (backend, sdk) | no broken requirements; backend lock = `pip freeze` (51 pins) |
| `alembic upgrade head` / `alembic check` (dev DB) | `0003 → 0004 → 0005`; "No new upgrade operations detected" |
| migration cycle (disposable DBs) | `test_migrations.py` 45 passed: base ↔ head through `0004`/`0005`, data preservation, refused upgrade/downgrade |
| `docker compose config --quiet` / `--profile app config --quiet` | exit 0 / exit 0 |
| `python -I scripts/verify_phase0.py --quiet` | 732 PASS, 0 FAIL |
| Secret/privacy scan (399 tracked and new files, raw bytes) | 0 occurrences of the five local secret values; 0 key-shaped strings (`sk-…`, `lli_…`, bearer, private key) |
| Backend dev log during E2E and the live seed | 0 tracebacks; no case input or scripted output text |
| `git diff --check` (tracked and new files) | clean (line-ending notices only, `core.autocrlf=true`) |
| Dev DB after all runs | content checksums identical to the post-`0005` state |

**Mutation checks** (scratch script; each file restored byte for byte, md5-verified): **15/16 killed** — RE2 logging on, schema `pattern` allowed, remote `$ref` accepted, default retrieving registry, T40 bypass, boolean = number, floats in bodies, `NULL` in the denominator, winner on partial evidence, partial cost comparison, mock labelled real, budget pre-check skipped, unknown cost → 0, rejected review without expected output, unversioned rule accepted. **Survived:** removing `inconclusive` from the API's eligible set — the migration-0004 trigger still refuses it and the API maps that to the same `409 review_not_eligible` (defence in depth; the trigger is covered by the raw-SQL tests).

**No real OpenAI request:** the backend venv has no `openai` package; a static test forbids importing it anywhere in `app/`; evaluation and experiment code import no network library (static); an experiment ran with every non-loopback socket and DNS lookup blocked (0 attempts); SDK tests keep their offline guard; no API key was read or used.

**Environment:** Windows 11, Python 3.12.6 (backend and SDK venvs), Node 22.23.4 (project-scoped), PostgreSQL 17.11 (Docker), Microsoft Edge (Playwright), `jsonschema` 4.26.0, `google-re2` 1.1.20251105.

**New and changed tests.** Backend +280 (1425 → 1705): new `test_exact_json.py` 17, `test_safe_regex.py` 35, `test_safe_schema.py` 43, `test_evaluation_engine.py` 59, `test_evaluation_boundaries.py` 11, `test_golden_dataset.py` 6, `test_experiment_runner.py` 9, `test_experiment_report.py` 9, `test_phase4c_integrity.py` 16, `test_experiment_api.py` 26; `test_case_rules.py` rewritten for v1 (53), `test_migrations.py` 36 → 45, `test_dataset_api.py` updated to v1; pinned route/table sets updated for the documented additive endpoints and tables. Frontend +6 (`phase4c.test.tsx`) and updated `phase4b.test.tsx`; the Phase 4B E2E now expects the v1 rule and checks the frozen decision.

## 10. Risks (`SOURCES_AND_RISKS.md`)

R57 **resolved** (rule contract, RE2, schema safety), R58 **resolved** (database index). New: R60 regex worst case ~0.5 s per evaluation and synchronous runs (bounded), R61 the JSON Schema subset refuses some legitimate schemas, R62 mock-only synthetic results and synthetic cost estimates, R63 no experiment page yet (open), R64 the form refuses decimals JavaScript would round, R65 the mock knows only the golden inputs. Still open from earlier: R54 (no login, loopback trust), R55 (self-declared identities; D8 deferred), R56 (heuristic redaction).

## 11. Files

New backend: `app/core/exact_json.py`, `app/services/evaluation/{__init__,rules,engine,safe_regex,safe_schema,json_values}.py`, `app/services/experiments/{__init__,golden,mock_provider,runner,report}.py`, `app/api/experiments.py`, `app/repositories/experiments.py`, `app/models/experiment.py`, `alembic/versions/0004_phase4c_review_case_integrity.py`, `alembic/versions/0005_phase4c_experiments.py`, the tests of §9. Changed backend: `app/api/{datasets,reviews,json_body}.py`, `app/repositories/{datasets,reviews}.py`, `app/models/{__init__,dataset,review}.py`, `app/services/case_rules.py`, `app/main.py`, `pyproject.toml`, `requirements.lock`, `tests/integration/conftest.py`, existing tests as listed. New frontend: `tests/unit/phase4c.test.tsx`. Changed frontend: `lib/{review-forms,json,action-state}.ts`, `lib/api/{result,review-types}.ts`, `components/datasets/{test-case-form,case-list}.tsx`, `components/reviews/{decision-form,review-section}.tsx`, the test-case page, `tests/unit/phase4b.test.tsx`, `tests/e2e/phase4b.spec.ts`. New script: `scripts/dev/seed_golden_dataset.py`. Docs: `ADR.md` (ADR-22…25), `ARCHITECTURE.md` (§4.9–4.13 notes, §5 rows, §5.8, new §5.9, §6, §7), `SECURITY.md`, `TEST_MATRIX.md`, `SOURCES_AND_RISKS.md`, `PHASE_STATUS.md`, this report.

## 12. Phase 4 closure prerequisites

1. Architectural review of Phase 4C (ADR-22…25, the rule contract, the experiment report rules).
2. A read-only experiment page in the dashboard (report decoded strictly, SYNTHETIC/simulated labels, partial/incomplete comparisons shown as such, no winner on incomplete evidence) with unit and E2E tests, including the Phase 4 re-check of **T48** (R63).
3. The end-to-end demo loop in the UI: failed trace → review → decision → approved case → experiment → comparison (`DEMO_SCRIPT.md` step 5), then the Phase 4 feature freeze, closure report, CI run and the `phase-4` tag (not created here).
4. Optional, needs its own approval: a real-provider executor (budget, `LLMI_ALLOW_REAL_LLM`, explicit per-run approval); decision-actor history (D8).

## 13. Final re-run and verdict

The backend suite was re-run after the last code and documentation change (adding `app/services/experiments/__init__.py`, and the `ARCHITECTURE.md` §4 notes that the migration contract test parses): `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` → **1705 passed, 0 failed, 0 skipped** (209 s), exit 0; Ruff clean; secret scan 0/0; `git diff --check` clean; `verify_phase0.py` 737 PASS, 0 FAIL. Frontend, SDK and E2E inputs did not change after their runs in §9.

**Verdict: PASS for the Phase 4C scope** on the evidence above. Gate A and Gate B passed; T36–T40 PASS (phase-4c); T33–T35 re-verified. No tag, no push, no real or paid call, Phase 5 not started. Awaiting architectural review.
