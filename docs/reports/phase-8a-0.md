# Phase 8A-0 — Multi-provider free API integration: architecture and readiness audit

**Date:** 2026-11-09 UTC. **Status: audit complete, nothing implemented.**

**Recommendation: GO for offline implementation after the §13 decisions. NO-GO for any real Groq or OpenRouter request** until the §12 blockers are cleared and each request is separately authorized.

**Reviewed 2026-11-09: conditional GO for offline development, NO-GO for all real requests.** §16 records the review, which changes decision D4, renumbers the proposed ADRs and renumbers the checkpoints. The text of §1 to §15 is kept as written at the audit; where they differ, §16 governs.

Provider API requests in this phase: **0**. Provider documentation web pages were read, but no API endpoint was called, including the public model-list endpoints. No `.env` was read, and PostgreSQL, Docker and containers were not touched. No adapter, guard, ADR or migration was changed. No commit, push, PR, merge, reset or rebase.

Evidence labels: **CODE** (read in this repository), **PROBE** (run offline against the real rule code), **CI** (read from the public GitHub API), **DOCS** (official pages read 2026-11-09), **UNVERIFIED** (absent or contradictory in what could be read).

---

## 1. Findings that shape the design

| # | Finding | Evidence | Consequence |
|---|---|---|---|
| F1 | Compare, Overview and Traces can represent all three providers with **no schema, API or stored-data change** | CODE, PROBE | §7 |
| F2 | `/models/compare` groups by provider, requested model, response model and `prompt_version`. It does not group by prompt name, and all three Gemini scenarios use `v1`, so **they merge into one row per model** | CODE: `repositories/metrics.py:327`, `gemini_scenarios.py:301,327,352` | New prompts need a distinct `prompt_version` each (§6) |
| F3 | `price_applicable("groq")` and `("openrouter")` return **True** today. Nothing is mispriced, because no such price rows exist, but the code comment requires a rule for each real provider. Cost is written once at ingest, so the rule must land before the first real span | PROBE; `services/pricing.py:154-164` | Checkpoint 8A-2 |
| F4 | A Groq-shaped key is **not detected** by the SDK guard, the backend value rules, `secret_scan.py`, the history gate or `verify_phase0.py`. It passes the model and response-ID rules. OpenRouter's `sk-or-` shape is detected | PROBE, CODE | §5.4, R111 |
| F5 | `FreeTierSession` and the policy are hard-wired to Gemini's model table, so they cannot be reused | CODE: `gemini_live.py:282` | A new guard module; Gemini's stays untouched |
| F6 | ADR-33 amends ADR-05 for Phases 6 and 7 only, and ADR-39 says Gemini has "exactly two entry points" | CODE | New ADRs must name what they supersede (§9) |
| F7 | Experiments cannot be reused for real runs: `mode: "real"` returns 409, the form forces mock, and triggers force `synthetic` provenance | CODE: ADR-25, ADR-26 | Separate future design (§10) |
| F8 | **Groq's Free plan limits are not published in any page that could be read.** The only table is Developer-plan, and two pages disagree on it | DOCS | Blocker B1 |
| F9 | Groq's models page lists `llama-3.1-8b-instant` and `llama-3.3-70b-versatile` as production, while its deprecations page shows both shut down on 2026-08-16 | DOCS | Both excluded |
| F10 | A `:free` OpenRouter model with a `models` fallback array **is billed at the paid price** if it falls back. The docs do not say a plain `:free` call can never charge | DOCS | §4.2: layered controls and a zero-credit account |
| F11 | OpenRouter can return **HTTP 200 with an error** in the body, or `finish_reason: "error"` | DOCS | The parser must not trust the status code |
| F12 | `_finish_reason_findings` only reads `openai` spans, so `length` on Groq or OpenRouter produces no diagnostic finding | CODE: `diagnostics/engine.py:561` | Accepted limit, R116 |

## 2. Repository baseline

| Item | Value |
|---|---|
| Root / branch | `C:/Users/PC/LLM-Insight-Development`, `feature/phase-7-gemini-demo` at `e581dc4` |
| Working tree before this report | Only the three user-owned untracked files |
| GitHub `main` | `91c4d7d`, the merge of PR #3 (2026-11-09 07:07Z). Parents `4350306` and `e581dc4` |
| Local `main` | **Stale at `4350306`.** Not fetched or updated here |

**Phase 7 evidence (CI):**
- Run 42576007136 on `e581dc4` and run 42577352545 on `91c4d7d` both completed successfully, with all seven jobs green, including `python`.
- That job sets `LLMI_TEST_REQUIRE_DB=1`, so the 25 database-backed 7C tests could not have been skipped silently. Only job conclusions were read, not the logs, so per-test counts are not asserted here.

