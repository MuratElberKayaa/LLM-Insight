# Phase 0 — Delivery Report

> **Reading note:** §1–§10 are the original delivery record at tag `phase-0` (commit `a16d64b`) and are kept unchanged as history. Some of them are now superseded: PADR-01…09 are accepted as ADR-13…21, there are 42 critical tests (not 39), and the Docker Engine was found not running. **§11 is the current state** after the ADR gate closure.

- **Date:** 2026-10-24
- **Commit/tag:** recorded in the Phase 0 Completion Report message. The commit cannot contain its own hash. Look it up with `git log -1 phase-0`.
- **Scope executed:** Phase 0 only (definition). No implementation code was written, no dependencies were installed, no paid APIs were called, and Phase 1 was not started.

## 1. Created / changed files

| File | Change |
|---|---|
| `.git/` (project-local) | `git init` in the project folder. Verified isolated from the home-directory repo |
| `.gitignore` | New. Secrets, Python, Node/Next, Playwright, DB dumps, OS/editor files |
| `.env.example` | New. Placeholders only (`CHANGE_ME` / empty) |
| `CLAUDE.md` | Edited. Added a "Git safety" section and a contract index (≤ 60 lines). Existing rules unchanged |
| `docs/PRD.md` | New |
| `docs/ADR.md` | New. ADR-01…12 confirmed, CR-01…09 accepted, PADR-01…09 proposed |
| `docs/ARCHITECTURE.md` | New. Diagrams, DB contract, API contract, version pins, per-phase layout |
| `docs/TELEMETRY_CONTRACT.md` | New. OTLP/HTTP and GenAI mapping, verified against official docs |
| `docs/METRICS_CATALOG.md` | New. M01–M19 P0, M20–M22 P1, P2 placeholders |
| `docs/SECURITY.md` | New |
| `docs/TEST_MATRIX.md` | New. T01–T48 with 39 critical and 10 blockers |
| `docs/DEMO_SCRIPT.md` | New. Mock demo scenario draft |
| `docs/SOURCES_AND_RISKS.md` | New. Consulted sources and risk register R01–R13 |
| `docs/PHASE_STATUS.md` | New. State plus checklists for phases 0–5 |
| `docs/fixtures/otlp_trace_3span.json`, `docs/fixtures/README.md` | New. Synthetic 3-span OTLP fixture and its expected results |
| `docs/reports/phase-0.md` | This file |
| `README_START_HERE.md`, `docs/MVP_MASTER_PLAN.md`, `docs/CLAUDE_PHASE_PROMPTS.md` | Unchanged; committed as received |

## 2. Locked ADRs / contracts affected

- **Locked ADRs:** ADR-01…12 are unchanged.
- **Accepted by the user (2026-10-24):** CR-01…09. CR-03 renames the failure-review fields from master plan §E (`category` and `status` become `issue_type`, `review_status` and `reviewer_decision`).
- **Proposed, not applied:** PADR-01…09. They cover span naming, `gen_ai.provider.name` plus aliases, the semconv pin, protobuf-only (415), lower body limits, no partial_success, the never-persist-content rule, promotion of T15/T16/T44 to critical, and additive DB columns.

## 3. What works, confirmed by commands

The repository is isolated, the docs exist and are internally consistent, and the fixture is structurally valid. The results are in §4. Nothing executable exists yet, so there are no functional claims.

## 4. Commands and exact outcomes

| # | Command | Outcome |
|---|---|---|
| 1 | `git init` (project folder) | `Initialized empty Git repository in C:/Users/PC/Desktop/LLM_Insight_Claude_Code_MVP_Paketi/.git/` |
| 2 | `git rev-parse --show-toplevel` | `C:/Users/PC/Desktop/LLM_Insight_Claude_Code_MVP_Paketi` (PASS: isolated) |
| 3 | `git remote` | empty (PASS: no remote, nothing can be pushed) |
| 4 | `git check-ignore -v .env` / `.env.local` / `.env.example` | `.env` ignored by `.gitignore:2`; `.env.local` by `.gitignore:3`; `.env.example` re-included by `!.env.example` (PASS) |
| 5 | `python -I verify_phase0.py <repo>` (scratchpad script, not committed) | exit 0, **119 PASS / 0 FAIL**, `SUMMARY: 0 FAIL`. The first run had 2 FAIL: (a) this report did not exist yet; (b) a check bug that matched `NEXT_PUBLIC_` inside warning comments. The check was fixed to inspect variable names only; the docs were not changed to make it pass |

