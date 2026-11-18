# Live prompt A/B — 13 November 2026 (Europe/Istanbul)

## Authorized scope

The user requested a real prompt-improvement demonstration and selected **Groq
`openai/gpt-oss-20b` + Gemini `gemini-3.1-flash-lite`, 24 calls**. They then confirmed both
accounts remain free, sufficient headroom exists for 12 requests per model, and no concurrent
account workload will run. This is a new authorization; historical single-call/batch approvals
are not reused. See `prompt-ab-approval-20261113.json` and the two `operator-ab-*.json` records.
The quota timestamp conservatively precedes the confirmation; consumption/reset values remain
unknown and are not invented. Prior verified ceilings are retained, not increased.

## Actual experiment

- Six invented support tickets: login blocked; invoice download workaround; urgent wording on a
  feature request; document saving blocked; browser-specific sign-in workaround; user-guide query.
- Same six case texts for both versions and providers. Expected labels stay in the evaluator.
- **`live-ticket-v1`**: concise classification instruction with allowed labels and JSON keys.
- **`live-ticket-v2`**: adds category definitions and impact-based priority policy; requests no
  Markdown/explanation. This measures the addition of explicit business rules, not merely wording.
- Eight fixed checks: JSON object, no Markdown, no duplicate keys, exact keys, category enum,
  priority enum, expected category, expected priority. No LLM judge and no post-result rule changes.
- AB/BA order alternates across cases. Each case/version is sampled once per model. Provider
  default temperature is retained, so these observations do not establish statistical significance.
- Same 1,024 output-token ceiling; composed-input conservative bound at most 799 tokens. No
  retry, fallback, concurrency or purchased mode. 24 requests are a ceiling, not a target to recover
  after failures. Model calls are genuine; the input dataset is synthetic.

The original `golden-prompt-v1/v2` mock fixtures and `/experiments` database contract are unchanged.
The new live comparison is a separate CLI/report flow using the same provider adapters and OTLP
pipeline. `live_compare --suite ticket-ab-v1` selects a closed, pinned suite; arbitrary prompts
from files or environment are not accepted. The existing shared batch lock prevents overlapping runs.

## New code

- `demo_app/prompt_ab_scenarios.py`: actual prompt texts, frozen inputs/reference labels, strict rules,
  input hashes and reviewed suite pin.
- `demo_app/live_prompt_ab.py`: immutable plan, one-use execution claim, pre-send reservations,
  sequential children, read-back verification, metadata journal and paired report.
- `demo_app/live_compare.py`: optional closed suite selection; existing provider/send gates unchanged.
- `sdk/tests/unit/test_prompt_ab.py` and `test_prompt_ab_wire.py`: offline orchestration, failure,
  privacy and actual outbound-text checks through both real adapters with fake transports.

Quality failures are recorded and execution continues: otherwise stopping on the first wrong answer
would bias an A/B study. Provider/transport, quota, integrity or export failure stops execution and
retains the shared lock; no automatic resume. An incomplete report cannot declare an improvement.
Each accepted result is checked against its stored project-scoped trace, token counts, parent/child
spans, model, prompt version and unknown cost. Latency in the report is the CLIENT span duration.

## Verification before live execution

- First regression attempt: **54 failed, 193 passed** because the installed SDK differed from the
  checkout. No provider call; the existing SDK-match guard correctly refused execution.
- Reinstalled the local SDK (`pip install --no-deps ./sdk`, pinned build dependency). Both audited
  SDK-match checks now return true; `pip check` passes. No SDK source was changed by this task.
- `pytest tests/unit/test_prompt_ab.py tests/unit/test_comparison_scenarios.py
  tests/unit/test_compare_runner.py tests/unit/test_compare_runner_gemini.py
  tests/unit/test_live_batch.py -q`: **247 passed** after matching the SDK.
- `pytest tests/unit/test_prompt_ab_wire.py -q`: **8 passed**, sockets to external services blocked.
- After adding the pre-send read-access check: A/B unit suite **30 passed** again.
- Ruff for new modules/tests with `sdk/pyproject.toml`: passed; provider/read secrets never printed.
- Initial plan `09aa19c82c2d4de5aaa52b3f28680529`: refused all 24 jobs due to stopped local Docker
  services, **zero provider calls**. Existing PostgreSQL/backend/frontend containers restarted,
  preserving volumes; backend readiness, authenticated read API and dashboard returned 200.
- Final plan `ee04815a042c4357a75c12107392f72a`: all 24 dry-run checks passed, zero refusals.

## Live evidence

Execution completed at 2026-11-12 23:21 UTC (13 November Europe/Istanbul): **24/24 calls**,
24 unique traces, zero transport/provider errors, no retries and no missing pairs.
All eight fixed rules must pass for a case to pass; technical completion is a separate measure.

| Model | V1 pass | V2 pass | Change | CLIENT p50 V1 / V2 |
|---|---:|---:|---:|---:|
| Groq GPT-OSS 20B | 3/6 | 5/6 | +2 cases | 444.2 / 452.9 ms |
| Gemini Flash Lite | 3/6 | 5/6 | +2 cases | 2744.4 / 2835.3 ms |

Both models still failed the expected category for invoice-download case t02 in both variants.
This is a reference-label mismatch, not conclusive evidence of model error: the billing/bug
definitions overlap for a broken invoice feature, and V2 gives no explicit tie-break precedence.
Keep the frozen score unchanged; a future study should clarify the policy and use a fresh test set.
GPT-OSS improved on t03/t06 priority; Gemini improved on t03/t05 output-format compliance.
Gemini's category-and-priority-only match rate was already 5/6 in V1 and remained 5/6 in V2;
its strict overall pass gain must not be described as improved semantic classification accuracy.
V2's extra business policy is part of the intervention, rather than a wording-only rewrite.
These descriptive results cover six fixed synthetic inputs, one response per version/model,
with no general winner or significance claim. Costs remain unknown, not zero.

Final [paired report with trace links](prompt-ab/ee04815a042c4357a75c12107392f72a/REPORT.md)
and [machine-readable evidence](prompt-ab/ee04815a042c4357a75c12107392f72a/report.json).
`docs/presentation/collect_live_evidence.py` independently reconciled the run-window Models API:
four groups of six, all 24 exact trace IDs, tokens, zero errors, unknown costs and CLIENT p50.
Read-only dashboard captures confirm the records in Models/Traces; quality rules remain in the
separate report, not the mock-only web Experiments page.

The refreshed day-window overview is **84 calls / 84 traces / 3 technical errors**:
GPT-OSS 35, Qwen 21, Gemini 28. The 24 A/B calls are included in these 84; the three technical
errors predate this run. Unknown cost applies to all 84. Sources: presentation `live-*-api.json`,
`live-observation.json`, `live-capture-manifest.json` and original, unaltered screenshots.

The presentation is updated as `docs/presentation/LLM-Insight-Sunum-v4.pdf` and `.pptx`,
still ten slides, with actual prompt comparison on slide 7 and explicit sample/UI limits.
Requested wording removals and the blue/white/orange layout with both corner logos are retained.
Final artifact checks: ten PDF pages and ten PPTX slides; both original logos and speaker notes
on every slide; zero measured text overflow or off-canvas shapes. All rendered PDF pages visually
reviewed. PowerPoint native rendering was not performed. Final documentation checker: **826 PASS,
0 FAIL**; new-code Ruff passes. Short Turkish talk track and read-only demo links are in
`docs/presentation/CANLI-AB-ANLATIM.md`. Temporary headless browser profile removed after capture.

Git commit, push, PR and public deployment are outside this task and have not been performed.
