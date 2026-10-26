# LLM Insight — Final MVP Delivery Report

| | |
|---|---|
| Product | LLM Insight: local observability and evaluation MVP for LLM applications |
| Release | Phase 5 final (MVP closure); expected annotated tag `phase-5` |
| Scope of acceptance | **Local, offline-only MVP.** Mock providers only; all demo and experiment data SYNTHETIC |
| Date | 2026-10-27 (UTC) |
| Baseline | `e7f8dcf` (Phase 5B) on `main`; previous release tag `phase-4` → `394920c`; database migration `0005 (head)` |
| Repository | https://github.com/MuratElberKayaa/LLM-Insight (public) |
| Release status at the time of writing | Local gates **verified** (§10, §14, §20). GitHub Actions run of the closure commit and the `phase-5` tag: **pending**, verified only after the push. The verified run is recorded in the `phase-5` tag annotation, never in this file before it exists |

Detailed evidence lives in the phase reports (`docs/reports/`, in particular `phase-4-final.md`, `phase-5a.md` and `phase-5b.md`). This document summarizes that evidence and adds the final release checks of Phase 5. Nothing here is a claim of production readiness.

---

## 1. Executive summary

LLM Insight implements the locked MVP loop **Observe → Diagnose → Evaluate → Improve** as a FastAPI modular monolith, an independent Python OpenTelemetry SDK, PostgreSQL with Alembic, and a Next.js dashboard.

- **Acceptance:** all 48 scenarios of the locked test matrix pass, including all 42 critical tests and all 10 presentation blockers, with no critical failure and no critical skip (§10).
- **Final local regression on the release tree** (§20):
  - backend 1726 passed;
  - SDK 350 passed;
  - frontend unit 311 passed;
  - Playwright end-to-end 55 passed;
  - 0 failed and 0 skipped in every suite.
- **Security audit verdict: no open finding above Low** (§11).
  - The `certifi` advisory (R74) is fixed with the approved security exception (`certifi` 2026.10.19), verified by hash in both lock files, both venvs and a backend image built from the release tree.
  - The `pip` 24.2 advisories, the unaudited interpreter and OS packages, and the unhashed locks (R75) are accepted for **local use only**. The same applies to the large trace page (R76) and the malformed Server Action argument case (R77).
- **Limits of this release:**
  - It is authorized for local use only.
  - Real-provider integration has never been exercised.
  - Costs are estimates.
  - Reviewer identities are self-declared.
  - Redaction is heuristic.

## 2. Problem and product purpose

Teams that ship LLM features often cannot answer, with evidence:

- which call was slow or failed, and what might explain it;
- what it cost, and whether that number is actually known;
- whether a prompt change improved quality on the same cases.

Generic APM tools do not model tokens, cost provenance or prompt versions. Hosted LLM tools often capture prompts by default.

LLM Insight's purpose is a **verifiable**, metadata-only alternative for a single engineer or team on a local machine. Every number on the dashboard is backed by stored rows and labelled with its provenance. A missing cost is shown as unknown, never as zero. Quality is measured by deterministic rules on human-approved cases, never by an LLM judge (`PRD.md`, ADR-10).

## 3. Implemented MVP features

| Area | Delivered capability | Phase |
|---|---|---|
| Ingest | `POST /v1/traces` OTLP/HTTP **binary protobuf** (real `opentelemetry-proto` types, protobuf `ExportTraceServiceResponse` and `google.rpc.Status`/`BadRequest`); gzip with a bounded inflater; limits 4 MiB wire / 16 MiB decompressed / 2048 spans (`413`); 10 s body-read deadline (`408`); atomic batches; `200` only after COMMIT | 1 |
| Identity | Projects and ingest keys (`lli_<prefix>_<secret>`, 256-bit), HMAC-SHA256 with a pepper, constant-time verification, revocation; CLI `create-key`, `list-keys`, `revoke-key` | 1 |
| Ledger | Idempotent spans and traces, one usage row per physical inference attempt, response-ID-first call keys, orphan spans kept as orphans | 1–2 |
| SDK | Independent `llm-insight-sdk`: private tracer provider, bounded `BatchSpanProcessor`, explicit export timeout, fail-open, CLIENT `chat {model}` spans with `gen_ai.*` and provenance, deterministic mock provider, OpenAI Chat Completions adapter (offline-tested only), FastAPI demo app | 2 |
| Cost | Versioned, append-only price catalogue (`openai-2026-10-24`, 6 verified rows), exact `Decimal` cost, cached-input and reasoning-output subsets priced once, unknown cost = `NULL` + reason | 2 |
| Metrics | M01–M15 (counts, error rates, p50/p95/p99 with `n`, known cost with unknown counts, usage mix, model comparison) | 2–3 |
| Dashboard | Overview, model comparison, project selector, trace explorer (cursor pagination, filters, URL state), trace detail (span tree, waterfall, attempts, usage, provenance) | 3 |
| Diagnose | `GET /api/v1/traces/{id}/diagnostics`: 18 deterministic, metadata-only rules; findings with evidence spans and `verified: false` hypotheses; shown as "Automatic · Unverified · Not a human review" | 4A–4B |
| Review | Human failure review (CR-03 enums), decision workflow, redaction of evidence and hypothesis, duplicate-open `409` | 4B |
| Testset | Human-approved trace-to-testset with typed synthetic input, server-side redaction, lineage to review and trace, one case per (dataset, review) in the database | 4B–4C |
| Evaluate | Rule contract `llminsight.rule.v1`: restricted JSON Schema 2020-12 subset, field, exact and RE2-only regex rules; 12-case SYNTHETIC golden dataset | 4C |
| Improve | Mock-only A/B experiments of two prompt versions on identical case ids, bounded execution (4 concurrent runs, 15 s), honest report and experiment pages | 4C–4D |
| Hardening | Rate limits with `429` + `Retry-After` on every route (T43); Server Action guard fixes (R68, R71); ADR-27 | 4–5A |
| Delivery | README, demo script, security audit, benchmark, clean-clone acceptance, this report and `PROJECT_PITCH.md` | 5 |