**Phase 7 is not fully closed:**
- `PHASE_STATUS.md` still shows 7C as pending and 7D as not done.
- No real request has been made through the 7B content runner.
- The Gemini Free Tier evidence guard **expires 2026-11-11 13:00 UTC**, after which every real Gemini call refuses until the evidence is re-read and re-released.

Open risks unrelated to this work: R102 (npm advisory, deadline 2026-11-30 UTC) and R105 (historical GitHub references).

## 3. Provider facts (DOCS, read 2026-11-09)

Several facts came through a page-reading tool, and long pages were partly summarized. Anything used for a release decision must be re-read at the evidence gate (8A-8).

| Topic | Groq | OpenRouter |
|---|---|---|
| Endpoint | `POST https://api.groq.com/openai/v1/chat/completions` | `POST https://openrouter.ai/api/v1/chat/completions` |
| Auth | `Authorization: Bearer` | `Authorization: Bearer` |
| Output limit field | `max_completion_tokens` (`max_tokens` deprecated) | `max_tokens` |
| Free terms | Free plan exists; limits on the account page only. **UNVERIFIED** numerically | `:free`: 20 per minute; 50 per day below 10 purchased credits, 1000 above. Failed attempts count (collections page) |
| Plan visible through the API | No | Only through a separate key endpoint (`is_free_tier`, `limit`), not called here |
| Becoming paid | Upgrade needs a payment method; usage then billed per token | Buying credits; a fallback to a paid model is billed |
| Spend control | "Only available on paid plans"; monthly block with a 10–15 minute lag; 400 `blocked_api_access` | Per-key `limit`; budget 403s |
| Response ID | `id` (string) | `id` (`gen-…`) |
| Usage (non-stream) | `prompt_tokens`, `completion_tokens`, `total_tokens` plus timing fields. Detail fields **not documented** for chat completions | Same three, plus `prompt_tokens_details.{cached_tokens,cache_write_tokens}`, `completion_tokens_details.reasoning_tokens`, `cost`, `is_byok`; always included |
| Finish reasons | Enumeration **absent**; example shows `stop` | `stop`, `length`, `content_filter`, `tool_calls`, `error` |
| Errors | `{error:{message,type}}`; 400, 401, 403, 404, 413, 422, 424, 429, 498, 499, 5xx | `{error:{code,message,metadata}}`; 400, 401, 402, 403, 408, 429, 502, 503; **200 with error body** |
| Reasoning | gpt-oss returns reasoning by default; `include_reasoning: false` excludes it. Whether it counts against the limit or inside `completion_tokens` is **absent** | Counted as output and against `max_tokens`; `reasoning.exclude` |
| Data | No retention by default; logs up to 30 days for reliability or abuse; ZDR available. Services Agreement §4.2: no training on inputs or outputs unless permitted. §3.6: DPA does not cover Beta services | Prompt logging opt-in, off by default. Some upstreams may train: providers that log are routed to only if the training toggle is on. Terms 4.1 ("requires users to purchase Credits") conflicts with the free tier |

**Models.**
- **Groq candidate:** `openai/gpt-oss-20b` (production, not deprecated). Preview models are excluded because Groq says they should not be used in production and its DPA does not cover Beta services. Free-plan availability is **UNVERIFIED**.
- **OpenRouter candidate:** `google/gemma-4-26b-a4b-it:free`, with `nvidia/nemotron-3.5-lightning:free` as alternate. Both are general text models, taken from page links; neither page carries a model-specific data note. Status and expiry are **UNVERIFIED**, and the docs warn availability "changes frequently".
- **Refused outright:** `openrouter/free` (random model, breaks comparison), `openrouter/auto` (paid), `liquid/*` (page states prompts may train its models).

**Semconv pin `9ffc7e00c639`:** `groq` is a listed provider value, `openrouter` is not. The attribute note says to set it from the instrumentation's best knowledge. Decision in ADR-40.

## 4. Fail-closed policies

### 4.1 Common to both

Same model as ADR-35. An API key alone enables nothing, and any failed check refuses with exit 2 and sends nothing.

