# Phase 7A — Gemini content demo: architecture and source audit

**Date:** 2026-11-09 UTC. **Scope:** design only. No application code, dependency, environment file, Docker resource, database or service was changed. **Provider requests in this phase: 0.** No commit and no push.

Everything proposed here (module names, commands, schemas, ADR-39, risk numbers) is a **design proposal, not working software**. None of the commands below exists yet.

---

## 1. Verified source and Git baseline

| Item | Value | How verified |
|---|---|---|
| Repository root | `C:/Users/PC/LLM-Insight-Development` | `git rev-parse --show-toplevel` |
| Local branch | `feature/phase-7-gemini-demo` (new, no commits of its own) | `git status --short --branch` |
| HEAD | `435030648240ee54af78622a0d4d30737a84ba0b` | `git rev-parse HEAD` |
| GitHub `main` | same SHA | `git ls-remote origin refs/heads/main` (2026-11-09) |
| Main CI | Run 42524191827, `push`, attempt 1, completed, **success**, head equals the SHA above, 7 of 7 jobs success: `docs`, `python`, `frontend`, `security-python`, `security-history`, `e2e`, `image-build` | Read through the public GitHub API in this phase. Raw job logs were not read |
| Worktrees | One (this folder) | `git worktree list` |

### User-owned untracked files

Not modified, staged, renamed or reformatted. SHA-256 prefixes were recorded before 7A and re-checked after this report was written; the result is in §11.4.

| File | SHA-256 prefix |
|---|---|
| `LLM_INSIGHT_HANDOFF.md` | `8AE86D5131347FFE` |
| `docs/PHASE7_LIVE_DEMO_PLAN.md` | `B8E8A61C29FCF705` |
| `docs/OPUS_PHASE7_PROMPTS.md` | `31FFD28B6FDD011F` |

### Sources read

`CLAUDE.md`, `frontend/AGENTS.md` (the only `AGENTS.md`; Next.js rules, not relevant to a CLI runner), both Phase 7 planning documents, `docs/ADR.md` (ADR-19, ADR-21, ADR-33…38, supersession rule), `docs/PHASE_STATUS.md`, `docs/PHASE6_LIVE_INTEGRATION_PLAN.md` Part III, `docs/TELEMETRY_CONTRACT.md` §3–4, `docs/SECURITY.md` §6, `docs/ARCHITECTURE.md` §5.1, `docs/reports/r104.md` (R102 and R104-6), `docs/reports/phase-6c-b.md`, `demo_app/live_gemini.py`, `demo_app/live_common.py`, `sdk/src/llm_insight_sdk/{gemini_adapter,gemini_live,live,providers,config,instrumentation,telemetry,_guards}.py`, `sdk/tests/gemini_fixtures.py`, `sdk/tests/unit/test_gemini_runner.py`, `sdk/tests/integration/test_live_gemini.py`, `.env.example`, `.github/workflows/ci.yml`, `security/bandit-baseline.json`.

### Stale statements found (recorded, not edited)

| Where | Says | Current fact | Action |
|---|---|---|---|
| `docs/PHASE7_LIVE_DEMO_PLAN.md` snapshot | Branch `feature/r104-ci-coverage`, `ac5c868`, R104 in progress | R104 merged; `main` is `4350306` | Proposed note below; user's file untouched |
| `docs/PHASE7_LIVE_DEMO_PLAN.md` §3 | The user may add new synthetic examples | The 7A brief requires a fixed registry; the brief wins. A new example is a reviewed code change with a version bump | Recorded here |
| `docs/PHASE_STATUS.md`, `docs/reports/r104.md` | "R104 stays open until PR #2 is merged" | Merged, main CI green | **Separate R104 closure task; deliberately not edited in 7A** |
| `CLAUDE.md` | "One provider adapter: OpenAI"; ADR-13…21 listed as the accepted set | ADR-33 amends ADR-05 for Phase 6; ADR-33…38 accepted at design level on 2026-11-01 (`PHASE_STATUS.md` line 315) | Interpret through later ADRs (§7); not edited |

**Proposed update to the user's plan file (not applied).** In `docs/PHASE7_LIVE_DEMO_PLAN.md` §7, tick "7A kaynak audit'i ve tasarım tamamlandı" once this report is reviewed, and add a dated note that the §1 Git snapshot predates the R104 merge. `LLM_INSIGHT_HANDOFF.md` needs no update from this phase.

---

## 2. Current implementation inventory

Status words: **EXISTS**, **MISSING**, **BROKEN**, **UNKNOWN**. Nothing was executed against a provider, backend or database, so section A means "the source says so", not "it ran today".

### A. Current source facts (read in this checkout at `4350306`)

| Item | Status | Detail |
|---|---|---|
| Smoke runner `demo_app/live_gemini.py` | EXISTS | Fixed `INPUT_TEXT`, dry run by default, one request with `--execute`, exit 0/1/2, root span `demo.live-gemini`, prompt `live-smoke` / `v1`, output text never printed |
| Provider-neutral helpers `demo_app/live_common.py` | EXISTS | `check_backend`, `dashboard_links`, `emit` |
| Content runner, scenario registry, preview | MISSING | To be built in 7B |
| `GeminiTextAdapter`, `ObservedGeminiClient` | EXISTS | One physical `generateContent`, `automatic_continuation=False`, AFC disabled, only `max_output_tokens`, `temperature`, `top_p` allowed |
| Response object for local use | EXISTS | `TextOutcome.provider_response`, excluded from `repr` and equality; absent on `status="error"` |
| Thought and text accessor | MISSING | The adapter offers no output-text helper. A content runner needs its own extraction (§6.3) |
| Endpoint boundary (ADR-34) | EXISTS | `live_gemini_client_problems`, applied after reservation, at instrumented-client build and immediately before send |
| Audited library versions | EXISTS | `google-genai 2.30.1`, `httpx 0.28.1`, `httpcore 1.0.9`; the same versions are pinned in `sdk/requirements-dev.lock` |
| Free Tier guard (ADR-35) | EXISTS | `evaluate_free_tier_policy`, `FreeTierSession` |
| Hard caps | EXISTS | 1 request, concurrency 1, 0 retries, timeout 1–30 s (default 20), output 16–1024 (thinking included), input bound ≤ 1024 |
| Input bound | EXISTS | UTF-8 bytes + 64 (`live.input_token_bound`). A safe upper bound, **not** a provider token count. Maximum composed input is therefore 960 bytes |
| Free Tier evidence | EXISTS | `FREE_TIER_VERIFIED_AT = 2026-10-28T13:00Z`, window 14 days. The guard refuses after **2026-11-11 13:00 UTC** |
| Model allowlist | EXISTS | `gemini-3.1-flash-lite` only |
| Cost semantics | EXISTS | `status: unknown`, `billed_amount_usd: null`, backend never prices `gcp.gemini` |
| OTLP export | EXISTS | `telemetry.py`: official `OTLPSpanExporter`, `BatchSpanProcessor`, process-local counters, bounded flush and shutdown |
| Allowlisted labels | EXISTS | `gen_ai.prompt.name` (≤ 128) and `gen_ai.prompt.version` (≤ 64) already pass; no backend change is needed for scenario identity |
| `llminsight.validation.*` attributes | EXISTS | In the allowlist, but not used by this design (§5) |
| Offline harness | EXISTS | `FakeGemini` with sentinels for output, thought and error text; `block_network`; integration fixture `live_backend` |
| `demo_app/` static checks in CI | EXISTS (partly) | Bandit and mypy cover `demo_app`; the Ruff commands of the `python` job run inside `sdk/`, so **Ruff does not cover `demo_app`** in CI. 7B must run it explicitly |
| Smoke `api_path` | **BROKEN (limited)** | `dashboard_links` returns `/api/v1/traces/{id}` without the required `project` parameter (`ARCHITECTURE.md` §5). Used as printed, it cannot pass validation. Source-derived, not run. Left unchanged; the new runner composes its own path |

