# Phase 10F — Threshold alert rules (F6, M21)

**Date:** 2026-11-20 (UTC). **Branch:** `feature/phase-10-medium-term`. No migration, no outbound connection, no provider request. The decision is recorded as ADR-59 (batch authorization, recommended options).

## 1. Result

`GET /api/v1/alerts` evaluates the rules in `backend/alerts/rules.json` on the overview metrics of the window. The shipped rules are:

- error rate ≥ 0.05;
- inference p95 ≥ 10 s;
- time to first chunk p95 ≥ 2 s;
- known cost ≥ $1.00.

Each rule returns `firing`, `ok` or `insufficient_data` (below `min_n`). A partially priced cost is flagged as a lower bound. The overview shows the firing rules, or one quiet line when none fire.

**Nothing is sent anywhere:** the response says `delivery: "none"`.

## 2. Decisions (recommended options)

| Question | Chosen | Rejected |
|---|---|---|
| Delivery | None. Rules are evaluated when read | Webhook, Slack or email now: these need a destination allowlist, secret handling and a threat review (`SECURITY.md` §7) |
| Rule storage | A repository JSON file, validated strictly | Database rules with a UI editor (a new write surface, ADR-27) |
| Metrics | The overview's own numbers: M04, M06, M10, M20 | New queries or anomaly scores |
| Too little data | `insufficient_data`, never `ok` | Treating no data as healthy |
| Unknown cost | A known cost below the threshold with unknown calls is `ok` plus `lower_bound` | Treating unknown as 0 |
| Bad rules file | A server error (logged), never a silent "ok" | Skipping bad rules |

## 3. Changes

- **Backend:** `app/services/alerts.py` (new), `app/api/alerts.py` (new), `backend/alerts/rules.json` (new) and `main.py`.
- **Frontend:** `lib/api/alerts.ts` (strict decoder) and `components/overview/alert-strip.tsx`. `lib/server/overview.ts`, `app/page.tsx` and `lib/server/backend.ts` are updated.
- **Docs:** `ADR.md` (ADR-59), `ARCHITECTURE.md` §5, `METRICS_CATALOG.md` (M21) and `SECURITY.md` §1.

## 4. Tests

| Command | Result |
|---|---|
| `backend: … pytest tests/unit/test_alerts.py` | 21 passed: the shipped rules load; firing, ok and insufficient states; the inclusive threshold; no fire below `min_n`; the cost lower bound; no priced call is not zero; exact strings; 13 malformed-rule refusals; an unreadable file |
| `backend: LLMI_TEST_REQUIRE_DB=1 … pytest tests/integration/test_phase10f_alerts.py` | 3 passed: 25 attempts with 5 failed, so the error rate of 0.2 fires; latency ok; TTFC and cost insufficient; an empty window fires nothing; `404` and `401` |
| `frontend: npm test` (`phase10f.test.tsx`) | 14 new tests: the decoder ties state to value, threshold and `n`; it refuses a firing lower bound, a wrong count, duplicate IDs, a delivery channel or a float threshold; rendering |

The route lists and the frontend request count (now 11) were updated with notes.