| Control | Rule |
|---|---|
| Mode and consent | `LLMI_PROVIDER_MODE=real`, plus a provider mode and a provider-specific consent phrase: `groq-free-plan-single-call` / `openrouter-free-model-single-call` |
| Key | `GROQ_API_KEY` / `OPENROUTER_API_KEY`, presence only, never an argument. The profile binds each variable to one host. A prefix check refuses a swapped key, naming no value |
| Attestation | `LLMI_GROQ_PLAN=free` / `LLMI_OPENROUTER_ACCOUNT_STATE=no-purchased-credits`, plus a `_CHECKED_ON` UTC date at most one day old. Described everywhere as an operator check, not proof |
| Allowlist | Code-pinned models with source URLs and a verified-at time. Refused after 14 days (Groq) or **7 days** (OpenRouter) |
| Caps | 1 request per process, concurrency 1, **0 retries**, timeout ≤ 30 s, output 16–1024, input bound ≤ 1024 |
| Execution | Dry run by default; `--execute` is the only authorization. No abbreviations, run-all, resend or continuation |
| Environment | Refuse if set: `GROQ_BASE_URL`, `OPENROUTER_BASE_URL`, `OPENAI_BASE_URL`, `OPENAI_API_BASE` |
| Interruption | Reported `possibly_sent`; never "not sent" unless provable |

### 4.2 Provider-specific

**Groq.**
- Send exactly `model`, `messages` (one user message), `max_completion_tokens`, `stream: false`, `include_reasoning: false`.
- Never `service_tier`, tools, `n`, `response_format` or `user`.
- Post-call halt on: `service_tier` other than `on_demand` or null; `blocked_api_access` (spend limits exist only on paid plans, so seeing it **contradicts the free attestation**); a response model that does not match.

**OpenRouter.**
- Send exactly `model`, `messages`, `max_tokens`, `stream: false`, `reasoning: {exclude: true}`, and `provider: {allow_fallbacks: false, data_collection: "deny", max_price: {prompt: 0, completion: 0, request: 0}}`.
- `max_price` details came from a summarized part of the page. Confirm them in 8A-3 before relying on them.
- Never `models`, `route`, `plugins` or `sort`, or any variant suffix other than `:free`.
- Post-call halt on: `usage.cost` not exactly 0; `is_byok` true; a response model other than the requested ID, with or without `:free`.
- **The real boundary is the account:** a dedicated key on an account with no purchased credits. Request-level controls are a second layer. The post-call check only detects, it cannot prevent.

A halt means exit 3, session halted, and a visible `free_plan_violation` code. It never triggers another request.

## 5. Adapter, telemetry and normalization

### 5.1 Client (ADR-40, ADR-41)

One closed registry of two frozen profiles over a **direct `httpx` client**, implementing the unchanged `TextGenerationAdapter`. URLs are constants; no base URL can be supplied by anyone. `httpx==0.28.1` and `certifi==2026.10.19` are already in the lock, so no new package enters.

| Option | Result |
|---|---|
| **Direct `httpx` (recommended)** | No library environment reads, default retries or redirect following to neutralize. Small strict parser that we own. Cost: untested against live responses until 8A-8 |
| Official `groq` 1.7.0 and `openrouter` 1.3.143 | Two new dependency trees, each needing an ADR-34-style audit. OpenRouter's upload date could not be confirmed for the two-week rule |
| `openai` 3.28.0 with a custom base URL | Undermines ADR-32's single-host check and mixes with the Responses adapter |

Boundary, re-checked before the send as in ADR-34: exact `httpx.Client`, plain `HTTPTransport`, no redirects, `trust_env=False`, no mounts, hooks or auth, CA-verifying TLS with the `certifi` bundle. Headers are exactly `Authorization`, `Content-Type`, `Accept`, `User-Agent`. No `traceparent`, `HTTP-Referer` or title header is sent. `real` provenance refuses a mock transport.

### 5.2 Usage

| Semconv | Groq | OpenRouter |
|---|---|---|
| `input_tokens` | `prompt_tokens` | `prompt_tokens` |
| `output_tokens` | `completion_tokens` | `completion_tokens` (includes reasoning) |
| `cache_read.input_tokens` | `prompt_tokens_details.cached_tokens` if present | same |
| `cache_write.input_tokens` | not mapped | `prompt_tokens_details.cache_write_tokens` |
| `reasoning.output_tokens` | `completion_tokens_details.reasoning_tokens` if present | same |

Rules, matching Gemini's discipline:
- A missing prompt, completion or total count gives `missing`, so usage is unknown.
- `prompt + completion != total`, a subset above its total, or a non-integer gives `inconsistent`, so usage is unknown.
- Nothing is estimated, and absent details stay NULL.
- Whether Groq includes reasoning in `completion_tokens` is **UNVERIFIED**, so the first real call must compare the totals.

### 5.3 Outcomes and errors

No new `ERROR_CATEGORIES`, `OutcomeStatus` or `TextOutcome` fields are needed.