Smaller smoke-runner limits that the new runner must not inherit:

| Limit | Consequence | New runner |
|---|---|---|
| Dry-run `execution_allowed` ignores backend readiness | Can read `true` while `--execute` would refuse | Includes backend readiness |
| Default `argparse` abbreviation | `--exec` works as `--execute` (gates still apply) | `allow_abbrev=False` |
| `force_flush()` counts only failures during the flush | A batch that failed earlier on the worker can give `flushed: true` with `spans_export_failed > 0` | Export outcome also requires the counters (§5.3) |
| Result of `telemetry.shutdown()` is ignored | Timeout at shutdown is invisible | Reported |
| Ctrl-C after the span starts | Attempt counted, process exits with no JSON | Emits a final JSON line with `possibly_sent` |

### B. Historical report claims (context, not re-verified)

| Claim | Source |
|---|---|
| One real Gemini Free Tier call, project `gemini-live` #148, trace `bec4f18a3f9a29375a61290dfc62798d`, 18 / 1 tokens, two spans, unknown cost | `reports/phase-6c-b.md`, `PHASE6_LIVE_INTEGRATION_PLAN.md` §14 |
| ADR-33…38 accepted at design level, 2026-11-01 | `PHASE_STATUS.md` line 315 |
| Earlier test totals (for example backend 1765, SDK 817) | Phase reports. Not treated as current |

### C. Runtime facts not verified

| Item | Status | Detail |
|---|---|---|
| `sdk/.venv`, `backend/.venv`, `frontend/node_modules` in this checkout | **MISSING** | Absent on 2026-11-09. No SDK test can run here until an environment is built |
| `backend/.env`, `frontend/.env.local` | **MISSING** | Absent. A root `.env` is present; its values were not read |
| Global Python has `google-genai` | MISSING | `PackageNotFoundError` |
| Backend on 8000, dashboard on 3001, PostgreSQL on 55432 | UNKNOWN | Not probed in 7A |
| Project #148 and its trace today | UNKNOWN | Not re-read |
| `google-genai` 2.30.1 runtime behaviour | UNKNOWN | Library not available here. The design does not depend on `response.text` (§6.3) |
| Existing test suites today | UNKNOWN | NOT RUN: no environment. Inherited CI evidence is run 42524191827 |

### D. Provider and account facts that cannot be inferred

| Item | Status |
|---|---|
| The project's AI Studio Billing Tier, linked billing account, active model quota | UNKNOWN; private operator check |
| Whether `GEMINI_API_KEY` exists and belongs to the intended project | UNKNOWN; not read |
| Whether `gemini-3.1-flash-lite` is still Stable and on the Free Tier today | UNKNOWN; last evidence 2026-10-28 |
| Thinking-token consumption for the proposed prompts | UNKNOWN; `thinking_config` is refused by ADR-33, so it cannot be switched off |
| Whether Google stores or uses a given request | Google's published policy only: Free Tier content may be used to improve products. This is why input must stay synthetic |

### Installed SDK versus checkout

`README.md` and `sdk/README.md` install the SDK **non-editable**: `pip install --no-deps ./sdk`. During a run, `demo_app` comes from the repository root and `llm_insight_sdk` from `site-packages`. Three consequences:

1. Any change under `sdk/src`, including a future evidence-date update, has **no effect** until the SDK is reinstalled.
2. A stale install can make a dry run report old guard facts (old `FREE_TIER_VERIFIED_AT`) next to new `demo_app` code.
3. Tests that pass against a stale install do not prove the checkout.

The design adds a fail-closed check (§4.3) and keeps `sdk/src` unchanged in 7B.

### Reading earlier one-provider statements

ADR-05 and `CLAUDE.md` name one provider, OpenAI. ADR-33 amends ADR-05 **"for the post-MVP Phase 6 only"**. Phase 7 is beyond that wording, so reusing the Gemini adapter in a demo needs an explicit statement, which proposed ADR-39 provides without adding any provider surface.

---

## 3. Three proposed scenarios

### 3.1 Rules for all scenarios

- Fixed registry in code (`MappingProxyType` of frozen dataclasses). No file is opened at runtime, so there is no path to control.
- One execution selects **one** scenario. No run-all, no loop, no list argument.
- The composed input is one Python string, passed as `TextRequest.input_text`. Fixture data never reaches a request field or generation setting.
- Composition is exactly: `PREAMBLE + "\n\n" + task + "\n\n" + label + ":\n" + fixture`.
- `PREAMBLE` is exactly: `SYNTHETIC LLM Insight demo data. Every name, number and event below is invented.`
- Any text change requires a version bump and a new pinned SHA-256 in tests.
- Fixtures contain no real person, email address, phone number, card-like digit run or key-shaped string, and must pass the repository's history secret gate.
- The request cap is the scenario's own value. `LLMI_LIVE_MAX_OUTPUT_TOKENS` and `LLMI_LIVE_MAX_INPUT_TOKENS` can only cause a refusal; they cannot change the request.
- Text is English so validation stays deterministic and the byte bound stays predictable (review item, §11.2).

