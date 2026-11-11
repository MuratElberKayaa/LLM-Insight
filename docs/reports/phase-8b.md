# Phase 8B — Multi-provider live comparison MVP: offline implementation

**Date:** 2026-11-11 UTC. **Branch:** `feature/live-comparison-groq-openrouter`, from `origin/main` at `3e4d622`, with upstream tracking removed; not pushed.

**Verdict:**
- **Offline implementation:** complete, and verified by the local checks in §6.
- **Real provider requests:** **NO-GO**. Every lock is closed, and the §8 pre-send blockers are open.
- **Provider requests made:** 0. No credential, `.env`, developer database or Docker resource was read or changed.

## 1. Baseline (verified on GitHub, public API, 2026-11-11)

| Fact | Evidence |
|---|---|
| PR #10 merged | merged 18:24:37Z, head `74e1df1`, merge commit `3e4d622` = `origin/main` |
| PR run on `74e1df1` | run 43075270239: all seven jobs `success` |
| Post-merge `main` run on `3e4d622` | run 43077503488: all seven jobs `success` |
| `e2e` job (both runs) | steps 10 (browser install and checks) and 11 (Playwright) succeeded; one warning annotation, `BROWSER_RENDERING_PACKAGES_MISSING`, seven font packages |

The gate fails closed on each R133 criterion, so a successful `e2e` job means all of them held:
- fewer than 72 passing tests;
- a project count other than five;
- a rate-limit rejection;
- a missing library package;
- a failed launch check.

The summary artifact was not downloaded, because that needs authentication.

**The owner's working tree:**
- It is unchanged: `feature/phase-8a6-comparison-dashboard` at `980dfc6`.
- Its uncommitted Phase 8A-7 files and the three untracked user files were not opened or modified.

## 2. What was built

| Package | Result | Files |
|---|---|---|
| A. Providers | OpenRouter adapter for one explicit `org/name:free` model, reusing the Groq endpoint boundary unchanged (ADR-46). The Groq adapter, guard and session are unchanged | `sdk/src/llm_insight_sdk/openrouter_adapter.py`, extra `openrouter` in `sdk/pyproject.toml` |
| B. Free-only safety | OpenRouter free-model guard with three locks, a zero-price routing evidence lock, credential binding, exact-body check, post-call halts, and Groq's process slot shared (ADR-47). R127 corrected | `sdk/src/llm_insight_sdk/openrouter_live.py`, `.env.example`, `security/secret-history-baseline.json` (SH-40), `scripts/qa/secret_history_gate.py` |
| C. Comparison | `demo_app.live_compare` executes `groq` and `openrouter` through one closed registry, with the same three scenarios, text and 1024-token cap (ADR-48) | `demo_app/live_compare.py` |
| D. Dashboard | One necessary note in Models / Compare when real-labelled attempts are shown: the label is asserted, not verified, and the upstream is not shown. No action can start a request | `frontend/components/models/model-comparison.tsx` |
| Contract | Shared SYNTHETIC span fixture, emitted by the SDK and ingested by the backend unchanged | `docs/fixtures/openrouter_client_span.v1.json` |

## 3. OpenRouter request and response handling

- **Request:** exactly `{"model": "<org/name:free>", "messages": [one user message], "max_tokens": n, "stream": false, "reasoning": {"exclude": true}, "provider": {"allow_fallbacks": false, "data_collection": "deny", "max_price": {"prompt": 0, "completion": 0, "request": 0, "image": 0}}}`.
- **Never sent:**
  - `models`, `route`, `plugins`, `sort` or `order`;
  - tools, sampling settings or attribution headers.
- **Refused models:**
  - routers (`openrouter/free`, `openrouter/auto`) and aliases;
  - `:nitro`, `:floor` and paid IDs.
- **Response:**
  - A 200 that carries an `error` object is an error, never a completion.
  - A choice error or `finish_reason: error` is a failure.
  - A different response model, including the paid variant of the same model, is `model_mismatch` and halts the session.
- **Usage:** input, output, cache read, cache write and reasoning counts, or unknown; nothing is estimated.
- **Free-routing signals:** `usage.cost` and `usage.is_byok` are reduced to states for the guard only. They are never exported or stored.
- **Never read:** reasoning text, `native_finish_reason`, the upstream provider and error text.
- **Documentation:** re-read 2026-11-11 (provider selection, API overview, errors, free variant, limits, usage accounting, reasoning tokens, model fallbacks).

  Two gaps remain, and both are treated as blockers:
  - `max_price` behaviour for a 0 cap is not documented (R135);
  - the non-streaming `provider` field is not documented (R137).

## 4. Free-only and safety controls (WP B)

