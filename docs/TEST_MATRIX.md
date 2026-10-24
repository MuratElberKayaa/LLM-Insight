# LLM Insight — Test Matrix (T01–T48)

Source: master plan §I. The IDs and scenario names are preserved exactly; the expected outcomes are refinements taken from the contracts. **Every test is planned and none has been run.** A test's status changes only when there is real command or CI output.

**Legend:**
- **Crit ★** = critical (starred in the master plan). There are 39. Phase 5 gate: 0 FAIL on all of them (CR-04).
- **Blocker ⛔** = presentation blocker. There are 10, all of them also critical.
- **Phase** = the phase where the test is implemented → the phase(s) where it is re-verified.
- **Level:** U = unit, I = integration (real PostgreSQL), E = end-to-end (Playwright / compose).

| ID | Group | Scenario | Expected outcome | Crit | Blocker | Phase | Level | Status |
|---|---|---|---|---|---|---|---|---|
| T01 | Ingestion | Valid protobuf request | `200` + protobuf `ExportTraceServiceResponse`, `partial_success` unset; rows stored | ★ | ⛔ | 1→5 | I | NOT RUN |
| T02 | Ingestion | Parent-child | 3-span fixture: children reference root; tree reconstructs | ★ | | 1→3 | I | NOT RUN |
| T03 | Ingestion | Resource / service name | `service.name` from resource; multiple resourceSpans/scopes; missing → `unknown_service` | ★ | | 1 | I | NOT RUN |
| T04 | Ingestion | Duplicate upsert | Same payload twice → counts unchanged, `200` both times | ★ | ⛔ | 1→2,5 | I | NOT RUN |
| T05 | Ingestion | Invalid key | Missing / unknown / revoked → `401`, identical message, nothing stored, key not logged | ★ | | 1 | I | NOT RUN |
| T06 | Ingestion | Corrupt protobuf | `400` + `google.rpc.Status`, nothing stored | ★ | | 1 | I | NOT RUN |
| T07 | Ingestion | gzip valid | `Content-Encoding: gzip` decoded; corrupt gzip → `400` | | | 1 | I | NOT RUN |
| T08 | Ingestion | Oversized payload | Wire > limit, decompressed > limit (gzip bomb), spans > limit → `413` | ★ | | 1 | I | NOT RUN |
| T09 | SDK | Real/mock client span attributes | CLIENT span `chat {model}` with required `gen_ai.*` + provenance | ★ | | 2 | U/I | NOT RUN |
| T10 | SDK | Nested context | LLM span is child of demo FastAPI root span (same trace) | ★ | | 2→3 | I | NOT RUN |
| T11 | SDK | Provider success usage | Provider-reported usage mapped to `gen_ai.usage.*`, `usage_source=provider_reported` | ★ | | 2 | U | NOT RUN |
| T12 | SDK | 429 / timeout status | Span `ERROR`, `error.type` set, attempt recorded; app gets a handled error | ★ | | 2 | U | NOT RUN |
| T13 | SDK | Export-down fail-open | Backend down: app request succeeds, no exception, latency bounded by design | ★ | | 2→5 | I | NOT RUN |
| T14 | SDK | Retry attempt IDs | Same `call_id`, `attempt` 1..n; separate usage rows per attempt | | | 2 | U/I | NOT RUN |
| T15 | SDK | No secrets in span | No API key / auth header in any exported attribute | | | 2→5 | U | NOT RUN |
| T16 | SDK | No content default | No message content exported; backend drops it if sent | | | 2→5 | U/I | NOT RUN |
| T17 | Cost/metrics | Provider token priority | Provider-reported values win over any estimate | ★ | ⛔ | 2 | U | NOT RUN |
| T18 | Cost/metrics | Unknown token/cost null | Missing usage → NULL tokens, `cost_status=unknown`, never 0 | ★ | | 2→3 | U/I | NOT RUN |
| T19 | Cost/metrics | Decimal known fixture | Hand-computed Decimal cost equals the stored value exactly | ★ | ⛔ | 2 | U | NOT RUN |
| T20 | Cost/metrics | Cached subset no double count | Cached tokens priced once, not added to input | ★ | ⛔ | 2 | U | NOT RUN |
| T21 | Cost/metrics | Reasoning subset no double count | Reasoning tokens not added to output | ★ | | 2 | U | NOT RUN |
| T22 | Cost/metrics | Price version persisted | `price_version` stored per usage row; price change doesn't rewrite history | ★ | | 2 | I | NOT RUN |
| T23 | Cost/metrics | Duplicate usage ignored | Re-export → one usage row, cost unchanged | ★ | ⛔ | 2→5 | I | NOT RUN |
| T24 | Cost/metrics | p95 sample + percentiles math | SQL `percentile_cont` = NumPy `linear` on fixture; `n` returned | ★ | | 2→3 | U/I | NOT RUN |
| T25 | API/DB | Pagination | Cursor pagination stable; `limit` max enforced | ★ | | 1→3 | I | NOT RUN |
| T26 | API/DB | Timestamp timezone | ns epoch → UTC `TIMESTAMPTZ`; API returns ISO-8601 with `Z` | ★ | | 1→3 | U/I | NOT RUN |
| T27 | API/DB | Orphan span | Missing parent stored and returned as orphan | ★ | | 1→3 | I | NOT RUN |
| T28 | API/DB | Project filter auth | Other project's trace → `404`; list never mixes projects | ★ | | 1→3 | I | NOT RUN |
| T29 | API/DB | Empty aggregation | No data → `n=0`, null values (not 0), `200` | ★ | | 2→3 | I | NOT RUN |
| T30 | API/DB | DB outage / transaction rollback | Mid-batch failure → nothing persisted; DB down → `503` + Retry-After; `/health/ready` `503` | ★ | | 1→5 | I | NOT RUN |
| T31 | API/DB | Migration clean recreate | `alembic upgrade head` / `downgrade base` / `upgrade head` on a disposable DB | | | 1→5 | I | NOT RUN |
| T32 | API/DB | Status counts and total distinct API calls | M02/M04 counts match fixture; trace vs inference counts separate | ★ | | 2→3 | I | NOT RUN |
| T33 | Eval/workflow | Failure review create | Valid enum values → `201`; invalid `issue_type` → `422` | ★ | | 4 | I | NOT RUN |
| T34 | Eval/workflow | No approval no test case | Missing or false `approved` → `422`, no row | ★ | ⛔ | 4→5 | I | NOT RUN |
| T35 | Eval/workflow | Redaction before case save | Email / phone / key-like input is masked before insert | ★ | ⛔ | 4→5 | U/I | NOT RUN |
| T36 | Eval/workflow | Golden JSON pass | Valid output passes the JSON Schema + field rule | ★ | | 4 | U | NOT RUN |
| T37 | Eval/workflow | Golden JSON fail | Schema violation fails with a reason | ★ | | 4 | U | NOT RUN |
| T38 | Eval/workflow | Prompt A/B same dataset | Both versions run on identical case IDs | | | 4 | I | NOT RUN |
| T39 | Eval/workflow | Evaluation provenance labels | Each result carries `real`/`synthetic`; mock → synthetic | ★ | | 4 | I | NOT RUN |
| T40 | Eval/workflow | Missing expected output handling | `passed=NULL` ("not evaluated"), excluded from the pass-rate denominator | | | 4 | U | NOT RUN |
| T41 | Security/UI | XSS escape on labels | `<script>` in a model/service name renders as text | ★ | | 3→5 | E | NOT RUN |
| T42 | Security/UI | Server-only API secret | Build output / client bundle / HTML contain no admin token | ★ | ⛔ | 3→5 | E | NOT RUN |
| T43 | Security/UI | Request rate limit | Burst over limit → `429` + Retry-After (ingest and API) | ★ | | 3→5 | I | NOT RUN |
| T44 | Security/UI | Malformed header/auth log sanitization | Logs contain no key/header value after bad-auth requests | | | 1→5 | I | NOT RUN |
| T45 | Security/UI | Trace list UI screenshot assertion | Seeded DB data visible in the list; screenshot saved | ★ | | 3 | E | NOT RUN |
| T46 | Security/UI | Drilldown tree E2E | Trace list → detail shows root → child tree | ★ | ⛔ | 3→5 | E | NOT RUN |
| T47 | Security/UI | Reproducible compose smoke | Clean clone → `docker compose up --build` → init → demo works | | | 5 | E | NOT RUN |
| T48 | Security/UI | Real vs synthetic provenance on dashboard | SYNTHETIC badge on mock data; aggregates split | ★ | | 3→4,5 | E | NOT RUN |

**Counts:** 48 tests, 39 critical, 9 non-critical (T07, T14, T15, T16, T31, T38, T40, T44, T47), 10 blockers (T01, T04, T17, T19, T20, T23, T34, T35, T42, T46).

**Proposed (PADR-08, not applied):** promote T15, T16 and T44 to critical. This is an upgrade only.

**Not in the 48 but required by the phase prompts:** multiple resourceSpans, bad IDs, invalid timestamps, wrong Content-Type (`415`), empty request (`200`), and the standard OTel exporter smoke (Phase 1). These are tracked as sub-cases of T01/T03/T06 in phase reports.

**Benchmark (Phase 5, no pre-set targets):** re-ingest 100–1000 span fixtures, and measure trace-open time on a 10k-span dataset. Report N, hardware, method, p50 and p95.

**Commands (planned):** `pytest`, `ruff check`, `alembic upgrade/downgrade`, `npm run lint`, `npm run typecheck`, `npm run build`, `npx playwright test`. None exist yet.