| Observation | Status | Category | Span `error.type` |
|---|---|---|---|
| `stop` with non-empty text | completed | | |
| `length` | incomplete (reason kept) | | |
| `content_filter` | blocked | `content_blocked` | none; reason recorded |
| `stop` with empty text, or no choices | failed | `empty_response` | `{p}.response.empty` |
| `finish_reason: error` | failed | `response_failed` | `{p}.response.failed` |
| Other or missing finish reason | failed | `unexpected_status` | `{p}.response.unexpected_finish` |
| **200 with `error` body** | error | by `error.code` | the code, else `{p}.response.error` |
| Unreadable body or wrong shape | error | `malformed_response` | `{p}.response.malformed` |
| Timeout / transport | error | `timeout` / `connection_error` | `timeout` / `httpx.{Class}` |
| 400 / 413 / 422 | error | `invalid_request` | status |
| 400 `blocked_api_access` | error, **halt** | `precondition_failed` | `400` |
| 401 / 404 | error | `authentication_failed` / `not_found` | status |
| 402 | error, **halt** | `payment_required` | `402` |
| 403 with moderation `reasons` / otherwise | error | `content_blocked` / `permission_denied` | `403` |
| 408 | error | `timeout` | `408` |
| 429 | error | `rate_limited` | `429` |
| 498 | error, **halt** (Flex is never sent) | `provider_error` | `498` |
| 5xx | error | `provider_error` | status |

Provider error fields `message`, `metadata.raw`, `flagged_input` and `patterns` are **never read**. Only `error.code` and a typed `metadata.error_type` passing the keyword rule may be used.

### 5.4 Spans, cost and correlation

- One CLIENT span per physical request: operation `chat`, provider `groq` or `openrouter`, `server.address`/`port`, `gen_ai.request.max_tokens`, response model, response ID, finish reasons verbatim, `llminsight.{call_id,attempt,provenance,usage_source}`. PROBE: all pass the rules unchanged, so **`llminsight.attrmap.v1` is unchanged**.
- Never exported: content, reasoning text, `native_finish_reason`, timing fields, `usage.cost`, upstream provider, error text. Timing fields and `usage.cost` may appear in the local JSON line only, labelled "credits" (the docs disagree between credits and USD).
- `resp:groq:{id}` and `resp:openrouter:{id}` give distinct usage keys, so existing idempotency applies.
- **Cost** is always `unknown` / `price_missing`, never 0, through new `APPLICABILITY` rules (ADR-43). Groq's published per-token prices are not applied, because a Free plan call has no such charge.
- **Correlation:** one process, one provider, one scenario, one trace, root span `demo.live-compare`. Cross-provider grouping uses `prompt_version` and a **dedicated comparison project**, so existing data is untouched.
- **Secret detection:** add the Groq key shape to `_guards.py`, `secret_scan.py` (which the history gate reuses) and `verify_phase0.py`. The backend's §3.1 rules are part of `attrmap.v1`, and ADR-15 requires `attrmap.v2` for a change, so the backend copy is deferred (R111).

## 6. Shared synthetic prompts

Same contract for all providers: one user message, one attempt, no system prompt, tools or structured output, **uniform `max_output_tokens` = 1024** (reasoning models can exhaust 512), provider-default temperature. Text is built as `PREAMBLE + task + label + fixture`, using the existing preamble.

The registry is new. `gemini_scenarios.py` stays unchanged, because its validators hard-code Gemini's fixtures. Inputs are checked against pinned hashes before any client exists. The hashes below are a draft and are pinned in 8A-5.

| ID / `prompt_version` | Bytes (bound) | Draft SHA-256 |
|---|---|---|
| `cmp-ticket-triage` / `cmp-v1.ticket-triage` | 621 (685) | `5d9040f3…c267ff` |
| `cmp-json-aggregate` / `cmp-v1.json-aggregate` | 661 (725) | `89684d94…b31a37` |
| `cmp-release-summary` / `cmp-v1.release-summary` | 595 (659) | `815eef60…35ea47` |

**1. Ticket triage.** Task: Classify the support ticket below. Reply with only one JSON object with exactly these keys: "category" (one of "billing", "login", "bug", "feature_request", "other"), "priority" (one of "low", "medium", "high"), "reason" (one sentence, at most 120 characters). Do not use Markdown.
Fixture: Ticket T-2044 from a fictional customer of the fictional Lumen Tasks app: after resetting my account this morning, the sign-in page keeps rejecting me and I cannot get in. My invoices and the rest of the app look fine from what my teammate can see.
Checks: exact keys, enums, reason length, category `login`.

