# Two-phase local multi-model integration

Status: Phase 1 implementation and focused offline validation complete. Phase 2 has one
operator-approved Qwen smoke, verified through Project 1 and the dashboard. Existing traces and
earlier consumed approvals are preserved. This document
supersedes the old six-success target's execution schedule, not its historical evidence.
The future target is 4 models x 3 scenarios x 20 = **240 planned requests**, plus separately
approved smoke tests. No batch is authorized by this report.

## Phase 1: integration and batch engine

### Evidence and model status (2026-11-12 UTC)

| Model | Local integration | Live readiness |
|---|---|---|
| Groq `openai/gpt-oss-20b` | Existing adapter and local execution gate retained | Conditional GO for operator dry-run; new smoke approval and current daily budget required |
| Gemini `gemini-3.1-flash-lite` | Existing adapter and local execution gate retained | Conditional GO for operator dry-run; new smoke approval and current daily budget required |
| Groq `qwen/qwen3.8-27b` | Same adapter; model-specific `reasoning_format: hidden`; evidence allowlist released after account confirmation | Smoke verified; batch needs fresh quota and its own approval |
| OpenRouter `deepseek/deepseek-v4-flash-0731:free` | Exact selection registered; existing adapter retained | **NO-GO**: public endpoint list empty; R135/account-routing evidence unresolved; all independent gates remain closed |

Groq Qwen operator account evidence: 30 RPM, 1,000 RPD, 8,000 TPM, 200,000 TPD. The operator
confirmed **Billing Free**, checked "now", for the same organization after supplying those
model limits. Confirmation receipt recorded at **2026-11-12T16:19:56Z**, not an invented screen
capture time. Operator also confirmed only LLM Insight will use these accounts during batches.
The common Groq evidence window was refreshed to that receipt time; expires after 14 days.
GPT-OSS quotas previously confirmed at the same limits are retained. Current consumed quota,
reset time and any split input/output limits still require a fresh pre-batch account check.

Official sources checked in this session:

- [Groq Qwen model](https://console.groq.com/docs/model/qwen/qwen3.8-27b): exact ID exists,
  text output, preview status. [API reference](https://console.groq.com/docs/api-reference)
  documents `reasoning_format: hidden`; mutually exclusive with `include_reasoning`.
  GPT-OSS retains its original `include_reasoning: false` body without paid service selection.
- [Groq deprecations](https://console.groq.com/docs/deprecations): Qwen 3.6 replacement is
  Qwen 3.8; no silent substitution performed.
- [Groq limits](https://console.groq.com/docs/rate-limits): organization-wide quotas; account
  Limits page is authoritative. Public tab rendering does not establish this account's plan.
  Split ITPM/OTPM may apply; inspect the TPM breakdown. [Billing FAQ](https://console.groq.com/docs/billing-faqs)
  documents paid upgrade requiring a payment method. Operator confirmed the current Free plan.
- Gemini evidence remains [phase-8f.md](phase-8f.md), checked 2026-11-12T13:06:58Z, Free,
  15 RPM / 250,000 TPM / 500 RPD. No TPD invented. Expiry 2026-11-26T13:06:58Z.
- [OpenRouter model](https://openrouter.ai/deepseek/deepseek-v4-flash-0731%3Afree) lists the
  exact free model. Its [public endpoint metadata](https://openrouter.ai/api/v1/models/deepseek/deepseek-v4-flash-0731:free/endpoints)
  returned `endpoints: []` during this review. This is public catalog retrieval, not inference.
  [Routing documentation](https://openrouter.ai/docs/guides/routing/provider-selection)
  describes maximum-price filtering and `data_collection: deny`. No available endpoint was
  established that meets **all** existing restrictions. R135 remains open; no routing gate was
  released. [Rate limits](https://openrouter.ai/docs/api_reference/limits) are account-wide;
  batch policy conservatively caps this no-purchased-credit case at 20 RPM / 50 requests per
  day and charges failed/uncertain attempts against its budget. Operator says no credit card
  was entered; this is not treated as proof of endpoint availability or a complete account audit.

### Batch semantics

`demo_app/live_batch.py` wraps `demo_app.live_compare`; no additional provider client or SDK.
The three scenario texts/hashes/version identities are unchanged. All SDK caps remain one
request, one concurrency, zero retries, 1,024 input bound and 1,024 output ceiling.

- A plan binds model, provider, scenario/prompt hash/version, Project 1, OTLP endpoint,
  exactly 20 planned trials, source fingerprint, quota document hash and successful smoke-result
  hash to a unique batch ID and SHA-256. Plans are created only after an eligible dry-run and
  read-API verified recent smoke. A plan makes no inference request.
- `execute --approve <spec-sha256>` is one explicit approval of that immutable batch. Changed
  code/spec/quota/smoke needs a new plan and approval. Approval is permanently consumed before
  the first child. CLI defaults never execute. Never programmatically chain plan to execute
  without the operator reviewing and approving that exact displayed specification.
- Each trial starts a new dry-run subprocess, checks policy/backend/SDK/ingest readiness, then
  starts a new single-request subprocess. Both use the same private environment snapshot.
  The existing guard rechecks policy at its send boundary. Batch scope supplies that child's
  single-call consent; it cannot authorize another model or scenario.
- A fixed workspace-wide exclusive lock prevents two batch managers running simultaneously.
  An exclusive permanent claim prevents reusing approval. Reservations are flushed/fsynced
  **before** launching a send. Metadata results are flushed/fsynced immediately after return.
  Read-API trace verification is recorded separately; duplicate trace IDs stop execution.
- Missing/failed exports, provider/permission/rate/quota/transport failures, uncertain sends,
  failed local validation, source/policy changes and backend verification failures all stop.
  Even provably-unsent failures are not retried. Child timeouts/interruptions kill and reap the
  child. A hard parent termination leaves the global lock; a surviving child must be investigated.
- Completion/stop clears active authorization. Crash recovery is **inspection only**; a
  previously started batch cannot resume, regardless of how many trials finished. No automatic
  second batch. No credential, prompt, completion or raw child error output is journaled.

The batch lock coordinates this CLI only. Standalone runners, Playground, other machines and
other apps must remain idle under the operator's exclusive-account attestation. If shared
usage cannot be stopped, **batch NO-GO**: there is no invented cross-provider quota polling API.

### Quota contract

[batch-quota-template.json](batch-quota-template.json) is intentionally invalid until completed.
It contains no secrets. Use the exact model and account/project/key scopes that will execute.
For each dimension, use the most restrictive applicable limit across all shared scopes.

- `checked_at`, `valid_until`, `daily_reset_at`: explicit ISO timestamps with UTC offset.
  A snapshot is at most five minutes old when planning/starting. Validity is at most one hour,
  ending no later than the earliest applicable quota reset. Execution refuses if a child's
  90-second supervisory deadline could cross validity. Unknown reset => no batch.
- All five booleans must reflect actual operator checks. `project_1_ingest` confirms the ingest
  key belongs to existing Project 1; the empty OTLP readiness probe alone cannot discover its
  project. The backend read token is checked by reading the Project 1 smoke trace.
- `limits`: RPM, RPD, TPM required; Groq TPD required. `null` for optional TPD/ITPM/OTPM means
  explicitly checked **not applicable**, not "unknown". If unsure, do not approve the snapshot.
- `daily_used`: all account/shared-scope requests (including failed/uncertain ones) and a
  conservative token count. For several scopes, use the largest consumed count and smallest
  limit. Full batch headroom of 20 requests and 40,960 bounded tokens is reserved up front.
  Unknown usage cannot be entered as zero. Existing local journal activity must predate the
  snapshot; stale snapshots cannot carry unused allowance into a second batch.
- Rate pacing is deliberately conservative: one request followed by **61 seconds after its
  completion**; the initial minute window is drained. Monotonic time enforces pacing. Full
  input+output ceilings, including reasoning, are reserved, without refunds for failures.
  Each send re-reads and validates the immutable quota document and expiry; editing/deleting
  it revokes the run. Plan approximately 21-31 minutes per batch, not a throughput benchmark.

### Commands (repository root, operator PowerShell)

Credentials remain in the operator process only. Retain the already documented provider,
Free-plan attestation, Project 1 ingest and OTLP settings. Add a read-only backend API token
to `LLMI_BATCH_READ_TOKEN` privately, without editing `.env` or displaying the value:

```powershell
$env:LLMI_BATCH_READ_TOKEN = [System.Net.NetworkCredential]::new('', (Read-Host 'Backend read token' -AsSecureString)).Password
```

Initial single-request dry-run, with selected model and matching provider environment:

```powershell
$env:LLMI_LIVE_MODELS = 'qwen/qwen3.8-27b'
$env:LLMI_LIVE_PROVIDERS = 'groq'
sdk/.venv/Scripts/python -m demo_app.live_compare --provider groq --scenario cmp-ticket-triage --dashboard-url http://127.0.0.1:3001
```

Groq attestation remains `LLMI_GROQ_MODE=free-plan`, `LLMI_GROQ_PLAN=free`, checked-on UTC date;
Gemini retains `LLMI_GEMINI_MODE=free-tier`, Billing Tier free and its checked-on UTC date.
`LLMI_PROVIDER_MODE=real`, exact existing single-call consent, single request/concurrency,
zero retries and token ceilings remain required. Do not inherit a timeout setting from a
different provider: Groq has its fixed audited deadline; Gemini permits its existing range.

After the **separately approved and verified smoke**, prepare one batch (example Gemini):

```powershell
sdk/.venv/Scripts/python -m demo_app.live_batch plan --model gemini-3.1-flash-lite --scenario cmp-ticket-triage --quota docs/reports/operator-batch-quota.json --smoke-result docs/reports/phase2-gemini-smoke-result.json
```

Review the displayed specification. Only after explicit batch approval, substitute the actual
ID/hash displayed by that plan; these placeholders are not runnable authorization:

```powershell
sdk/.venv/Scripts/python -m demo_app.live_batch execute --batch <BATCH_ID> --approve <SPEC_SHA256> --quota docs/reports/operator-batch-quota.json --smoke-result docs/reports/phase2-gemini-smoke-result.json
sdk/.venv/Scripts/python -m demo_app.live_batch inspect --batch <BATCH_ID>
```

State lives under `docs/reports/batches/<id>/`; never delete it or overwrite earlier results.
If stopped after reserving a trial, `execution.lock` deliberately remains. Inspect the journal,
child process state and backend/provider evidence before any further work. After investigation,
the operator may remove **only** that stale lock, retaining the permanent claim and journal;
a fresh quota snapshot, new batch and new approval are still required. This is never an automatic
retry or resume. `possibly_sent` is not evidence that a request failed to arrive.

### Offline validation

Final focused selection: **657 test cases verified** (656 passed in the final combined run;
one stale single-model allowlist assertion was updated for Qwen and its targeted rerun passed).
The batch module's **44 tests** also passed independently. This includes the actual child
process dry-run protocol and fake-provider Qwen adapter/guard/runner/OTLP integration, not
developer-database integration. The unchanged Groq adapter suite passed in the earlier run.

Validation history: an initial 930-case run yielded 914 passes and 16 SDK-checkout refusals
after Qwen evidence changed while the previously installed SDK was being tested. Reinstalled
the final working tree; both Groq and Gemini SDK checkout checks are now **true**. The final
selection rechecked those affected paths. No gate was bypassed to make a test pass.
Ruff lint/format, strict mypy on the four changed runtime modules, `pip check`, `git diff --check`
and documentation verification (**800 PASS**) passed. GitHub CI/release checks were not run.

Tests use temp files, fake clocks, scripted
provider transports and the existing disposable OTLP stub; they never use developer PostgreSQL
or real provider credentials. The one real subprocess test has no credentials or config and
must refuse before backend/provider I/O. No changes to frontend or dependencies are required.

## Phase 2: operator smoke and first batch

Phase 1 offline checks passed. For each remaining eligible model, perform a fresh dry-run, show
eligibility/backend/SDK/model/Project 1/refusals, and ask for explicit approval of **one new smoke
request**. Save its safe output in a new exclusive file; never overwrite any
of the five historical trace results. Verify stored trace and dashboard before planning a batch.
An API key's presence/shape in dry-run is not proof of provider acceptance; only the approved
smoke can establish that. No paid route or credential-validation inference is performed silently.

First operator task: [Qwen preflight instructions](phase2-operator-preflight.md). The operator
supplied a timestamped dry-run result at 2026-11-12T16:37:38Z. It passed: model, provider,
scenario/prompt identity, endpoint, Project 1, Free attestation, backend, SDK match and safe
single-call limits; no refusals and `not_sent`.
At 2026-11-12T16:25:56Z the [tool-process dry-runs](phase1-local-readiness.json) showed correct
SDK matches for all four selections, and backend `/health/ready` returned 200. Groq GPT-OSS,
Gemini and Qwen refused **only** missing provider key / unavailable ingest configuration in
this tool process. Thus no successful credential-bearing preflight is claimed. OpenRouter also
reports its independent execution/evidence/routing/account refusals. All request states are
`not_sent`; zero inference requests at that earlier check. Operator keys are not inherited by
tool processes.
The [dashboard baseline](phase1-dashboard-baseline.json) returned HTTP 200 for Overview,
Traces and Models Compare at 16:28 UTC; this is availability evidence, not a new trace validation
or a visual browser test.

### Approved Qwen smoke (2026-11-12)

The operator explicitly approved one Groq Qwen request. Runner exit **0**, one physical attempt,
technical outcome completed, deterministic validation passed, OTLP export confirmed (two spans,
zero export failures). Trace: `d19e7e13d1e8a09411045bc87cfb8610`. Groq reported 171 input and 28
output tokens; reasoning usage unreported. Provider latency 469 ms; cost **unknown**. No retry.
The independent Project 1 read API checks all passed: real asserted provenance, correct model and
prompt version, no error, valid root/client parentage and usage link, token match, one attempt,
zero dropped attributes and complete (not truncated) payload. Artifact:
[smoke verification](phase2-qwen-smoke-verification.json).

Overview, Traces, exact trace detail and Models Compare all returned HTTP 200. Qwen is visible
in each; the trace ID appears in Traces/detail, and detail/Compare show 171/28 tokens and unknown
cost. Checks used server-rendered visible page text, not browser interactions or visual layout.
This smoke satisfies the prerequisite for **planning** a Qwen batch, not its approval.

Before planning that 20-request batch, obtain a fresh account quota snapshot. Groq account use
must still be exclusive to this runner. The snapshot needs current requests and tokens already
used today across the organization, exact UTC daily reset time, and whether separate ITPM/OTPM
limits appear on the Limits page. Include this smoke (one request, 199 reported tokens) in daily
usage. Do not treat unknown usage as zero. The immutable batch plan will be shown first; then ask
for a separate approval bound to that exact 20-request plan.

The operator checked Organization Usage and confirmed the page shows **limits only**: 30 RPM,
1,000 RPD, 8,000 TPM, 200,000 TPD; no separate ITPM/OTPM display. It does not show current
daily request/token use or reset time. Therefore Qwen's 20-call batch is currently **NO-GO**
under fail-closed quota checks. The Qwen smoke remains valid. Do not substitute zero consumption
or infer a reset time. The blocking safe artifact is
[qwen-batch-quota-template.json](qwen-batch-quota-template.json); it intentionally leaves usage
and reset unknown.

Continue other smoke preflights separately; they do not authorize batches and still need one
explicit approval each before execution. See
[Groq GPT-OSS dry-run instructions](phase2-gpt-oss-preflight.md).

GPT-OSS operator dry-run also passed at 2026-11-12T16:50:59Z: correct model/scenario/Project 1,
backend ready, installed SDK matches, zero refusals, 685 input bound, and `not_sent`.
Safe output: `phase2-gpt-oss-dry-run-20261112-165059.json`. Its single-call execution still
requires separate operator approval; batch remains NO-GO while daily consumption/reset are
unavailable.

The operator then explicitly approved one GPT-OSS smoke. Runner exit **0**, one physical
attempt, completed, deterministic validation passed, export confirmed (two spans, zero failures).
Trace `b7ee3ed3e2b75fa9cac3d0b792bd1185`: 222 input / 283 output tokens, including 248 reasoning
tokens, provider latency 1,062 ms, cost **unknown**. The independent Project 1 API checks passed:
real asserted provenance, expected prompt/model, two spans with correct parent and usage links,
matching tokens, one attempt, no errors/drops/truncation, and unknown cost. Details:
[GPT-OSS smoke verification](phase2-gpt-oss-smoke-verification.json).

The live Models Compare DOM check later confirmed the GPT-OSS ticket group visibly rendered
`444 / 379` input/output tokens and `308` reasoning tokens through its `group-tokens` component;
the earlier server-rendered-text check was incomplete. The compare API reports median CLIENT
latency 547.664 ms, two successful real attempts, and unknown cost. See
[DOM check](phase2-gpt-oss-models-dom-check.json).

On 2026-11-12, the operator reported that `/openai/v1/models` returned HTTP 200 with both
`openai/gpt-oss-20b` and `qwen/qwen3.8-27b`, and explicitly attested sufficient Groq quota for the
requested run. This supersedes the earlier batch NO-GO based on unavailable numeric daily usage.
No daily used count or reset time is inferred: the batch plan records this as
`operator_attested_sufficient_headroom` with `daily_used` and `daily_reset_at` left null. Groq
response rate-limit headers are reduced to numeric metadata and journaled; a signal exceeding
the known Free limits or showing less than one next-call token bound stops the batch.
The batch runner is `demo_app.live_batch` (`plan`, `execute`, `inspect`): one immutable plan,
one-use approval digest, global exclusive lock, fsynced per-trial reservations/results, sequential
one-call children, zero retry, and no resume after uncertain sends. It is not started until the
plan passes fresh local dry-run and the operator approves this exact 20-call GPT-OSS plan.

The operator then invoked that approved GPT-OSS batch. The manager reserved trial 1, sent exactly
one request, recorded a completed real response and confirmed the corresponding Project 1 read API
trace `6c60ad46f690e5a9d4f787308303c3fc` (222 input, 139 output including 101 reasoning tokens;
813 ms; cost unknown). It stopped before trial 2 because the initial signal guard incorrectly
treated Groq's `x-ratelimit-limit-requests=1000` RPD header as exceeding the 30 RPM account cap.
Groq documents this header as RPD; the response also reported 999 remaining requests, an 8,000 TPM
limit and 7,611 remaining tokens. The adapter/manager mapping is corrected and offline-tested.
The original approval remains consumed and its global lock retained; this batch was not resumed and
no retry was sent. A narrow `recover-plan` / `recover-execute` path was added for this exact known
state: it refuses corrupt/changed journals, process uncertainty, mismatched locks, missing rate
signals, or stale quota; it preserves completed trials, requires a fresh dry-run and a new one-use
approval for each continuation, and schedules only the uncompleted suffix. The first recovery
execution completed and verified trials 2–4 (traces `1a2bef9a253e0f56d4b56d8b012db9eb`,
`0d0bc9a8d04f9c51d5fbf2ec5833347d`, `2efac818b737edfc226ac777d810ef99`) before stopping with a
generic interruption/internal marker while trial 5 remained unreserved. A later invocation refused
on the then-unhandled journal continuation state and sent no request. Current integrity inspection
confirms a contiguous, fully verified prefix 1–4, the matching retained global/recovery locks, no
active SDK batch process, and 16 pending trials. The next recovery dry-run and a new approval for
trials 5–20 are pending; neither recovery authorization is reusable.

Then select **one** eligible model/scenario, obtain a fresh quota snapshot and prepare/approve
its 20-request batch. Never ask 20 times. All remaining 11 model/scenario batches remain separately
scheduled and require separate approvals. OpenRouter's three batches remain blocked; its 60
scenario requests plus smoke cannot fit a 50/day free account in one day even if R135 is resolved.

Dashboard evidence must use Project 1 real-only filters. Journal/backend trace IDs separate
technical status, deterministic validation and export success. Compare CLIENT latency, input/
output/reasoning token subsets, model and prompt version; cost stays **unknown**, never $0.
Synthetic experiment fixtures remain presentation features, not live-provider outcomes.
Existing observation now includes 7 real traces (6 completed responses, 1 Gemini timeout); see
[live-comparison-progress.md](live-comparison-progress.md). No 240-request success is claimed.

After the first approved batch, report verified per-trial results, Overview/Traces/Models Compare,
remaining schedule and unresolved blockers. Re-lock local Groq/Gemini execution at validation
closure and reinstall/retest SDK. Git commit/push/PR/merge, GitHub CI and production release
verification remain explicitly **deferred**; this is operator-authorized local development only.
