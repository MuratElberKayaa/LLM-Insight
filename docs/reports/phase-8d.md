# Phase 8D — Real provider readiness (Groq, Gemini, OpenRouter)

**Date:** 2026-11-12 UTC. **Branch:** `feature/live-comparison-development` at `0ab5e71` (= `origin/main`, PR #12 merge). **No code change.** This report and the `PHASE_STATUS.md` entry are the only edits, and they are uncommitted.

**Scope kept:**
- 0 provider requests, no `--execute`, no lock changed;
- `.env` and keys not opened;
- no new repository, directory, database or project.

## 1. GO / NO-GO

| Provider | Model | Verdict | Remaining blockers |
|---|---|---|---|
| **Groq** | `openai/gpt-oss-20b` | **NO-GO** | **G1** account Limits evidence; **G2** reviewed release commit; **G3** per-request authorization |
| **Gemini** | `gemini-3.1-flash-lite` | **NO-GO, closest to ready** | **M1** AI Studio Billing Tier and limits evidence; **M2** reviewed evidence re-release, which also unlocks Gemini (R140); **M3** your confirmation that the PR #12 merge is the R123 review; **M4** per-request authorization |
| **OpenRouter** | `google/gemma-4-26b-a4b-it:free` | **NO-GO, stays NO-GO** | **O1** R135 still unresolved: no enforceable zero-cost guarantee is documented; **O2** routability under `data_collection: "deny"` is unknown; **O3** reviewed release; **O4** authorization |

The attestations you gave are recorded as **operator attestations**, not as verified billing evidence: Groq Free Plan; Gemini Free Tier; OpenRouter no purchased credits, no BYOK, no auto top-up.

## 2. Official evidence (read 2026-11-12)

| Fact | Source (verbatim) |
|---|---|
| Groq `openai/gpt-oss-20b` is a production model: 131,072 context, 65,536 max completion | https://console.groq.com/docs/models: "Production Models"; the rate-limit column is headed "RATE LIMITS (DEVELOPER PLAN)" |
| Groq publishes no Free Plan limits for it | https://console.groq.com/docs/rate-limits: "Note that the limits shown below are the base limits for the Developer plan"; "You can view the current, exact rate limits for your organization on the limits page in your account settings." The two pages also conflict (1K RPM and 250K TPM against 30 RPM and 8K TPM) |
| Gemini 3.1 Flash-Lite is Stable | https://aistudio.google.com/docs/models/gemini-3.1-flash-lite (the target of `ai.google.dev/gemini-api/docs/models/...`): "Stable: gemini-3.1-flash-lite"; output limit 65,536; updated May 2026 |
| Gemini Free Tier pricing | https://aistudio.google.com/docs/pricing, section "Gemini 3.1 Flash-Lite", Standard, Free Tier: input "Free of charge", output "Free of charge", "Used to improve our products" "Yes" (so SYNTHETIC prompts only) |
| Gemini Free Tier limits are shown only in AI Studio | https://aistudio.google.com/docs/rate-limits: "Rate limits depend on a variety of factors (such as your usage tier) and can be viewed in Google AI Studio." No Flash-Lite Free figures are given |
| OpenRouter `:free` variant exists | https://openrouter.ai/google/gemma-4-26b-a4b-it:free/providers: "Price" "Free"; the endpoint list and per-endpoint data policy are not in the page |
| `max_price` | https://openrouter.ai/docs/guides/routing/provider-selection: "will prevent your request from running if the price is not available". Behaviour for a value of 0, and the error returned, are not stated |
| `data_collection` | the same page: "`deny`: use only providers which do not collect user data". The case where no provider qualifies is not stated |
| Privacy routing failure | https://openrouter.ai/docs/faq: "Providers that do log, or where we have been unable to confirm their policy, will not be routed to unless the model training toggle is switched on"; and if none of the providers match the account's privacy level, "you will get an error and your request will not complete" |
| Free and paid privacy settings differ | https://openrouter.ai/docs/guides/privacy/provider-logging: "There are separate settings for paid and free models." |
| No never-charged guarantee | https://openrouter.ai/docs/guides/routing/model-variants/free: "Free variants provide access to models without cost". The only billing warning concerns `models` fallback arrays, which the adapter never sends. No page states that a `:free` request cannot be charged |
| Caps | https://openrouter.ai/docs/faq: "you will be rate limited to 50 free model API requests per day" (below 10 purchased credits) |

## 3. Provider findings

**Groq (task 3).** The Free Plan limits cannot be taken from the documentation: the only tables are labelled Developer plan, and they conflict with each other. Evidence still needed (**G1**), as a dated note or screenshot that you keep out of the repository:
- the account's plan as shown in the console;
- for `openai/gpt-oss-20b`, the RPM, RPD, TPM and TPD values on the account's Limits page;
- the model listed and not deprecated on that date.

The release commit G2 then sets three values:
- `groq_live.FREE_PLAN_MODELS` to the one model, with the two doc URLs and "account Limits page, <date>" as sources;
- `FREE_PLAN_EVIDENCE_VERIFIED_AT` to that UTC time;
- `groq_adapter.REAL_EXECUTION_ENABLED = True`.

**Gemini (task 4).**
- **R123 review:**
  - Merged in PR #12, and both PR #12 CI runs passed.
  - The strict-date fix and the three-layer key-logging fix are in `0ab5e71`.
  - All 559 Gemini tests ran and passed today as part of §5.
  - It is open (M3) only if the PR #12 merge was not meant as the architectural review.
- **Prepared evidence, not applied.** The model and pricing facts above support re-releasing the existing allowlist entry unchanged:
  - `FREE_TIER_MODELS["gemini-3.1-flash-lite"]`, stability `stable`;
  - sources `https://ai.google.dev/gemini-api/docs/pricing` and `.../models/gemini-3.1-flash-lite`, now served from `aistudio.google.com/docs/...`.
- **Proposed reviewed change (M2):** set `FREE_TIER_VERIFIED_AT` to the UTC time of the re-read, only after your AI Studio check (M1) of:
  - the project's Billing Tier `free`;
  - the Free Tier limits shown for the model.
- **Caution (R140):** the readiness probe (§5) shows Gemini's **only** remaining refusal is `free_tier_evidence_stale`. That one commit therefore unlocks Gemini, unlike Groq and OpenRouter, which also have an execution flag. If you want the release and the authorization kept separate, add a Gemini `REAL_EXECUTION_ENABLED` flag first (a small reviewed change; not made here).

**OpenRouter (task 5, R135). It stays NO-GO.**
- **O1:** no page documents what `max_price: 0` does, or that a `:free` request cannot be charged. The guard's post-call halt (cost not exactly 0) detects a charge; it cannot prevent one.
- **O2:** the endpoints serving the `:free` variant, and their logging or training policy, are not published on the pages read. With `data_collection: "deny"`, and an account whose free-model training toggle is off, the request may have no eligible endpoint and fail. That is not a charge, but it does count against the 50-per-day cap.
- **Unchanged, and not to be relaxed:** `max_price` 0, `allow_fallbacks: false`, no `models` list, and no substitute model. A release would need an OpenRouter statement, or a reviewed decision to accept a request whose zero cost is checked only after the fact.

## 4. Local project and trace mixing (task 6)

- **Environment you reported:** backend at `http://127.0.0.1:8010` (I checked `/health/live` and `/health/ready`, both 200, with no credential); project 1 with an active ingest key; PostgreSQL healthy.
- **Accepted limitation, project 1:** comparison traces are stored next to that project's other traces, instead of in the dedicated project of ADR-45 D5. What stays separate and what mixes:
  - `/models/compare` still gives one row per provider, model and `prompt_version` (`cmp-v1.ticket-triage`), so the three comparison rows stay separable;
  - Overview totals (attempts, tokens, unknown-cost counts) and the trace list **mix** with the existing data;
  - filter Compare and Overview by `prompt_version=cmp-v1.ticket-triage`;
  - the runner's output says `project_id_source: operator_env_unverified`.

  No code change: the runner already allows any positive project ID.

## 5. Targeted offline tests (today, on `0ab5e71`)

Run with an SDK install byte-identical to this checkout, and without writing bytecode into the tree.

| Check | Result |
|---|---|
| Groq, OpenRouter and Gemini guards; the Gemini endpoint (R123); all three comparison runners; scenarios; the Groq and OpenRouter adapters (10 modules) | **1414 passed, 0 failed** |
| Readiness probe: one **dry run** per provider (never `--execute`) with the §6 operator settings, SYNTHETIC fake keys, project 1, today's date and the real clock, against the local OTLP stub | all three exit 0, `sent=false`, `execution_ready=true`, `sdk_checkout_match=true`; scenario 621 bytes, bound 685, cap 1024, `cmp-v1.ticket-triage`; 0 network attempts, 0 spans |
| Remaining refusals in the probe | Groq: `real_execution_disabled`, `free_plan_evidence_missing`, `model_not_free_plan_verified`. Gemini: `free_tier_evidence_stale`. OpenRouter: `real_execution_disabled`, `free_model_evidence_missing`, `free_routing_evidence_missing`, `model_not_free_verified` |

**Finding in my test setup:** with the 8C install (LF line endings) against this CRLF checkout, `sdk_checkout_match` correctly reported `false`. That mismatch was in my setup, not in yours: your own dry runs reported `true`.

## 6. Execution gate checklist (one request per provider, `cmp-ticket-triage`, cap 1024)

**Before every request:**
1. The reviewed release commit for **this provider only** is merged and its CI is green. The other providers stay locked.
2. Its evidence is current: Groq 14 days, Gemini 14 days, OpenRouter 7 days. The attestation date is today in UTC.
3. **Shell (values never printed):**
   - `LLMI_PROVIDER_MODE=real`;
   - the provider mode, attestation and date;
   - `LLMI_LIVE_PROVIDERS` set to the one provider and `LLMI_LIVE_MODELS` to the one model;
   - `LLMI_LIVE_MAX_REQUESTS=1`, `LLMI_LIVE_MAX_INPUT_TOKENS=1024` (the bound is 685) and `LLMI_LIVE_MAX_OUTPUT_TOKENS=1024`;
   - no `LLMI_LIVE_MAX_RETRIES` above 0, and none of the refused variables (`*_BASE_URL`, proxies, `SSL_CERT_*`, `SSLKEYLOGFILE`, `GOOGLE_*`);
   - `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:8010/v1/traces`, the project 1 ingest key and `LLMI_DASHBOARD_PROJECT_ID=1`.
4. You set the consent phrase yourself, for this provider only:

   | Provider | Consent phrase |
   |---|---|
   | Groq | `groq-free-plan-single-call` |
   | Gemini | `gemini-free-tier-single-call` |
   | OpenRouter | `openrouter-free-model-single-call` |

5. Dry run: `python -m demo_app.live_compare --provider <name> --scenario cmp-ticket-triage`. It must show `policy_eligible`, `execution_ready` and `execution_allowed` all true, `refusals: []`, `sdk_checkout_match: true` and the expected model and cap.
6. Written authorization: provider, model, scenario and date, for exactly one `--execute`.

**After the request:**

7. Record the one JSON line. Never re-run after a failure, timeout or `possibly_sent`.
8. First-response checks (`reports/phase-8b.md` §8.2):
   - the span and usage row are stored, with no content;
   - cost is `unknown`;
   - Groq: totals add up and the service tier is on-demand or null;
   - Gemini: the attestation still holds;
   - OpenRouter, if ever released: the response model equals the `:free` ID, cost is 0, and BYOK is false.
9. Clear the consent phrase. Review the result before authorizing the next provider.

**Order:** Groq, then Gemini, then OpenRouter, the last only if O1 and O2 are ever resolved.

## 7. Minimum changes before the first authorized request

- **Groq:** one reviewed commit (G2) after your Limits evidence (G1).
- **Gemini:** your AI Studio check (M1), then one reviewed commit (M2) refreshing `FREE_TIER_VERIFIED_AT`. Optionally, first a reviewed Gemini execution flag, so that release and authorization stay separate (R140).
- **OpenRouter:** none can be made safely now: O1 and O2 have to be resolved first.
- **No other code change is required.** Nothing here was unlocked.
