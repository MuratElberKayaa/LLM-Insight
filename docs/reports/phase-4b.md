# Phase 4B — Human Failure Review & Trace-to-Testset

- **Date:** 2026-10-25 to 2026-10-26 (UTC; the machine's local clock is UTC+3).
- **Instruction (user, Phase 4B):** implement the locked P0 Human Failure Review (CR-03) and human-gated Trace-to-Testset, keeping automatic diagnostics (Phase 4A) separate from human decisions; stop after Phase 4B. The session hit a usage limit after implementation and verification; the resume instruction (2026-10-26) asked to continue without restarting (§13).
- **Delivered:** migration `0003` (`ARCHITECTURE.md` §4.8–4.10), review/dataset/test-case API (§5.7), server-side redaction and rule validation, `reviews` on the trace read API, the dashboard workflow (Trace Detail review section beside an unverified diagnostics panel, Trace-to-Testset page, Datasets pages) through four narrow Server Actions with CSRF checks (§5.8), and unit, integration, mutation and live browser tests.
- **Not done (by instruction):** Phase 4C (rule engine/evaluation, experiments, A/B, T36–T40), LLM-as-judge, Optimization Lab, real OpenAI calls, public deployment, tags, pushes.
- **Untouched:** the home-directory repository, global Git/Node configuration, credentials, the `llminsight-postgres-1` container and volume (only the additive migration was applied to the dev DB, §8), dependencies and lock files, the SDK source, the CI workflow, the pricing catalogue and stored costs.

## 1. Baseline verification (before any change)

| Check | Result |
|---|---|
| Git root | `C:/Users/PC/Desktop/LLM_Insight_Claude_Code_MVP_Paketi` (own repository) |
| Branch / HEAD / tree | `main`, `888b188` (Phase 4A), clean |
| Tags | `phase-0` `a16d64b`, `phase-1` `cec668b`, `phase-2` `75ff8c3`, `phase-3` `e5a631c` (annotated); unchanged at the end |
| Remote | `origin` = `https://github.com/MuratElberKayaa/LLM-Insight` (nothing pushed) |
| PostgreSQL | `llminsight-postgres-1` healthy on `127.0.0.1:55432`; dev DB `0002 (head)` |
| Backend `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` | **1247 passed**, 0 failed, 0 skipped (181 s), exit 0 |
| SDK same command | **350 passed**, 0 failed, 0 skipped (82 s), exit 0 |
| Frontend `npm run typecheck` / `npm test` | exit 0 / **237 pass**, 0 fail, 0 skipped |

## 2. Contract review and acceptance-criteria mapping (before coding)

Read: `CLAUDE.md`, `MVP_MASTER_PLAN.md` (§A, §D1, §E, §F, §H Phase 4, §I, §J), `CLAUDE_PHASE_PROMPTS.md` §5, `PRD.md`, `ADR.md`, `ARCHITECTURE.md` (§4.1, §4.8–4.13, §5–5.6, §7), `SECURITY.md`, `TELEMETRY_CONTRACT.md` (content attributes), `TEST_MATRIX.md`, `PHASE_STATUS.md`, `SOURCES_AND_RISKS.md`, `DEMO_SCRIPT.md`, `reports/phase-3-final.md`, `reports/phase-4a.md`, plus the trace read API, admin guard, migrations `0001`/`0002`, the ingest value heuristics (`services/ingestion/sensitive.py`), the frontend backend client, request guard and Trace Detail page.

### 2.1 Locked contract elements used (verbatim sources)

| Element | Source | Content |
|---|---|---|
| Review fields | CR-03, `ARCHITECTURE.md` §4.9 | `issue_type`, `evidence`, `hypothesis`, `review_status`, `reviewer_decision`, plus `trace_id`, `span_id?`, `created_by`, timestamps |
| `issue_type` | §4.9 | `retrieval_missing`, `chunk_boundary`, `prompt_instruction`, `generation_mismatch`, `schema_violation`, `unknown` |
| `review_status` | §4.9 | `open` (default), `in_review`, `closed` |
| `reviewer_decision` | §4.9 | `pending` (default), `hypothesis_supported`, `hypothesis_rejected`, `inconclusive` |
| Review invariant | §4.9 table check | `review_status <> 'closed' OR reviewer_decision <> 'pending'` |
| Duplicate review | §4.9 | partial `UNIQUE(project_id, trace_id, issue_type) WHERE review_status <> 'closed'` → `409` |
| Review text | §4.9, `SECURITY.md` §1 | `evidence` LEN(4000), `hypothesis` LEN(2000), redaction applied before insert |
| Accountability | §4.9 | `created_by` LEN(100), self-declared (no user auth in the MVP) |
| Soft trace reference | §4.1 | no FK to `traces`; the API validates the trace exists in the project at insert |
| Review API | §5 | `POST /api/v1/failure-reviews` `{trace_id, span_id?, issue_type, evidence, hypothesis, created_by}` → `201`; duplicate open → `409`; `PATCH /{id}` for status/decision |
| Linked reviews | §5 | `GET /api/v1/traces/{trace_id}` returns linked reviews |
| Datasets | §4.8 | `test_datasets(id, project_id, name LEN(100), created_at)`, `UNIQUE(project_id, name)` |
| Test cases | §4.10 | `dataset_id`, `origin_trace_id?` (soft), `origin_review_id?` (FK, `ON DELETE SET NULL`), `sanitized_input` LEN(8000) redacted before insert, `expected_output` JSONB NULL, `rule` JSONB with `type ∈ {json_schema, field_match, exact, regex}`, `redaction_applied` (true via the API), `approved_by` LEN(100), `approved_at`, `created_at` |
| Approval | §4.10, §5 | no row without approval; `approved != true` → `422`, nothing stored; requires `approved_by` and sanitized input |
| Content | ADR-19, `SECURITY.md` §1, DEMO step 5 | telemetry never stored prompts; the human **types** safe synthetic input; human-entered dataset cases are the only "opt-in synthetic content" |
| Hypothesis wording | ADR-10, §4.9 | a cause is a hypothesis; `hypothesis_supported` is not proof of root cause |
| Access | CR-06, §5.1 | admin bearer token server-side only; mandatory `project`; one indistinguishable `404`; no `403`; `422` names fields, never echoes values |
| Migrations | ADR-21, §4.13 | §4.8–4.12 come in Phase 4 and must match the contract column for column; T31 upgrade → downgrade → upgrade |

### 2.2 Exact T33–T40 criteria (`TEST_MATRIX.md`, unchanged) and Phase 4B ownership

| ID | Expected outcome (verbatim) | Phase 4B ownership |
|---|---|---|
| T33★ | Valid enum values → `201`; invalid `issue_type` → `422`; duplicate open → `409` | **Owned, full** |
| T34★⛔ | Missing or false `approved` → `422`, no row | **Owned, full** (4→5: Phase 5 re-verifies) |
| T35★⛔ | Email / phone / key-like input is masked before insert; `redaction_applied=true` | **Owned, full** (4→5) |
| T36★ | Valid output passes the JSON Schema + field rule | Phase 4C (rule engine). 4B only stores the rule — NOT RUN |
| T37★ | Schema violation fails with `evaluation_detail` | Phase 4C — NOT RUN |
| T38 | Both versions run on identical case IDs | Phase 4C — NOT RUN |
| T39★ | Each result carries `real`/`synthetic`; mock → synthetic | Phase 4C — NOT RUN |
| T40 | `passed=NULL` ("not evaluated"), excluded from the pass-rate denominator | Phase 4C — NOT RUN. 4B only makes sure a case without `expected_output` can be stored (the precondition T40 needs) |

### 2.3 Instruction requirement → implementation → evidence

| # | Requirement (Phase 4B instruction) | Planned implementation | Planned evidence |
|---|---|---|---|
| R1 | Select an existing trace of the current project, inspect metadata | Existing Trace Detail page (Phase 3B) is the entry point; review section added to it | E2E |
| R2 | Record an explicit review decision with an approved `issue_type` only | `POST /api/v1/failure-reviews` (CR-03 body, strict schema, enum-checked by API **and** DB `CHECK`); `PATCH /api/v1/failure-reviews/{id}` for status/decision; form with no preselected `issue_type` | T33 integration, unit, E2E |
| R3 | Retrieve the persisted review; distinguish reviewed / unreviewed traces | `reviews` in `GET /api/v1/traces/{id}` (contract field, was always `[]`); additive per-item review counts in `GET /api/v1/traces` | integration, E2E |
| R4 | Never auto-create a review from diagnostics/inference errors; no category → `issue_type` mapping | Reviews are written only by the POST endpoint; no code path from diagnostics or ingest writes `failure_reviews`; the diagnostics module is not imported by the review code | integration (diagnostics call → 0 reviews; ingest of failing traces → 0 reviews), static import test |
| R5 | Diagnostics shown as optional, UNVERIFIED supporting evidence, visually separate | Read-only "Machine observations (unverified)" section on Trace Detail, decoded against `llminsight.diagnostics.v1`; evidence spans link to the waterfall; the review form never reads it | unit (decoder, rendering), E2E |
| R6 | Human-gated trace → test case; no recovery of missing content; no fake prompts | Conversion form starts empty; input and expected result are typed by the human; only references (`origin_trace_id`, `origin_review_id`) come from the trace | integration, E2E |
| R7 | Explicit approval, review state, required fields, confirmation, redaction, isolation, provenance | `approved: true` (literal boolean), `approved_by`, server redaction, trace/review/dataset all checked in the project, conversion only from a decided review (D4) | T34/T35 integration, isolation tests |
| R8 | Prevent duplicates, cross-project refs, writes on load / from GET, implicit approval | One case per (dataset, review) under a transaction advisory lock (D5); every write is POST/PATCH from an explicit form submit; GET handlers stay read-only snapshots | concurrency + isolation tests, static tests |
| R9 | Migration preserving data, up/down tested | `0003` creates §4.8–4.10 only (4.11–4.12 are Phase 4C); downgrade refuses while human rows exist | migration tests with data |
| R10 | Telemetry/cost immutability | No Phase 1–3 table altered; tests compare spans/usage/traces/prices before and after review and case writes | integration |
| R11 | Local admin auth, project scope, server-side credentials, CSRF, safe rendering, no sensitive URL params, no double submit, keyboard access | Server Actions calling fixed backend endpoints server-side; Origin/Sec-Fetch-Site check in `proxy.ts` plus Next.js's own Server Action origin check (D6); text-only rendering; pending-state submit buttons; labelled form controls | unit, static, E2E |
| R12 | Phase 4C readiness | Strict `rule` validation (D1), deterministic serialization, stable ids, `GET /api/v1/datasets/{id}/cases` | integration |

### 2.4 Gap analysis — where the locked contracts are silent, and the Phase 4B decision

None of these gaps needs a new table, column, enum value or relationship, and the approved schema can safely store human-supplied test content (`sanitized_input` with mandatory server redaction, `expected_output`/`rule` JSONB). So the stop condition of the instruction is **not** met. Each decision below is the narrowest reading of the contract and is recorded for the architectural review.

| ID | Gap | Decision (Phase 4B) |
|---|---|---|
| D1 | `rule` JSONB: only `rule->>'type'` is defined; the parameters of each type are not | Minimal payload per type, following the semantics in `CLAUDE_PHASE_PROMPTS.md` §5 ("JSON Schema, expected field/value match, exact-match/regex"): `{"type":"json_schema","schema":<object\|boolean>}`, `{"type":"field_match","fields":[<dotted path>…]}` (compared against `expected_output`), `{"type":"exact"}`, `{"type":"regex","pattern":<string>}`. Unknown keys are refused. No evaluation is implemented. JSON Schema meta-validation needs `jsonschema` (not installed); 4B checks structure only and refuses non-local `$ref` values; full meta-validation is a 4C item |
| D2 | No endpoint creates or lists datasets or lists cases | Additive admin endpoints, as `GET /api/v1/projects` was added in Phase 3C: `GET`/`POST /api/v1/datasets`, `GET /api/v1/datasets/{id}/cases` |
| D3 | Review state transitions are not specified beyond the enums and the closed ⇒ decided check | `PATCH` may set any enum values whose result satisfies the table check; reopening a closed review is refused with `409` when another non-closed review of that issue type exists (the partial unique index). `evidence`/`hypothesis` are immutable after creation (the contract lists PATCH for status/decision only) |
| D4 | "Appropriate human review state" for conversion is not specified | A test case may reference a review only when its `reviewer_decision` is not `pending` (an explicit human decision was recorded), and the review must belong to the same project and the same `origin_trace_id`. A trace-origin case without a review is refused |
| D5 | No duplicate rule for test cases (§4.10 has no unique key) | One case per (dataset, review): checked inside the insert transaction under `pg_advisory_xact_lock` (no schema change; the pattern of migration `0002`). Second conversion → `409` |
| D6 | The dashboard is GET/HEAD-only with no Server Functions (§5.3, `SECURITY.md` §5), so it cannot write | Narrow Server Actions, each bound to one fixed backend write endpoint with server-side re-validation; `proxy.ts` allows `POST` only on the pages that host the forms and only when `Origin` equals the loopback host and `Sec-Fetch-Site` (when sent) is `same-origin`; Next.js also rejects Server Actions whose `Origin` does not match `Host`. No route handlers, no generic proxy. Documented as an amendment of §5.3 |
| D7 | Request-body limits for `/api/v1` writes are not specified | JSON only (`415` otherwise), 64 KiB cap (`413`), strict schemas with unknown fields refused, `422` naming fields only |
| D8 | Who changed a review's status/decision is not recorded (no column) | Not invented. `updated_at` records when; the limitation is documented. `created_by`/`approved_by` stay self-declared as the contract says |
| D9 | Redaction of structured fields (`expected_output`, `rule`) is not specified | Masking an assertion would change its meaning, so string values in these fields that match the credential/personal-data heuristics are **refused** (`422`) instead of masked; free text (`sanitized_input`, `evidence`, `hypothesis`) is **masked** as the contract says. Identity fields (`created_by`, `approved_by`) and dataset names refuse such values |

## 3. Persistence and API (backend)

**Schema (migration `0003`, `backend/alembic/versions/0003_phase4b_reviews_datasets_cases.py`).** `test_datasets`, `failure_reviews` and `test_cases` exactly as `ARCHITECTURE.md` §4.8–4.10 (the contract test parses the document and compares every column, type, nullability and default). All §4.9 checks, the closed ⇒ decided table check, `INDEX(project_id, trace_id)` and the partial `UNIQUE(project_id, trace_id, issue_type) WHERE review_status <> 'closed'` are in the database; `test_cases.origin_review_id` is `ON DELETE SET NULL`; traces are referenced softly (no FK). §4.11–4.12 (experiments) are not created. No Phase 1–3 table is altered. The rule-type check is written `COALESCE(rule->>'type','') IN (…)` because the bare expression lets a typeless rule through as NULL (§4.10 note).

**Endpoints (§5.7).** `POST /api/v1/failure-reviews`, `PATCH /api/v1/failure-reviews/{id}`, `POST /api/v1/datasets/{id}/cases` (contract) plus `GET`/`POST /api/v1/datasets` and `GET /api/v1/datasets/{id}/cases` (additive, D2). `require_admin` first; mandatory `project`; one byte-identical `404` for unknown or foreign records; strict JSON bodies (D7); writes in one transaction that commits only on success; `409` codes `duplicate_open_review`, `duplicate_dataset`, `duplicate_test_case`, `review_not_decided`. `GET /api/v1/traces/{id}` now returns `reviews` (with linked test cases) and `reviews_truncated`; list items carry `reviews: {total, not_closed}`.

**CR-03 compliance.** Only the six `issue_type` values (API check and DB `CHECK`); a new review is always `open`/`pending`, so creation cannot pre-decide anything; status and decision change only by `PATCH`, a separate explicit action whose result must satisfy the table check; `evidence`/`hypothesis` are immutable after creation; the UI and API wording keep "hypothesis supported" distinct from a proven root cause (ADR-10).

**Human approval guarantees (T34, D4, D5).** A test case is written only by `POST /api/v1/datasets/{id}/cases` with `approved` equal to the JSON boolean `true` (missing, `false`, `null`, `"true"`, `1` → `422`, no row), an `approved_by` label and a non-empty input. A case that cites a trace must cite a review of that trace in the same project whose decision is not `pending`. One case per (dataset, review), serialized by `pg_advisory_xact_lock`. In the dashboard the request is sent only after the approval box is ticked. `approved_at` is server time.

**Test-case data contract (D1).** `sanitized_input` (≤ 8000, masked), `expected_output` (optional JSON, ≤ 16 KiB, depth ≤ 32), `rule` one of `{"type":"json_schema","schema":…}` (local `$ref` only), `{"type":"field_match","fields":[…]}` (paths must exist in `expected_output` when one is given), `{"type":"exact"}`, `{"type":"regex","pattern":…}`; unknown keys refused; `origin_trace_id` + `origin_review_id` (both or neither); `redaction_applied = true`. Cases are returned exactly as stored, ordered by id, byte-identical on repeated reads, with keyset pagination: stable identity for Phase 4C. Nothing is evaluated.

## 4. Diagnostics versus human review

- The diagnostics endpoint is unchanged and still writes nothing. The review side never imports the diagnostics package and vice versa (`tests/unit/test_phase4b_boundaries.py`, AST import checks); `insert_review`/`update_review` are called only from `api/reviews.py`, `insert_case` only from `api/datasets.py`; ingest, OTLP and read paths never mention the new tables.
- Integration: a failing trace (failed inference, failed validation) plus its diagnostics, detail and list reads leaves `failure_reviews` empty, and the diagnostic categories share no value with `issue_type`; repeated reads leave the review rows' `xmin` unchanged (no hidden writes from GET).
- UI: the "Machine observations — Automatic · Unverified · Not a human review" panel (dashed, muted) is separate from the "Human failure review — Human decision" section; every hypothesis is shown as "Possible explanation (not verified)" with limitations and evidence-span links into the waterfall; the panel contains no form control; the review form starts with no issue type and empty text; the frontend refuses diagnostic categories as issue types. The decoder accepts only `llminsight.diagnostics.v1` with `verified: false`, `language_model_used: false`, `content_used: false` (checked on recorded real responses).

## 5. Dashboard workflow and web security

- **Pages:** Trace Detail (review state near the top; at the end, the diagnostics panel, existing reviews with a decision form each, "Record a new review"); `/traces/{id}/reviews/{id}/test-case` (references-only source panel, "telemetry holds no prompt or completion" notice, empty test-case form, dataset creation); `/datasets` and `/datasets/{id}` (cases with provenance, redacted input, rule, "Not evaluated yet"); a "Human review" column on the trace list; "Datasets" in the navigation.
- **Server Actions (§5.8):** exactly `submitReview`, `submitDecision`, `submitDataset`, `submitTestCase` (four ids in the build manifest), each re-validating its `FormData` and calling one allowlisted write (`sendJson`: one method per path). No route handlers; the token never reaches the browser (bundle scan 0; no token in any E2E response body).
- **CSRF:** `proxy.ts` accepts `POST` only on the three form pages and only with `Origin` equal to the loopback host, `Sec-Fetch-Site` same-origin when sent, and the `Next-Action` header; otherwise `403` (`405` for other paths and methods). Next.js's own Origin/Host check remains; action bodies ≤ 128 KB.
- **XSS:** all stored text is rendered as React text; hostile markup in evidence, reviewer names, span names, dataset names and test content renders literally (unit and live E2E with a `window.__llmiXss` probe).
- **Double submission and honesty:** submit disabled while pending; refused submissions keep the typed values; success clears the form; a timeout after sending says the outcome is unknown.
- **Accessibility:** labelled controls with hints, keyboard-only submission (Enter), focus order to the submit button, one `h1`, captioned tables (E2E).

Screenshots (real backend, SYNTHETIC run-owned data): `reports/phase-4b-screenshots/phase4b-01-trace-unreviewed.png`, `-02-review-recorded.png`, `-03-test-case-approved.png`, `-04-dataset.png`.

## 6. Tests and checks (final state)

| Command (directory) | Result |
|---|---|
| `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` (backend) | **1425 passed**, 0 failed, 0 skipped (211 s), exit 0 |
| same (sdk) | **350 passed**, 0 failed, 0 skipped (77 s), exit 0 |
| `npm run typecheck` / `npm run lint` (frontend) | exit 0 / exit 0 (0 warnings) |
| `npm test` (frontend) | **268 pass**, 0 fail, 0 skipped, exit 0 |
| `npm run build` / `npm run check:bundle` | exit 0 / 546 files scanned, findings 0, exit 0 |
| `npm audit --omit=dev` | 0 vulnerabilities |
| `npm run test:e2e` (real backend on :8000, Edge) | **48 passed** (44 existing + 4 Phase 4B), 0 failed, exit 0; teardown deleted the run's projects, `remaining: []` |
| `ruff check .` / `ruff format --check .` (backend, sdk) | exit 0 / exit 0 (both) |
| `python -m mypy src` and `--config pyproject.toml ../demo_app` (sdk, strict) | no issues (8 and 3 files), exit 0 |
| `python -m pip check` (backend, sdk) | no broken requirements |
| `alembic upgrade head` / `alembic current` / `alembic check` (dev DB) | `0002 → 0003`, `0003 (head)`, "No new upgrade operations detected" |
| `docker compose config --quiet` / `--profile app config --quiet` | exit 0 / exit 0 |
| `python -I scripts/verify_phase0.py --quiet` | 732 PASS, 0 FAIL |
| Secret / privacy scan (all tracked and new files, raw bytes incl. PNGs) | 0 occurrences of every local secret (DB password and URL, pepper, admin/API token); 0 key-shaped strings; the only value hits are the non-secret service name and header placeholder already in `.env.example`; 3 e-mail-shaped strings in pre-existing, unchanged test files |
| Invisible/bidi character scan (sources and tests) | 0; now a permanent static test |
| Backend dev log during E2E | 0 tracebacks; no review or test-case text logged (request lines only) |
| `git diff --check` (tracked changes and new files) | clean |

**New and changed tests.** Backend +178: `test_review_api.py` 53, `test_dataset_api.py` 49, `test_redaction.py` 32, `test_case_rules.py` 28, `test_phase4b_boundaries.py` 5, migration cases +11. Frontend unit +31: `phase4b.test.tsx` 27, static boundary tests +3, decoder +1. E2E +4 (`phase4b.spec.ts`). Pinned shape tests were updated for the documented additive fields (route set, list-item `reviews`, detail `reviews_truncated`); the static "no Server Actions" test was replaced by the narrower §5.8 rules; the three recorded trace fixtures were re-recorded from the real backend with the same requests (identical apart from the new fields); a recorded diagnostics fixture was added.

**Coverage of the instruction's test list:** creation and retrieval (T33, detail, list), authorization (`401` before validation), `issue_type` validation, no diagnostics-to-review writes, missing/invalid/foreign trace, span, review and dataset references (identical `404`s), confirmation and approval (T34, UI tick), redaction (T35, review text, refusal in assertions and labels), required fields, duplicate conversion (sequential, and 8 concurrent → 1 row), rollback (failure after insert → `503`, 0 rows), cross-project attempts, XSS, CSRF (unit and live `403`s), metadata-only guarantee (no content attribute read; nothing copied), cost/usage/price immutability (byte-identical dumps), trace and diagnostics API regressions (full suites), keyboard accessibility, browser-to-backend flow.

**Mutation checks** (scratch scripts; each file restored byte for byte, md5-verified): backend 9/9 killed (approval check, input masking, review masking, advisory lock, duplicate check, project filter on review lookup, pending-review gate, API `issue_type` check, closed-without-decision); frontend 4/4 killed (Origin check, approval tick, review text as HTML, diagnostic category accepted as issue type).

## 7. T33–T40 (exact criteria of `TEST_MATRIX.md`)

| ID | Status | Evidence |
|---|---|---|
| T33★ | **PASS (phase-4b)** | every enum → `201`; invalid `issue_type` → `422` naming the field, no echo, no row; duplicate open → `409` (also while `in_review`); allowed again after closing; E2E duplicate refusal |
| T34★⛔ | **PASS (phase-4b)** | missing / false / null / string / number `approved` → `422` `["approved"]`, 0 rows; the UI refuses without the tick and stores nothing (E2E, API-checked) |
| T35★⛔ | **PASS (phase-4b)** | e-mail, phone, key and card number masked before insert; DB dump and response contain none of them; `redaction_applied = true`; E2E phone masked |
| T36★ | NOT RUN | rule engine is Phase 4C (rules are only stored and validated) |
| T37★ | NOT RUN | Phase 4C (`evaluation_detail` needs `experiment_results`) |
| T38 | NOT RUN | Phase 4C (A/B runs) |
| T39★ | NOT RUN | Phase 4C (evaluation results) |
| T40 | NOT RUN | Phase 4C; 4B only verifies the precondition (a case without `expected_output` is stored and labelled "not evaluated" in the UI) |

T31 (migrations), T41 (XSS) and T42 (server-only secret) were re-verified on the new code; their recorded statuses are unchanged.

## 8. Migration evidence

- Disposable DBs: up → down to `0002` → `0001` → base → up, schema identical; `0002 → 0003` with telemetry, a SYNTHETIC price and a computed usage row: every Phase 1–2 row byte-identical after upgrade and after downgrade; downgrade refused (transaction rolled back, version stays `0003`) while any review, dataset or test case exists; FK, unique, index and 78 check constraints match the models; DB-level enum, length, hex, closed-needs-decision and rule-type checks; `ON DELETE SET NULL` keeps the trace reference.
- Dev DB: upgraded `0002 → 0003` once; row counts and content checksums of `projects`, `project_api_keys`, `traces`, `spans`, `usage_records`, `model_prices` identical before the upgrade, after it and after all E2E runs; the three new tables hold 0 rows.

## 9. Privacy and redaction controls and limits

Free text is masked (credential, e-mail, IBAN, phone, 10+ digit numbers) with counts reported to the user; assertions and labels are refused instead of masked (D9); unsafe characters are refused; nothing is read from or copied out of telemetry, which never stored prompts, completions, system instructions, tool definitions or prompt variables (ADR-19). Limits: the heuristics miss names, addresses and other free-form personal data and can over-mask harmless long numbers (R56); identities are self-declared and the decision actor is not recorded (R55, D8).

## 10. Risks (new or changed; `SOURCES_AND_RISKS.md`)

R53 resolved (diagnostics now visible in the dashboard). New: R54 dashboard writes without login (loopback trust), R55 self-declared identities and no decision-actor column, R56 heuristic redaction, R57 rule payload defined in 4B without JSON Schema meta-validation, and a regex evaluation (ReDoS) risk for 4C, R58 duplicate rule enforced by the API lock (no DB constraint; standalone cases not deduplicated), R59 forms need JavaScript.

## 11. Phase 4C prerequisites

1. Architectural decision on D1–D9 (especially the `rule` payload D1, the conversion gate D4 and the duplicate rule D5).
2. Install `jsonschema` (pinned candidate 4.26.0, two-week rule) for schema meta-validation and evaluation; bound regex evaluation time and input size (R57).
3. Migration for §4.11–4.12 (`experiments`, `experiment_results`) with the same data and downgrade tests.
4. Deterministic engine for the four rule types, `passed = NULL` when the expected output a rule needs is missing (T40), mock-only runs labelled synthetic (T39), identical case ids for both prompt versions (T38).
5. A synthetic dataset of 10+ cases (cases may be standalone, without a source trace).

## 12. Files

New backend: `alembic/versions/0003_phase4b_reviews_datasets_cases.py`, `app/models/{review,dataset}.py`, `app/repositories/{reviews,datasets}.py`, `app/services/{redaction,case_rules}.py`, `app/api/{reviews,datasets,human_input,json_body}.py`, and the tests in §6. Changed backend: `app/main.py` (routers), `app/models/__init__.py`, `app/api/traces.py` (reviews on detail and list), `app/services/ingestion/sensitive.py` (public aliases of the existing patterns; detection unchanged), test fixtures and pinned tests. New frontend: `app/datasets/**`, `app/traces/[traceId]/reviews/**`, `components/{reviews,datasets}/*`, `lib/actions/{reviews,datasets}.ts`, `lib/server/{reviews,datasets,review-page}.ts`, `lib/api/{review-types,review-schema,diagnostics,ids}.ts`, `lib/{review-forms,action-state}.ts`, tests and fixtures. Changed frontend: `proxy.ts`, `next.config.ts`, `lib/request-guard.ts`, `lib/server/backend.ts`, `lib/api/{result,trace-schema,trace-types}.ts`, the Trace Detail page, the trace list, the side navigation, the README. Docs: `ARCHITECTURE.md` (§4.10 note, §5 table, §5.1, §5.3, §5.6, new §5.7 and §5.8, §7), `SECURITY.md`, `TEST_MATRIX.md`, `SOURCES_AND_RISKS.md`, `PHASE_STATUS.md`, `DEMO_SCRIPT.md`, this report and its screenshots. `ADR.md` is unchanged: no locked decision was altered, and D1–D9 are recorded here for review rather than added as ADRs without approval.

## 13. Interruption and resumption

All implementation, tests and verification in §3–§8 were completed on 2026-10-25 before the usage limit. On resuming (2026-10-26) the working tree was inspected: own root, `main` at `888b188`, nothing staged, all Phase 4B files present, tags unchanged, dev DB `0003`, no TODO/FIXME. File timestamps confirmed that every final run in §6 started after the last code change. Work after resuming: `PHASE_STATUS.md`, sections 1 and 3–14 of this report, the doc check, `git diff --check` and the commit. No code was changed after the final test runs.

## 14. Verdict

**PASS for the Phase 4B scope** on the evidence above: CR-03 review and human-approved Trace-to-Testset work end to end against the real backend; T33, T34 and T35 pass; T36–T40 remain NOT RUN for Phase 4C. Decisions D1–D9 await the architectural review. No tag, no push, Phase 4C not started.
