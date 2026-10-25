# LLM Insight — Test Matrix (T01–T48)

Source: master plan §I. IDs and scenario names are preserved exactly; the expected outcomes are refinements taken from the contracts. A status changes only with real command or CI output, recorded in the named phase report.

**Legend:**
- **Crit ★** = critical: the 39 starred in the master plan plus T15, T16 and T44 promoted by ADR-20, for **42** in total. Phase 5 gate: 0 FAIL on all 42.
- **Blocker ⛔** = presentation blocker. There are 10, all of them critical.
- **Phase** = the phase where the test is implemented → the phase(s) where it is re-verified.
- **Level:** U = unit, I = integration (real PostgreSQL), E = end-to-end (Playwright / compose).
- **Status:** `NOT RUN`, or `PASS` / `PARTIAL` / `FAIL (phase-N)` backed by `reports/phase-N.md`. `PARTIAL` means only the part owned by that phase was executed; the test is not passed until its owning phase re-verifies it in full.

| ID | Group | Scenario | Expected outcome | Crit | Blocker | Phase | Level | Status |
|---|---|---|---|---|---|---|---|---|
| T01 | Ingestion | Valid protobuf request | `200` + protobuf `ExportTraceServiceResponse`, `partial_success` unset, sent only after COMMIT; rows stored | ★ | ⛔ | 1→5 | I | PASS (phase-1c) |
| T02 | Ingestion | Parent-child | 3-span fixture: children reference root; tree reconstructs | ★ | | 1→3 | I | PASS (phase-1c) |
| T03 | Ingestion | Resource / service name | `service.name` from resource; multiple resourceSpans/scopes; missing → `unknown_service` | ★ | | 1 | I | PASS (phase-1c) |
| T04 | Ingestion | Duplicate upsert | Same payload twice → row counts unchanged, `200` both times | ★ | ⛔ | 1→2,5 | I | PASS (phase-1c) |
| T05 | Ingestion | Invalid key | Missing / unknown / revoked → `401`, identical message, nothing stored, key not logged | ★ | | 1 | I | PASS (phase-1c) |
| T06 | Ingestion | Corrupt protobuf | `400` + binary `google.rpc.Status` with `BadRequest` (no values echoed), nothing stored | ★ | | 1 | I | PASS (phase-1c) |
| T07 | Ingestion | gzip valid | `Content-Encoding: gzip` decoded; corrupt gzip → `400` `INVALID_GZIP` | | | 1 | I | PASS (phase-1c) |
| T08 | Ingestion | Oversized payload | Wire > 4 MiB, decompressed > 16 MiB, spans > 2048 → `413` each (count-based 413 is our policy) | ★ | | 1 | I | PASS (phase-1c) |
| T09 | SDK | Real/mock client span attributes | CLIENT span `chat {model}` with required `gen_ai.*` + provenance | ★ | | 2 | U/I | PASS (phase-2b) |
| T10 | SDK | Nested context | LLM span is a child of the demo FastAPI root span (same trace) | ★ | | 2→3 | I | PASS (phase-2c) |
| T11 | SDK | Provider success usage | Provider-reported usage mapped to `gen_ai.usage.*`, `usage_source=provider_reported` | ★ | | 2 | U | PASS (phase-2b) |
| T12 | SDK | 429 / timeout status | Span `ERROR`, `error.type` set, attempt recorded; app gets a handled error | ★ | | 2 | U | PASS (phase-2b) |
| T13 | SDK | Export-down fail-open | Backend down: app request succeeds, no exception, latency bounded by design | ★ | | 2→5 | I | PASS (phase-2a) |
| T14 | SDK | Retry attempt IDs | Same `call_id`, `attempt` 1..n; one usage row per attempt; M02 > M03 | | | 2 | U/I | PASS (phase-2a) |
| T15 | SDK | No secrets in span | No API key / auth header in any exported attribute | ★ | | 2→5 | U | PASS (phase-2b) |
| T16 | SDK | No content default | No message content exported; backend drops it if sent (counted) | ★ | | 2→5 | U/I | PASS (phase-2b) |
| T17 | Cost/metrics | Provider token priority | Provider-reported values win over any estimate | ★ | ⛔ | 2 | U | PASS (phase-2b) |
| T18 | Cost/metrics | Unknown token/cost null | Missing usage → NULL tokens, `cost_status=unknown` + reason, never 0 | ★ | | 2→3 | U/I | PASS (phase-2b) |
| T19 | Cost/metrics | Decimal known fixture | Hand-computed Decimal cost equals the stored value exactly | ★ | ⛔ | 2 | U | PASS (phase-2b) |
| T20 | Cost/metrics | Cached subset no double count | Cached tokens priced once, not added to input | ★ | ⛔ | 2 | U | PASS (phase-2b) |
| T21 | Cost/metrics | Reasoning subset no double count | Reasoning tokens not added to output | ★ | | 2 | U | PASS (phase-2b) |
| T22 | Cost/metrics | Price version persisted | `price_version` + `model_price_id` stored per usage row; price change doesn't rewrite history | ★ | | 2 | I | PASS (phase-2b) |
| T23 | Cost/metrics | Duplicate usage ignored | Re-export → one usage row, cost unchanged | ★ | ⛔ | 2→5 | I | PASS (phase-2b) |
| T24 | Cost/metrics | p95 sample + percentiles math | SQL `percentile_cont` = NumPy `linear` on fixture; `n` returned | ★ | | 2→3 | U/I | PASS (phase-2c) |
| T25 | API/DB | Pagination | Cursor pagination stable; `limit` max enforced | ★ | | 1→3 | I | PASS (phase-1c) |
| T26 | API/DB | Timestamp timezone | ns epoch → UTC `TIMESTAMPTZ`; API returns ISO-8601 with `Z`; ns preserved | ★ | | 1→3 | U/I | PASS (phase-1c) |
| T27 | API/DB | Orphan span | Missing parent stored and returned as orphan | ★ | | 1→3 | I | PASS (phase-1c) |
| T28 | API/DB | Project filter auth | Other project's trace → `404`; list never mixes projects | ★ | | 1→3 | I | PASS (phase-1c) |
| T29 | API/DB | Empty aggregation | No data → `n=0`, null values (not 0), `200` | ★ | | 2→3 | I | PASS (phase-2c) |
| T30 | API/DB | DB outage / transaction rollback | Mid-batch failure → nothing persisted; DB down → `503` + Retry-After; `/health/ready` `503` | ★ | | 1→5 | I | PASS (phase-1c) |
| T31 | API/DB | Migration clean recreate | `alembic upgrade head` / `downgrade base` / `upgrade head` on a disposable DB; schema matches `ARCHITECTURE.md` §4 | | | 1→5 | I | PASS (phase-2b) |
| T32 | API/DB | Status counts and total distinct API calls | M02/M04 counts match fixture; trace vs inference counts separate | ★ | | 2→3 | I | PASS (phase-2c) |
| T33 | Eval/workflow | Failure review create | Valid enum values → `201`; invalid `issue_type` → `422`; duplicate open → `409` | ★ | | 4 | I | PASS (phase-4b) |
| T34 | Eval/workflow | No approval no test case | Missing or false `approved` → `422`, no row | ★ | ⛔ | 4→5 | I | PASS (phase-4b) |
| T35 | Eval/workflow | Redaction before case save | Email / phone / key-like input is masked before insert; `redaction_applied=true` | ★ | ⛔ | 4→5 | U/I | PASS (phase-4b) |
| T36 | Eval/workflow | Golden JSON pass | Valid output passes the JSON Schema + field rule | ★ | | 4 | U | NOT RUN |
| T37 | Eval/workflow | Golden JSON fail | Schema violation fails with `evaluation_detail` | ★ | | 4 | U | NOT RUN |
| T38 | Eval/workflow | Prompt A/B same dataset | Both versions run on identical case IDs | | | 4 | I | NOT RUN |
| T39 | Eval/workflow | Evaluation provenance labels | Each result carries `real`/`synthetic`; mock → synthetic | ★ | | 4 | I | NOT RUN |
| T40 | Eval/workflow | Missing expected output handling | `passed=NULL` ("not evaluated"), excluded from the pass-rate denominator | | | 4 | U | NOT RUN |
| T41 | Security/UI | XSS escape on labels | `<script>` in a model/service name renders as text | ★ | | 3→5 | E | PASS (phase-3c) |
| T42 | Security/UI | Server-only API secret | Build output / client bundle / HTML contain no admin token | ★ | ⛔ | 3→5 | E | PASS (phase-3c) |
| T43 | Security/UI | Request rate limit | Burst over limit → `429` + Retry-After (ingest, auth-failure and API buckets) | ★ | | 3→5 | I | PARTIAL (phase-1c) |
| T44 | Security/UI | Malformed header/auth log sanitization | Logs contain no key/header value after bad-auth requests | ★ | | 1→5 | I | PASS (phase-1c) |
| T45 | Security/UI | Trace list UI screenshot assertion | Seeded DB data visible in the list; screenshot saved | ★ | | 3 | E | PASS (phase-3c) |
| T46 | Security/UI | Drilldown tree E2E | Trace list → detail shows root → child tree | ★ | ⛔ | 3→5 | E | PASS (phase-3c) |
| T47 | Security/UI | Reproducible compose smoke | Clean clone → `docker compose up --build` → init → demo works | | | 5 | E | NOT RUN |
| T48 | Security/UI | Real vs synthetic provenance on dashboard | SYNTHETIC/MIXED/UNKNOWN badges; mixed and unknown never counted as real | ★ | | 3→4,5 | E | PASS (phase-3c) |