| Requirement | Groq (unchanged, ADR-44) | OpenRouter (ADR-47) |
|---|---|---|
| Locks | `REAL_EXECUTION_ENABLED` false; no Free Plan evidence or model | `REAL_EXECUTION_ENABLED` false; no free-model evidence or model; `ZERO_PRICE_ROUTING_VERIFIED_AT` None |
| Entitlement | operator attestation `LLMI_GROQ_PLAN=free`, at most 1 day old; never inferred from documentation | operator attestation `no-purchased-credits` (no credits, BYOK or top-up), at most 1 day old; never inferred from documentation |
| Evidence expiry | 14 days | 7 days, for both the model and the routing evidence |
| Paid path | no paid mode; post-call halt on a paid-plan indicator | no paid mode, no model list, `allow_fallbacks: false`, `max_price` 0; halt on 402, a non-zero or missing cost, BYOK, or a model mismatch |
| One request | 1 per session and per process | 1 per session, and per process **across both providers** (shared slot) |
| Retries, tokens, timeouts | 0 retries; output 16–1024; input bound at most 1024; fixed 30 s deadline | the same |
| Environment | base URLs, proxies, CA overrides and `SSLKEYLOGFILE` refused | the same list |
| Credential binding | the request must carry the validated `GROQ_API_KEY` | the request must carry the validated `OPENROUTER_API_KEY`; foreign key shapes refused |
| Redaction | metadata-only spans; no content, reasoning, error text or key | the same, plus no `usage.cost`, `is_byok` or upstream identity |

## 5. Comparison (WP C)

- **Scenarios:** the three versioned SYNTHETIC scenarios are unchanged (`cmp-ticket-triage`, `cmp-json-aggregate`, `cmp-release-summary`), each pinned by SHA-256.
- **Identical inputs:** both providers receive the same composed text, `prompt_version` and 1024-token cap. A test asserts that the two request bodies carry the same message.
- **Runner output:**
  - per provider: request and response model, finish reason, latency, all token counters, error category, provenance and validation rule codes;
  - cost is `unknown` for both, never 0;
  - for OpenRouter, the plan and the cost basis add the attestation and the routing constraints, and `provider_reported_cost` and `provider_reported_byok` appear as states only.
- **Integration evidence** (`sdk/tests/integration/test_live_compare_openrouter.py`, PostgreSQL in CI):
  - a Groq run and an OpenRouter run of one scenario in one dedicated project give exactly two `/models/compare` rows with the same `prompt_version`;
  - each row has unknown cost and low-sample flags;
  - neither a 0 catalogue row nor a reported 0 credits produces a computed cost.
- **No ranking:** a group describes the requests in it; one sample per group is not a ranking (R126, R139).

## 6. Verification (local, Windows; nothing here is GitHub CI)

Commands ran in the worktree. The SDK ran in a scratchpad venv built from `sdk/requirements-dev.lock` with the SDK reinstalled (`pip check` clean), with `PYTHONPATH` set to the worktree source. The backend used the existing backend venv read-only.

| Check | Command | Result |
|---|---|---|
| SDK unit suite (non-editable install of this checkout, no `PYTHONPATH`) | `python -m pytest tests/unit` | **2388 passed, 0 failed, 0 skipped**. A first run with `PYTHONPATH` pointing at the source had 2 failures in `test_install.py`, which asserts an installed package; that was the setup, not the code |
| SDK integration directory, without a database | `python -m pytest tests/integration` | **38 passed** (database-free), **111 skipped** (database-backed, NOT RUN) |
| of which, new OpenRouter adapter tests | `tests/unit/test_openrouter_adapter.py` | **155 passed** |
| of which, new OpenRouter guard tests | `tests/unit/test_openrouter_live_guard.py` | **117 passed** |
| of which, runner tests (Groq and OpenRouter) | `tests/unit/test_compare_runner.py`, `test_compare_runner_openrouter.py` | **104 passed** |
| SDK integration, new OpenRouter module | `tests/integration/test_live_compare_openrouter.py` | 9 collected, **9 skipped: no database locally**. NOT RUN; they need the CI `python` job with `LLMI_TEST_REQUIRE_DB=1` |
| SDK lint, format, strict types | `ruff check .`, `ruff format --check .`, `mypy src`, `mypy ../demo_app` | 0 findings; formatted; no issues (16 and 11 source files) |
| Backend unit suite | `python -m pytest tests/unit` | **1247 passed** (1221 before, plus 26 new OpenRouter contract tests), 1 existing Starlette deprecation warning |
| Frontend | `npm run typecheck`, `npm run lint`, `npm test` | clean, clean, **354 passed** (353 + 1 new) |
| Frontend build, bundle scan | `npm ci` from the lock, then `npm run build`, `npm run check:bundle` | build **passed**. The bundle scan found no token to search for (no `frontend/.env.local` here), so it checked nothing locally; CI runs it with its throwaway token |
| QA gate self-tests | `python -I -m unittest discover -s scripts/qa/tests -p "test_*.py"` | **256 ran, OK, 15 skipped** (the same 15 by design) |
| History baseline | `secret_history_gate.py validate-baseline` | **valid: 40 entries**, F9-CHANGE-ME-VALUE 12 |
| Bandit 1.9.4 baseline gate | `bandit -r backend/app sdk/src demo_app scripts`, then `bandit_baseline_check.py evaluate` | **PASS, 44 = 44** (High 1, Medium 5, Low 38); the new modules add no finding |
| Docs | `python -I scripts/verify_phase0.py` | **799 PASS, 0 FAIL** |
| Raw secret detectors | the 11 `secret_scan.py` patterns over the 27 changed and new files | **1 match**: the `.env.example` placeholder that SH-40 covers (§7) |
| History-secret gate | a throwaway clone that commits the same 27 blobs on `3e4d622` and adds a PR-style merge ref; validate-baseline, coverage, scan, evaluate | **PASS: 40 findings = 40 baseline entries, 0 problems**; the only finding in the new blobs is SH-40 |

