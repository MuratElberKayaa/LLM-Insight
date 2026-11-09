# Phase 8A-1 — Fail-closed free-plan cost and Groq key detection

**Date:** 2026-11-09 UTC. **Status: implemented offline and committed locally on the feature branch; not pushed; not yet run in CI.**

**Recommendation for checkpoint 8A-2 (adapter): conditional GO**, once the first CI run of this code is green and the reviewer authorizes the checkpoint. **Real Groq and OpenRouter requests remain NO-GO.**

Provider requests: **0**. No credential, billing interaction, deployment, Docker command or database connection. No `.env` was read. No adapter, migration, Experiments code, Gemini code or dependency changed. One local commit, made by the closure instruction. No push, PR, merge, stash or reset.

Evidence labels: **EXECUTED** (run in this session on Windows 11, Python 3.12.6), **COLLECTED, NOT RUN** (needs PostgreSQL, so CI only), **REQUIRES GITHUB**.

---

## 1. Repository safety

| Item | Value |
|---|---|
| Starting branch | `feature/phase-7-gemini-demo` at `e581dc4`, already merged by PR #3 |
| GitHub `main` | `91c4d7d`; local `main` left stale at `4350306` |
| Working branch | **`feature/phase-8a-free-provider-safety`**, created from `origin/main` at `91c4d7d` |
| How the work moved | `git fetch origin main`, then `git switch --no-track -c`. `e581dc4` and `91c4d7d` have the identical tree `a5a5ccd…`, verified before switching, so the uncommitted files carried over untouched |
| Backup | The two uncommitted docs were copied to the scratchpad first; hashes matched after the switch |
| Stashes / resets / rebases | None |
| User files | Untracked and unchanged: `8AE86D51…`, `B8E8A61C…`, `31FFD28B…` |

Phase 7 history is unchanged. Phase 7C's item in `PHASE_STATUS.md` remains open for the reviewer, and **Phase 7D is not complete**.

The 8A-0 report now records the review in §16: the five decisions, the additional requirements, the ADR renumbering and the checkpoint renumbering.

## 2. What changed

| Area | Change |
|---|---|
| Pricing (ADR-40) | `APPLICABILITY` gains `groq` and `openrouter`; a stored price never applies |
| Detection (ADR-41) | `(?<![A-Za-z0-9])gsk_[A-Za-z0-9]{20,}` in `sensitive.py` and `_guards.py`; detector `groq_key` in `secret_scan.py`, reused by the history gate; added to `verify_phase0.py`. Free-text redaction shares the backend pattern, so it gained coverage with no code change |
| History baseline | Only `tool.detector_digest` changed, and `REVIEWED_BASELINE_SHA256` with it. No entry added, removed or reclassified. Done by hand under the documented change rule |
| Docs | `ADR.md` §13 (ADR-40, ADR-41), `TELEMETRY_CONTRACT.md` §3 and §3.1, `PRICING.md` §3, `SECURITY.md` §9, risks R110 and R111 |

**Files changed (19):**
- Code: `backend/app/services/pricing.py`, `backend/app/services/ingestion/sensitive.py`, `sdk/src/llm_insight_sdk/_guards.py`, `scripts/qa/secret_scan.py`, `scripts/qa/secret_history_gate.py`, `scripts/verify_phase0.py`, `security/secret-history-baseline.json`.
- Tests: `backend/tests/unit/test_pricing.py`, `backend/tests/unit/test_metadata_privacy.py`, `backend/tests/unit/test_redaction.py`, `sdk/tests/unit/test_guards.py`, `scripts/qa/tests/test_secret_history_gate.py`, and new `backend/tests/integration/test_phase8a_free_provider_safety.py`.
- Docs: the five named above and `PHASE_STATUS.md`.

Plus this report and `reports/phase-8a-0.md`, both untracked.

## 3. The `attrmap.v1` determination (for review)

**Can security-only pattern hardening preserve `attrmap.v1`? Yes in substance, but not under the contract's existing wording, so an amendment was required.**

