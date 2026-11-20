# Phase 9E — Re-review of the npm audit exceptions R102 and R144

**Date:** 2026-11-19 (UTC). **Branch:** `feature/phase-9b-reference-price`. No dependency changed. `package.json` and `package-lock.json` are untouched.

**Authorization:** the owner asked for the remaining short-term phases to run in one batch, taking the recommended option at each decision and recording it (2026-11-19). Both exceptions had a review deadline of 2026-11-30.

## 1. Result

| Risk | Before | After |
|---|---|---|
| R144 `picomatch` GHSA-fc5q-39xc-vjpf | Temporary dev-only exception until 2026-11-30 | **Removed.** GitHub withdrew the advisory on 2026-11-18T15:38:56Z, and npm audit no longer reports it |
| R102 `minimatch` GHSA-g3hv-75wg-m249 | Exception approved 2026-11-01, deadline 2026-11-30 | **Re-approved once.** `approvedOn` 2026-11-19, deadline **2026-12-07**, to adopt a fix that is still inside the two-week rule |

The full audit gate gives a CONDITIONAL PASS: 7 dev-only findings, all R102, review by 2026-12-07 UTC. The production audit reports 0. This is still **not a clean audit**.

## 2. Evidence

| Check | Result |
|---|---|
| `GET https://api.github.com/advisories/GHSA-fc5q-39xc-vjpf` (public, unauthenticated) | `withdrawn_at: 2026-11-18T15:38:56Z`, severity high, range `<= 4.0.7`, no patched version |
| `POST https://registry.npmjs.org/-/npm/v1/security/advisories/bulk` with `{"picomatch": ["2.3.2", "4.0.7"]}` | `{}` (no advisory) |
| `npm audit --json`, empty npm cache, two runs (2026-11-19T13:35Z) | 7 findings, the same in both runs: `minimatch` and the six `@typescript-eslint` / `typescript-eslint` dependents, moderate. No `picomatch` |
| Gate on the 2026-11-18 policy | CONDITIONAL PASS plus `NOTICE: R144 (GHSA-fc5q-39xc-vjpf) no longer appears in the audit: remove the exception` |
| `npm view typescript-eslint time` | 8.73.0 published 2026-11-16T17:07:26.146Z |
| `npm view @typescript-eslint/typescript-estree@8.73.0 dependencies` | `minimatch: ^11.0.1`, outside the advisory range |
| `npm view picomatch time` | Latest 4.0.7 (2026-08-24); no newer release |

Only public package and advisory names were sent; no project data left the machine.

## 3. Decisions (recommended options, recorded)

1. **R144: remove the exception.** The finding no longer exists in the audit, so keeping the exception would accept nothing. If GitHub republishes the advisory, the fail-closed gate rejects it as an unapproved advisory and forces a new review. That is the safe default.
2. **R102: extend once, to 2026-12-07, instead of upgrading now.** The fix, `typescript-eslint` 8.73.0, is younger than the two-week rule (CR-09) until 2026-11-30T17:07:26Z, which is after the current deadline. The alternatives were rejected:
   - **Adopting 8.73.0 now under a security exception to CR-09:** a moderate, dev-only finding does not justify bypassing the supply-chain rule.
   - **Letting the gate expire:** CI would fail from 2026-12-01 with no safe remedy until the upgrade.

   The new deadline is 18 days after approval, within the 31-day maximum (`MAX_EXCEPTION_DAYS`). It is not an open-ended renewal: the remediation text names the planned upgrade.

## 4. Changes

- `frontend/security/npm-audit-policy.json`:
  - the R144 object is removed;
  - R102 changes only `approvedOn`, `approvedBy`, `reviewDeadline`, `blockingConditions` (the deadline sentence), `justification` (appended) and `remediation` (appended);
  - every other field is unchanged, and a test checks this.
- New fixtures in `frontend/tests/fixtures/npm-audit/`:
  - `policy-r102-r144-2026-11-18.json`, a byte-for-byte copy of the 2026-11-18 policy;
  - `full-r102-2026-11-19.json` and `-run2.json`, the two real reports.
- `frontend/tests/scripts/audit-policy.test.mjs`:
  - section 18 now tests the 2026-11-18 policy copy against its own reports. Its CLI case uses `run()` with that copy, because the CLI always reads the current file.
  - New section 19 tests the current policy against the 2026-11-19 reports. It checks the pass, that the R102 fields are unchanged apart from the reviewed ones, the 31-day limit, the new expiry at 2026-12-08T00:00Z, refusal before the approval date, the old picomatch report now failing, the R144 removal notice, and the CLI on the real report.
- `docs/SOURCES_AND_RISKS.md`: the R102 row gets its re-review; the R144 row is marked closed.

## 5. Checks

| Command (from `frontend/`, pinned Node 22.23.4) | Result |
|---|---|
| `npm run test:audit-policy` | **116 passed, 0 failed** (110 before, 6 new) |
| `npm audit --omit=dev --audit-level=info` | exit 0, found 0 vulnerabilities |
| `npm audit --json` (empty cache) + `node scripts/audit-policy.mjs --report … --npm-exit-code 1` | CONDITIONAL PASS, R102 only, review by 2026-12-07 UTC |
| `python -I scripts/verify_phase0.py` (repo root) | 865 PASS, 0 FAIL |

## 6. Follow-up (dated)

- **On or after 2026-11-30T17:07:26Z:**
  1. Upgrade `typescript-eslint` and the pinned `@typescript-eslint/*` 8.71.1 packages to 8.73.x.
  2. Re-run the full audit, typecheck, lint and tests.
  3. Delete R102.
  4. Remove the `minimatch` 10.x copy from the policy tests' current section.
- **Before 2026-12-07:** if the upgrade cannot be made, re-review R102 again. The gate fails on 2026-12-08T00:00Z by design.
