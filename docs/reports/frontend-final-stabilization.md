# Frontend final stabilization (UI-R1…UI-R4)

**Date:** 2026-11-24 (UTC). **Branch:** `feature/phase-10-medium-term`. **HEAD:** `daa6430`, with every UI change still uncommitted. No commit, push, PR or merge was made, no real model was called, and the development database and `.env` files were not touched.

**Scope:**
- close the remaining gaps of the frontend modernization (no new feature or design phase);
- prepare Linux visual regression;
- review the whole change set before a commit.

## 1. Fixed issues

| Issue (from `phase-ui-r4.md` §9) | Fix | Regression tests |
|---|---|---|
| **Experiments: `Started` cut at 1280 px** with the sidebar open (the table scrolled inside its region) | Long dataset names wrap in a bounded column; the start time shows date and time on two lines (full value in the title) | E2E (`ui-r4.spec.ts`): at 1280 × 800 the experiment table region has no internal horizontal scroll, and the first `Started` cell ends inside the region. Unit: the wrapping and the two-line cell are present |
| **Dataset case index → card left keyboard focus on the link** | Cards take programmatic focus (`tabIndex=-1`, an `aria-label` "Test case #n", a visible focus ring). `CaseFocus` (a small client component; it stores nothing) moves focus to the card for index links, previous/next links, a `#case-n` deep link on load, and Back/Forward | E2E: the card is focused after a next link, an index click, Enter on an index link (keyboard) and a deep link. Unit: `tabindex="-1"`, the name and the focus ring; the handler targets only case cards, has a strict hash pattern and uses no storage |

No other page was redesigned.

