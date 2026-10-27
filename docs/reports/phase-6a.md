# Phase 6A — Real LLM integration foundation and OpenAI observability adapter — Report

- **Date:** 2026-10-27. **Branch:** `feature/phase-6a-live-foundation` (from `main` = `origin/main` = tag `phase-5` = `1a57d65`). Not pushed, not tagged, not merged.
- **Environment:** Windows 11 Home 10.0.26200, Python 3.12.6, Node 22.23.4 (project-local), Docker Desktop, PostgreSQL 17 (compose), `openai` 3.28.0 (pinned).
- **Spend:** **none.** No request was sent to OpenAI or any other provider. No `OPENAI_API_KEY` exists in `.env` or the process environment (checked for presence only). Every provider interaction in tests is a SYNTHETIC fixture behind `httpx2.MockTransport`, with all non-loopback name resolution and connections blocked.

## 1. Baseline and repository audit (Step 1)

| Check | Result |
|---|---|
| Repository root | `git rev-parse --show-toplevel` = the project folder (independent repo; home-directory repo untouched) |
| `main` vs `origin/main` | both `1a57d6575e68bfaf65a4b30034893de34f79d518` after `git fetch` |
| `phase-5` | annotated tag object, peels to `1a57d65` |
| Working tree | clean before the branch was created |
| Branch | `feature/phase-6a-live-foundation` created from `main`; `main` not modified |
| SDK baseline | `pytest`: **350 passed** before any edit was installed |

The architecture inventory (14 capabilities, each traced to source) is `PHASE6_LIVE_INTEGRATION_PLAN.md` §1. Key findings:
- A mature, offline-tested **Chat Completions** adapter already existed (Phase 2B). It was **extended, not duplicated**: the Responses API reuses the same client class, client checks, per-attempt span helper, retry loop and error classifier.
- Nothing guarded a real provider call: `LLMI_PROVIDER_MODE` and `LLMI_REAL_LLM_BUDGET_USD` were documented in `.env.example` but read by no code; the backend's `LLMI_ALLOW_REAL_LLM` governs mock-only experiments only.
- The backend contract already fits Responses metadata: no column, migration, allowlist entry or `attrmap` version was needed.
- `openai` 3.28.0 performs **no response validation** (probed offline: `{}` → a `Response` with `id=None`; an HTML body → `str`; wrong field types pass through; invalid JSON → bare `JSONDecodeError`). The adapter therefore type-checks every field it reads.
- The pinned semconv (`semantic-conventions-genai@9ffc7e00c639`, `openai.md`, re-read 2026-10-27) defines `openai.api.type = responses` and no Responses-specific operation name; the applicable predefined value is `chat`.

## 2. Design contract (Step 2)

- `docs/PHASE6_LIVE_INTEGRATION_PLAN.md`: sections A–L (abstraction, Responses API, scope, streaming plan, telemetry mapping, error classification, permission, spending guards, metadata-only rule, runner, multi-provider path, data flow), backward compatibility, test plan, 6B prerequisites.
- `docs/ADR.md` §8: **ADR-28** (Responses API in the existing adapter on `attrmap.v1`), **ADR-29** (fail-closed live guard, conservative in-process spend accounting), **ADR-30** (local runner, dry run by default, no browser surface). Recorded as accepted within the Phase 6A instruction, following the ADR-25/26 precedent, for the architectural review; ADR-01…27 untouched.
- Risks **R79–R84** added to `SOURCES_AND_RISKS.md`.

## 3. Code changes (Steps 3–6)

| File | Change |
|---|---|
| `sdk/src/llm_insight_sdk/openai_adapter.py` | + `ObservedOpenAIClient.create_response` (sync, non-streaming, `store=False` forced, refused parameters, default 1 attempt, `retry_if`, `attempt_observer`); + `OpenAIResponsesTextAdapter`; + `usage_from_response`, `responses_request_params`, `response_status`, `error_category`, `is_unambiguously_retryable`. Shared span helper gained `api_type` (default `chat_completions`) — Chat behaviour unchanged |
| `sdk/src/llm_insight_sdk/instrumentation.py` | + `InferenceRecorder.mark_failed(error_type)` (additive; `_finish` unchanged without it) |
| `sdk/src/llm_insight_sdk/providers.py` (new) | `TextRequest`, `TextOutcome`, `AttemptRecord`, `TextGenerationAdapter` protocol, `ERROR_CATEGORIES` |
| `sdk/src/llm_insight_sdk/live.py` (new) | `evaluate_policy`, `LivePolicy`, `LiveSession` (reserve/settle), `estimate_cost`, `worst_case_attempt_cost`, verified price mirror `openai-2026-10-24`, hard caps |
| `demo_app/live_openai.py` (new) | `python -m demo_app.live_openai [--execute] [--model M] [--dashboard-url U]` |
| `.env.example` | `LLMI_LIVE_*` keys added **empty** (fail-closed); comments; no secret value |
| Docs | plan (new), ADR §8, SOURCES_AND_RISKS R79–R84, SECURITY §2/§6, TELEMETRY_CONTRACT §3 note, PRICING §3 note, DEMO_SCRIPT note, README config row, `sdk/README.md` section |
| Tests (new) | `sdk/tests/unit/test_openai_responses.py` (45), `test_live_guard.py` (77), `test_live_runner.py` (23), `sdk/tests/integration/test_live_responses.py` (6); `openai_fixtures.py` + Responses bodies, raw-body steps, quota 429 |