**Counts:**
- 48 tests in total.
- 42 critical.
- 6 non-critical: T07, T14, T31, T38, T40, T47.
- 10 blockers: T01, T04, T17, T19, T20, T23, T34, T35, T42, T46.

**Phase 3 final re-verification (`reports/phase-3-final.md` §4, §8):** T41, T42, T45, T46 and T48 were re-run on the closure state (44/44 E2E, bundle scan 0); their statuses stay `PASS (phase-3c)`. Still open: T33–T40 (Phase 4, NOT RUN), T43 (PARTIAL, Phase 5) and T47 (Phase 5, NOT RUN).

**Phase 4A (`reports/phase-4a.md`):** trace diagnostics are additive scope outside the 48 IDs. Their tests are recorded in the report as `D01`–`D20` (diagnostic test groups, not matrix IDs). **No matrix status changed:** T33–T40 test the human Failure Review, Trace-to-Testset, golden-set and A/B workflow, which Phase 4A does not implement, so they stay NOT RUN. The diagnostics endpoint re-applies the T18 (unknown never 0), T27 (orphans) and T28 (project isolation) rules to a new response; those statuses are unchanged.

**Phase 4B (`reports/phase-4b.md` §6):** T33, T34 and T35 are implemented and **PASS (phase-4b)** on real PostgreSQL (integration) and through the browser (E2E: review, decision, dataset, approved test case, masked input), with mutation checks. T34 and T35 are blockers re-verified in Phase 5 (4→5). **T36–T40 stay NOT RUN:** they test the deterministic rule engine, A/B runs and evaluation results of Phase 4C; Phase 4B only stores well-formed rules and allows a case without `expected_output` (the precondition of T40), which is not evaluated. T31 was re-run with migration `0003` (upgrade → downgrade → upgrade, plus 0002→0003 with data and refused downgrade with human rows); its status stays `PASS (phase-2b)`. T41 (hostile review text, span names and test-case content render as text) and T42 (bundle scan 0, no token in any E2E response of the new pages) were re-verified on the Phase 4B pages; their statuses stay `PASS (phase-3c)`.