**Not run:**
- mutation tests, by the owner's rule for this milestone; targeted adversarial tests were used instead (§6.1);
- the database-backed integration tests and the Playwright E2E suite, which need the CI services;
- GitHub CI for this branch;
- any real provider request.

### 6.1 Targeted adversarial tests (instead of a mutation campaign)

- **Send gate.** Each of the following is refused, sends nothing, creates no span, and leaves the slot free:
  - each lock alone; each policy refusal alone; each refused environment variable;
  - a Groq, Google, ingest or plain `sk-` key in `OPENROUTER_API_KEY`;
  - a client built with another key (`credential_not_bound`);
  - a client changed after construction; TLS key logging; another URL, host or port; an extra header;
  - a body with fallbacks on, `0` in place of `false`, a partial or non-zero `max_price`, `order`, `sort`, `models`, `route`, `reasoning.exclude: false`, streaming, or no routing object;
  - a model other than the policy's; an output cap above the policy.
- **One request.** Checked:
  - a second request in a session is refused;
  - after an OpenRouter request, the process cannot make a Groq request, and the reverse also holds;
  - connect-phase failures are `not_sent`, later failures `possibly_sent`, and nothing reopens.
- **Halts.** Each stops the session after one request:
  - 402, and a 200 body with `error.code` 402;
  - a non-zero, missing or textual cost; BYOK true or missing;
  - the paid model in the response; usage above the bounds; a redirect.

  A 429, 503, 401 or `length` response does not halt.
- **Hostile responses.** Each becomes `malformed_response` and never a completion:
  - non-JSON, `NaN`, a list body, a chunk object, a missing or invalid model, two choices, a user-role message;
  - a non-JSON content type, gzip encoding, an oversized declared length, or an endless stream.
- **Privacy.** Sentinels for the prompt, output, reasoning, error message, upstream name, native finish reason, raw metadata and key are absent from every span, outcome `repr`, runner line, log and stored row.

## 7. Security and history