**Not changed:** backend code, schema, migrations (still `0005`), frontend, compose files, Dockerfiles, CI workflow, dependency pins and lock files, the Chat Completions behaviour, backend experiments (still mock-only).

## 4. Telemetry field mapping

As specified in `PHASE6_LIVE_INTEGRATION_PLAN.md` §E. Verified on the wire and in PostgreSQL: a Responses span is `chat gpt-5.4-mini`, kind `CLIENT`, child of the caller's span, `openai.api.type = responses`, the five usage counters mapped from `usage.*` (reported zeros kept, absent subsets absent), `server_dropped_attributes_count = 0`, usage key `resp:openai:{id}` (`provider_response_id`), cost `0.0015750000` for the T19 usage (identical to the Chat path and to `PRICING.md` §4).

## 5. Security boundaries

- **Default OFF.** The shipped `.env.example` enables nothing; on this machine the dry run printed ten refusals and `--execute` exited 2 without creating a client.
- **A key alone enables nothing** (tested). Its presence is the only thing read by the guard; it is never in a message, `repr`, output line, span, log or API response (tested with sentinels).
- **No browser surface:** no route, Server Action or endpoint was added; the dashboard cannot trigger a call or receive a key. The runner pins `https://api.openai.com/v1` and refuses when `OPENAI_BASE_URL` is set.
- **Metadata only (ADR-19):** input, instructions, output text, provider error bodies and request IDs are absent from the OTLP wire, the SDK log, the runner output, the backend log and the read API (tested). Provider exception messages are never logged; only class, status and OpenAI `code` are read.
- **`store=False`** is always sent; `store` ≠ `False`, `conversation` and `previous_response_id` are refused, so no provider-side conversation state is created.
- **Secret hygiene:** `.env` and `frontend/.env.local` git-ignored and untracked (verifier); no `NEXT_PUBLIC_*` variable; bundle scan 0 findings; history and working-tree secret scan (§7); test keys are assembled from parts so no key-shaped literal exists in source.

## 6. Cost control design

Pre-call worst-case reservation (input bound = UTF-8 bytes + 64, every allowed attempt, higher input rate, rounded up) against a per-request cap and a session budget; request count and non-blocking concurrency limits; post-call booking at the backend-formula estimate or, without one, at the bound (**never 0**); halts on usage above the bound, an unknown estimate or extra attempts; interrupted calls book every allowed attempt. Ambiguous failures (timeout, connection, 5xx) are never retried; only a non-quota 429 may be, when `LLMI_LIVE_MAX_RETRIES > 0`. Prices: the verified catalogue only (no invented price), mirrored and drift-tested; refused after 14 days of evidence (the current window ends 2026-11-07T21:40Z). All money `Decimal`; estimate and billed amount are distinguished in every output (`cost_basis`: "not a billed amount"). R66, R67 and R75 are respected: no claim of durable idempotency, distributed or account-wide limits, or toolchain coverage beyond what was run.

## 7. Quality gates (Step 8) — exact commands and results