- `TELEMETRY_CONTRACT.md` §3 said any change to these rules after the `phase-1` tag needs `attrmap.v2`. Read literally, this change needed `v2`.
- `attributes_schema_version` records how attributes map to stored fields. This change leaves every attribute name, alias, type, format, limit, fallback and column meaning the same. It can only withhold more values.
- There is no precedent: Git history shows no credential pattern changed since the tag.
- ADR-41 therefore adds one exception to that sentence, limited by six conditions, and amends ADR-15 for that case only. Anything else still needs `v2`. **The backend remains authoritative**, and `SCHEMA_VERSION` is asserted to stay `llminsight.attrmap.v1`.

### 3.1 Review closure of ADR-41

The review accepted the exception in principle, subject to six conditions. The first check found that condition 6 failed on exact text: ADR-41 said "ADR-15 and ADR-19 unchanged". Three edits to ADR-41 were approved and applied: the heading, the status clause and a Supersession bullet. No code changed as a result.

| # | Condition | Result | Basis |
|---|---|---|---|
| 1 | No schema, mapping, alias, type or stored-version change | PASS | No diff in `attrmap.py`, `normalize.py`, `usage.py`, `service.py`, `app/models`, `alembic` or `app/repositories`; a test pins the version |
| 2 | Detection only becomes stricter | PASS | One pattern added to each list; `contains_credential` is `any(...)` |
| 3 | SDK and backend equivalent | PASS | Identical regex; a test compares the source text |
| 4 | Non-secret IDs and metadata compatible | PASS | Model IDs, response IDs, provider names and ten lookalikes tested |
| 5 | Stored telemetry not rewritten or reinterpreted | PASS by code | Detection runs at ingest and on new human input only; no migration, no read path. Stored rows were **not inspected** |
| 6 | Explicitly amends ADR-15, only for monotonic hardening, with no future mapping changes authorized | PASS after the approved correction | ADR-41 heading, status and Supersession bullet |

### 3.2 Local secret scanner findings

`secret_scan.py`, run with no env file, exits 1 with 41 hits on both a clean clone at `91c4d7d` and this repository. Every hit was matched by object, detector, offset and length to a reviewed entry of `security/secret-history-baseline.json`. **Unexplained: 0.** Only detector names, paths, classifications and counts were printed.

| Location | Detector | Classification | Hits |
|---|---|---|---|
| Historical blobs | `url_userinfo` | `F9-DOC-EXAMPLE` | 20 |
| Historical blobs | `url_userinfo` | `F9-TEST-FIXTURE` | 8 |
| Current files | `url_userinfo` | `F9-DOC-EXAMPLE` | 5 |
| Current files | `url_userinfo` | `F9-TEST-FIXTURE` | 8 |

None comes from `groq_key`. The exit code is non-zero because this command's `ALLOW` list is narrower than the reviewed baseline; CI uses the baseline, through `secret_history_gate.py`. The classifications are those of the earlier reviewed baseline and were not reviewed again here.

## 4. Design points to check

- **Key format is undocumented.** `gsk_` appears only as a placeholder in Groq's security page, and third-party scanners disagree on the length (40 or more, 48, 52). The rule uses a minimum of 20 and no exact length.
- **The look-behind deliberately omits `_`,** so a key pasted after `GROQ_` is still caught.
- **Zero rates are covered.** A row with a price of 0 does not yield a computed 0.
- **The Gemini rule and OpenAI pricing are unchanged,** and a test pins the full set of `APPLICABILITY` keys.

## 5. Executed results

Tests were written first and failed as expected: **34 backend, 19 SDK, 13 gate failures**. One backend failure was my own wrong attribute name in a test, fixed in the test. The closure added 8 conflicting-catalogue pricing cases and a local-scanner test.

The full suite below finished at 13:05 local time. After that only `ADR.md`, `PHASE_STATUS.md`, `SOURCES_AND_RISKS.md` and this report changed. Backend unit, QA self-tests, Bandit, the history gate, the detector scan, `verify_phase0` and `git diff --check` were run again after those edits, with the same results. The SDK suite was not run again because no SDK file changed.

