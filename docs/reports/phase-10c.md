# Phase 10C — Pseudonymous sessions (F2)

**Date:** 2026-11-20 (UTC). **Branch:** `feature/phase-10-medium-term`. No migration, no new secret, no provider request.

**Authorization:** the batch authorization of 2026-11-20 takes the recommended option at each decision and records it, including decisions on locked ADRs. The decision is recorded as ADR-56. It amends ADR-19 for one key; user identifiers stay denied.

## 1. Result

Conversations can now be grouped and inspected without storing their identifiers. At ingest, `gen_ai.conversation.id` is replaced by a keyed, project-scoped pseudonym, `llminsight.session.hash` (`s1_` + 32 hex). Three ways to use it:

- `GET /api/v1/sessions` aggregates traces, failures, attempts, tokens and known cost per pseudonym;
- `GET /api/v1/traces?session=` drills down to one session;
- the dashboard has a Sessions page that links each session to its traces.

## 2. Decisions (recommended options, recorded in ADR-56)

| Question | Chosen | Rejected |
|---|---|---|
| Where to hash | Server side, at ingest; the raw value exists only in memory | SDK-side hashing: it is SDK-only, spreads the secret and is easy to spoof |
| How | HMAC-SHA256 with K = HMAC(`LLMI_KEY_PEPPER`, "llminsight.session.v1"), message = project id ‖ 0x00 ‖ raw; 128 bits kept | Unkeyed SHA-256: guessable IDs could be confirmed by anyone |
| Scope | Project-scoped: the same ID in another project gives an unrelated hash | A global hash, which would link projects |
| Client-sent hash | Not allowlisted: dropped and counted | Trusting it |
| Invalid IDs | Empty, over 256 characters, control characters, non-string or repeated: dropped and counted | Truncating, which would create a different identity |
| Users | `user.*` / `enduser.*` stay denied | Pseudonymising them now, which needs its own privacy review |
| Storage | The existing span JSONB, with no index (no migration) | A column and index, a scale follow-up |
| Privacy wording | "Pseudonymous, not anonymous", in the API (`pseudonymous: true`), on the page and in `SECURITY.md` | — |

## 3. Changes

| Area | Files |
|---|---|
| Ingest | `services/ingestion/sessions.py` (new: key derivation, pseudonymizer, raw-value rules), `normalize.py` (optional pseudonymizer; a replaced ID is not counted as dropped), `service.py` (the project's pseudonymizer) |
| Read | `repositories/sessions.py` (new), `api/sessions.py` (new), `repositories/traces.py` and `api/traces.py` (`session` filter, `^s1_[0-9a-f]{32}$`), `main.py` |
| Frontend | `lib/api/sessions.ts` (strict decoder), `lib/server/sessions.ts`, `components/sessions/session-table.tsx`, `app/sessions/page.tsx`, `components/side-nav.tsx`, `lib/trace-query.ts` (`session` parse and forward), `app/traces/page.tsx` (session notice), `lib/server/backend.ts` (path allowlist) |
| Docs | `TELEMETRY_CONTRACT.md` §3, `SECURITY.md` §1, `ARCHITECTURE.md` §5, `ADR.md` (ADR-56) |

## 4. Tests

| Command | Result |
|---|---|
| `backend: … pytest tests/unit/test_sessions_pseudonym.py` | 13 passed: an independent HMAC recomputation, project and pepper separation, the separator, raw-value rules, normalization (replaced, not counted), no pseudonymizer means dropped, a forged hash is dropped |
| `backend: LLMI_TEST_REQUIRE_DB=1 … pytest tests/integration/test_phase10c_sessions.py` | 4 passed. The sentinel raw IDs, one email-shaped, appear in no stored span, trace or usage row and in no response. Hashes match the recomputation, and the cross-project hash differs. The test also covers aggregates, the trace filter, `422` without echo for a raw ID, the limit and truncation, `404` and `401` |
| `frontend: npm test` (`phase10c.test.tsx`) | 16 new tests: decoder refusals (raw ID, duplicates, cost and count invariants), table links, the trace-query filter, the path allowlist |

**A real defect caught by the integration test.** The sessions query counted failed traces as `status == "ERROR"`, but `traces.status` is lowercase (`ok` / `error`). It returned 0 failed traces and was fixed to `error`.

**Earlier pins updated, with notes.** These tests pinned structures this phase changes on purpose:

- the backend route lists (`test_phase5a_rate_limit.py`, `test_health.py`);
- the frontend `getJson` reader list (`security-static.test.ts`);
- the navigation (`shell.test.tsx`);
- the trace-query defaults (`trace-query.test.ts`).

Their rules are unchanged: every route is admin-only and rate-limited, and only `lib/server` modules call the backend.

## 5. Risks

- **Pseudonymous data.** Where the ID is personal data (an email, a customer number), the hash remains personal data under KVKK/GDPR. There is still no retention job (open since the MVP).
- **Pepper rotation.** Rotating the pepper unlinks sessions across the rotation.
- **Scale.** Without a JSONB index the session list scans the project's spans in the window.