Output caps leave headroom because thinking tokens count against them and cannot be disabled, while a truncated answer cannot be retried. The expected answers themselves need far fewer tokens than these caps.

Sizes and hashes below were **measured** from the proposed text in a scratch script outside the repository.

### 3.2 Scenario A: support-ticket classification

| Field | Value |
|---|---|
| Scenario ID / version | `support-ticket-classification` / `1` |
| Fixture ID | `ticket-001` |
| Prompt name / version | `demo.ticket-classify` / `v1` |
| Label | `TICKET` |
| Composed input | 601 bytes, bound 665, SHA-256 `18d3061e26e4b09759b469d674c0aba18a87a3610e83534ef679d758c2cbdf0d` |
| Registry size limit | 64–800 bytes |
| Output cap | 512 |
| Expected form | One JSON object |
| Demonstrates | Structured decision from short text, checked locally against a known label |

Task text:

```text
Classify the support ticket below. Reply with only one JSON object with exactly these keys: "category" (one of "billing", "login", "bug", "feature_request", "other"), "priority" (one of "low", "medium", "high"), "reason" (one sentence, at most 120 characters). Do not use Markdown.
```

Fixture:

```text
Ticket T-1001 from a fictional customer of the fictional Acme Notes app: I was charged twice for the same monthly plan on my last invoice. The app works fine, but I need the duplicate charge reversed before my next billing date.
```

Rules: `json_object`, `no_duplicate_keys`, `exact_keys`, `category_enum`, `priority_enum`, `reason_length` (1–120), `expected_category` (`billing`). Priority is not asserted, because more than one value is defensible.

### 3.3 Scenario B: JSON data interpretation

| Field | Value |
|---|---|
| Scenario ID / version | `json-data-interpretation` / `1` |
| Fixture ID | `weekly-usage-001` |
| Prompt name / version | `demo.json-interpret` / `v1` |
| Label | `DATA` |
| Composed input | 657 bytes, bound 721, SHA-256 `1e9f1ba31ae538115071dc95e5fd81aeef150685ccbc27112d157116105d4951` |
| Registry size limit | 64–860 bytes |
| Output cap | 768 |
| Expected form | One JSON object |
| Demonstrates | Reading structured data and deriving values that the runner recomputes itself |

Task text:

```text
Read the JSON data below. Reply with only one JSON object with exactly these keys: "total_requests" (integer), "highest_error_day" (the "day" value with the most errors), "error_rate_percent" (total errors divided by total requests, times 100, rounded to one decimal), "observation" (one sentence, at most 120 characters). Do not use Markdown.
```

Fixture (one line; it is prompt text, parsed only by the registry check):

```json
{"dataset":"SYNTHETIC-weekly-usage","days":[{"day":"Mon","errors":3,"requests":120},{"day":"Tue","errors":2,"requests":180},{"day":"Wed","errors":12,"requests":240},{"day":"Thu","errors":4,"requests":160}],"unit":"requests"}
```

Rules: `json_object`, `no_duplicate_keys`, `exact_keys`, `total_requests_integer` (a boolean is not an integer), `expected_total` (700), `expected_day` (`Wed`), `expected_rate` (3.0, tolerance ±0.05), `observation_length` (1–120). Expected values are computed from the fixture by the runner.

### 3.4 Scenario C: short text summarization

| Field | Value |
|---|---|
| Scenario ID / version | `short-text-summary` / `1` |
| Fixture ID | `release-note-001` |
| Prompt name / version | `demo.short-summary` / `v1` |
| Label | `RELEASE NOTE` |
| Composed input | 611 bytes, bound 675, SHA-256 `740179ed99445b9564589b5c07518f4cea890fbb98dfa4ed112a680728be61b5` |
| Registry size limit | 64–800 bytes |
| Output cap | 512 |
| Expected form | Plain text |
| Demonstrates | Free-form output checked only by deterministic structure |

Task text:

```text
Summarize the release note below in at most two sentences and at most 280 characters, in plain text without Markdown. Mention the known issue.
```

Fixture:

```text
Release note for the fictional Acme Notes app, version 4.2. Search is now three times faster on large notebooks. Offline edits sync automatically when the connection returns. The old desktop theme is removed; users keep their colour settings. A known issue remains: exporting a notebook with more than 500 pages can time out, and the team plans a fix in the next release.
```

Rules: `non_empty`, `max_chars` (280), `max_two_sentences` (a terminator counts only before whitespace or end of text, so `4.2` does not), `no_markdown`, `mentions_known_issue` (case-insensitive `export`).

### 3.5 Local validation

- Runs only when the provider status is `completed`. `incomplete` gives `not_evaluated`, reason `provider_incomplete`. Every other status gives `not_evaluated`.
- Text over 4096 characters is refused before parsing.
- JSON parsing rejects duplicate keys and the non-standard constants `NaN` and `Infinity`.
- One surrounding Markdown code fence (optionally tagged `json`) may be removed, reported as `fence_stripped: true`, because the adapter refuses API structured output (ADR-33). Anything else around the object is a failure.
- The result holds rule codes and booleans only, never a value from the answer.
- A failed validation **never** triggers another request and never changes the provider status.
- Passing is not proof of quality: it shows the deterministic rules held for one answer. There is no LLM judge.

---

## 4. CLI behavior

### 4.1 Commands (proposed, not implemented)

```text
sdk/.venv/Scripts/python -m demo_app.live_gemini_content --scenario <id>                     # dry run
sdk/.venv/Scripts/python -m demo_app.live_gemini_content --scenario <id> --execute           # one request, if allowed
sdk/.venv/Scripts/python -m demo_app.live_gemini_content --scenario <id> --execute --preview # plus local preview
```

| Option | Meaning |
|---|---|
| `--scenario ID` | Required. Registry lookup only. An unknown value is refused and not echoed |
| `--execute` | Sends at most one request |
| `--preview` | Opt-in local terminal preview |
| `--model` | Must be in `LLMI_LIVE_MODELS` (same as the smoke runner) |
| `--dashboard-url` | Used for the printed link only. Must be a loopback HTTP origin |

