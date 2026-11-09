# Phase 7C — Gemini content demo: OTLP integration and offline security evidence

**Date:** 2026-11-09 UTC. **Status: local implementation complete; CI verification pending.** The database-backed tests have been written and collected, but they have **not run**. They need the first GitHub Actions run of this code.

**Provider requests in this phase: 0.** No Docker command, no database connection, no `.env` change, no push, PR, merge or tag. No Phase 7D work.

Evidence labels: **LOCALLY EXECUTED** (run in this session, Windows 11, Python 3.12.6), **NOT RUN LOCALLY**, **REQUIRES GITHUB**.

---

## 1. Source and Git state

| Item | Value |
|---|---|
| Root / branch | `C:/Users/PC/LLM-Insight-Development`, `feature/phase-7-gemini-demo` |
| HEAD before this phase | `64aa4641070bbd14d1be95ebb20e5d2bf51de0f7` |
| Parent / base | `7397a142…`, `43503064…`; base is an ancestor of HEAD |
| GitHub | `main` is still the base SHA; the feature branch is not on the remote |
| Working tree before work | Only the three user-owned untracked files |

### Phase 7B source audit

`demo_app/live_gemini_content.py`, the shared `live_gemini.run_single_request`, ADR-39 and the Phase 7B report were re-read against each other. The report's scenario table, exit codes, JSON sections, refusal behaviour and module list match the committed code. No defect was found, and no production code changed in this phase.

The three historical editable-install failures were checked against the actual assertions in `sdk/tests/unit/test_install.py`: two require `site-packages` in the import path and one requires `llm_insight_sdk/py.typed` in the distribution's file list. An editable install cannot satisfy them. In a non-editable install, as CI uses, all 7 tests in that file pass (§5). The file was not changed.

## 2. Risk review (R106 to R109)

No risk was accepted or reclassified, and none blocks offline integration verification. Evidence notes were added to `SOURCES_AND_RISKS.md`.

| Risk | Classification (unchanged) | Finding in this phase |
|---|---|---|
| R106 terminal preview retention | Mitigated by design; residual by nature | Preview-on tests cover stderr content and the exporter path; PostgreSQL and read-API checks are pending CI |
| R107 stale SDK install | Mitigated | **Residual now stated:** the comparison covers six modules, so a stale `_guards.py` or `config.py` is not detected. Left as is; widening it is a production change outside 7C |
| R108 dashboard `ok` is not completion | Open by design | A test asserts stored status `ok` with a non-zero exit for blocked and incomplete outcomes; pending CI |
| R109 operator-supplied project ID | Open by design | A test asserts a wrong valid ID gives 404 and storage follows the ingest key; pending CI |

R104 remains resolved. **R102 remains OPEN, not remediated, deadline 2026-11-30 UTC. R105 remains OPEN, not accepted.**

## 3. Test architecture

One new file: `sdk/tests/integration/test_live_gemini_content.py`, 59 tests.

```text
demo_app.live_gemini_content
  → FakeGemini (httpx.MockTransport, synthetic provenance, non-loopback blocked)
  → GeminiTextAdapter → SDK spans → official OTLP/HTTP protobuf exporter
  → [34 tests] local OTLP stub: decodes the real protobuf bytes; no database
  → [25 tests] live `python -m app` → disposable PostgreSQL → project-scoped read API
```

- **Reused, unchanged:** the `live_backend` session fixture, `FakeGemini` and its sentinels, `block_network`, the OTLP stub, and `gemini_env`.
- **Test-owned data:** the existing fixture creates the database `llminsight_sdk_it_test` and drops it afterwards. Each project has a unique name, each provider response a unique ID. Nothing else is cleaned or touched. Project 148 cannot be reached from this harness.
- **Clock:** `now=NOW` is injected, as in the existing tests, so these tests do not depend on the real date. The production guard and its 2026-11-11 13:00 UTC expiry are unchanged.
- **Real-mode boundary:** with `provenance="real"` the fake client is refused and sends nothing. No endpoint check was weakened.
- **Local safety:** the fixture stops at a missing backend interpreter before reading `.env`. Every local run here set `LLMI_BACKEND_PYTHON` to a path that does not exist, so the database code was unreachable.

### Evidence matrix

"Wire" means LOCALLY EXECUTED against the real exporter bytes. "DB + API" means written and collected, **NOT RUN LOCALLY, REQUIRES GITHUB**.

| Requirement | Wire | DB + API |
|---|---|---|
| Success, 3 scenarios: one request, SHA-256, prompt and fixture identity, spans, parent link, trace ID | yes | written |
| Provider identity, models, reported and normalized tokens, finish reason | yes | written |
| One usage row, no parent double count, null and unknown cost | attributes | written |
| Status, duration, project ID, deep link, `api_path` used verbatim | link shape | written |
| Dry run writes nothing | n/a | written |
| Re-export: trace, span, usage and attempt counts stable | n/a | written |
| Foreign project: indistinguishable 404, no list entry, no rows | n/a | written |
| Wrong but valid project ID (R109) | n/a | written |
| A timeout, B 429, C blocked (2 forms), D MAX_TOKENS, E missing usage, F inconsistent usage, G malformed, H local validation | 9 cases | 9 cases written |
| I invalid scenario or fixture | unit (7B) | written |
| J export failure: key revoked after readiness | n/a | written |
| K backend unavailable, and lost after readiness | yes | n/a |
| L flush or shutdown uncertainty, while storage succeeded | unit (7B) | written |
| M interruption, before and after the send | after the send | both written |
| N invalid project context, 7 values | yes | n/a |
| O missing trace correlation | refused run | API 404 and 422 written |
| Privacy, preview off: stdout, stderr, all logs, protobuf bytes | yes, 6 outcomes | stored rows, API, backend log written |
| Preview on: opt-in, terminal rule, bounds, escaping, no thought or error text | yes | not in telemetry: written |
| Real provenance refuses the fake client | yes | n/a |