| Command | Result |
|---|---|
| `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` (backend, real PostgreSQL) | **1726 passed**, 0 failed, 0 skipped (189 s; 1 warning: R20 Starlette deprecation) |
| `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` (sdk, live backend) | **501 passed**, 0 failed, 0 skipped (116 s) = 350 baseline + 151 new |
| `npm test` (frontend) | **311 passed**, 0 failed, 0 skipped, 0 cancelled |
| `npx playwright test` (frontend, real backend `python -m app` on :8000, installed Edge) | **55 passed**, 0 failed, 0 skipped (2.3 min); teardown `remaining: []`. Ports 8000/3001 were held by an unrelated project's container and the compose `app` stack: with the user's approval those three containers were stopped for the run and restarted afterwards (nothing removed) |
| `npm run typecheck` / `npm run lint` | clean / 0 warnings |
| `npm run build` / `npm run check:bundle` | built / 632 files, 23 in the browser bundle, **0 findings** |
| `ruff check`, `ruff format --check` (backend; sdk incl. `demo_app/live_openai.py`; `scripts/qa`, dev fixtures) | all passed; 140 / 35 files formatted |
| `python -m mypy src`; `python -m mypy --config-file pyproject.toml ../demo_app` (sdk, `strict = true`) | no issues (10 / 4 files) |
| `pip check` (backend; sdk) | no broken requirements |
| `alembic current` / `alembic check` | `0005 (head)` / no new upgrade operations |
| `docker compose config --quiet`; `docker compose --profile app config --quiet` | valid |
| `python -I scripts/verify_phase0.py` | **797 PASS, 0 FAIL** |
| `pip-audit -r <lock> --no-deps --disable-pip -s pypi\|osv` (both locks; pip-audit 2.10.1 from the Phase 5B throwaway venv) | no known vulnerabilities (4/4) |
| `pip-audit --path <venv>/Lib/site-packages` (both venvs) | `pip` 24.2 only (12 advisories, up from 6 at Phase 5B as the database grew); R75, toolchain only, unchanged |
| `npm audit` | 0 vulnerabilities |
| `bandit -r` on the changed/new production files (bandit 1.9.4) | 1 Medium (B310 `urlopen` in the runner's readiness probe; the URL is validated by `LLMInsightConfig`: http/https only, plain http loopback-only) — accepted, same class as Phase 5B F6/F7; 0 High |
| `python -I scripts/qa/secret_scan.py --repo . --env .env --env frontend/.env.local` (staged tree) | 806 blobs, 29 messages, 463 working files, 9 local secret values (names only): **0 secret-value hits**; 36 pattern hits (exit 1), all `url_userinfo` at the Phase 5B F9 fixture locations; the one more than Phase 5B (35) is the pre-existing `SECURITY.md` example line in the new blob of that edited file. No hit in a Phase 6A addition |
| `git diff --check` | clean |

Dependency changes: none (`git diff` of both locks, both `pyproject.toml`, `package.json`, `package-lock.json` is empty).

Two test-only corrections during development (product unchanged): an assertion expected `setting_invalid` where the guard correctly answers `session_budget_too_small` for a tiny positive budget; and the leak test now inspects the SDK's own logger, because the `openai` library's DEBUG log prints the provider request ID (documented as R84). A test literal shaped like an OpenAI key was assembled from parts after the doc verifier's secret scan flagged it.

## 8. Known limitations

R79 (in-process limits only), R80 (Chat adapter still retries ambiguous failures; not used by the runner), R81 (framing allowance unverified until a real call), R82 (diagnostics do not interpret Responses `incomplete` reasons), R83 (price mirror can drift; drift-tested, 14-day refusal), R84 (third-party DEBUG logs). Also: Responses support is sync-only; streaming, tools, background and multi-turn state are refused; R66/R67/R75 unchanged; the SDK version string stays `0.2.0` (a version bump is a release decision for the review).

## 9. Phase 6B live-test prerequisites

`PHASE6_LIVE_INTEGRATION_PLAN.md` §5: architectural acceptance of ADR-28…30; explicit approval of the spend, model and ceilings for that run; re-verified prices (mandatory after 2026-11-07T21:40Z); a key set by the operator in their own shell (never pasted into Claude Code or a file), preferably a dedicated provider project with a provider-side budget and rate limit; running backend with seeded prices and a project key; dry run reviewed first; afterwards compare reported `input_tokens` with the bound and the stored row with the provider's usage page.

## 10. Decisions for the architectural review

1. Operation `chat` + `openai.api.type = responses` on `attrmap.v1` (no `v2`).
2. Responses `incomplete` reasons recorded verbatim, not mapped to Chat's `length` (diagnostics extension deferred, R82).
3. A returned failed/malformed response marks the span `ERROR` with three new `error.type` identifiers (`openai.response.*`); these are not yet in the diagnostics interpretation table.
4. Tools refused in the Responses path (cost representability) — a later phase needs tool-charge pricing first.
5. The SDK-side price mirror (ADR-01) with a 14-day evidence window, versus an authenticated price read from the backend.
6. In-process session limits (R79) plus a provider-side project budget as the account-level control; no persistent local ledger.
7. Keeping the Phase 2B Chat retry defaults unchanged (R80) rather than changing an accepted behaviour.
8. Whether the SDK version should be bumped for the new public API.

## 11. Verdict

Phase 6A acceptance gate: the foundation is implemented, documented and tested offline end to end (SDK → OTLP → backend → PostgreSQL → read API), with real execution off by default and no spend. **Stopped for architectural review; Phase 6B not started.**