**Found and fixed while verifying (root causes, not assertions):**
- **The first `Started` fix was not enough.** The new E2E check measured 40 px of overflow, so the dataset column was narrowed (10 rem), the in-table mode badge made smaller, and the state shortened in the table ("Not finished"; the full wording stays in the title and for screen readers).
- **`phase3c` "custom UTC range" failed in 2 runs and in 4 of 5 isolated repeats.** A temporary diagnostic spec (deleted) showed:
  - no hydration error;
  - hydration 0.4–1.9 s after `load`;
  - a summary click before hydration never reached `window`, so the `<details>` stayed closed.

  The cause is React holding discrete events on not-yet-hydrated Suspense content and replaying them, which is framework behaviour and not a code change of this pass. The test clicked too early. It now waits for hydration through a shared `waitForHydration` helper in `e2e-env.ts` (the auto-refresh control's `data-hydrated`). Result: 5/5 isolated, then the full suite passes.

  For users the same effect exists for the first moments after a slow load (an early click is held); this is noted in §5.
- **Mojibake in a test:** an earlier edit had put two real backspace bytes into `ui-r4.test.tsx`. The project's Trojan Source test caught them, and the bytes were replaced with the `\b` escape. All 90 changed text files were then scanned: no control or bidi character.

## 2. Linux visual regression

### 2.1 The CI environment, as configured

- **Runner:** GitHub-hosted `ubuntu-24.04` (`ci.yml`, job `e2e`), x86_64.
- **Node:** 22.23.4 (`frontend/.nvmrc`). **Python:** 3.12.6.
- **Browser:** Playwright **1.64.0** with its bundled Chromium (`npx playwright install chromium`; `scripts/qa/e2e_check.py` pins the version and checks the host is Ubuntu 24.04). Locally, msedge is used.
- **Fonts:** whatever the runner image has. The gate only *checks* `fonts-liberation`, `fonts-noto-color-emoji`, `fonts-unifont`, `fonts-ipafont-gothic`, `fonts-wqy-zenhei`, `fonts-tlwg-loma-otf`, `fonts-freefont-ttf`, `xfonts-*` and the Chromium libraries, and installs none.
- **Command:** `bash scripts/qa/e2e_gate.sh test`, which runs `npx playwright test` with `LLMI_E2E_CI=1`. A skipped test fails the gate.
- **Baseline names:** `tests/e2e/<spec>-snapshots/<name>-<platform>.png`. Windows makes `-win32`, Linux makes `-linux`, so the two sets never collide.

### 2.2 Could a CI-equivalent environment be built locally?

**No.**
- **WSL:** it answers again, but runs Ubuntu **22.04**, not 24.04 (`/etc/os-release`); earlier it timed out with `Wsl/Service/WSAETIMEDOUT`.
- **Docker:** there is no Ubuntu or Playwright image locally. Pulling one and installing Playwright 1.64.0 Chromium plus the fonts above would give an *approximation*, but not the runner image's full font set. A pixel comparison depends on exactly that.
- **E2E stack:** it is loopback-bound behind a host check, so a container would need extra plumbing that CI does not have.

Images produced in any of these would be plausible-looking but unverified baselines, so none were produced. No Windows image was copied under a Linux name, and no pixel test was made to pass artificially. The running containers and databases were left as they were.

### 2.3 What is in place instead

- **Acceptance manifest** `frontend/tests/e2e/visual-baselines.json`: one entry per visual spec, `"linux": "pending" | "accepted"`. Both are `pending` now.
- **Policy** `frontend/tests/setup/visual-policy.ts`, used by `playwright.config.ts`:

| Situation | Locally (msedge) | CI (`LLMI_E2E_CI=1`) |
|---|---|---|
| Spec `pending` | runs against `-win32` | **not discovered**; the log prints `[visual-regression] NOT RUN on this platform: <spec>: <reason>` |
| Spec `accepted`, every `-win32` baseline has a `-linux` twin | runs | **runs** (it must) |
| Spec `accepted`, a Linux twin missing | runs | **the run stops** before any test (`[visual-regression] refused: …`) |
| Visual spec not in the manifest | runs | **the run stops** |
| Missing baseline during a run | written (local default) | **never written** (`updateSnapshots: "none"`), so it fails instead of passing |

Checked with `--list`:
- CI mode: 93 tests, both visual specs NOT RUN with their reason.
- Local: 104 tests.
- Capture mode (`LLMI_VISUAL_LINUX=1`): 104 tests.
- With `ui-r3` set to `accepted` and no Linux images: `Error: [visual-regression] refused: ui-r3-visual.spec.ts is accepted for Linux but lacks every Linux baseline`, exit 1. The manifest was restored afterwards.

- **Capture workflow** `.github/workflows/visual-baselines.yml`:
  - `workflow_dispatch` only, never on push or PR; `contents: read`; the same pinned actions and e2e gate phases as `ci.yml` (which is unchanged).
  - It runs `phase4d.spec.ts` (seeds the golden dataset and mock experiments), `ui-r3-visual.spec.ts` and `ui-r4-visual.spec.ts` with `--update-snapshots=all`. Output is redacted with the gate's own `e2e_check.py redact`.
  - It uploads **only** the `*-linux.png` files with `SHA256SUMS` as the artifact `linux-visual-baselines` (14 days), then cleans up.
  - It compares and accepts nothing.

### 2.4 Exact steps in CI (owner)

1. Push the branch, without the Linux images. The `e2e` job runs the functional suite and prints `NOT RUN` for both visual specs. Visual regression is **not** verified on Linux at this point.
2. Actions → **visual-baselines** → *Run workflow* on that branch.
3. Download the `linux-visual-baselines` artifact. Check `SHA256SUMS`, then open every image and compare it with its `-win32` twin. The content must match (same sections, data and masks); only font rendering may differ.
4. Copy the images to `frontend/tests/e2e/ui-r3-visual.spec.ts-snapshots/` and `…/ui-r4-visual.spec.ts-snapshots/`.
5. In `frontend/tests/e2e/visual-baselines.json`, set each reviewed spec to `"linux": "accepted"` with `reviewed_by`, `reviewed_at` and `capture_run` (the run URL). Commit images and manifest together.
6. Push. From then on the `e2e` job **must** run those specs on Linux: a missing image stops the run, and a diff beyond the tolerance fails it.
7. Optional: to make it unmissable, list the visual specs in the gate's expectations, as the existing test-count floor (`MIN_All commands ran on the final code. Frontend commands ran from `frontend/` with Node 22.23.4; the others from the repo root or `backend/` as noted.

| Check | Command | Result |
|---|---|---|
| TypeScript | `npm run typecheck` | **PASS** |
| ESLint | `npm run lint` (0 warnings allowed) | **PASS** |
| Frontend unit | `npm test` | **PASS: 575 passed**, 0 failed (565 before + 10 in `frontend-final.test.tsx`) |
| Audit policy | `npm run test:audit-policy` | **PASS: 116** |
| Production build | `npm run build` | **PASS** |
| Bundle secret scan | `npm run check:bundle` | **PASS: 516 files, 25 in the browser bundle, 0 findings** |
| Backend unit | `pytest -q tests/unit` (backend) | **PASS: 1394 passed** |
| Backend lint/format | `ruff check .` / `ruff format --check .` (backend) | **PASS** |
| Dev scripts (CI's exact commands) | `backend/.venv/bin/ruff check|format --check scripts/dev/seed_trace_fixtures.py scripts/dev/e2e_fixtures.py` (root, ruff defaults) | **PASS** |
| QA gate self-tests | `python -I -m unittest discover -s scripts/qa/tests` | **PASS** (15 skipped, as before) |
| Docs | `python -I scripts/verify_phase0.py --quiet` | **PASS: 865, 0 FAIL** |
| Credential patterns in the change set | `scripts/qa/secret_scan.py` PATTERNS over every changed or untracked file, PNGs as bytes; no env file read | **PASS: 136 files, 0 hits** |
| Control/bidi characters | scratchpad scan of 90 changed text files | **PASS: 0** |
| **Playwright E2E, full suite, local (msedge, Windows)**, disposable `_test` database | `npx playwright test` | **PASS: 104 passed**, 0 failed, 0 skipped (4.9 min). This includes 6 + 5 visual tests against the Windows baselines |
| **Visual regression on Windows** (msedge, `-win32` baselines) | part of the run above | **PASS: 11 tests, 21 baselines** |
| **Visual regression on Linux** (CI, Chromium, `-linux` baselines) | — | **NOT RUN.** No Linux baseline exists; the CI e2e job lists both visual specs as NOT RUN (93 tests instead of 104) |

**What is therefore not verified:**
- The frontend's look on the CI renderer (Linux Chromium) is **not** covered by any pixel comparison yet.
- The full frontend test coverage is **not** complete until §2.4 is done.
- The functional E2E suite (93 tests in CI mode) is expected to run on CI. It has passed locally, not yet on GitHub after these changes.

**E2E history of this pass** (all on disposable databases, dropped afterwards):

| Run | Result | Cause |
|---|---|---|
| 1 | 97 passed, 1 failed, 6 not run | The new 1280 px check found the table still 40 px too wide (fixed) |
| 2 | 103 passed, 1 failed | `phase3c` early click before hydration (diagnosed above, fixed) |
| Repeat of that test ×5, before the fix | 1 passed, 4 failed | — |
| Repeat ×5, after the fix | 5 passed | — |
| **3 (final)** | **104 passed** | — |

**Bundle** (sum of `.next/static`):

| | UI-R4 | Final | Δ |
|---|---|---|---|
| JS chunks | 1 162 051 B (19 files) | 1 171 565 B (20 files) | +9 514 B (+0.8 %): the `CaseFocus` client component, its own chunk on the dataset page |
| CSS | 50 537 B | 51 311 B | +774 B |`) does.

## 3. Full test results (final state)

All commands ran on the final code. Frontend commands ran from `frontend/` with Node 22.23.4; the others from the repo root or `backend/` as noted.

| Check | Command | Result |
|---|---|---|
| TypeScript | `npm run typecheck` | **PASS** |
| ESLint | `npm run lint` (0 warnings allowed) | **PASS** |
| Frontend unit | `npm test` | **PASS: 575 passed**, 0 failed (565 before + 10 in `frontend-final.test.tsx`) |
| Audit policy | `npm run test:audit-policy` | **PASS: 116** |
| Production build | `npm run build` | **PASS** |
| Bundle secret scan | `npm run check:bundle` | **PASS: 516 files, 25 in the browser bundle, 0 findings** |
| Backend unit | `pytest -q tests/unit` (backend) | **PASS: 1394 passed** |
| Backend lint/format | `ruff check .` / `ruff format --check .` (backend) | **PASS** |
| Dev scripts (CI's exact commands) | `backend/.venv/bin/ruff check|format --check scripts/dev/seed_trace_fixtures.py scripts/dev/e2e_fixtures.py` (root, ruff defaults) | **PASS** |
| QA gate self-tests | `python -I -m unittest discover -s scripts/qa/tests` | **PASS** (15 skipped, as before) |
| Docs | `python -I scripts/verify_phase0.py --quiet` | **PASS: 865, 0 FAIL** |
| Credential patterns in the change set | `scripts/qa/secret_scan.py` PATTERNS over every changed or untracked file, PNGs as bytes; no env file read | **PASS: 136 files, 0 hits** |
| Control/bidi characters | scratchpad scan of 90 changed text files | **PASS: 0** |
| **Playwright E2E, full suite, local (msedge, Windows)**, disposable `_test` database | `npx playwright test` | **PASS: 104 passed**, 0 failed, 0 skipped (4.9 min). This includes 6 + 5 visual tests against the Windows baselines |
| **Visual regression on Windows** (msedge, `-win32` baselines) | part of the run above | **PASS: 11 tests, 21 baselines** |
| **Visual regression on Linux** (CI, Chromium, `-linux` baselines) | — | **NOT RUN.** No Linux baseline exists; the CI e2e job lists both visual specs as NOT RUN (93 tests instead of 104) |

**What is therefore not verified:**
- The frontend's look on the CI renderer (Linux Chromium) is **not** covered by any pixel comparison yet.
- The full frontend test coverage is **not** complete until §2.4 is done.
- The functional E2E suite (93 tests in CI mode) is expected to run on CI. It has passed locally, not yet on GitHub after these changes.

**E2E history of this pass** (all on disposable databases, dropped afterwards):

| Run | Result | Cause |
|---|---|---|
| 1 | 97 passed, 1 failed, 6 not run | The new 1280 px check found the table still 40 px too wide (fixed) |
| 2 | 103 passed, 1 failed | `phase3c` early click before hydration (diagnosed above, fixed) |
| Repeat of that test ×5, before the fix | 1 passed, 4 failed | — |
| Repeat ×5, after the fix | 5 passed | — |
| **3 (final)** | **104 passed** | — |

**Bundle** (sum of `.next/static`):

| | UI-R4 | Final | Δ |
|---|---|---|---|
| JS chunks | 1 162 051 B (19 files) | 1 171 565 B (20 files) | +9 514 B (+0.8 %): the `CaseFocus` client component, its own chunk on the dataset page |
| CSS | 50 537 B | 51 311 B | +774 B |

## 4. Git change summary

**Branch** `feature/phase-10-medium-term`. **HEAD** `daa6430` (Phase 10, committed). Everything below is **uncommitted**: 53 tracked files modified (+2023 / −470 lines) and 87 untracked files.

| Area | Paths | Content |
|---|---|---|
| Frontend app | 14 `frontend/app/**` | Overview, Traces, trace page, Sessions, Models, Datasets, Experiments (incl. setup, case result, imported report) |
| Frontend components | 38 `frontend/components/**` | Shell (`shell-frame`, collapsible sidebar), context bar, breadcrumbs, explorer (rail, waterfall, step badges), model pair, session timeline, chart tooltip, evaluation workspace, `case-focus` |
| Frontend lib | 10 `frontend/lib/**` | `agent-analysis`, `context-links`, `model-compare`, `session-timeline`, `case-filters`, span-tree and loader additions |
| Frontend tests | 38 `frontend/tests/**` | Unit (`ui-r1…r4`, `frontend-final`); E2E (`ui-r2`, `ui-r3`, `ui-r3-visual`, `ui-r4`, `ui-r4-visual`, `ui-r3-fixtures`); 21 Windows baselines; `visual-baselines.json`; `waitForHydration` |
| Frontend config | `playwright.config.ts`, `tsconfig.test.json`, `tests/setup/visual-policy.ts` | Visual policy; unit build of the policy |
| CI | `.github/workflows/visual-baselines.yml` (new) | Manual Linux baseline capture; `ci.yml` unchanged |
| Dev fixtures | `scripts/dev/seed_trace_fixtures.py`, `scripts/dev/e2e_fixtures.py` | Run-id fix, `ui-r2-agent` and `ui-r3-sessions` SYNTHETIC sets |
| Backend | 3 `backend/tests/unit/*` (new) | Fixture tests only. **No backend code, migration, API, schema, lock or dependency change** (`git diff HEAD -- backend/app backend/alembic backend/requirements.lock frontend/package*.json`: empty) |
| Docs | `docs/PHASE_STATUS.md`, `docs/reports/phase-ui-r1…r4.md`, this report, `docs/reports/assets/{ui-r2,ui-r3,ui-r4,final}/` | Reports and evidence |

- **No stray local files:** `test-results/`, `.next/`, `.test-build/`, `.env*` and the scratchpad are ignored or outside the repo, and none is in the change list.
- **Contract documents unchanged:** ARCHITECTURE.md, ADR.md and TELEMETRY_CONTRACT.md.

**Image assets (7.2 MB in all):**

| Set | Size | Needed? |
|---|---|---|
| Visual baselines (21 `-win32`) | 0.7 MB | **Required**: the visual specs compare against them |
| Report screenshots (`ui-r2` 7, `ui-r3` 9, `ui-r4` 9, `final` 1) | 2.2 + 1.9 + 2.5 + 0.15 MB | Evidence for the reports. Optional for the product; the largest are the two full-page dataset screenshots (≈0.6 MB each) |

All images are SYNTHETIC E2E data, secret-checked before capture, with no PNG text chunks. Keeping them is reasonable for a reviewed PR. If repository size matters, the full-page report screenshots could move to the PR description or a release artifact instead; baselines must stay.

## 5. GO / NO-GO

**Commit: GO**, as one local commit with the user's Git identity, after the owner's review:
- every local check passes, including the full local E2E (104/104) and Windows visual regression;
- no backend, API or schema contract changed;
- no credential pattern or stray file is in the change set.

**PR and merge: CONDITIONAL GO.**
- Push and open the PR; the CI `e2e` job must pass its 93 functional tests.
- Until §2.4 (Linux baselines captured, reviewed and accepted) is done, the PR must state "visual regression on Linux: NOT RUN". Do not describe the frontend coverage as complete.
- Merging before Linux acceptance is acceptable only if the reviewer accepts that gap explicitly.

**NO-GO for:** claiming Linux visual coverage, or setting any manifest entry to `accepted` without the CI-captured, reviewed images.

**Open after this pass (no new phase started):**
1. Linux visual baselines (§2.4, owner).
2. Early clicks during the first ~0.4–2 s on a slow load are held until React has hydrated (framework behaviour). A later improvement could shrink the hydrated surface or show a brief "loading" affordance.
3. The other items of `phase-ui-r4.md` §9 (server-side case filter, dataset editing, experiment list filters) need backend work and were not touched.