## Sub-cases (inherit the parent's criticality)

These are the required protocol, dedup and provenance cases. They are reported under their parent ID and are not new test IDs. Executed sub-cases and their results are listed per phase report (Phase 1B: `reports/phase-1b.md` §4; Phase 1C: `reports/phase-1c.md` §3; Phase 1 closure re-run and R08 evidence: `reports/phase-1-final.md` §3; Phase 2A SDK evidence: `reports/phase-2a.md` §4; Phase 2B pricing and OpenAI adapter evidence, including T23-S1/S2 with costs: `reports/phase-2b.md` §4; Phase 2C metrics, FastAPI demo and T48-S1 at the aggregate API: `reports/phase-2c.md` §6; Phase 3A dashboard foundation and Overview, T41/T42/T48: `reports/phase-3a.md` §5; Phase 3B trace explorer, T41/T45/T46/T48 and the UI re-verification of T02/T18/T25–T28: `reports/phase-3b.md` §6.5; Phase 3C model comparison, project selector, R40/R41 and the re-verification of T41/T42/T45/T46/T48 with run-owned fixtures: `reports/phase-3c.md` §7).

| Sub-case | Parent | Scenario | Expected outcome |
|---|---|---|---|
| T01-S1 | T01 | Commit-before-success | When the `200` arrives, the rows are visible from a separate DB connection. Response `Content-Type: application/x-protobuf` |
| T01-S2 | T01 | Empty request | `200`, `partial_success` unset, no rows |
| T01-S3 | T01 | Standard exporter smoke | Python `opentelemetry-exporter-otlp-proto-http` 1.45.1 → rows stored |
| T03-S1 | T03 | Multiple resourceSpans / scopeSpans | All spans stored with the correct scope and resource |
| T03-S2 | T03 | Alias precedence | Both `gen_ai.provider.name` and `gen_ai.system` present → the standard value is stored, the alias dropped and counted |
| T06-S1 | T06 | Invalid IDs | Wrong length / all-zero trace or span ID → `400`, `BadRequest.field_violations[].reason=INVALID_*`, no ID value in the body |
| T06-S2 | T06 | Bad timestamps | `end < start` → `END_BEFORE_START`; ≥ 2^63 → `TIMESTAMP_OUT_OF_RANGE` |
| T06-S3 | T06 | JSON media type | `Content-Type: application/json` → `415`, response `Content-Type: application/json`, JSON-encoded Status |
| T06-S4 | T06 | Other / missing media type | `text/plain` or none → `415`, binary Status, `application/x-protobuf` |
| T06-S5 | T06 | Unsupported encoding | `Content-Encoding: br` → `415` |
| T06-S6 | T06 | Atomic batch | 1 invalid span among 10 valid → `400`, **0** rows written |
| T08-S1/S2/S3 | T08 | Each limit separately | Wire, decompressed (gzip bomb) and 2049 spans → `413` each |
| T23-S1 | T23 | Double instrumentation | Two spans with the same `gen_ai.response.id` → one usage row, collision counter + 1 |
| T23-S2 | T23 | Key precedence | Response ID present → `call_key_source=provider_response_id`; absent with call_id → `sdk_attempt`; neither → `span_fallback` |
| T30-S1 | T30 | DB error after partial insert | `503`, 0 rows from the batch, retry succeeds idempotently |
| T48-S1 | T48 | Mixed trace | Real + synthetic inference spans → trace `mixed`, excluded from "reported real" trace counts; its real usage row is counted at usage level only |
| T48-S2 | T48 | Real claim without response ID | Successful inference span claiming `real` with no `gen_ai.response.id` → stored `unknown` |

**Benchmark (Phase 5, no pre-set targets):** re-ingest 100–1000 span fixtures and measure trace-open time on a 10k-span dataset. Report N, hardware, method, p50 and p95.

**Commands:** `pytest`, `ruff check`, `alembic upgrade/downgrade` exist (backend, see `PHASE_STATUS.md`). Frontend (Phase 3A, in `frontend/`): `npm run typecheck`, `npm run lint`, `npm test`, `npm run build`, `npm run check:bundle`, `npm run test:e2e` (Playwright, real backend; Phase 3B `traces.spec.ts` also needs `scripts/dev/seed_trace_fixtures.py`).

**Documentation check (exists):** `python -I scripts/verify_phase0.py`.
