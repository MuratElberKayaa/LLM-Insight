# Phase 10D — Deterministic error patterns (F8)

**Date:** 2026-11-20 (UTC). **Branch:** `feature/phase-10-medium-term`. No migration, no provider request. The decision is recorded as ADR-57 (batch authorization, recommended options).

## 1. Result

`GET /api/v1/error-patterns` groups every ERROR span of the window by an exact fingerprint of stored identifiers: source (inference, tool, agent or other), provider, model or tool name, and `error.type`.

Each pattern carries its spans, traces, first and last trace start, and the newest trace. It also carries `error_class`, the diagnostics engine's exact-match reading of `error.type` (for example `http_429` or `timeout`).

The overview shows the 10 largest patterns with a link to the newest trace. It says that a pattern counts stored rows and is not a root cause, and that the overview's provider, model, status and provenance filters do not apply.

## 2. Decisions (recommended options)

| Question | Chosen | Rejected |
|---|---|---|
| Grouping | Exact equality of identifiers | Similarity or LLM clustering (ADR-10; not reproducible) |
| Fields | source, provider, model (inference only), tool_name (tool only), error_type | Span names (free text; redacted names would merge) |
| Scope | All ERROR spans of the window, tools and agents included | The overview's provider/model filters, which would hide tool and agent errors |
| Explanation | `error_class` as a label, with no cause stated | A cause statement |

## 3. Changes

- **Backend:** `repositories/error_patterns.py` (new: one grouped query, newest trace by `array_agg(… ORDER BY …)`) and `api/error_patterns.py` (new), registered in `main.py`.
- **Frontend:** `lib/api/error-patterns.ts` (strict decoder) and `components/overview/error-patterns.tsx`. `lib/server/overview.ts` gains one more read, and `app/page.tsx` and `lib/server/backend.ts` are updated.
- **Docs:** `ARCHITECTURE.md` §5 and `ADR.md` (ADR-57).

## 4. Tests

| Command | Result |
|---|---|
| `backend: LLMI_TEST_REQUIRE_DB=1 … pytest tests/integration/test_phase10d_error_patterns.py` | 3 passed. Four patterns from seven error spans in exact order: a 429 inference pattern (3 spans, 2 traces), a tool without `error.type`, an agent `_OTHER` and an inference timeout. The test also checks the `http_429`, `timeout` and `not_recorded` classes, the newest trace ID, that an OK span and an out-of-window trace are excluded, the limit and truncation, `404` and `401` |
| `frontend: npm test` (`phase10d.test.tsx`) | 15 new tests: sums and order invariants, field-by-source rules, class versus error type, rendering, the filters note |

**A defect caught on the first run.** The first query used `array_agg … WITHIN GROUP`. PostgreSQL rejects that for `array_agg` and returned a `ProgrammingError`, which the API answered with a generic `500`. It now uses `array_agg(x ORDER BY …)` through SQLAlchemy's `aggregate_order_by`.

The frontend's request-count test (`backend.test.ts`) went from 9 to 10 overview reads, with a note. Its rule, that every request carries the explicit project, is unchanged.