There is deliberately no `--input-file`, `--prompt`, `--max-output-tokens`, `--temperature`, `--all`, `--retry` or `--fallback-model`. Abbreviated options are off. Argument errors produce a JSON refusal with exit 2 and do not echo the value.

`python -m demo_app.live_gemini` keeps its command, fixed input and output contract.

### 4.2 Stdout contract

Stdout is exactly one JSON line, schema `llminsight.demo.gemini_content.v1`. It never contains input text, output text, thoughts, safety ratings, headers, keys or provider error messages.

| Group | Fields |
|---|---|
| Header | `schema`, `mode`, `provider_request_sent`, `provider_request_state` (`not_sent` / `sent` / `possibly_sent`), `execution_allowed`, `refusals[{code,message}]` |
| Scenario | `id`, `version`, `fixture_id`, `prompt_name`, `prompt_version`, `input_sha256`, `input_utf8_bytes`, `input_token_bound`, `max_output_tokens` |
| Plan | The smoke plan, with the scenario's cap and bound |
| Environment | `backend{ready,reason}`, `sdk_checkout_match` (`true` / `false` / `unknown`) |
| Provider | `outcome`, `incomplete_reason`, `provider_reason`, `error_category`, `provider`, `request_model`, `response_model`, `attempts`, `duration_ms`, `provenance` |
| Usage | `tokens`, `provider_usage`, `usage_check`, `cost` (same block as the smoke runner) |
| Session | `requests_started`, `max_requests`, `physical_attempts`, `halted` |
| Validation | `status` (`passed` / `failed` / `not_evaluated`), `reason`, `rules[{rule,passed}]`, `fence_stripped` |
| Export | `outcome` (§5.3), `flushed`, `shutdown_ok`, `spans_ended`, `spans_exported`, `spans_export_failed` |
| Preview | `requested`, `shown`, `reason`, `input_chars`, `output_chars`, `truncated`, `thought_parts_excluded`, `non_text_parts` |
| Correlation | `trace_id`, `project_id`, `project_id_source` (`operator_env_unverified`), `dashboard_url`, `api_path` |

`input_sha256` identifies the local text. It does not prove what the provider received.

### 4.3 Behavior table

Exit codes: **0** dry run printed, or completed with validation passed and export confirmed. **1** provider failure, block, or interrupted. **2** refused, nothing sent. **3** (new, this runner only) the provider returned `completed` or `incomplete`, but validation did not pass or export is not confirmed; the JSON says which. Codes 0/1/2 keep their ADR-36 meaning for the smoke runner.

| # | Case | Request state | Provider outcome | Validation | Exit |
|---|---|---|---|---|---|
| 1 | Dry run, all gates pass | `not_sent` | absent | absent | 0 |
| 2 | Dry run with policy refusals | `not_sent` | absent | absent | 0, `execution_allowed: false` |
| 3 | Unknown scenario or invalid arguments | `not_sent` | absent | absent | 2 |
| 4 | Registry entry fails its own check | `not_sent` | absent | absent | 2 |
| 5 | `--execute`, any policy refusal, including `free_tier_evidence_stale` | `not_sent` | absent | absent | 2 |
| 6 | `--execute`, backend not ready | `not_sent` | absent | absent | 2 |
| 7 | `--execute`, `sdk_checkout_match` is `false` | `not_sent` | absent | absent | 2 |
| 8 | `--execute --preview`, stderr not a terminal, or `CI` / `GITHUB_ACTIONS` set | `not_sent` | absent | absent | 2 |
| 9 | Input bound or cap above policy (`input_too_large`, `output_limit_exceeded`) | `not_sent` | absent | absent | 2 |
| 10 | Client construction fails or endpoint check refuses | `not_sent` | absent | absent | 2 |
| 11 | Re-check immediately before send refuses | `not_sent` | absent | absent | 2 |
| 12 | `STOP`, rules pass, export confirmed | `sent` | `completed` | `passed` | 0 |
| 13 | `STOP`, a rule fails | `sent` | `completed` | `failed` | 3 |
| 14 | `STOP`, export not confirmed | `sent` | `completed` | as evaluated | 3 |
| 15 | `MAX_TOKENS` / `CONTINUATION` | `sent` | `incomplete` | `not_evaluated` | 3 |
| 16 | Blocked prompt, or safety-type finish | `sent` | `blocked` | `not_evaluated` | 1 |
| 17 | No candidate, other or missing finish reason | `sent` | `failed` | `not_evaluated` | 1 |
| 18 | Unreadable body | `sent` | `malformed` or `error` | `not_evaluated` | 1 |
| 19 | 429 | `sent` | `error`, `rate_limited` | `not_evaluated` | 1 |
| 20 | Timeout (the request may have reached the provider) | `sent` | `error`, `timeout` | `not_evaluated` | 1 |
| 21 | Connection or other HTTP error | `sent` | `error`, by category | `not_evaluated` | 1 |
| 22 | Usage missing or inconsistent, otherwise as 12 | `sent` | `completed` | as evaluated | 0, `tokens: null`, `usage_check` says why |
| 23 | Ctrl-C during the call | `possibly_sent` | `interrupted` | `not_evaluated` | 1 |

In every sent case the attempt count is one and nothing is resent. A rerun is a new operator decision that needs its own authorization.

`sdk_checkout_match` compares SHA-256 of the installed `gemini_live`, `gemini_adapter`, `live`, `providers`, `instrumentation` and `telemetry` modules with `sdk/src/llm_insight_sdk/`. If the source tree is absent the value is `unknown`, which does not refuse.

### 4.4 Preview

| Property | Design |
|---|---|
| Activation | `--preview` only. Off by default. In a dry run it shows the synthetic input only |
| Destination | Stderr. Stdout stays one JSON line. Refused before any send unless stderr is a real terminal and `CI` / `GITHUB_ACTIONS` are unset |
| Output shown for | `completed`, and `incomplete` labelled `TRUNCATED`. Never for `blocked`, `failed`, `malformed` or `error` |
| Bounds | Input 1200 characters; output 2000 characters and 40 lines, then a truncation marker |
| Excluded in every mode | Thought parts, thought signatures, safety ratings, credentials, headers, raw provider errors, the serialized response |
| Escaping | C0 controls except newline, DEL, C1 controls, ESC sequences, carriage return, bidi and isolate controls, zero-width characters, and lone surrogates are shown as visible `\xNN` / `\uNNNN` text |
| Layout | Each line prefixed `preview| ` so it cannot pose as runner output |
| Persistence | None by this system. **Terminal scrollback, screen recording and capture tools can still keep it**; the runbook says so |