Not built (out of MVP scope by contract): ClickHouse, Redis, S3, Kafka, Kubernetes, a mandatory Collector, a second provider, LLM-as-judge, an "Optimization Lab" subsystem, user login, streaming metrics, alerts, retention job.

## 4. Observe → Diagnose → Evaluate → Improve workflow

1. **Observe.** An instrumented FastAPI app (the demo or the user's own) creates a SERVER request span. The SDK adds one CLIENT inference span per physical provider attempt, carrying metadata only: model, tokens, status, `error.type`, call id, attempt, prompt version, provenance. The spans go over OTLP/HTTP protobuf with `X-Project-Key`. The backend authenticates, limits, decodes, applies the allowlist and value checks, upserts idempotently, prices the usage and commits. The dashboard shows KPIs with `n`, the trace list and the span tree.
2. **Diagnose.** On a trace page, the machine observations panel lists deterministic findings from stored metadata (for example a failed attempt, a validation failure, a recovered retry), each with its evidence spans. It never fills in a review. A person records a failure review: issue type, evidence, a **hypothesis** and a self-declared name. Later they record a decision (supported, rejected or inconclusive). The cause is never presented as a verified fact.
3. **Evaluate.** From a decided review, the person converts the trace into a regression test case. They choose a dataset, type a synthetic input (telemetry never stored the prompt), choose a rule and optionally an expected output, then tick an explicit approval. Without the tick nothing is stored. Redaction masks e-mail addresses, phone numbers, long numbers and key-like values before insert.
4. **Improve.** The person starts a mock-only experiment: dataset, prompt version A vs B, a priced model, and a "mock only" confirmation. Both versions run on the same case ids. The report shows passed / failed / not evaluated per version, the pass rate with its denominator, synthetic latency, and estimated cost or "Total unknown". It names a better version only when the paired evidence is complete. On the golden set it names none, because one case times out under version B.

The end-to-end loop is verified in the browser against PostgreSQL by the E2E suite and on a clean clone (§14).

## 5. System architecture

```text
 instrumented app (demo_app: mock / offline fake-OpenAI)
   └─ llm-insight-sdk ── OTLP/HTTP binary protobuf + X-Project-Key ──┐
                                                                     ▼
              FastAPI backend (127.0.0.1:8000, one process, one worker)
              POST /v1/traces : auth → limits → decode → allowlist/value checks
                                → idempotent trace/span/usage upsert → Decimal pricing → COMMIT
              /api/v1/*       : admin Bearer token; traces, metrics, diagnostics, reviews,
                                datasets, test cases, mock experiments (rate-limited)
                                     │ SQLAlchemy + Alembic (0001–0005)
                                     ▼
                         PostgreSQL 17 (docker compose, 127.0.0.1:55432)
                                     ▲
 browser ── 127.0.0.1:3001 ── Next.js dashboard (server components; 5 Server Actions;
                              admin token server-side only; Host / Origin / CSRF guard; nonce CSP)
```

The locked decisions are ADR-01…12, CR-01…09 and ADR-13…27 (`ADR.md`). In summary:

- modular monolith with an independent SDK (ADR-01);
- a single PostgreSQL (ADR-02);
- direct binary-protobuf OTLP ingest, traces only (ADR-03, ADR-04, ADR-16);
- one provider adapter (ADR-05);
- metadata-only telemetry (ADR-07, ADR-19);
- local single-admin posture (ADR-08, CR-06);
- deterministic quality with human approval (ADR-10);
- versioned `Decimal` pricing (ADR-11);
- lineage from trace to experiment result (ADR-12).

## 6. Backend, SDK, frontend and database

**Backend (`backend/`).**

- Python 3.12.6, FastAPI, SQLAlchemy, Alembic, `opentelemetry-proto`, psycopg 3, `google-re2`, `jsonschema`. One uvicorn worker on the selector event loop (R32).
- Ingest is in `app/api/otel.py` and `app/services/ingestion/`. Pricing is in `app/services/pricing/`, metrics in `app/services/metrics.py`, diagnostics in `app/services/diagnostics/`.
- Reviews, datasets and test cases, with redaction, are in `app/api/{reviews,datasets}.py` and `app/services/redaction.py`. Evaluation and experiments are in `app/services/{evaluation,experiments}/`.
- Rate limiting is in `app/core/ratelimit.py`.
- Sixteen `/api/v1` routes, every one behind `require_admin` and the `/api/v1` rate limit (T43 route-list test).

**SDK (`sdk/`).**

- Installable on its own; never imports the backend (ADR-01). Pinned OpenTelemetry 1.45.1.
- Fails open: a backend outage never breaks the application (T13).
- The OpenAI adapter is optional (extra `openai`) and verified only against offline fake transports with network access blocked.

**Dashboard (`frontend/`).**

- Next.js 16.4.3, React 19.3.1, TypeScript 6.0.3 (strict), Tailwind 4, Recharts 3. Node 22.23.4 is installed project-locally with a checksum check.
- Every backend call is server-side (`lib/server/`), with no route handlers.
- Five Server Actions exist: review, decision, dataset, test case, experiment. The build manifest lists exactly these five.
- `proxy.ts` enforces:
  - a loopback `Host` (`421` otherwise);
  - GET/HEAD only, except same-origin POSTs with `Next-Action` on the four write pages (`403`/`405` otherwise);
  - a nonce CSP and security headers.

**Database.** PostgreSQL 17, eleven tables, column for column with `ARCHITECTURE.md` §4 (ADR-21):

| Group | Tables |
|---|---|
| Identity | `projects`, `project_api_keys` |
| Telemetry | `traces`, `spans`, `usage_records` |
| Pricing | `model_prices` |
| Review and testset | `test_datasets`, `failure_reviews`, `test_cases` |
| Experiments | `experiments`, `experiment_results` |

- Migrations `0001`–`0005` are tested upgrade → downgrade → upgrade (T31), and `alembic check` reports no drift.
- Integrity triggers:
  - append-only prices and experiment results;
  - synthetic-only mock results;
  - review decision freeze and conversion eligibility.
- Key constraints: `UNIQUE(project_id, canonical_call_key)` for usage idempotency, and a partial unique index giving one case per (dataset, review).

## 7. Telemetry and privacy design

- **Metadata only (ADR-07, ADR-19).** Prompts, completions, system instructions, tool definitions, exception messages and OTLP `Status.message` are never persisted, even when a client sends them.
- **Allowlist plus always-deny list.** Only the attributes in `TELEMETRY_CONTRACT.md` §3 are stored. Everything else is dropped and counted (`server_dropped_attributes_count`).
- **Value checks (R08).** Every persisted untrusted string is checked for:
  - format and length;
  - unsafe characters (control, bidi, zero-width);
  - obvious credential formats;
  - personal-data shapes (e-mail, phone or ID-like numbers).

  A refused value is dropped or becomes `redacted_span`. These are **heuristics**: they cannot detect every secret or every piece of personal data.
- **Credentials.**
  - Ingest keys are stored only as HMACs.
  - The admin token lives in the backend env and the dashboard server env only. It never appears in `NEXT_PUBLIC_*`, the bundle, HTML or browser storage (T42).
  - Logs never contain key or header values (T44).
- **Provenance.**
  - Every span and result is labelled `real`, `synthetic`, `mixed` or `unknown`.
  - A `real` claim without a provider response ID is downgraded to `unknown`.
  - `mixed` and `unknown` are never counted as real (T48).
  - The API states `provenance_verified: false`, because provenance is client-asserted (R16).
- **Delivery is best effort.** The SDK queue is bounded and in memory, with no persistent queue. Spans can be lost on a crash or a long outage (R26). This is not a billing-grade system.

## 8. Human review and testset workflow

- Failure reviews use the CR-03 fields and enums:
  - `issue_type`: `retrieval_missing`, `chunk_boundary`, `prompt_instruction`, `generation_mismatch`, `schema_violation`, `unknown`;
  - `review_status`: open / in review / closed;
  - `reviewer_decision`: pending / supported / rejected / inconclusive.

  A second open review on the same trace is refused (`409`, T33). Evidence and hypothesis pass the same redaction as test input.
- Diagnostics and reviews are separate. No code path writes a review from a diagnostic, and static and integration tests check this.
- **Approval boundary (T34, blocker).** A test case is stored only with `approved: true` sent explicitly. Missing or false approval is `422` with no row. The dashboard requires the approval tick, and without it the server action stores nothing (E2E and clean clone).
- **Redaction before insert (T35, blocker).**
  - E-mail, phone, long digit runs and key-like values are masked in the input, and the row records `redaction_applied`.
  - Structured assertions (`expected_output`, `rule`) that look like secrets or personal data are refused rather than altered.
- **Lineage and integrity.**
  - A converted case carries `origin_trace_id` and `origin_review_id`. Only decided, eligible reviews of the same trace and project can be converted.
  - The review decision is frozen once a case cites it (API row locks plus database triggers, ADR-24).
  - The partial unique index allows one case per (dataset, review).
- **Identity.** `created_by` and `approved_by` are self-declared labels with no login. Who changed a decision is not recorded (R55).

## 9. Deterministic evaluation and synthetic A/B

- **Rules (ADR-23, `llminsight.rule.v1`).**
  - JSON Schema 2020-12 subset: keyword allowlist at every depth; no `pattern`, `format` or remote references; local `$defs` only; a keyword budget.
  - Field rules and exact match.
  - RE2-only regexes: linear time, with program, memory and input caps.
  - Exact numbers end to end.
  - Outcomes are `true`, `false` or `NULL` ("not evaluated"). `NULL` stays outside every pass-rate denominator (T40).
- **Golden dataset.** `golden-synthetic-v1` has 12 SYNTHETIC cases covering every rule type, including one without an expected output.
- **Experiments (ADR-25, ADR-26).**
  - Only `mode: "mock"` runs. `real` is validated and then refused (`409`), and the backend has no real executor and does not install `openai`.
  - A scripted provider answers known (prompt version, input) pairs.
  - Both versions run on identical case ids in order (T38).
  - Every result is `synthetic`, enforced by the service and a trigger (T39).
  - Each run is one transaction: complete or nothing. Bounds: 4 concurrent runs and 15 s per run, per process (R67).
- **Report honesty.**
  - Quality is compared only on complete paired evidence.
  - Cost is compared only when both versions are fully priced.
  - Latency is reported per version only, with p95 flagged as unreliable below 20 samples.
- **Verified outcome (clean clone, Phase 5B).** The run used 13 cases: the 12 golden cases plus 1 human-approved case.

  | Measure | Version A (`golden-prompt-v1`) | Version B (`golden-prompt-v2`) |
  |---|---|---|
  | Passed / evaluated | 7 / 12 | 5 / 11 |
  | Not evaluated | 1 | 2 |

  Other results:
  - 26 of 26 results were `synthetic`, and both versions ran on the same 13 case ids.
  - The human-approved case failed under v1 (`schema_violation`) and passed under v2.
  - No better version was named: G12 timed out under v2.
  - Cost was not compared, because v2's total is unknown (`reports/phase-5b.md` §7).
- **Limitations.**
  - Synthetic outputs say nothing about a real model (R62).
  - The mock answers only the golden inputs (R65).
  - Small samples; no significance test.
  - A lost response followed by a resubmission creates a second, independent run (R66).

## 10. Test matrix acceptance

The locked matrix (`TEST_MATRIX.md`) defines 48 scenarios: 42 critical (the 39 starred in the master plan plus T15, T16, T44 by ADR-20) and 10 presentation blockers (T01, T04, T17, T19, T20, T23, T34, T35, T42, T46). The full per-scenario evidence is in `reports/phase-5b.md` §3. No definition was changed in Phase 5.

| Measure | Result |
|---|---|
| Scenarios PASS | **48 / 48** |
| Critical PASS | **42 / 42**; no critical failure, no critical skip |
| Presentation blockers PASS | **10 / 10** |
| Non-critical (T07, T14, T31, T38, T40, T47) | 6 / 6 PASS |

**Phase 5 final re-check (this release):** the evidence was checked rather than re-invented.

- All 78 tests named in the Phase 5B evidence matrix, and the two named test modules, are present and passed in the final backend and SDK JUnit results of the release tree.
- The E2E suite passed, including:
  - T41, T42, T45, T46 and T48 (named E2E tests);
  - the human review → approved case loop (T33–T35);
  - the live experiment loop against PostgreSQL (T38–T40);
  - project isolation (T28);
  - CSRF and route-security replays (R68, R71);
  - the dashboard 429 path (T43).
- T47 (clean-clone compose demo) rests on the Phase 5A and Phase 5B runs (§14). It was not repeated, because no product file changed after Phase 5B.

## 11. Security audit and accepted risks

Tools ran from a throwaway virtual environment, never as project dependencies: `pip-audit` 2.10.1, `bandit` 1.9.4, `npm audit` (npm 10.9.9), the project's bundle scan and `scripts/qa/secret_scan.py`.

**Dependency audit (final run).**

| Check | Result | Exit |
|---|---|---|
| `pip-audit -r backend/requirements.lock --no-deps --disable-pip`, services PyPI and OSV | No known vulnerabilities | 0 / 0 |
| `pip-audit -r sdk/requirements-dev.lock …`, PyPI and OSV | No known vulnerabilities | 0 / 0 |
| `pip-audit --path` backend and SDK venvs | only `pip` 24.2 (6 advisories: PYSEC-2026-1795, -1796, -2875, -2876, -3721, -196) | 1 (accepted, R75) |
| `npm audit`, `npm audit --omit=dev` | 0 vulnerabilities | 0 / 0 |
| `bandit` production code (14,148 lines) | High 1 (B202, false positive F5), Medium 4 (B310 ×3, B608, false positives F6, F7), Low 38; no `nosec` | 1 (classified) |
| `bandit` tests (16,067 lines) | High 0, Medium 25, Low 2,764 (asserts and fixtures) | 1 (classified) |

**R74, `certifi` security exception (approved).**

- **Advisory.** `certifi` 2026.7.22 trusted `emSign ECC Root CA - G3`, whose intermediates have compromised keys (PYSEC-2026-4320 / GHSA-6q48-6c7w-9gw2). OSV gives the fixed version as 2026.10.19.
- **Pinned version** (a deliberate exception to the two-week pin rule; not downgraded):
  - `certifi==2026.10.19` is the only `certifi` line in `backend/requirements.lock` and in `sdk/requirements-dev.lock`.
  - It is the official PyPI release: not yanked, uploaded 2026-10-20, wheel SHA-256 `42d35340…670e1b`, identical to the hash recorded at install time.
  - OSV lists no advisory for 2026.10.19. A newer 2026.10.23 exists, but no advisory requires it.
- **Installed versions.**
  - Both venvs carry `certifi` 2026.10.19, and their `cacert.pem` is byte-identical to the official wheel's (SHA-256 `50f79f98…0785`).
  - The bundle contains no `emSign ECC Root CA - G3`.
- **Docker image.**
  - A backend image built from an exact `git archive` of the release baseline (no `.env` in the build context) installs `certifi` 2026.10.19 with the same bundle hash, and `pip check` is clean inside it.
  - The dashboard image has no Python and no `certifi`.
  - **Note:** a backend image built on this machine before the fix (the local `llminsight-backend:latest`) still contains 2026.7.22. Compose reuses it only without `--build`, so the README and the demo script now require `--build`.
- **No older `certifi` as a transitive dependency.** The locks are complete and installed with `--no-deps`, and `pip check` is clean. Two older trust stores exist outside the dependency graph; both are R75 items:
  - **pip's vendored copy.** `pip` 24.2 vendors its own `certifi` 2024.07.04 (`pip/_vendor/certifi`) in each venv and in the image. That copy still lists the emSign root and is used only by pip itself for TLS during package installs.
  - **The OS trust store.** The `python:3.12.6-slim-bookworm` image's Debian `ca-certificates` 20230311 also enables that root. It is used by stdlib `ssl` default contexts, not by `requests` or `httpx`. The backend makes no outbound TLS connection at runtime (mock mode, no provider client).

**R75, accepted local-only limitations (not resolved vulnerabilities).**

- **`pip` 24.2.** The venv bootstrap and the base image carry six install-time advisories (archive, path and entry-point handling; fixed in 25.3–26.2). `pip` also vendors the older `certifi` 2024.07.04. It is not imported at runtime, and exploitation needs a malicious package, a malicious index or an on-path TLS attacker during installation.
- **Python 3.12.6 patch status.** 3.12.6 is not the newest 3.12 patch release, and the interpreter's own CVE status is not covered by `pip-audit`.
- **OS image packages.** No container or OS package scanner was run on `python:3.12.6-slim-bookworm` or `node:22.23.4-bookworm-slim`. Their package CVE status, including the OS trust store, is unknown.
- **No dependency hashes.** The lock files pin exact versions without `--hash` entries, so installs trust PyPI and TLS for integrity.
- **Remediation before any use beyond the local machine:** current `pip`, a current Python 3.12 patch release, image scanning, and hash-pinned locks.

**R76, large trace pages (accepted, no performance claim).** The dashboard's server-rendered trace page grows by about 13 KB of HTML per span: about 26 MB and a median of 1.8 s at the 2000-span cap on the benchmark laptop, while the API serves the same trace in 0.18 s. Traces of 10–100 spans render in about 46–142 ms. No production-scale or large-trace guarantee is made.

**R77, malformed Server Action arguments (accepted).**

- A same-origin request that dispatches a Server Action with a malformed argument list makes the action throw. The response is a generic `500` with a digest, not a clean refusal.
- Nothing is written. The Phase 5 route probe dispatched `submitDataset` with an empty argument list 40 times, and the dataset and experiment counts stayed 0.
- No request value, secret or traceback reaches the client. Phase 5 final checked this with six same-origin requests (`submitDataset` and `submitExperiment`, each with an empty list, a bare string and invalid JSON as the argument body). Each received a 102–103-byte RSC error record that carries only a numeric digest: `500` for `submitDataset`, `200` for `submitExperiment`. None contained an error message, stack, file path or exception type. The exception is logged on the server side only, and its log holds no secret value. Dataset and experiment counts stayed 0.
- Cross-origin and foreign-`Host` variants never dispatch.

**R68 / R69 / R71 protections preserved.**

- The dashboard guard runs on every path except `/_next/static/…` and exactly `/_next/image`.
- Write pages are UI entry points, not an authorization boundary (ADR-27).
- Final evidence:
  - the static guard unit test;
  - the live replay E2E ("a genuine experiment Server Action POST runs only on its write page");
  - the raw-socket probe on the production build: 78 targets × 17 Host/Origin variants plus method checks, 1339 requests. Of the 40 dispatches, all were on write pages (or targets Next.js normalizes to one) under same-origin headers only. There were **0 violations**, the positive control dispatched, and nothing was stored.

**Secret scans.**

- **Bundle scan (T42):** 632 files, 23 in the browser bundle; 0 findings.
- **History scan** (`secret_scan.py`, after a control run that detected a planted value in history and an OpenAI-shaped key in the working tree):
  - Coverage: 799 reachable blobs, 27 commit and tag messages, 452 working files; 7 local secret values and 10 credential patterns.
  - **0 secret-value hits.**
  - 36 pattern hits, all URL user-info placeholders (dummy user and password pairs) in redaction tests and docs (the triaged F9 fixtures, one more historical blob of `SECURITY.md` than in Phase 5B).
- **Logs:** the logs of every server and suite started in Phase 5 contain no secret value, ingest key or Bearer value.
- **Env files:** `.env` and `frontend/.env.local` are git-ignored and untracked.

**Accepted risks in force (all Low unless stated).**

| Risk | Description |
|---|---|
| R08, R56 | Heuristic redaction (Med) |
| R09, R23, R67 | Per-process limits, counters and run bounds |
| R16 | Client-asserted provenance (Med) |
| R24, R34 | Connection-level limits; Windows selector socket cap |
| R26 | Best-effort delivery |
| R28, R29 | Price catalogue currency and coverage |
| R38 | CSP `style-src 'unsafe-inline'` |
| R39, R54 | No login; local readers and writers (Med if exposed) |
| R43, R73 | Host ingest and host tools for the clean-clone demo |
| R55 | Self-declared identities |
| R62 | Synthetic experiments (Med) |
| R66 | No idempotency key for experiment runs |
| R75–R77 | Toolchain, large trace pages, malformed action arguments (above) |

Full register: `SOURCES_AND_RISKS.md`.

## 12. Benchmark methodology and outcomes

Source: `reports/phase-5b.md` §6, the script `scripts/qa/benchmark.py`, and raw results in `reports/phase-5b-benchmark.json`. **No target was set or claimed.**

**Method.**

- **Environment:**
  - Windows 11, AMD Ryzen 7 6800HS, 15.3 GiB RAM, NVMe SSD;
  - Python 3.12.6;
  - PostgreSQL 17.11 in Docker Desktop (WSL2, default settings);
  - one backend worker with its rate-limit buckets raised for that process only;
  - production dashboard.
- **Data:** a test-owned database that is created and dropped, with SYNTHETIC 10-span traces.
- **Ingest:** 100, 500 and 1000-span batches. Per run, 3 warm-up batches were discarded and 15 measured; each payload was then re-sent and the row counts checked unchanged; 3 runs per size.
- **Trace open:** a dataset of exactly 10,000 spans. API: 50 requests per run; dashboard: 20 requests per run; 3 runs each.
- **Statistics:** client wall time on one persistent connection, sequential requests; p50/p95 by linear interpolation; medians of per-run p50s are reported.

**Outcomes (p50; ranges are across runs).**

| Workload | p50 | p95 range |
|---|---|---|
| Ingest 100 spans, fresh / re-ingest | 112.1 ms / 83.3 ms | 106–126 ms / 92–127 ms |
| Ingest 500 spans, fresh / re-ingest | 415.1 ms / 368.3 ms | 454–471 ms / 396–452 ms |
| Ingest 1000 spans, fresh / re-ingest | 889.7 ms / 854.3 ms | 1016–1090 ms / 947–1080 ms |
| API trace detail, 10 / 100 / 1000 / 2000 spans | 18.4 / 27.3 / 106.0 / 183.7 ms | up to 252 ms |
| API diagnostics, 2000 spans | 125.7 ms | 183–189 ms |
| API trace list (`limit=50`) / overview | 21.2 / 121.3 ms | 25–27 / 130–156 ms |
| Dashboard trace page, 10 / 100 / 1000 / 2000 spans | 46.2 / 142.5 / 1028.3 / 1831.2 ms | up to 2003 ms |
| Dashboard trace list / overview | 52.3 / 140.6 ms | 59–62 / 149–153 ms |

**Observations.**

- Ingest runs at about 1.1 k spans/s on one worker.
- Re-ingest costs 74–96 % of a fresh ingest, because duplicates are fully decoded before the database discards them.
- Every re-ingest left the counts unchanged (T04, T23).
- The dashboard trace page is the bottleneck on large traces (R76).

**Reproducibility.**

- The benchmark script and its raw output are committed.
- For the final release, every figure in the Phase 5B report was recomputed from the raw JSON and matched exactly.
- The benchmark was not re-run: no product code changed after Phase 5B.
- The numbers describe one laptop with client, servers and database on the same machine, sequential requests only and no network. They are **not** a capacity or production-scale claim.

## 13. Reproducible local installation

The step-by-step commands are in the root `README.md` (host quick start, compose `app` profile, configuration, tests). In short:

1. `python -I scripts/dev/init_env.py` creates the git-ignored `.env`, with generated secrets that are never printed.
2. `docker compose up -d --wait postgres` starts PostgreSQL 17 on `127.0.0.1:55432`.
3. Set up the backend:
   - create a venv from `backend/requirements.lock`;
   - run `alembic upgrade head`;
   - run `python -m app.cli create-key --project demo` (the key is printed once);
   - run `python -m app.cli seed-prices`;
   - start the backend with `python -m app` on `127.0.0.1:8000`.
4. Create the SDK venv from `sdk/requirements-dev.lock`, then send demo traffic with `python -m demo_app.web --drive` (5 SYNTHETIC traces).
5. Set up the dashboard:
   - `scripts/dev/install_node.py` installs Node 22.23.4, checksum-checked;
   - `init_frontend_env.py` writes `frontend/.env.local`;
   - run `npm ci`, `npm run build` and `npm start` on `127.0.0.1:3001`.
6. `scripts/dev/seed_golden_dataset.py` loads the 12 SYNTHETIC golden cases.

Required tools: Python 3.12 (3.12.6 used), Docker with Compose v2 (Engine 28.5.1, Compose 2.40.3 used), PostgreSQL 17 through compose, Node 22.23.4 (project-local), Microsoft Edge for Playwright only, and Git.

**Compose `app` profile.** `docker compose --profile app up -d --build --wait` also builds and starts the backend and dashboard containers. The dashboard is published on `127.0.0.1:3001` only. The backend container binds loopback inside its own network namespace and is **not** published, so **host OTLP ingestion still needs a host backend** on the same database (R43). Always keep `--build`.

## 14. Clean-clone evidence

| Run | Source | Result |
|---|---|---|
| Phase 5A (T47) | Fresh clone from GitHub (`394920c`) plus the six Phase 5A code files | `init_env`; `docker compose -p llminsight-t47 --profile app up -d --build --wait` (3 services healthy); init in the container (`0005 (head)`, key, 6 prices); host backend; demo traffic (5 traces, 13 spans); golden dataset; browser demo through review → approved case → mock A/B → results. PASS (`reports/phase-5a.md` §5) |
| Phase 5B | Clone of the local repository at `fe259e7` plus the Phase 5B working-tree changes (the exact release-baseline tree) | The same flow plus the host dashboard (`npm ci`, build, bundle scan 0). In the clone's PostgreSQL: case 13 linked to its trace and review, `redaction_applied` true; 13 identical case ids under both versions; 26/26 `synthetic`; the case failed under v1 and passed under v2. PASS (`reports/phase-5b.md` §7, eight screenshots) |
| Phase 5 final | `git archive` of `e7f8dcf` | Backend image built with no `.env` in the context; `certifi` 2026.10.19 and `pip check` clean inside (§11) |

Ports were relocated (55433, 8010, 3011), and a separate compose project name kept the developer's containers and volume untouched. Only test-owned resources were removed afterwards.

## 15. GitHub CI coverage and limitations

The workflow is `.github/workflows/ci.yml`: Ubuntu 24.04, Python 3.12.6, Node from `frontend/.nvmrc`. Actions are pinned to full commit SHAs, permissions are `contents: read`, and no secrets are used.

| Job | Steps |
|---|---|
| `docs` | `verify_phase0.py` |
| `python` | throwaway `.env`; both compose configs; compose PostgreSQL; backend venv from the lock and `pip check`; Ruff; dev-script lint; `alembic upgrade head` + `alembic check`; backend pytest with `LLMI_TEST_REQUIRE_DB=1` (a missing database fails instead of skipping); SDK venv and `pip check`; SDK Ruff and strict mypy; SDK pytest including live-backend tests |
| `frontend` | `npm ci`, `npm audit`, typecheck, ESLint, unit tests, production build, bundle secret scan |

**Local-only gates (not in CI):**

- the Playwright E2E suite (needs a seeded live backend and Microsoft Edge);
- the compose image build;
- the clean-clone demo;
- `pip-audit` and `bandit`;
- the history secret scan;
- the route and backend probes;
- Ruff on `scripts/qa/`;
- the benchmark.

Windows-only behaviour (R32, R34) is exercised only locally.

**Run of the closure commit: pending at the time of writing.** It is verified after the push, for the closure commit's own SHA, and recorded in the `phase-5` tag annotation. The previous verified run was 40372061697 on `394920c` (`phase-4`). Raw job logs need an authenticated download, so no raw-log security audit is claimed unless the logs are actually read.

## 16. Real-provider validation limitations

- **No real OpenAI call has ever been made** by this project, its tests, its CI, its demo or its benchmark. The real smoke test planned for Phase 2 was never run: there was no approval, key or budget for it.
- The OpenAI adapter is verified against `openai` 3.28.0 types and SYNTHETIC fixtures behind fake transports, with non-loopback network access blocked in the tests. Live provider behaviour (real error shapes, real usage reporting, service tiers, latency) is unverified.
- The backend refuses real-provider experiments (`409`), has no real executor and does not install `openai`.
- A real mode would need explicit approval, an API key and a budget. It would also need durable idempotency (R66) and a pre-call budget guard.
- Costs shown anywhere are **estimates**, computed from provider-reported usage (synthetic in every run so far) and a verified catalogue. They are not invoices, and coverage is limited to six verified price rows (R28, R29).

## 17. Local-only deployment restrictions

The MVP is authorized for **local, single-user use on a trusted machine only**.

**What enforces the local boundary:**

- The backend and dashboard bind `127.0.0.1`.
- The dashboard refuses non-loopback `Host` values (`421`).
- Compose publishes on `127.0.0.1` only.

**What it does not protect against.** Anyone who can reach the machine's loopback interface can read every project and create reviews, datasets, approved cases and mock experiments (R39, R54). The single admin token reads all projects (R45).

**Mandatory before any public or multi-user deployment** (`SECURITY.md` §7):

- real user authentication, with a CSRF review;
- TLS;
- per-project authorization;
- a distributed rate limiter;
- audit logging;
- a privacy (KVKK/GDPR) review of retention and deletion;
- the R75 toolchain remediation;
- connection-level limits (R24);
- a new threat review.

## 18. Remaining technical debt

| Item | Reference |
|---|---|
| No persistent telemetry queue; best-effort delivery | R26 |
| In-process limiter, counters and experiment bounds; one worker only | R09, R23, R67 |
| Synchronous experiment runs; no idempotency key | R66 |
| Large trace pages (no streaming or virtualization of the span tree) | R76 |
| Malformed Server Action arguments answered with `500` | R77 |
| Toolchain: `pip` 24.2 (and its vendored `certifi`), Python patch level, OS packages unscanned, no lock hashes | R75 |
| Re-ingest does full decoding before discarding duplicates | `reports/phase-5b.md` §6 |
| Price catalogue maintenance and narrow coverage | R28, R29 |
| CSP `style-src 'unsafe-inline'` | R38 |
| ESLint 9 end of upstream support | R36 |
| Starlette `httpx` test-client deprecation | R20 |
| Actor of review status/decision changes not recorded | R55 |
| Restricted JSON Schema subset | R61 |
| Clean-clone demo needs host venvs for ingest; Windows long paths | R43, R73 |

## 19. Future development opportunities

These are ideas, not commitments. Each would need a locked contract, acceptance tests and an ADR.

- **Real-provider smoke test** (P1 prerequisite). An approved, budgeted run would validate the OpenAI adapter against the live API: usage mapping, error classification and service tiers.
- **Durable ingestion.** An optional OTel Collector with a disk queue, or a server-side queue, would give stronger delivery semantics.
- **Authentication and multi-user.** Sessions, per-project authorization, an audit log and recorded actors for reviews.
- **Background evaluation worker.** Idempotent experiment jobs instead of synchronous requests (R66, R67).
- **P1 product items** from `PRD.md`: streaming time-to-first-chunk, threshold alerts, safe CSV export, a retention job, user feedback, and a second provider adapter or an OpenInference normalizer.
- **Scale.** Virtualized trace rendering (R76), cheaper duplicate detection on re-ingest, and post-MVP storage options such as ClickHouse (P2).
- **Supply-chain hardening.** Hash-pinned locks, current `pip` and Python patch releases, and image scanning in CI.

## 20. Final release evidence and references

**Final local regression on the release tree** (2026-10-27, Windows 11, Python 3.12.6, Node 22.23.4, PostgreSQL 17.11 in Docker 28.5.1; the backend was started on `127.0.0.1:8000` for the E2E suite and stopped afterwards):

| Command | Outcome | Exit |
|---|---|---|
| `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` (backend, real PostgreSQL) | **1726 passed**, 0 failed, 0 skipped, 1 warning (R20), 208 s | 0 |
| same (sdk, unit + live backend) | **350 passed**, 0 failed, 0 skipped, 79 s | 0 |
| `npm test` (frontend) | **311 passed**, 0 failed, 0 skipped, 0 cancelled | 0 |
| `npx playwright test` (Edge, live backend) | **55 passed**, 0 failed, 0 skipped, 0 flaky (2.0 min); teardown `remaining: []`; dev database counts unchanged | 0 |
| `npm run typecheck` / `npm run lint` | clean / 0 warnings | 0 / 0 |
| `npm run build` / `npm run check:bundle` | built / 632 files, 0 findings | 0 / 0 |
| `npm audit`; `npm audit --omit=dev` | 0 vulnerabilities | 0 / 0 |
| `ruff check`, `ruff format --check` (backend 140 files; sdk 29; `scripts/dev` CI files + `scripts/qa` 6) | all passed | 0 |
| `mypy src`; `mypy --config-file pyproject.toml ../demo_app` (sdk, strict) | no issues (8 / 3 files) | 0 |
| `pip check` (backend; sdk) | no broken requirements | 0 |
| `alembic current` / `alembic check` | `0005 (head)` / no new upgrade operations; migration cycle tests (T31) in the backend suite | 0 |
| `docker compose config --quiet`; `--profile app config --quiet` | valid | 0 / 0 |
| Backend image from `git archive` of the baseline | built; `certifi` 2026.10.19, `pip check` clean | 0 |
| `pip-audit` (2 locks × PyPI/OSV) | no known vulnerabilities | 0 |
| `pip-audit --path` (2 venvs) | `pip` 24.2 only | 1 (accepted, R75) |
| `bandit` (production; tests) | classified, no unresolved true positive | 1 / 1 (bandit exits 1 on any finding) |
| `route_probe.py` (production build, :3005) | 1339 requests, 0 violations | 0 |
| `secret_scan.py` (history + tree), control run first | 0 secret-value hits; 36 triaged placeholder pattern hits | 1 (pattern hits, triaged) |
| `python -I scripts/verify_phase0.py --quiet`; `git diff --check` | recorded in `PHASE_STATUS.md` (final checks) | — |

**Release identifiers.**

| Item | Status |
|---|---|
| Closure commit | the commit that adds this report on `main` (its SHA is recorded in the `phase-5` tag annotation) |
| Expected tag | annotated `phase-5` on that exact commit, created **only after** its own GitHub Actions run passes; **pending** at the time of writing |
| Previous tags | `phase-0` … `phase-4` unchanged (`phase-4`: tag object `4d24a91` → `394920c`) |
| GitHub Release object | none (not authorized) |

**Cost and model usage.** Infrastructure cost was $0 (local machine; GitHub-hosted runners on a public repository). There were zero LLM provider API calls in every phase.

**References.**

| Topic | Documents |
|---|---|
| Overview and setup | `README.md`, `DEMO_SCRIPT.md`, `PROJECT_PITCH.md` |
| Scope and decisions | `PRD.md`, `ADR.md`, `MVP_MASTER_PLAN.md` |
| Contracts | `ARCHITECTURE.md`, `TELEMETRY_CONTRACT.md`, `METRICS_CATALOG.md`, `PRICING.md` |
| Security, risks and tests | `SECURITY.md`, `SOURCES_AND_RISKS.md`, `TEST_MATRIX.md` |
| Status and evidence | `PHASE_STATUS.md`, `reports/phase-0.md` … `reports/phase-5b.md` |
