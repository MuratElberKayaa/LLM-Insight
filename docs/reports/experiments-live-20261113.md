# Completed live comparisons in Experiments

The user requested viewing the existing Groq/Gemini V1/V2 results in Experiments and authorized
the integration. No new provider request was made. The two records use their true
`live-ticket-v1/v2` names; the old `golden-prompt-v1/v2` mock data is preserved.

## Working local pages

- [Experiments](http://127.0.0.1:3001/experiments?project=1): completed live comparisons above mock runs.
- [Groq GPT-OSS 20B](http://127.0.0.1:3001/experiments/live/2?project=1): V1 3/6, V2 5/6.
- [Gemini Flash Lite](http://127.0.0.1:3001/experiments/live/1?project=1): V1 3/6, V2 5/6.

Each report includes six paired cases, failed rule names, all twelve trace links, provider-reported
tokens and CLIENT p50. Both versions are actual model instructions. Responses are real; test inputs
are synthetic. The UI explains small sample size, the invoice category ambiguity and the difference
between format and category/priority success. No winner or statistical significance is asserted.

## Storage and access

ADR-51 and ARCHITECTURE sections 4.13/5 define the additive contract. Migration 0006 stores
immutable snapshots in `imported_experiments`, uniquely keyed by project/run/model. Import accepts
only fixed identifiers and eight boolean rules for twelve distinct, completely paired trace IDs.
The authenticated backend checks project-scoped trace/model/provider/prompt identity and measured
usage, then derives metrics from PostgreSQL. It does not trust supplied totals. Unknown cost stays
unknown, and no budget is fabricated to fit the old executor schema.

Rule outcomes are imported evaluator evidence; raw responses were intentionally not retained, so
the backend cannot independently re-evaluate their semantics. This distinction is visible in the UI.
Same evidence returns the same ID, conflicting changes return 409, extra content is rejected.
No new dashboard write action, provider executor or live-run button is enabled. Stored snapshots
can outlive telemetry retention; a trace link may then return not found.

## Verification

- Backend: `pytest tests/integration/test_imported_experiments.py tests/integration/test_migrations.py
  tests/integration/test_experiment_api.py tests/unit/test_experiment_report.py
  tests/unit/test_experiment_runner.py -q -p no:cacheprovider`: **104 passed**, one existing
  Starlette/httpx deprecation warning. Disposable test databases only.
- Frontend: **356 passed**, no failures or skips; new schema/component tests plus existing suite.
- Production Next build and TypeScript succeeded; ESLint and targeted backend Ruff passed.
  Final documentation check: **839 PASS, 0 FAIL**; `git diff --check` clean.
- Auth and project isolation, rejected malformed imports, immutable evidence, idempotency,
  non-destructive downgrade and unchanged telemetry counts covered by integration tests.
- Original report and persisted 24 trace IDs/rules/token counts/CLIENT durations independently
  reconciled; Project 1's 12 November UTC real trace total remains **84**; mock Experiment #58
  still present. This is the presentation's selected day, not an all-time count.
- Headless Edge rendered the list and both detail pages. Each detail shows 12 trace links and
  the correct 3/6 and 5/6 scores. Screenshot inspected; existing theme retained.
- Sources: `experiments-live-verification.json`, `experiments-live-{backend,frontend}-tests.txt`,
  `../presentation/experiments-*-browser.txt`, `../presentation/experiments-capture-manifest.json`
  and `../presentation/assets/experiments-*-full.png`.

Initial test fixture issues (wrong prompt attribute and repeated synthetic response IDs) were fixed
at the fixture source. Schema inventories/security reader allowlist were extended for the new table
and read-only loader. Temporary copied Next docs caused a documentation secret-pattern false positive
on a documentation example; the temporary reference directory was removed after reading.

The first local service activation approval review timed out. The system-permitted single retry
succeeded, and backend/frontend were recreated without resetting PostgreSQL volumes. Only the
additive migration and two completed live snapshots were applied to development data.
No Git staging, commit, push, PR or public deployment was performed.
