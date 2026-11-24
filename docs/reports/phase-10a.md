# Phase 10A — Agent, tool, retrieval and MCP steps (F1)

**Date:** 2026-11-20 (UTC). **Branch:** `feature/phase-10-medium-term` from `ad1ec8e`. No migration and no provider request. Nothing is committed yet: the owner asked for one commit at the end of the 10A–10F batch.

**Authorization:** the owner chose the medium-term batch in the recommended order, with the recommended option taken at each decision and recorded, including decisions on locked ADRs (2026-11-20). The decision is recorded as ADR-54.

## 1. Result

Agent traces are now readable step by step without storing any content:

- each span carries a step kind (`agent`, `tool`, `retrieval`, `mcp`, `inference`, …) and its recorded identifier;
- the trace detail shows an "Agent steps" section and tool labels in the waterfall;
- a deterministic rule flags a tool executed at least five times in one trace, as an unverified hypothesis;
- the SDK can emit these spans.

## 2. Decisions (recommended options, recorded in ADR-54)

| Question | Chosen | Rejected |
|---|---|---|
| What to store | Identifiers only: agent name/id/version, tool name/type/call id, data source id, MCP method and protocol version | Tool arguments and results, even redacted (ADR-19; redaction is heuristic) |
| Content keys | Always denied: tool call arguments/result, tool and agent descriptions, `mcp.session.id`, `mcp.resource.uri` | Leaving them to the "not allowlisted" path, which would be one config mistake away from storage |
| Mapping version | `llminsight.attrmap.v2`, a superset with every v1 rule unchanged, recorded per span | Staying at v1: ADR-15 requires a new version for an allowlist change |
| Inference detection | Unchanged (ADR-13): agent and tool spans create no usage or cost row | Treating `execute_tool` as inference |
| Classification | Derived on read (`genai_steps.py`); nothing stored | A new column, which would need a migration and a backfill question |
| Loop signal | One `repeated_tool_calls` finding per trace (≥ 5 executions of one tool name), hypothesis only, `rules.v2` | One finding per tool: its ID would collide on the trace subject |
| Tool failures | Already covered by `non_inference_span_error` | A duplicate rule |
| MCP session ID | Denied now; hashing it is Phase 10C's decision | Storing it in clear |

## 3. Changes

| Area | Files |
|---|---|
| Mapping | `backend/app/services/ingestion/attrmap.py`: version v2, 9 allowlisted keys, 6 denied keys |
| Steps | `backend/app/services/genai_steps.py` (new): `step()`, `summary()`, bounded to 50 tools |
| API | `backend/app/api/traces.py`: `genai_step` per span, `agent_summary` per detail (additive) |
| Diagnostics | `catalog.py`, `engine.py`: category `repeated_tool_calls`, limitation `tool_identity_client_declared`, `rules.v2` |
| SDK | `_guards.py` (mirrored v2 rules), `instrumentation.py` (`StepRecorder`, `agent_span`, `tool_span`), `telemetry.py` (`Telemetry.agent_span`, `Telemetry.tool_span`), `__init__.py`, `README.md` |
| Frontend | `lib/api/trace-types.ts`, `trace-schema.ts` (strict decoder; summary checked against the returned spans), `lib/span-tree.ts`, `components/traces/waterfall.tsx` (step labels), `trace-detail-sections.tsx` (`AgentSteps`), the trace page |
| Fixtures | `tests/fixtures/traces.ts` (a test mirror of the classification); `trace-detail-nested.live.json` and `trace-detail-retry.live.json` re-recorded (§5) |
| Docs | `TELEMETRY_CONTRACT.md` §3, `ADR.md` (ADR-54), `ARCHITECTURE.md` §5 |

## 4. Tests

| Command | Result |
|---|---|
| `backend: … pytest tests/unit/test_genai_steps.py` | 19 passed |
| `backend: LLMI_TEST_REQUIRE_DB=1 … pytest tests/integration/test_phase10a_agent_steps.py` | 3 passed |
| `backend: LLMI_TEST_REQUIRE_DB=1 … pytest` (full) | see §6 |
| `sdk: … pytest tests/unit/test_agent_steps.py` + instrumentation, guards and telemetry | 203 passed |
| `sdk: ruff check src tests`, `ruff format --check src tests`, `mypy src` | pass |
| `frontend: npm run typecheck`, `npm run lint`, `npm test` | pass; **446 passed**, 16 new |
| `python -I scripts/verify_phase0.py` | 865 PASS, 0 FAIL |

**What the integration test proves.** A synthetic agent trace goes through the real OTLP ingest:

- `agent → chat → 5 × search_kb (one ERROR) → MCP lookup_order → chat`;
- the content keys sent on purpose are dropped and counted, and the sentinel string appears nowhere in the stored rows;
- only the two chat spans get usage rows;
- the trace detail returns the expected steps and summary;
- diagnostics return one `repeated_tool_calls` finding with facts and five evidence spans, plus the existing error finding for the failed tool.

**Version pins updated.** Ten earlier tests pinned `attrmap.v1`, `rules.v1` or the diagnostics group list. Each was updated with a one-line note: Groq, OpenRouter and comparison contracts, ADR-41 metadata privacy, ingest persistence, multi-provider read, the Groq key safety test, the trace API detail with its key set, and diagnostics. Their intent is unchanged. The ADR-41 test now says that the version moves only with an allowlist change. Tests that insert historical `v1` rows by SQL were not touched.

**Mutation check** (unit tests, each mutant restored).

- Caught from the start: `execute_tool` classified as inference; tool ordering; the truncation flag.
- Missed at first, caught after a new test:
  - `UNSET` counted as a failed tool. The SDK leaves successful spans `UNSET`, so this was a real gap.
  - An unknown operation reclassified as MCP.

## 5. Re-recorded contract fixtures (read-only)

The backend ran on `127.0.0.1:8041` against the local development database and was stopped afterwards. It received only `GET /api/v1/traces/{id}?project=1` for the two recorded traces. With `genai_step` and `agent_summary` removed, each new response equals the old recording, and the files were rewritten with a dated note. Neither trace has agent or tool steps: `inference_calls` is 2 and everything else is 0.

`diagnostics.live.json` was left as recorded in Phase 4B. It still says `rules.v1`, the decoder does not pin the ruleset, and the two traces gain no new finding.

## 6. Open items

- **Full backend suite and CI.** The local result is in `PHASE_STATUS.md`, Phase 10A. CI runs after the owner's push.
- **SDK lint scope.** `ruff check .` in `sdk/` crashes on access-denied `.pytest-tmp*` folders left from earlier sessions. Those are user-owned, pre-existing and not deleted. The CI equivalent, a clean checkout, has no such folders.
- **Untouched lint findings.** Ruff findings in `demo_app/live_batch.py` predate this phase. That file is not part of CI's ruff scope.
- **Threshold.** The repeated-tool threshold of 5 is a heuristic, and the finding says the repetitions may be intended.