**2. JSON aggregate.** Task: Read the JSON data below. Reply with only one JSON object with exactly these keys: "total_requests" (integer), "highest_error_day" (the "day" value with the most errors), "error_rate_percent" (total errors divided by total requests, times 100, rounded to one decimal), "observation" (one sentence, at most 120 characters). Do not use Markdown.
Fixture: `{"dataset":"SYNTHETIC-comparison-usage","days":[{"day":"Mon","errors":4,"requests":200},{"day":"Tue","errors":6,"requests":300},{"day":"Wed","errors":15,"requests":150},{"day":"Thu","errors":5,"requests":350}],"unit":"requests"}`
Checks: total 1000, day `Wed`, rate 3.0 ± 0.05.

**3. Release summary.** Task: Summarize the release note below in at most two sentences and at most 280 characters, in plain text without Markdown. Mention the known issue.
Fixture: Release note for the fictional Lumen Tasks app, version 7.1. Boards now load twice as fast on slow connections. Recurring tasks can be paused for a chosen period. The legacy calendar widget is removed; existing reminders are kept. A known issue remains: importing a project with more than 300 tasks can stall, and the team plans a fix in the next release.
Checks: at most 280 characters, at most two sentences, no Markdown, mentions `import`.

**What this comparison can and cannot show.**
- Same prompt, cap and attempt policy, on different models and infrastructure.
- Latency includes Groq queue time and OpenRouter routing, and upstream hosts can vary between OpenRouter calls.
- Validation is a few deterministic rules on a single answer. It does not appear in `/models/compare`.
- p95 needs n ≥ 20 per group. At 60 requests per provider, that exceeds OpenRouter's 50 per day, and every request is individually authorized. **Expect p50 and low-sample flags, not a ranking.**

## 7. Read API readiness

**Verdict: all three can represent the data correctly with no change and no modification of accepted data.** Offline evidence only; the three-provider database test is 8A-6.

| Check | Result |
|---|---|
| `provider` filter format | `groq`, `openrouter` pass, backend and `frontend/lib/scope-filters.ts:15` |
| Model IDs with `/` and `:free` | Pass the SDK rule, and match the dashboard's `MODEL_RE` |
| Grouping | Provider and both models are kept. A failed call has NULL `response_model`, so it forms its own honest row |
| Trace list | `provider` is an `EXISTS` on usage rows, no hard-coded names (ADR-37) |
| Cost | `known_cost_usd: null`, `cost_complete: false`, reason counts shown; never 0 |
| Frontend | No hard-coded provider names in `app/`, `lib/` or `components/` |
| Migration | None |

Limits to state in the UI docs: scenario separation depends on F2's `prompt_version` rule; OpenRouter upstream identity is not visible (R114).

## 8. Offline test gates

No real request and no developer database. PostgreSQL only on the CI runner, as in 7C.

| Gate | Must prove |
|---|---|
| **Unit: usage** | Both mappings, each `missing`/`inconsistent` path, bool and negative counts, absent details stay NULL |
| **Unit: outcomes** | Every §5.3 row, including 200-with-error and empty content; no error text reaches any output |
| **Unit: guard** | Every refusal independently; stale evidence and attestation; injected clock; no paid mode exists |
| **Unit: request** | Body and headers are **exact allowlists**; forbidden fields, models and suffixes refused before a send |
| **Unit: endpoint** | Each boundary condition alone refuses; redirect, proxy and mock under `real` refused; re-check before send |
| **Fake provider** | One request per run, none on failure, timeout or 429; every halt condition; `possibly_sent` on interrupt |
| **OTLP wire** | Real exporter bytes, three scenarios, two providers; attribute sets exact |
| **PostgreSQL (CI)** | One usage row per attempt; re-export stable; `cost_status=unknown`, `price_missing`, never 0; project isolation; `server_dropped_attributes_count = 0` |
| **Read API (CI)** | Compare, Overview, Traces with OpenAI, Gemini, Groq and OpenRouter rows; filters exact; distinct `prompt_version` rows |
| **Privacy** | Sentinels absent from stdout, stderr, DEBUG logs, protobuf bytes, DB rows, API; reasoning never previewed |
| **Security** | Ruff, mypy, Bandit gate without baseline change or `nosec`, pip-audit, history scan; Groq shape detected |
| **Regression** | Gemini and OpenAI suites unchanged and passing; `test_install` non-editable; Experiments still refuses `real` |
| **Mutation** | Removing a halt, adding a retry, following a redirect, cost 0, exporting error text: each caught |
| **Dashboard (CI e2e)** | Seeded four-provider Compare and filters render; unknown cost shown as unknown |

A missing database must fail required tests, as in 7C.

## 9. Proposed ADRs (not added to `ADR.md`)