The verification script checks:
- All 18 required files exist.
- T01–T48 are unique and in order.
- The critical set equals the master-plan starred set (39), and the blocker set equals the master-plan blocker list (10, a subset of critical).
- CR-03 field names and `issue_type` values are present.
- Legacy GenAI names appear only in alias or "not used" context.
- Every cross-referenced doc and every PADR/CR ID resolves.
- Fixture structure: 3 spans, hex IDs, parent links, timestamps, required semconv attributes, no content attributes, labeled synthetic.
- `CLAUDE.md` is ≤ 60 lines.
- No secret patterns appear in any file, and every secret-bearing `.env.example` key is a placeholder with no `NEXT_PUBLIC_*` keys.

**Read-only external checks:** I fetched the OTLP spec (web page plus raw markdown, with key sentences confirmed by grep), the GenAI semconv pages, the PyPI and npm registry JSON, and endoflife.date. Details are in `SOURCES_AND_RISKS.md`.

**NOT RUN:** protobuf parsing of the fixture (no `opentelemetry-proto` installed; Phase 1, T01), any pytest/ruff/npm command (no code exists), and `docker info`.

## 5. Manual test results

None. Phase 0 has no runnable artifact.

## 6. Security & cost risks

See `SOURCES_AND_RISKS.md` R01–R13. The top risks:
- **R01:** home-directory repo contamination. Mitigated by the isolated repo and the CLAUDE.md rule.
- **R03:** ambiguity in the exporter timeout units.
- **R04:** no verified prices, so costs show `unknown`.
- **R06:** Docker daemon state unknown.

No paid API calls were made. Infrastructure cost: $0.

## 7. Deviations and ADR proposals

- PADR-01…09 await a decision (see `ADR.md` §3).
- Two documented deviations from OTLP recommendations are pending approval: JSON is not accepted (spec SHOULD), and the body limit is below the RECOMMENDED 64 MiB.

## 8. Open issues

| Priority | Issue |
|---|---|
| High | PADR-01…09 decision needed before Phase 1 migrations (PADR-09 changes table columns) |
| High | Docker daemon availability unverified (R06) |
| Medium | TypeScript major version for Next 16 (Phase 3 decision, R07) |
| Medium | Exporter timeout env units (Phase 2, R03) |
| Low | Node 22.11.0 patch update before Phase 3 (R12) |
| Info | The home-directory repo `C:/Users/PC` has a 0-byte index and DailyDietAPP remote. It is out of scope and untouched; you may want to inspect it separately |

## 9. Phase 1 prerequisite checklist

- [ ] User approves the Phase 0 deliverables
- [ ] User decides PADR-01…09
- [ ] Docker Desktop running (`docker info` succeeds), with disk space for the `postgres:17` image
- [ ] Python 3.12 virtual environment for `backend/` (created in Phase 1)
- [ ] Approval to install pinned backend dependencies from `ARCHITECTURE.md` §6, re-checked at install time
- [ ] Local `.env` created from `.env.example` with generated `LLMI_KEY_PEPPER`, `LLMI_ADMIN_TOKEN` and `POSTGRES_PASSWORD` (user-side; never committed)

## 10. Verdict

**PASS (Phase 0 documentation gate), pending user review.** This verdict rests on the verification output in §4, item 5. It says nothing about any implementation, which does not exist yet.

## 11. Phase 0 closure — ADR decision gate (2026-10-24)

The user approved the final ADRs on 2026-10-24. This work is a separate documentation commit on top of `phase-0`. The `phase-0` tag was **not** moved or amended.

### 11.1 Files changed in the closure commit

| File | Change |
|---|---|
| `docs/ADR.md` | PADR-01…09 → accepted ADR-13…21, each with context, decision, alternatives, consequences, affected files and tests. CR-04 amended to 42 critical tests |
| `docs/TELEMETRY_CONTRACT.md` | New §0 support statement (limited interoperability). Processing order. Response encoding that honours the request Content-Type. `415` behaviour. `BadRequest` details without values. Count-based `413` policy. 200 only after commit. Alias precedence. Call identity with retry-versus-re-export rules and fallback limitations. Provenance with `mixed` |
| `docs/ARCHITECTURE.md` | §4 is now a column-level DB contract: type, nullability, default, check, uniqueness/index, origin (§E / ADD with reason / CR-03) and tests for every column. BIGSERIAL surrogate keys, with OTel IDs as TEXT external identifiers. `server_dropped_attributes_count`. Added `googleapis-common-protos` pin |
| `docs/METRICS_CATALOG.md` | Provenance aggregation rules; event-time windows; M02 retry/re-export note; M19 rename |
| `docs/TEST_MATRIX.md` | T15, T16, T44 ★ (42 critical); 17 protocol / dedup / provenance sub-cases |
| `docs/SECURITY.md` | Auth-failure limiter; key-scoped ingest bucket; redaction of review text; new threat rows |
| `docs/SOURCES_AND_RISKS.md` | `google.rpc` and OTLP re-read sources; risks R14–R18; R06 narrowed |
| `docs/PRD.md`, `docs/PHASE_STATUS.md`, `CLAUDE.md`, `.env.example`, `docs/fixtures/README.md` | 42 critical; status; doc index; `LLMI_RATE_LIMIT_AUTHFAIL_PER_MIN`; fixture expectations (response-ID key, provenance, renamed counter) |
| `scripts/verify_phase0.py` | **New.** Reproducible, stdlib-only documentation check |
| `docs/reports/phase-0.md` | Reading note and this §11 |