Output extraction reads `candidates[0].content.parts` explicitly, keeping only parts whose `thought` is not `True` and whose `text` is a string. It does not call `response.text`, whose behaviour for 2.30.1 could not be verified here. A function call, inline data or other part type means no output preview and a `non_text_parts` count. The same extraction feeds validation.

---

## 5. Observability and trace contract

### 5.1 Expected trace

```text
python -m demo_app.live_gemini_content
└─ demo.live-gemini-content                     INTERNAL, root, no gen_ai.* attributes
   └─ generate_content gemini-3.1-flash-lite    CLIENT, child, one physical request
        └─ OTLP/HTTP protobuf POST /v1/traces  → PostgreSQL → /api/v1/traces/{id} → dashboard
```

| Property | Source | Stored or shown as |
|---|---|---|
| Trace ID | Root span context, 32 hex | `trace_id`, unique per project |
| Parent and child | Current-span context; the CLIENT span is opened inside the root | API `spans[].role` and `children` |
| Project | Ingest key, resolved by the backend | `project_id` in storage |
| Provider | `gen_ai.provider.name = gcp.gemini` | `usage_records.provider` |
| Requested / response model | `gen_ai.request.model`, `gen_ai.response.model` (`modelVersion`) | Both kept, never merged |
| Scenario identity | `gen_ai.prompt.name`, `gen_ai.prompt.version` | JSONB, `traces.prompt_version` |
| Reported tokens | `gen_ai.usage.*` from the ADR-33 mapping | `usage_records` |
| Provider counts and check | Local stdout only | Not stored |
| Duration | Span times | Trace and span duration |
| Status | ERROR with `error.type` when the attempt fails | Trace `status` |
| Finish reason | `gen_ai.response.finish_reasons`, verbatim | JSONB |
| Provenance | `llminsight.provenance = real` from the adapter | "Real (asserted)", `provenance_verified: false` |
| Cost | Backend rule, no price for `gcp.gemini` | `unknown`, reason `price_missing`, never 0 |

No new attribute, column, migration, allowlist entry, route or dashboard control is required. Local validation results stay in stdout; they are computed after the CLIENT span has ended, and adding them to the trace would widen scope.

Usage is counted once: only the CLIENT span carries `gen_ai.*`, the root has none, and the backend's project-scoped canonical call key makes re-ingestion idempotent.

**Reading limit to state on stage.** A `MAX_TOKENS` or safety-type finish leaves the CLIENT span UNSET by design (ADR-33), so the dashboard can show `ok` while the CLI says `incomplete` or `blocked`. The finish reason is visible on the span. Dashboard `ok` does not mean the provider completed the answer.

### 5.2 Two outcomes, kept separate

| Question | Evidence | Can differ because |
|---|---|---|
| Did the provider answer? | `provider.outcome` | |
| Was telemetry accepted for delivery? | `export.outcome` | Backend outage, timeout |
| Is the trace stored in the right project? | Read API or dashboard, §5.4 | Wrong project ID, rejected data |

An export failure never changes the provider outcome and never triggers a resend. The OTLP exporter's own bounded retries target only the local backend, and ingestion is idempotent. They are not provider retries.

### 5.3 Export outcome

| Value | Condition |
|---|---|
| `confirmed` | `flushed` and `shutdown_ok` true, `spans_export_failed` = 0, `spans_exported` = `spans_ended` |
| `failed` | Any export failure and no span exported |
| `partial` | Some exported, some failed or still queued |
| `unknown` | Telemetry never started, or a flush or shutdown timeout with no failure counted |

`confirmed` means the local backend answered success for the export. It is not proof of storage; §5.4 is.

### 5.4 How the operator verifies the trace in the correct project

`LLMI_DASHBOARD_PROJECT_ID` is operator-supplied. The SDK cannot learn the project from the ingest key, so a wrong ID gives a not-found page rather than wrong data.

1. Take `trace_id`, `project_id` and `dashboard_url` from the stdout line.
2. Confirm the ID belongs to the expected project name through `GET /api/v1/projects`.
3. Read `GET /api/v1/traces/{trace_id}?project={project_id}`, using the admin credential already held by the operator and without printing it. Expect 200.
4. Check: two spans, root named `demo.live-gemini-content` with role `root`, CLIENT span as its child, one usage row, provider `gcp.gemini`, tokens equal to the stdout `tokens`, `cost_status` unknown, `provenance_verified: false`.
5. Open `dashboard_url`. On stage this is the preferred path because the credential stays server-side. Confirm the same trace ID, model, tokens and duration.
6. Optional negative check: the same trace with another project ID must answer 404.

The dashboard's lists default to a 24-hour window; a later showing of an older trace needs a date range that covers its event time (`reports/phase-6c-b.md`). Reads are not provider requests. There is no dashboard execution control.

---

## 6. Security and privacy boundaries

### 6.1 Invariants inherited unchanged

| Invariant | Enforced by |
|---|---|
| Endpoint `https://generativelanguage.googleapis.com/v1beta`, Developer API, no redirect, no environment proxy or CA | ADR-34, three checks before sending |
| Free Tier policy, consent phrase, operator attestation ≤ 1 day old | ADR-35, `evaluate_free_tier_policy` |
| One request per session, concurrency 1, zero retries | `FreeTierSession`, adapter |
| No tools, streaming, history, Vertex, custom endpoint, continuation, AFC | Adapter refusal and forced settings |
| No model fallback, no paid mode | Allowlist; no such code path |
| Audited library versions | `gemini_library_problems` |
| Prompt and response never in spans, PostgreSQL, read API or dashboard | ADR-19; SDK sets no content attribute; backend drops such attributes |
| Cost `unknown` / `null`, usage never invented | ADR-11, ADR-33, ADR-35 |
| `real` is asserted, not independent proof | `TELEMETRY_CONTRACT.md` |

### 6.2 Added by this design