**ADR-40 — Closed chat-completions profiles for Groq and OpenRouter.** Amends ADR-05 and extends ADR-33's scope to Phase 8. Two frozen profiles behind the unchanged protocol. Provider values `groq` (semconv) and `openrouter` (not listed at the pin; identifies the service called, not the unknown upstream). Rejected: upstream name as provider; a generic base-URL adapter; `attrmap.v2`.

**ADR-41 — Direct `httpx` with an endpoint boundary.** Extends ADR-34's rule to two hosts. §5.1 controls, re-checked before send. Rejected: the official SDKs and the `openai` client (§5.1).

**ADR-42 — Free-plan guards.** §4 policy in a new module. ADR-35 and `gemini_live.py` unchanged. Attestation is an operator check, and the account state is the real boundary. Post-call indicators halt and never resend.

**ADR-43 — Cost semantics.** `APPLICABILITY` rules returning False for `groq` and `openrouter`, so usage is always `price_missing`. `usage.cost` is local-only. Rejected: storing `computed` 0; a new unknown reason (migration, no stronger guarantee).

**ADR-44 — Comparison runner.** `python -m demo_app.live_compare --provider … --scenario …`, one process and one request. Gemini goes through the existing `run_single_request`, unchanged. **Supersedes** ADR-36's first sentence as amended by ADR-39: "exactly two entry points" becomes three. Distinct `prompt_version` per scenario, dedicated project.

**ADR-45 — Real dataset evaluation stays out of Experiments.** Records §10. Nothing implemented. ADR-25 and ADR-26 unchanged.

## 10. Real dataset evaluation (future, separate)

| | Existing Experiments | Future provider evaluation |
|---|---|---|
| Execution | Scripted mock, in-process | Real provider, local CLI only |
| Variable | Two prompt versions, one model | One prompt set, several providers |
| Surface | Dashboard write (ADR-26) | **No dashboard write, no execute button** |
| Provenance | Forced `synthetic` by trigger | `real` asserted |
| Tables | `experiments`, results (migration 0005) | New tables and new ADR; none reused |
| Cost | Labelled synthetic estimate | Unknown, never 0 |
| Output | Never stored | Never stored: hash and rule booleans only |

Requirements before design: this phase's adapters proven real; fixed synthetic golden datasets only; deterministic checks only (ADR-10, no LLM judge); per-run authorization; rate-budget planning; lineage through `trace_id` (ADR-12). Nothing here changes `mode: "real"` returning 409.

## 11. Implementation plan

Each checkpoint is one reviewable commit by you, stopping at its gate. Before 8A-1: update local `main` and branch from it.

| # | Scope | Files | Acceptance |
|---|---|---|---|
| **8A-1** | Docs only | `docs/ADR.md`, `TELEMETRY_CONTRACT.md` §3 note, `PRICING.md` §3, `SECURITY.md` §6, `SOURCES_AND_RISKS.md`, `PHASE_STATUS.md` | ADRs accepted by you; `verify_phase0.py` passes |
| **8A-2** | Pricing rule, secret detection | `backend/app/services/pricing.py`, `backend/tests/unit/test_pricing.py`, `sdk/src/llm_insight_sdk/_guards.py`, `scripts/qa/secret_scan.py`, `scripts/verify_phase0.py`, their tests | Both providers `price_missing` even if rows are seeded; OpenAI and Gemini unchanged; history gate passes |
| **8A-3** | Adapter | new `sdk/src/llm_insight_sdk/chat_completions_adapter.py`, `providers.py` (docstring), `pyproject.toml` (extra `free-chat`), new `tests/unit/test_chat_completions_adapter.py` | §8 usage, outcome, request gates; lock unchanged |
| **8A-4** | Guard and endpoint | new `free_chat_live.py`, adapter boundary, new `test_free_chat_guard.py`, `test_free_chat_endpoint.py`, `.env.example` | Guard and endpoint gates; dry run sends nothing |
| **8A-5** | Scenarios and runner | new `demo_app/comparison_scenarios.py`, `demo_app/live_compare.py`, unit tests, `README.md`, `sdk/README.md` | Hashes pinned; fake-provider and privacy gates; Gemini runners unchanged |
| **8A-6** | Integration | new `sdk/tests/integration/test_live_compare.py`, `backend/tests/integration/test_multi_provider_read.py` | Wire local; DB and API **CI only**, reported NOT RUN LOCALLY until CI |
| **8A-7** | Dashboard | `scripts/dev/seed_trace_fixtures.py`, `frontend/tests/e2e/` new spec | Four-provider e2e green in CI; no product code change expected |
| **8A-8** | Operator package | `docs/reports/phase-8a-8.md`, allowlist constants | Evidence re-read; §12 cleared; **stops before any send** |
| **Later** | One real request per provider | | Separate explicit approval for each |

## 12. Blockers for real requests