### 11.2 Final ADR status

All of these are accepted, and none is open.
- **Locked:** ADR-01…12.
- **Contract resolutions:** CR-01…09 (CR-04 amended by ADR-20).
- **Phase 0 proposals:** ADR-13 (span naming/detection), ADR-14 (provider attribute; the standard name beats the alias), ADR-15 (semconv pin `9ffc7e00c639`, `llminsight.attrmap.v1`), ADR-16 (binary protobuf only), ADR-17 (4 MiB / 16 MiB / 2048 spans → 413), ADR-18 (atomic, 200 after commit, no partial success), ADR-19 (never persist content), ADR-20 (42 critical), ADR-21 (complete DB contract).

### 11.3 OTLP 1.11.1 conformance after the corrections (re-verified from the raw spec on 2026-10-24)

| Spec rule | Status |
|---|---|
| Success: `200` + `ExportTraceServiceResponse`, `partial_success` unset | Compliant |
| Response uses the request's Content-Type; `application/json` for JSON payloads | Compliant for both OTLP media types: a JSON request gets a JSON-encoded `415` Status. **Limitation:** for non-OTLP media types, both MUSTs can't be met, so a binary Status is returned |
| 4xx/5xx body is a `Status` message | Compliant |
| `400` for bad data, `BadRequest` in details (SHOULD) | Compliant; field paths, rule text and reason codes only |
| MUST limit size incl. after decompression → `413` | Compliant. 4/16 MiB is below the RECOMMENDED 64 MiB (allowed). The count-based `413` is our policy |
| Servers SHOULD accept JSON | **Deviation** (ADR-16): documented as limited interoperability |
| Retryable codes 429/502/503/504; others not retried | Compliant (`429` and `503` carry Retry-After) |
| Empty request → success (SHOULD) | Compliant |

### 11.4 Verification commands and results

| # | Command | Result |
|---|---|---|
| 1 | `python -I scripts/verify_phase0.py --quiet` (first run) | exit 1: 658 PASS, 2 FAIL. (a) The check treated PRD's master-plan floor "At least 24 critical" as a stale count, so the check was narrowed to exclude "at least N" floors. (b) PHASE_STATUS referenced PADR without the former-name mapping, so the **doc** was fixed |
| 2 | `python -I scripts/verify_phase0.py --quiet` (after fixes) | exit 0: **660 PASS, 0 FAIL** |
| 3 | Mutation test on scratch copies (not committed), one defect each: T15 star removed; `cost_status=zero`; §E column `evidence` deleted; `ADD` without reason; `sk-…` key in a doc; real value in `.env.example`; `ADR-99` reference; stale `PADR-04`; bare `dropped_attributes_count`; UUID primary key | All 10 mutations → exit 1 with the expected FAIL line. Unmodified baseline → exit 0 |
| 4 | `docker info` (during the ADR review) | exit 1. Client 28.5.1 present. Server: `open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.` Engine not running (R18). Nothing was started |

The final run on the committed tree is recorded in the closure hand-off message together with the commit hash.

### 11.5 Remaining risks

- **R18:** Docker Engine not running (blocks Phase 1).
- **R14:** exact-match pricing can leave cost `unknown`.
- **R15:** limited OTLP interoperability.
- **R16:** provenance is client-asserted.
- **R17:** double-instrumented failed attempts are not deduplicated.
- **R03:** exporter timeout units.
- **R07:** TypeScript version.
- **R12:** Node patch level.
- **R01:** home-directory repo, still untouched.

### 11.6 Phase 1 prerequisites

- [ ] The user reviews this closure commit
- [ ] Docker Desktop started; `docker info` shows a Server section
- [ ] Approval to install the pinned backend dependencies (`ARCHITECTURE.md` §6), re-checked at install time
- [ ] A local `.env` created from `.env.example` with generated secrets (user-side, never committed)

### 11.7 Verdict

**Phase 0 documentation gate: PASS. ADR gate: CLOSED.** Both rest on the verification output in §11.4. **Phase 1 has not started:** no application code, migrations, dependencies or containers.