| Control | Purpose |
|---|---|
| Single execution path (§8) | No second copy of the reservation, client check, settlement and flush sequence |
| Fixed registry, no file or prompt argument | No arbitrary content, no configuration from data |
| Request cap from the scenario | Environment can refuse, never alter |
| `sdk_checkout_match` | Stale install cannot pass silently |
| Terminal-only preview, escaping and extraction (§4.4) | Limits accidental capture and terminal control injection |
| Export outcome from counters plus flush and shutdown | No false delivery claim |
| No provider calls in tests or CI | Fake transport and `block_network`; real provenance refused with a mock transport |

### 6.3 Not claimed

- That the tier is free: this is an operator attestation (R90).
- That synthetic labelling is a technical guarantee: fixtures are fixed and reviewed, which is the actual control.
- That the preview leaves no trace on the operator's machine.
- That any answer is correct beyond the listed deterministic rules.
- Billing-grade delivery. Telemetry remains best effort.

Security baselines are unchanged: `security/bandit-baseline.json`, `security/secret-history-baseline.json`, `security/npm-audit-policy.json` and the seven CI gates.

---

## 7. Proposed ADR

ADR register audit: the highest entry is **ADR-38**. The next number is **ADR-39**. Re-check at 7B. The text below is **not** added to `docs/ADR.md` in 7A.

> ### ADR-39 — Scenario-based Gemini demo runner with optional local preview (extends ADR-36)
>
> - **Status:** PROPOSED (Phase 7A, 2026-11-09). Not implemented. Takes effect only after review.
> - **Context:** ADR-36 makes `python -m demo_app.live_gemini` the only entry point for a real Gemini call, with one fixed sentence and metadata-only output. A one-word smoke reply cannot show a meaningful demonstration. ADR-33 amends ADR-05 for "the post-MVP Phase 6 only".
> - **Decision:**
>   - `python -m demo_app.live_gemini_content` is a second local CLI entry point. Same dry-run default and `--execute` gate. One scenario from a fixed code registry per process, at most one request.
>   - The security sequence lives in one function of `demo_app/live_gemini.py`, used by both runners. The smoke runner keeps its input, output schema, root span and exit codes.
>   - Default output is metadata only. `--preview` may show the fixed synthetic input and the model's non-thought text on the local terminal's stderr, bounded and escaped. Preview output is never sent to telemetry, stored, or returned by any API.
>   - Local validation is deterministic and metadata-only, separate from provider status, and never causes another request. Exit code 3 is additive and applies to this runner only.
>   - Provider identity, adapter surface, Free Tier guard, caps, endpoint boundary and cost semantics are inherited unchanged from ADR-33, ADR-34 and ADR-35, and apply to this runner under Phase 7.
> - **Precise supersession:**
>   - **ADR-36, sentence 1:** "is the only entry point for a real Gemini call" is replaced by "is one of exactly two entry points; the other is ADR-39's". Nothing else in ADR-36 changes.
>   - **ADR-33, Status:** "for the post-MVP Phase 6 only" is extended to this Phase 7 demo runner, with no change to its scope, settings or mapping. ADR-05 remains amended no further.
>   - **Not superseded:** ADR-19 (no persisted content; a local terminal preview is not persistence and is not SDK content capture), ADR-21, ADR-34, ADR-35, ADR-37, ADR-38, ADR-30.
> - **Alternatives rejected:** adding `--scenario` to the smoke runner (blurs its fixed contract); copying the execution sequence (forks a security path); storing content for the dashboard (ADR-19); API structured output (refused by ADR-33; would widen the adapter); a dashboard execute button (ADR-36).
> - **Consequences:** `SECURITY.md` §6 no longer says there is one path. Operators must treat the preview as visible on their own machine.
> - **Affected files:** see Phase 7B file map. **Tests:** Phase 7B and 7C.

The historical text of ADR-36 is not rewritten. Under the register's rule, a new entry names what it supersedes.

---

## 8. Phase 7B file and test map

### 8.1 Execution path

`live_gemini.main` currently holds the reservation, client check, telemetry start, adapter call, settlement and flush inline. Three options were weighed:

| Option | Verdict |
|---|---|
| Behaviour-preserving extraction into `run_single_request(...)` in the same file; both runners call it | **Recommended**: one security path, existing tests guard it |
| Copy the sequence into the new runner | Rejected: a forked security path |
| Add `--scenario` to the smoke runner | Rejected: changes its fixed contract |

Proposed signature, keyword-only: `policy, config, model, input_text, max_output_tokens, prompt_name, prompt_version, root_span, environ, client_factory, provenance`. It returns the outcome, trace ID, settlement, refusals, flush and shutdown results, stats and session snapshot, and re-raises interrupts after settling.

### 8.2 Files

| File | Change |
|---|---|
| `demo_app/gemini_scenarios.py` | New: registry, composition, registry check, validators |
| `demo_app/content_preview.py` | New: text extraction, escaping, bounds |
| `demo_app/live_gemini_content.py` | New: CLI, JSON schema, exit codes |
| `demo_app/live_gemini.py` | `run_single_request` extraction only, no behaviour change |
| `sdk/tests/unit/test_gemini_scenarios.py` | New |
| `sdk/tests/unit/test_gemini_content_preview.py` | New |
| `sdk/tests/unit/test_gemini_content_runner.py` | New |
| `docs/ADR.md` | Append ADR-39 after review |
| `docs/SECURITY.md` §6 | "Only path" wording; preview boundary |
| `README.md`, `sdk/README.md` | Usage, clearly labelled |
| `docs/SOURCES_AND_RISKS.md` | Register proposed risks |
| `docs/PHASE_STATUS.md`, `docs/reports/phase-7b.md` | Status and evidence |

**Must stay unchanged:** `sdk/src/**`, `demo_app/live_common.py` (Bandit entry BL-02 records its line), `backend/**`, `frontend/**`, migrations, `.github/workflows/ci.yml`, `security/**`, lock files, `.env*`, Docker files, and existing test files. `demo_app/live_gemini.py` has no Bandit baseline entry. The new code should add no finding; the baseline is not refreshed.

### 8.3 Tests