| # | Blocker | Resolution |
|---|---|---|
| B1 | Groq Free limits and model availability unknown | You read the account Limits page and record it |
| B2 | Groq docs contradict each other | Re-read at 8A-8; only models not on the deprecations page |
| B3 | OpenRouter free model IDs unverified | Re-read pages at 8A-8, or you approve one read of the public model list |
| B4 | Whether a plain `:free` call can charge is undocumented | Zero-credit account, §4.2 layers, then evidence from the first call |
| B5 | Groq reasoning accounting undocumented | First-call comparison, recorded before relying on totals |
| B6 | Gemini evidence expires 2026-11-11 13:00 UTC | Re-read and re-release before a Gemini comparison call |
| B7 | Telemetry correctness for live responses | Fake responses reflect docs only; the first real call is the test |

## 13. Decisions needed from you

| # | Decision | Recommendation | If different |
|---|---|---|---|
| D1 | Client approach | Direct `httpx` | Official SDKs add two audits and new locks |
| D2 | OpenRouter `data_collection` | `deny`: fail closed | `allow` widens free models but may let upstreams keep or train on the synthetic prompts |
| D3 | OpenRouter account | No purchased credits | Credits raise the cap to 1000 per day but create a paid path |
| D4 | Backend detection of Groq keys | Defer to `attrmap.v2`; SDK and scanners now | Changing now needs an ADR-15 exception |
| D5 | Comparison data location | Dedicated project | A shared project mixes series |

## 14. Risks to add (proposed; not yet in the register)

| ID | Risk | Severity | Mitigation |
|---|---|---|---|
| R110 | An attested-free account is actually paid | Med | Operator check, account state, post-call halts; provider billing is the real boundary |
| R111 | Groq key shape undetected in the backend until `attrmap.v2` | Low | SDK guard and scanners; keys never enter telemetry by design |
| R112 | OpenRouter free model vanishes or changes | Med | 7-day evidence window; no substitution |
| R113 | Free-tier data use by OpenRouter upstreams | Low | Synthetic only, `data_collection: deny`, no logging toggle |
| R114 | OpenRouter upstream not visible in Compare | Med | Stated limit; `attrmap.v2` if it matters |
| R115 | Parser built from docs differs from live responses | Med | Strict parser fails to `malformed`, never guesses; first-call review |
| R116 | Diagnostics ignore Groq and OpenRouter finish reasons | Low | As R89; rules v2 if needed |
| R117 | Small samples read as a ranking | Med | Low-sample flags; §6 statement |
| R118 | Failed attempts consume OpenRouter's daily cap | Low | No retries, one request per process |

## 15. Verdict

```text
PHASE / DATE: 8A-0, architecture and readiness audit / 2026-11-09 UTC
BASE: feature/phase-7-gemini-demo at e581dc4; GitHub main 91c4d7d
CHANGED: docs/reports/phase-8a-0.md, docs/PHASE_STATUS.md (uncommitted)
PROVIDER API REQUESTS / DOCKER / DATABASE / .ENV: 0 / none / none / not read
OFFLINE IMPLEMENTATION: GO, after D1-D5 and ADR acceptance
REAL GROQ OR OPENROUTER REQUESTS: NO-GO until B1-B7 and separate approval
```

## 16. Architectural review outcome (2026-11-09)

**Verdict: conditional GO for offline development. NO-GO for all real Groq and OpenRouter requests.** Phase 7 history is unchanged, and Phase 7D is **not** complete.

### 16.1 The five decisions

| # | Decision | Reviewer's outcome |
|---|---|---|
| D1 | Client | **Approved:** direct, explicitly configured `httpx`. No implicit retries, no redirects, no environment-based proxy routing, no uncontrolled endpoint override |
| D2 | OpenRouter data policy | **Approved and extended:** `data_collection="deny"` by default with no silent downgrade. Prefer ZDR where an eligible free endpoint supports it |
| D3 | OpenRouter account | **Approved and extended:** no purchased credits, no paid billing path, no BYOK, no automatic top-ups. Not treated as a billing guarantee |
| D4 | Backend detection of Groq keys | **My recommendation was rejected.** Do not defer. Harden the SDK, backend and repository/CI scanners now, and do not weaken the backend's authority. Determine whether security-only hardening can preserve `attrmap.v1`, and propose an ADR amendment if the accepted contract changes |
| D5 | Comparison data | **Approved:** a separate LLM Insight project for future multi-provider comparisons |

**Text this overrides:** F4's consequence, §5.4's "the backend copy is deferred", the D4 row of §13 and risk R111.

### 16.2 Additional binding requirements