- **R127, the reviewed baseline change:**
  - The `.env.example` Groq comment now states that the runner exists and execution is locked, and the OpenRouter block was added (empty values, no key).
  - This created one new blob, `ead24c1685dbabfeea7e6388f76a67ff7a404dcb`. It has exactly one detector match: the existing `CHANGE_ME` database-URL placeholder, `url_userinfo` at offset 467, length 42. Every byte up to the end of the match is identical to SH-39's blob.
  - Classified F9-CHANGE-ME-VALUE by hand as entry **SH-40**. In the same change: the `SECURITY.md` §9 register row and note, `REVIEWED_BASELINE_SHA256` = `203d442266a1a5eb2594ac5141c8cc201cbc81bb7f3ea9e33c8c1a9d2ea153f7` (the gate's own digest), and the self-test totals (40, F9-CHANGE-ME-VALUE 12).
  - No detector, class or rule changed, and no other entry was edited.
- **Raw secret detectors:** a test of the new guard first held a literal OpenAI-style prefix, which `verify_phase0` flagged. The value is now assembled from parts, as in the existing tests.
- **Incident, harmless:** while checking the new blob, one command (`git hash-object -w --stdin` with empty input) wrote the empty blob into the shared object store. No ref, commit or tracked file changed, and the object is unreferenced.

## 8. Operator-readiness checklist

### 8.1 Pre-send blockers (all must clear before any authorized request)

| # | Item | Owner action | Clears by |
|---|---|---|---|
| P1 | **Groq Free account and limits evidence** (B1, B2) | Read the Groq console Limits page for the chosen model and record its free limits and the date. Confirm the model on the official models page and not on the deprecations page | A reviewed commit that sets `groq_live.FREE_PLAN_MODELS` (one model, with sources) and `FREE_PLAN_EVIDENCE_VERIFIED_AT`; 14-day expiry |
| P2 | **OpenRouter exact free model** (B3) | Read the model's OpenRouter page: an exact `org/name:free` ID, price 0, available, no data-use note that conflicts with `data_collection: deny`. Read the free-model limits for the account (50 per day below 10 purchased credits) | A reviewed commit that sets `openrouter_live.FREE_MODELS` and `FREE_MODEL_EVIDENCE_VERIFIED_AT`; 7-day expiry |
| P3 | **OpenRouter zero-price routing** (B4, R135) | Establish, from current documentation or OpenRouter's own statement, how `max_price: 0` behaves for that `:free` model and which status a non-qualifying route returns | A reviewed commit that sets `ZERO_PRICE_ROUTING_VERIFIED_AT`; 7-day expiry |
| P4 | **OpenRouter account state** (R134) | Confirm in the account: no purchased credits, no BYOK key, no automatic top-up; create a dedicated key | Set `LLMI_OPENROUTER_ACCOUNT_STATE=no-purchased-credits` and today's `LLMI_OPENROUTER_ACCOUNT_CHECKED_ON` |
| P5 | **Groq account state** (R119) | Confirm the account plan is Free in the console | `LLMI_GROQ_PLAN=free` and today's `LLMI_GROQ_PLAN_CHECKED_ON` |
| P6 | **Credential and environment safety** | Set keys only in the operator's own shell, never in a committed file or chat. No `*_BASE_URL`, proxy, `SSL_CERT_*` or `SSLKEYLOGFILE` variables. Run the runner from this checkout, with `sdk_checkout_match: true` and the audited `httpx`/`httpcore`/`certifi` versions | The dry run shows none of `client_env_override`, `api_key_*`, `sdk_checkout_mismatch` or `client_library_not_audited` |
| P7 | **Dedicated comparison project** (D5, R125) | Run `python -m app.cli create-key --project llm-insight-compare`; use its ingest key in `OTEL_EXPORTER_OTLP_TRACES_HEADERS` and its ID in `LLMI_DASHBOARD_PROJECT_ID`; check with the read API that the project holds no other data | The dry run is `execution_ready: true`; the operator records the project ID |
| P8 | **Execution lock release** | Release `REAL_EXECUTION_ENABLED` for the one provider to be called, in a reviewed commit, after P1–P7 for that provider | CI green on that commit |
| P9 | **Explicit per-request authorization** | Authorize exactly one provider, model, scenario and date. Review the dry-run line first. One `--execute` per authorization; never re-run after a failure or timeout | The written authorization |

### 8.2 First-response validation (only after the first authorized response)

| # | Item | What is checked |
|---|---|---|
| V1 | Telemetry correctness for a live response (B7) | One CLIENT span with `llminsight.provenance=real`, `server_dropped_attributes_count = 0`, provider, models, response ID and finish reason; one usage row; cost `unknown`; no content anywhere in the stored rows or logs |
| V2 | Groq reasoning accounting (B5) | `prompt + completion = total`; whether reasoning counts appear and whether they sit inside `completion_tokens` |
| V3 | OpenRouter free-route signals (R136) | The response `model` equals the requested `:free` ID; `usage.cost` is exactly 0 and `is_byok` false (no halt); the OpenRouter activity page shows no charge for the request |
| V4 | Groq Free Plan signals (R124) | `service_tier` is `on_demand` or null; no paid-plan indicator; no halt |
| V5 | Runner and export | Exit code; `export.outcome = confirmed`; the project-scoped link opens the trace |
| V6 | Comparison view | After one run per provider: two rows with the same `prompt_version`, low-sample flags, unknown cost, the real-data note and the not-ranked notice |

A failed V-item is a reviewed finding before any further request, never a reason to retry.

## 9. Remaining blockers to the first real request

1. P1–P5 above: evidence and attestations, owner actions.
2. P3 (R135): OpenRouter zero-price routing verification. The code refuses until it is released.
3. Reviewed release commits (P8), one provider at a time. This branch's CI must pass first.
4. An explicit authorization for each single request (P9).
5. CI of this branch, **NOT RUN**: including the 9 new database-backed tests and the existing 25 comparison tests.

Not blockers for Groq or OpenRouter: R123 (Gemini only).

## 10. Risks

- **Added:** R134–R139 in `SOURCES_AND_RISKS.md`: the account is the real billing boundary; zero-price routing is unverified; the parser is built from documentation; the upstream is invisible; free-tier caps and availability; small-sample ranking.
- **R127:** closed.
- **R128:** now covers nine audited modules.
- **Rollback:** revert the milestone commit. There is no migration, stored-data or production-behaviour change, and both providers stay locked.