Each case keeps five facts separate: provider operation, local validation, telemetry delivery, database persistence, read-API visibility. Two tests exercise the separation in both directions: J (delivery failed, nothing stored, printed link 404) and L (delivery reported uncertain, rows actually stored).

## 4. Exit-code contract

Unchanged from Phase 7B and asserted here: 0 dry run or fully confirmed; 1 provider error, block or interruption; 2 refused before a send; 3 provider `completed` or `incomplete` with validation or export not confirmed. `incomplete` is exit 3, never reported as completed. Export confirmation is never treated as storage: storage is read back separately.

## 5. Verification results

### LOCALLY EXECUTED

| Check | Result |
|---|---|
| New integration file, editable SDK, no backend | 34 passed, 25 skipped |
| `tests/unit` + new integration file, **non-editable SDK** | **1123 passed, 0 failed, 25 skipped** |
| `tests/unit/test_install.py`, non-editable SDK | 7 passed |
| Collection: `tests/integration` / all of `tests` | 109 / 1198 collected |
| Required-database rule: `LLMI_TEST_REQUIRE_DB=1` with no backend | The test **errors** with `live backend required`; it does not skip |
| `ruff check .` and `ruff format --check .` from `sdk/` | PASS, 48 files formatted |
| Ruff on `demo_app` | PASS, 9 files formatted |
| `mypy src` / `mypy ../demo_app` | PASS, 12 / 9 source files |
| Bandit 1.9.4 with baseline gate | PASS, High 1 / Medium 5 / Low 38, 44 of 44 entries; baseline not changed |
| `scripts/qa/tests` self-tests | 207 ran, OK, 15 skipped |
| `verify_phase0.py`, `git diff --check`, secret detectors, user-file hashes | Run immediately before the commit; results in the reviewer's final report |

The 25 skips are exactly the database-backed tests. They are not passes.

**Mutation check** on a scratch copy, no database: 11 defects, 10 caught by the file's 34 database-free tests. The eleventh, removing only the runner's first ADR-34 check, survived because the instrumented-client checks refuse the same client. Removing each layer alone leaves the test passing; removing both makes it fail, so this is defence in depth and not a test gap.

### NOT RUN LOCALLY

| Item | Reason |
|---|---|
| The 25 new database-backed tests | No isolated PostgreSQL here; local Docker and the developer's database are forbidden. **Not a pass** |
| The 50 existing SDK integration tests | Same |
| Backend tests | No backend change; `backend/.venv` absent |
| Real Gemini request | Not authorized |

### REQUIRES GITHUB

| Item | State |
|---|---|
| `python` job with PostgreSQL | First run of this code decides the database-backed result; not yet run |
| `docs`, `frontend`, `security-python`, `security-history`, `image-build`, `e2e` | Not run on these commits. The gates are unchanged |
| pip-audit, secret-history, npm audit policy | No dependency or frontend change here; run by CI |

### CI readiness

- `sdk/tests/integration/` is collected by the existing `pytest -q -rs` step, and `LLMI_TEST_REQUIRE_DB=1` is set on the job, so a missing database fails.
- The SDK is installed non-editable from the checkout, and that install gate is preserved.
- No `ci.yml` change was needed. All seven jobs are unchanged.
- The last `main` run's `python` job took 6.7 minutes of its 30-minute limit, read from the public API, so the extra tests fit.
- Scenario, preview and CI-variable behaviour use explicit test environments, so the runner's own `CI` variable does not affect them.

## 6. Security findings

None new. No `nosec`, no baseline update, no permission or billing control changed. Observation: on an interrupted call OpenTelemetry attaches an exception event with a stack trace to the root span on the wire. It holds code locations and no content (tested). The backend keeps only the exception type (`normalize.py`).

## 7. Limitations

- Database persistence, read-API behaviour, idempotency and project isolation for this runner are **unverified until CI runs**. The tests could fail on first contact; several expectations were checked against the code and the contract, not by execution.
- All local results are from Windows; CI is Linux.
- The dashboard deep link is checked for shape only; no browser opened it.
- Fake transports show the system's behaviour, not how the real model answers. Real provenance remains asserted, billed amount unknown, content not stored.
- The Free Tier evidence guard refuses after 2026-11-11 13:00 UTC, and the timestamp was not changed.

## 8. Next verification prerequisite

You push the branch and read the `python` job result on the first GitHub run. Until then this phase stays at:

```text
PHASE / DATE: 7C, local implementation / 2026-11-09 UTC
BASE: feature/phase-7-gemini-demo, on 64aa464
IMPLEMENTED: sdk/tests/integration/test_live_gemini_content.py (59 tests)
PROVIDER REQUESTS / DOCKER / DATABASE: 0 / none / none
NEXT: first GitHub run of the python job; read the 25 database-backed results. Fix any failure at its cause. Then 7D only on a new instruction
VERDICT: PHASE 7C LOCAL IMPLEMENTATION COMPLETE — CI VERIFICATION PENDING
```