- OpenRouter uses an explicit, verified `:free` allowlist only.
- Automatic model routing, paid model fallbacks and any model list that can include a paid alternative are rejected.
- `allow_fallbacks=false` **and** a verified zero-price routing constraint are required before any real OpenRouter call. §4.2 had `max_price` as a layer to confirm; it is now a precondition.
- OpenRouter HTTP 200 responses containing provider errors are failures, not successful inference.
- Groq and OpenRouter cost stays `unknown`/null until the billing and applicability rules are supported by evidence. A stored catalogue price must not price a Free Plan request.
- No provider requests, credentials, billing interactions or deployment changes.
- Experiments stay mock-only, and real experiments are not implemented in Phase 8A.
- Metadata-only telemetry, existing data and current Gemini behaviour are preserved.

### 16.3 Resulting ADR changes

The two decisions implemented first take the next free numbers in `ADR.md`, so the proposals in §9 are renumbered. Only ADR-40 and ADR-41 are added to `ADR.md` in checkpoint 8A-1. The others remain proposals here until their checkpoint.

| New | §9 name | Subject | Change from §9 | State |
|---|---|---|---|---|
| ADR-40 | ADR-43 | Fail-closed cost for free-plan providers | Unchanged in substance | In `ADR.md`, implemented in 8A-1 |
| ADR-41 | new | Security-only credential hardening under `attrmap.v1` | New, from D4. Amends one sentence of `TELEMETRY_CONTRACT.md` §3 | In `ADR.md`, implemented in 8A-1, for review |
| ADR-42 | ADR-40 | Closed chat-completions profiles | Unchanged | Proposed |
| ADR-43 | ADR-41 | Direct `httpx` endpoint boundary | D1 wording | Proposed |
| ADR-44 | ADR-42 | Free-plan guards | D2, D3 and §16.2 added | Proposed |
| ADR-45 | ADR-44 | Comparison runner | Separate project (D5) is binding | Proposed |
| ADR-46 | ADR-45 | Real dataset evaluation outside Experiments | Unchanged | Proposed |

### 16.4 Checkpoint renumbering

The reviewer's 8A-1 combines the code of §11's 8A-2 with only the documentation it needs. §11's remaining checkpoints each move down by one.

| Reviewer's checkpoint | §11 checkpoint | Scope |
|---|---|---|
| **8A-1** | 8A-2, plus narrow parts of 8A-1 | Pricing rule, Groq key detection, ADR-40 and ADR-41 |
| 8A-2 | 8A-3 | Adapter |
| 8A-3 | 8A-4 | Guard and endpoint boundary |
| 8A-4 | 8A-5 | Scenarios and runner |
| 8A-5 | 8A-6 | Integration |
| 8A-6 | 8A-7 | Dashboard |
| 8A-7 | 8A-8 | Operator package |

## Sources (read 2026-11-09)

Groq: [rate limits](https://console.groq.com/docs/rate-limits), [models](https://console.groq.com/docs/models), [API reference](https://console.groq.com/docs/api-reference), [errors](https://console.groq.com/docs/errors), [your data](https://console.groq.com/docs/your-data), [deprecations](https://console.groq.com/docs/deprecations), [spend limits](https://console.groq.com/docs/spend-limits), [reasoning](https://console.groq.com/docs/reasoning.md), [service tiers](https://console.groq.com/docs/service-tiers.md), [billing FAQ](https://console.groq.com/docs/billing-faqs), [Services Agreement](https://console.groq.com/docs/legal/services-agreement).

OpenRouter: [limits](https://openrouter.ai/docs/api/reference/limits), [API overview](https://openrouter.ai/docs/api/reference/overview), [errors](https://openrouter.ai/docs/api/reference/errors-and-debugging), [usage accounting](https://openrouter.ai/docs/guides/guides/usage-accounting), [free variant](https://openrouter.ai/docs/guides/routing/model-variants/free), [provider selection](https://openrouter.ai/docs/guides/routing/provider-selection), [provider logging](https://openrouter.ai/docs/guides/privacy/provider-logging), [data collection](https://openrouter.ai/docs/guides/privacy/data-collection.md), [input and output logging](https://openrouter.ai/docs/guides/features/input-output-logging.md), [FAQ](https://openrouter.ai/docs/faq), [free models](https://openrouter.ai/collections/free-models), [privacy](https://openrouter.ai/privacy), [terms](https://openrouter.ai/terms).

Other: [semconv registry at the pin](https://raw.githubusercontent.com/open-telemetry/semantic-conventions-genai/9ffc7e00c639/model/gen-ai/registry.yaml), [groq on PyPI](https://pypi.org/project/groq/), [openrouter on PyPI](https://pypi.org/project/openrouter/).