| Check | Exact command (from the named directory) | Result |
|---|---|---|
| Backend unit | `backend`: `python -m pytest tests/unit -q -rs -p no:cacheprovider` (throwaway venv from `requirements.lock`) | **1173 passed, 0 failed, 0 skipped** |
| Targeted backend files | `backend`: `pytest tests/unit/test_pricing.py tests/unit/test_metadata_privacy.py tests/unit/test_redaction.py` | 446 passed |
| Backend Ruff | `backend`: `ruff check .`, `ruff format --check .` | PASS, 142 files (after fixing one line in the new test) |
| SDK, non-editable | `sdk`: `pytest tests/unit tests/integration/test_live_gemini_content.py -q` with no backend interpreter | **1158 passed, 0 failed, 25 skipped** (the 25 are the database-backed 7C tests, which pass only in CI) |
| `test_install.py`, non-editable | `sdk`: `pytest tests/unit/test_install.py -q` | 7 passed |
| SDK guards | `sdk`: `pytest tests/unit/test_guards.py -q` | 145 passed |
| SDK Ruff | `sdk`: `ruff check .`, `ruff format --check .` | PASS, 48 files |
| mypy strict | `sdk`: `mypy src`, `mypy --config-file pyproject.toml ../demo_app` | PASS, 12 and 9 files |
| QA self-tests | root: `python -I -m unittest discover -s scripts/qa/tests` | 214 ran, OK, 15 skipped |
| Bandit gate | Bandit 1.9.4 raw scan, then `bandit_baseline_check.py evaluate` | PASS; High 1, Medium 5, Low 38, 44 of 44; baseline unchanged, no `nosec` |
| History baseline | root: `secret_history_gate.py validate-baseline` | PASS, 38 entries |
| History gate, clean clone | `coverage`, `scan`, `evaluate` on a fresh clone of GitHub at `91c4d7d` | **PASS: 38 findings, all accepted, none from `groq_key`**; coverage COMPLETE, 1086 blobs |
| Local `secret_scan.py` | `--repo <clean clone>` and `--repo .`, no env file, classified against the baseline (§3.2) | Exit 1, 41 hits, all reviewed; 0 unexplained; none `groq_key` |
| Changed files | Detectors over the 21 changed and new files | 0 matches |
| `verify_phase0` regex | Real regex, extracted and applied to synthetic strings | Detects 20 and 51 characters; ignores the bare prefix and 19 |
| Doc check | root: `python -I scripts/verify_phase0.py` | 828 PASS, 0 FAIL |
| `git diff --check` | root | Clean |

**Mutation check** (scratch copy, offline): **21 mutations, 21 caught, 0 survived**. They removed or weakened each pricing rule, and changed each detector's presence, length, look-behind, character set and case, plus the mapping version and SDK-to-backend drift. One planned mutation exposed a weak test of mine, whose lookalike was too short, and that test was strengthened first.

**Coverage against the instruction:**

| Required | Tests |
|---|---|
| Positive detection | Six lengths, seven embedded forms, in the SDK, backend and scanner |
| Non-leakage | SDK span and logs; backend attributes, span name, logs and free-text masking; gate summary; DB, API and logs in CI |
| Safe lookalikes | Ten forms, including short, hyphenated, underscored, upper-case and inside a word |
| Compatibility | Free-plan model IDs, response IDs and provider names stay valid; existing suites unchanged and passing; mapping version pinned |
| Free usage never priced | Listed, zero and conflicting prices, requested and response model, OpenAI attributes, the ledger step, seeded catalogue rows in CI |

## 6. Not run

| Item | State |
|---|---|
| `backend/tests/integration/test_phase8a_free_provider_safety.py`, 5 tests | **COLLECTED, NOT RUN. Not a pass.** These fixtures read `.env` and connect to PostgreSQL, so they were never run from the repository |
| Rest of backend integration, 683 tests | COLLECTED, NOT RUN; 688 collected in total |
| Backend `alembic` | Not run; no migration exists to change |
| Frontend suites and E2E | Not run; no frontend file changed |
| pip-audit | Not run; no dependency changed |
| All seven CI jobs | **REQUIRES GITHUB** |

