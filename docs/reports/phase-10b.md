# Phase 10B — Streaming time to first chunk (F7, M20)

**Date:** 2026-11-20 (UTC). **Branch:** `feature/phase-10-medium-term`. No migration and no provider request. Nothing is committed yet: there is one commit at the end of the batch.

**Authorization:** the batch authorization of 2026-11-20 takes the recommended option at each decision and records it. The decision is recorded as ADR-55.

## 1. Result

M20 is now a real metric. The overview's inference-latency card states the client time to first chunk (p50, p95, `n`), or says why there is none:

- "no streaming attempt in scope", or
- "not recorded by N streaming attempt(s)".

Compare shows the same per group when the group streamed. The SDK can record it. Nothing is ever estimated.

## 2. Evidence (the conventions)

The source is https://github.com/open-telemetry/semantic-conventions-genai (read 2026-11-20): `docs/gen-ai/client-inference.md` and `docs/registry/attributes/gen-ai.md`.

- **`gen_ai.response.time_to_first_chunk`:** a double, Development stability, "Recommended if the request was a streaming request". Its definition: "Time from the start of a logical inference operation to the first chunk in its streaming response, in seconds." A chunk is a decoded stream event "carrying new, non-empty output". The attribute "MUST NOT" be set if no chunk is received.
- **`gen_ai.request.stream`:** a boolean, Development stability, "Conditionally Required … If and only if the request is streaming".
- **Metric `gen_ai.client.inference.time_to_first_chunk`:** it exists, but its section was not read. It is not used here.

## 3. Decisions (recommended options, recorded in ADR-55)

| Question | Chosen | Rejected |
|---|---|---|
| Mapping version | Add to the unreleased v2; there is no v3 within one batch | A v3 for an unreleased v2 |
| Value range | [0, 3600] s; outside it the value is dropped, never clamped | Clamping, which would invent a value |
| Range check for `num` | Applied only where a spec sets a range; the v1 `temperature` and `top_p` are unchanged | A global range |
| Aggregation | `percentile_cont` over JSON numbers, in ms, per provenance bucket and per Compare group | The metric instrument (the project ingests spans only) |
| No value | `available: false` with `no_streaming_attempts` or `not_recorded` | `0`, or an estimate from duration or tokens |
| SDK start point | The physical attempt's start (one span is one attempt). This is a recorded deviation from semconv's "logical operation" start | Passing a logical start through the retry wrapper (not needed for single-attempt streaming) |
| "First meaningful content" (PRD) | Not built: it needs content to tell text from reasoning or tool deltas | — |

## 4. Changes

| Area | Files |
|---|---|
| Mapping | `attrmap.py`: `SECONDS` spec, the new key, the range check in `_coerce` for `num` |
| Query | `repositories/metrics.py`: `_ttfc_columns()` in the overview and compare statements |
| API | `services/metrics.py`: `ttfc()` block; `inference.time_to_first_chunk` per bucket; per-group block |
| SDK | `instrumentation.py`: `RequestParams.stream`, `InferenceRecorder.record_first_chunk()`, `MAX_TTFC_SECONDS`; `README.md` |
| Frontend | `lib/api/types.ts`, `schema.ts` (`ttfcDecoder`, 6 invariants), `components/overview/kpi-grid.tsx` (`ttfcSentence`), `components/models/model-comparison.tsx` (first-chunk line) |
| Fixtures | `tests/fixtures/overview.ts` (`NO_TTFC`); `overview.live.json` and `models-compare.live.json` re-recorded (§6) |
| Docs | `TELEMETRY_CONTRACT.md` §3, `METRICS_CATALOG.md` (M20), `ARCHITECTURE.md` §5, `ADR.md` (ADR-55) |

## 5. Tests

| Command | Result |
|---|---|
| `backend: … pytest tests/unit/test_ttfc.py` | 14 passed: range, NaN/∞, string, bool, the v1 `temperature` unchanged, the three block states |
| `backend: LLMI_TEST_REQUIRE_DB=1 … pytest tests/integration/test_phase10b_ttfc.py` | 3 passed: three recorded values → p50 200 ms, p95 380 ms (linear), `streaming_attempts` 4, `n` 3; Compare group; streaming without values → `not_recorded` |
| `backend: LLMI_TEST_REQUIRE_DB=1 … pytest` (full, 10A + 10B) | **2047 passed, 0 failed, 0 skipped** |
| `sdk: … pytest tests/unit/test_streaming_ttfc.py` (+ agent steps, instrumentation) | 35 passed |
| `sdk: ruff check src tests`, `ruff format --check src tests`, `mypy src` | pass |
| `frontend: npm run typecheck`, `npm run lint`, `npm test` | pass; **459 passed** (13 new) |
| `python -I scripts/verify_phase0.py` | 865 PASS, 0 FAIL |

**A contaminated run, recorded.** An earlier full backend run (2023 passed, 5 failed) overlapped with a 10A mutation check that temporarily edited `genai_steps.py`. Its five failures were exactly the tests that mutant breaks. The run was discarded and repeated cleanly (2047 passed). Since then, mutation checks are run only when no full suite is running.

**Timer resolution.** The SDK timing test first assumed a 20 ms sleep measures at least 20 ms. On Windows, `time.monotonic` ticks at about 15.6 ms, and the test measured 15 ms. It now waits 50 ms and accepts ≥ 30 ms. The code was not changed.

## 6. Re-recorded contract fixtures (read-only)

The backend ran on `127.0.0.1:8041` against the local development database. It received only GET requests, with the recorded windows, and was then stopped.

- **`models-compare.live.json`:** identical to the 9B recording apart from `time_to_first_chunk` per group. All 7 groups are `no_streaming_attempts`.
- **`overview.live.json`** (Phase 3A recording): the only differences are `filters.provider`, an additive echo from Phase 6C-C, and the new M20 shape. The comparison is scripted, and no other field changed.