| File | Must prove |
|---|---|
| Scenarios | Exactly three IDs; pinned SHA-256 for each; byte limits; registry immutable; fixtures carry the marker and no detector match; each validator rule passes and fails; duplicate keys, `NaN`, wrong types, boolean as integer, fence rule, oversized text; validators return no answer values |
| Preview | Thought parts excluded; non-text parts withhold output; each control class escaped; bounds and truncation; no `response.text`; `incomplete` labelled; `blocked` / `failed` / `error` show nothing |
| Runner | Dry run creates no client (`NeverCalled`); composed text is the only `contents`; request carries only `maxOutputTokens` from the scenario; environment cannot change it; each row of §4.3; exactly one physical request with spare scripted answers unrequested; timeout and 429 not resent; sentinels absent from stdout and stderr; preview off prints nothing; preview on prints only allowed text; non-terminal refusal; `CI` refusal; stale SDK refusal; `allow_abbrev`; export outcomes; argument errors do not echo values; two spans, one trace ID; source check that the module has no `genai.Client(`, `httpx.Client(`, loop or retry path |
| Regression | `test_gemini_runner.py`, `test_gemini_endpoint.py`, `test_gemini_live_guard.py`, `test_gemini_adapter.py`, unmodified |
| Mutation | Remove escaping, remove thought filter, retry on 429, bypass the checkout check, widen the registry: each must be caught |

Required gates: SDK pytest with no network; `ruff check` and `ruff format --check` from `sdk/` **and explicitly on `demo_app/`**; `mypy src` and `mypy --config-file pyproject.toml ../demo_app`; Bandit with the existing baseline; `python -I -B scripts/verify_phase0.py --quiet`. Test against the checkout by reinstalling the SDK, or show `sdk_checkout_match: true`.

Prerequisite: `sdk/.venv` does not exist in this checkout and must be built from `sdk/requirements-dev.lock`.

---

## 9. Phase 7C evidence matrix

All cases use the fake transport, synthetic provenance, the real OTLP/HTTP protobuf exporter, an isolated test backend and a test-owned PostgreSQL database. Project #148 and its trace are not touched. Proposed test file: `sdk/tests/integration/test_live_gemini_content.py`.

| # | Requirement | Case | Required evidence |
|---|---|---|---|
| 1 | Offline fake provider | All | `block_network` shows no external attempt; `len(fake.requests) == 1` |
| 2 | Real OTLP/HTTP protobuf | Success | Stored rows arrive through `POST /v1/traces` |
| 3 | Isolated backend | All | Run-owned process and database name ending in `_test` |
| 4 | PostgreSQL rows | Success | Two spans, parent link, one usage row, null cost |
| 5 | Read API | Success | `/api/v1/traces/{id}?project=` equals stdout: trace, models, tokens, prompt name, finish reason, `provenance_verified: false` |
| 6 | Idempotent re-ingestion | Same payload twice | Span and usage counts unchanged |
| 7 | Project isolation | Second project | Same trace under another project is 404 |
| 8 | Dashboard link | Success | Printed URL has the right trace and project; `api_path` includes `project` |
| 9 | Metadata privacy | All, preview off | Sentinels absent from stdout, stderr, logs, protobuf body, all stored rows, read API |
| 10 | Preview privacy | Preview on | Only allowed input and answer; thought, error and control sentinels absent or escaped |
| 11 | Missing usage | No metadata, partial, mismatch | `tokens: null`, `usage_check` correct, usage row null, `usage_source` unknown, never 0 |
| 12 | Timeout | Transport timeout | `error` / `timeout`, one request, no second attempt, exit 1 |
| 13 | 429 | `RESOURCE_EXHAUSTED` | `rate_limited`, one request, exit 1, error sentinel absent |
| 14 | Blocked | Prompt block; safety finish | `blocked`, exit 1, no output preview; span status per ADR-33 |
| 15 | Incomplete | `MAX_TOKENS` | `incomplete`, validation `not_evaluated`, exit 3, finish reason stored |
| 16 | Malformed | Non-JSON body | `error` or `malformed`, exit 1 |
| 17 | Local validation failure | Invalid JSON, wrong value, fenced JSON | `failed` or fence reported, one request, exit 3, provider remains `completed` |
| 18 | Export failure | Backend stopped after readiness; 5xx | Provider outcome intact, export outcome not `confirmed`, exit 3 |
| 19 | Smoke compatibility | Existing suites unchanged | `test_live_gemini.py` and unit runner tests pass unmodified |

Evidence rules: exact commands, counts and revisions; NOT RUN or BLOCKED stated plainly; no reuse of historical totals; synthetic provenance described as such. Claim-to-evidence table as required by `docs/OPUS_PHASE7_PROMPTS.md`.

Environment decision needed before 7C: see §11.2.

---

## 10. Phase 7D operator gate

### 10.1 Presentation outline (about 6 minutes, offline-capable)

| Time | Step | Label on screen |
|---|---|---|
| 0:00–0:45 | Problem, and what metadata-only means | |
| 0:45–1:30 | Dry run of one scenario: gates, plan, input hash | No request sent |
| 1:30–3:00 | One response, by the live path if authorized, otherwise clearly labelled prior evidence; preview if used | "Real (asserted)" or "SYNTHETIC / dated evidence" |
| 3:00–4:15 | Same trace in the dashboard: parent and child, models, tokens, duration, unknown cost | Cost unknown, not 0 |
| 4:15–5:15 | Offline failure example | SYNTHETIC |
| 5:15–6:30 | Human failure review → approved test case → mock experiment | Mock, not a real A/B result |

### 10.2 Conditions for the single live request

Real execution requires a **new, explicit authorization for ONE request**, given after offline review and account preflight. Nothing in 7A, 7B, 7C or the offline part of 7D grants it.

| # | Gate | Who verifies | Evidence |
|---|---|---|---|
| 1 | 7B and 7C verdicts pass; ADR-39 accepted | Reviewer | Reports |
| 2 | Repository, revision and dirty scope recorded | Operator | `git` output |
| 3 | SDK installed from this checkout | Runner | `sdk_checkout_match: true` |
| 4 | Audited package versions | Runner | No `client_library_not_audited` |
| 5 | Model and Free Tier evidence fresh | Primary Google pages, read date recorded; update is its own code change | §11.1 |
| 6 | AI Studio Billing Tier is Free, no billing account linked, quota available | **Operator only, private** | Not claimed by the code |
| 7 | Attestation variables set, ≤ 1 day old | Operator | Dry run |
| 8 | Key held in the operator's own shell; never in arguments, documents or screenshots | Operator | Practice |
| 9 | Caps fit the scenario (input bound, output cap, timeout) | Runner | Dry run `execution_allowed: true` |
| 10 | Telemetry endpoint and project are the intended ones; backend ready | Operator, runner | §5.4 steps 1–2 |
| 11 | One scenario chosen; one manual command, no loop | Operator | Worksheet |