Collection and the required-database rule were checked in a scratch copy with no `.env`, and with the database address on a dead port. With `LLMI_TEST_REQUIRE_DB=1`, the new tests **error** instead of skipping (`PostgreSQL required`). Without it, 5 skip.

The five tests are in the mandatory suite: the CI `python` job sets `LLMI_TEST_REQUIRE_DB=1` and runs all of `backend/tests` with no marker or keyword exclusion. Their IDs:

- `tests/integration/test_phase8a_free_provider_safety.py::test_a_seeded_price_never_prices_a_free_plan_call[listed-groq--api.groq.com]`
- `tests/integration/test_phase8a_free_provider_safety.py::test_a_seeded_price_never_prices_a_free_plan_call[listed-openrouter-:free-openrouter.ai]`
- `tests/integration/test_phase8a_free_provider_safety.py::test_a_seeded_price_never_prices_a_free_plan_call[zero-groq--api.groq.com]`
- `tests/integration/test_phase8a_free_provider_safety.py::test_a_seeded_price_never_prices_a_free_plan_call[zero-openrouter-:free-openrouter.ai]`
- `tests/integration/test_phase8a_free_provider_safety.py::test_a_groq_key_never_reaches_the_database_the_api_or_the_logs`

**Remaining CI gates:** `docs`, `python` (including these five tests), `frontend`, `security-python`, `security-history`, `image-build` and `e2e`. None has run on this code.

## 7. Remaining risks

| Risk | Note |
|---|---|
| R110 | Detection is a heuristic on an undocumented format; a key in another format is not detected |
| R111 | ADR-41's exception was accepted in principle and is limited to credential-detection hardening. A span re-exported after this change whose stored row holds a Groq-shaped value keeps that row. Stored data was **not inspected** |
| New integration tests | Expectations were checked against code and contract, not by execution, and could fail on first contact |
| History gate digest | CI's `security-history` job checks the new digest against the committed baseline on first run. It passed here on a clean clone |
| Environment | All local results are Windows; CI is Linux |
| Header line | The "Last updated" line of `PHASE_STATUS.md` still describes 7C as pending. Left for the reviewer's 7C closure |

R102 (npm advisory, deadline 2026-11-30 UTC) and R105 remain open. The Gemini Free Tier evidence guard expires 2026-11-11 13:00 UTC and was not changed.

## 8. Recommendation

**Checkpoint 8A-2 (adapter, offline): conditional GO.** The conditions are:
1. The owner pushes this commit and opens the draft PR, and all seven CI jobs are green, including the 5 new integration tests and `security-history`.
2. The reviewer authorizes the checkpoint. It has not started.

Nothing in 8A-1 blocks the adapter's design: `TextGenerationAdapter`, the attribute mapping and the read APIs are unchanged.

**Real Groq and OpenRouter requests: NO-GO.** Blockers B1 to B7 of `reports/phase-8a-0.md` remain, as do the review's preconditions, including `allow_fallbacks=false` and a verified zero-price routing constraint.

```text
PHASE / DATE: 8A-1 / 2026-11-09 UTC
BRANCH: feature/phase-8a-free-provider-safety, one local commit on 91c4d7d, not pushed
PROVIDER REQUESTS / DOCKER / DATABASE / .ENV: 0 / none / none / not read
ADR-41: six conditions verified; condition 6 after the approved ADR-15 amendment
LOCAL: backend unit 1173 passed; SDK 1158 passed, 25 skipped; QA 214 OK, 15 skipped; mutations 21 of 21 caught
LOCAL SCANNER: 41 hits, all reviewed, 0 unexplained
NOT RUN: 5 new integration tests and all CI jobs
8A-2: CONDITIONAL GO. REAL REQUESTS: NO-GO
```

## Sources

[Groq security onboarding](https://console.groq.com/docs/production-readiness/security-onboarding), [gitleaks issue #2180](https://github.com/gitleaks/gitleaks/issues/2180), [secretlint issue #1446](https://github.com/secretlint/secretlint/issues/1446).