Stop rules: on refusal, fix the named gate; on timeout, 429, blocked output or export failure, **do not resend**. A timeout may have reached the provider. Another request needs a new decision.

Verdicts are separate: `OFFLINE_DEMO_READY` and `LIVE_REHEARSAL_VERIFIED`. The second stays `NOT RUN` until a real call is read back.

---

## 11. Risks and open decisions

### 11.1 Risks

| ID | Risk | State | Handling |
|---|---|---|---|
| R102 | `minimatch` dev dependency advisory | **OPEN, NOT REMEDIATED**; review deadline 2026-11-30 UTC | Outside Phase 7; not touched |
| R105 | `refs/pull/1/head` keeps old commits public | **OPEN, NOT ACCEPTED**; user decision | Outside Phase 7; not touched |
| R104 | CI coverage | **RESOLVED BY MERGE AND MAIN CI** (run 42524191827) | Documentation closure is a separate task; not done in 7A |
| R90 | Tier is an operator attestation | Open by design | Gate 6 |
| — | **Free Tier evidence expires 2026-11-11 13:00 UTC**, two days from this report | Time-critical | The timestamp is **not** moved. After expiry the guard refuses until official pages are re-read and a separate, evidenced SDK change plus reinstall is authorized. No clock faking |
| R106 (proposed) | Preview text captured by terminal logs or recording | New | Opt-in, terminal-only, runbook notice |
| R107 (proposed) | Stale non-editable SDK install | New | `sdk_checkout_match`, refusal |
| R108 (proposed) | Dashboard `ok` read as provider completion | New | Label on stage; finish reason shown |
| R109 (proposed) | Operator-supplied project ID in the link | New | §5.4 |
| — | Thinking tokens could exhaust a cap | Unknown until run | Headroom; outcome `incomplete`, never resent |

Proposed risk numbers are not registered in 7A. Highest registered is R105.

No official Google page was re-read and no account, billing-tier or quota check was performed in this phase. Fresh **official-source** evidence and private **operator-account** verification are different things, and both remain to be done before any live call.

### 11.2 Decisions for the reviewer

Each has a recommended default; none blocks review of this design.

| # | Decision | Recommendation |
|---|---|---|
| 1 | Accept ADR-39 as written | Accept |
| 2 | Extract `run_single_request` from the smoke runner | Yes, with existing tests unmodified |
| 3 | Exit code 3 | Keep; `0` should not mean "export unknown" |
| 4 | Fixture language | English; Turkish is a new scenario version |
| 5 | Evidence refresh | Only if a live call is really wanted; plan it as a separately authorized step |
| 6 | 7C PostgreSQL | Local user-owned server with a test-owned database needs your approval; otherwise the `python` CI job provides real PostgreSQL after **you** push. Unavailable means BLOCKED, not PASS |
| 7 | Environment for 7B | Build `sdk/.venv` from the lock file (git-ignored) |

### 11.3 Documentation checks in this phase

| Command | Result |
|---|---|
| `python -I -B scripts/verify_phase0.py --quiet` | First run: **FAIL** (827 PASS, 1 FAIL). `PHASE_STATUS.md` named the unregistered ADR-39. Cause fixed in the status text, check unchanged. Final run: **PASS**, 828 PASS, 0 FAIL |
| `git diff --check` | PASS, no output |
| `secret_scan.PATTERNS` applied raw to this report and `PHASE_STATUS.md` | PASS, 0 matches in each |
| SHA-256 of user-owned files | PASS, §11.4 |
| SDK, backend, frontend tests | NOT RUN: no environment in this checkout, and not required for a design-only phase |
| Gemini request | NOT RUN: forbidden |
| Bandit, Ruff, mypy | NOT RUN: no code changed |

### 11.4 User-file integrity

Re-checked with `sha256sum` after the report was written: `8AE86D5131347FFE`, `B8E8A61C29FCF705` and `31FFD28B6FDD011F`, all equal to §1. The files stay untracked.

---

## 12. Recommended next step

Review this design and decide §11.2. If accepted, start **Phase 7B** with a new instruction. Separately and whenever convenient, carry out the R104 documentation closure. Do not attempt a live call as part of 7B or 7C.

---

## 13. Next-session handoff

```text
PHASE / DATE: 7A, architecture and source audit / 2026-11-09 UTC
BASE BRANCH / HEAD / WORKTREE PATH: feature/phase-7-gemini-demo at 435030648240ee54af78622a0d4d30737a84ba0b (same as main) / C:/Users/PC/LLM-Insight-Development
USER FILES ALREADY PRESENT: LLM_INSIGHT_HANDOFF.md, docs/PHASE7_LIVE_DEMO_PLAN.md, docs/OPUS_PHASE7_PROMPTS.md (untracked, untouched)
IMPLEMENTED (actual files): none. Documents only: docs/reports/phase-7a.md (new), docs/PHASE_STATUS.md (7A entry). Uncommitted
DECISIONS / ADR STATUS: ADR-39 PROPOSED in this report only, not in docs/ADR.md. Highest accepted entry is ADR-38. Reviewer decisions in section 11.2
COMMANDS EXECUTED + PASS / FAIL / SKIP / NOT RUN: verify_phase0 PASS after one corrected FAIL (section 11.3); git diff --check PASS; detectors PASS; user-file hashes PASS; tests NOT RUN (no environment)
PROVIDER REQUEST COUNT IN THIS PHASE: 0
DATABASE / RUNTIME ACTIONS: none. Read-only git and public GitHub API reads
KNOWN RISKS / OPEN DECISIONS: Free Tier evidence expires 2026-11-11 13:00 UTC; R102 open (deadline 2026-11-30 UTC); R105 open; R104 documentation closure separate; sdk/.venv, backend/.venv absent; section 11
NEXT PHASE + EXACT PREREQUISITES: 7B after review of this design; sdk/.venv built from the lock file; ADR-39 accepted; section 8 is the file map. No real provider request
NEXT PROMPT: docs/OPUS_PHASE7_PROMPTS.md, section "7B görevi"
VERDICT: PHASE 7A — DESIGN READY FOR REVIEW
```
