# Phase 9A — Reference list price (F3): design and evidence

**Date:** 2026-11-19 (UTC). **Type:** design checkpoint. No code, no migration, no ADR change and no provider request in this phase. `docs/ADR.md` is unchanged. The decision below is **PROPOSED** and needs the owner's review before implementation (Phase 9B). **Update 2026-11-19:** the owner accepted it with D1–D6 as recommended; it is now ADR-52 in `ADR.md` and implemented in `reports/phase-9b.md`.

**Owner decision taken (2026-11-19):** F3 goes forward as a *reference list price*. Two other options were offered and not chosen: ledger pricing of paid tiers, and an OpenAI-only catalogue refresh.

## 1. Problem

Every real trace stored so far is Groq `openai/gpt-oss-20b` or Gemini `gemini-3.1-flash-lite`. All of them show `cost_status = unknown` with `price_missing`, by design:

- ADR-35 and ADR-40 forbid applying a stored price to `gcp.gemini`, `groq` and `openrouter`. No span attribute shows the billing tier, so a list price on a Free Tier call would claim spend that did not happen, and 0 would claim no spend.
- M11 already counts unknown-cost calls by reason (`METRICS_CATALOG.md` M11), so an "unknown-cost ratio" KPI would add nothing new.

What users cannot see today: **what the observed tokens would cost at the provider's published paid price.** That number is the one a model comparison needs, for example "v2 uses 40 % more output tokens, about $0.0003 per call at list price".

## 2. Proposal in one paragraph

A second, separate number next to the ledger cost, never inside it. The number is "list-price reference: what these provider-reported tokens would cost at the published Standard paid-tier price, verified on a stated date". It is computed **at read time** from a versioned, evidence-bound, code-only catalogue. It is never written to `usage_records`, never added to `known_cost_usd`, and always labelled *Reference · not billed · not an invoice*. Ledger semantics (ADR-11, ADR-35, ADR-40) do not change.

## 3. Invariants (must hold after 9B; each one gets a test)

| # | Invariant |
|---|---|
| I1 | `usage_records` columns are unchanged, no row is written or updated, and there is no migration |
| I2 | `cost_usd`, `cost_status`, `cost_unknown_reason`, `price_version` and `model_price_id` are unchanged for every existing and new row; Gemini, Groq and OpenRouter rows stay `unknown` / `price_missing` |
| I3 | M10, M11 and M12 values are byte-identical before and after (`known_cost_usd`, `cost_complete`, unknown counts) |
| I4 | The reference amount is never summed with, or shown in place of, a ledger cost. It appears only in its own JSON block and its own UI column |
| I5 | The reference uses only `provider_reported`, consistent usage. Unknown or estimated usage is counted as not priced, never estimated |
| I6 | Decimal only, the same equation and rounding as `PRICING.md` §4 (subsets priced once, reasoning inside output), rounded once, half-even, to 10 places |
| I7 | Exact `(provider, model)` match on the response model, or on the requested model only when the response model is absent; no alias or fuzzy match |
| I8 | Every reference amount carries its catalogue version, tier, `verified_at` and source URL |

## 4. Evidence: verified on 2026-11-19 from the official pages

Prices are per 1M tokens. The pages were read through the page fetcher. No raw-HTML second read was done yet; 9B repeats the read right before the catalogue is committed, as `PRICING.md` §2 did for OpenAI.

| Provider | Model | Input | Cached input | Cache write | Output | Tier | Source |
|---|---|---|---|---|---|---|---|
| `groq` | `openai/gpt-oss-20b` | 0.075 | 0.037 | not listed | 0.30 | on-demand (page lists one price) | https://console.groq.com/docs/model/openai/gpt-oss-20b |
| `gcp.gemini` | `gemini-3.1-flash-lite` | 0.25 (text / image / video) | 0.025 (text / image / video) | not representable (storage is $1.00 / 1M tokens per hour) | 1.50 ("including thinking tokens") | Paid Tier, Standard | https://aistudio.google.com/docs/pricing (redirected from `ai.google.dev/gemini-api/docs/pricing`) |

Page facts that shape the rules:

- **Groq:** the model page shows `$` and no ISO code; `groq.com/pricing` has no price table. Batch and Flex prices are on other pages, which were not read. No effective or last-updated date is shown. The page does not say how reasoning tokens bill. The adapter maps `completion_tokens` to output, with `reasoning_tokens` as a subset ([groq_adapter.py](../../sdk/src/llm_insight_sdk/groq_adapter.py) lines 26–27), so reasoning is priced inside output.
- **Gemini:** the table header says "Paid Tier, per 1M tokens in USD", and the Free Tier column says "Free of charge". Audio input is priced differently (0.50). Batch and Flex are 0.125 / 0.75, and Priority is 0.45 / 2.70. No prompt-length tiers are listed for this model. No last-updated date is shown. The adapter's output equals `candidatesTokenCount + thoughtsTokenCount` ([gemini_adapter.py](../../sdk/src/llm_insight_sdk/gemini_adapter.py) line 40), which matches "including thinking tokens".
- **OpenRouter:** excluded. The only model allowed by the guard is a `:free` variant, whose list price is 0, so a reference would say nothing. Its execution is locked, and no OpenRouter trace exists.
- **OpenAI:** excluded. It already has ledger pricing (ADR-11). A reference column would duplicate that cost and invite the two to be added together.

## 5. Design

### 5.1 Catalogue: code-only, versioned (recommended)

A new module, `backend/app/services/reference_pricing_catalog.py`, holds `REFERENCE_CATALOGUE_VERSION = "reference-2026-11-19"` and one frozen record per row. Each record has `provider`, `model`, `tier`, `currency`, Decimal prices (`None` = not listed), `verified_at`, `source_url` and `basis`.

- **Why not a table:** a `reference_prices` table would need migration 0007, an `ARCHITECTURE.md` §4 contract change, a seed command and append-only triggers. All of that buys nothing for a value that is never stored per row. The SDK's live guard already keeps evidence-bound price data in code (ADR-29).
- **History:** a price change is a new catalogue version in a new commit. The API always applies the current version and says which one it is.

### 5.2 Computation: read time, in the compare query

- The M15 query (`repositories/metrics.py` `model_groups`) gets additional per-group aggregate columns over **eligible** rows: `ref_calls`, `ref_plain_input = Σ(input − cached − cache_write)`, `ref_cached`, `ref_output`. A row is eligible when it is `provider_reported`, its counts are present and consistent (the same `usage_unknown_reason` rule as the ledger), and `cache_write_input_tokens` is 0 or NULL. Ineligible rows are counted by reason: `usage_missing`, `usage_inconsistent`, `cache_write_not_representable`.
- The group's `(provider, model)` selects one catalogue row exactly (I7). No row → `available: false`, reason `not_in_reference_catalogue`.
- `reference_usd = (plain·p_in + cached·p_cached + output·p_out) / 1e6`, computed once per group in Decimal and rounded once (I6). Cached tokens with a `None` cached price → `available: false`, reason `price_not_listed`. Cached tokens are never charged at the full input rate.
- Because each group has one model and one catalogue row, the sum of per-row costs equals the cost of the summed tokens. 9B tests this equality.

### 5.3 API (additive, read-only)

Each group in `GET /api/v1/models/compare` (the M15 response) gains:

```json
"reference_list_price": {
  "available": true,
  "label": "reference_not_billed",
  "currency": "USD",
  "tier": "paid_standard",
  "catalogue_version": "reference-2026-11-19",
  "verified_at": "2026-11-19T00:00:00Z",
  "source_url": "https://aistudio.google.com/docs/pricing",
  "reference_usd": "0.0001234500",
  "calls_priced": 6,
  "calls_not_priced": 0,
  "not_priced_by_reason": {"usage_missing": 0, "usage_inconsistent": 0, "cache_write_not_representable": 0},
  "stale": false
}
```

- The `cost` block is unchanged (I3). The overview gets no reference total, which keeps it from being read as spend (I4).
- Money is a string, as everywhere else in the API.

### 5.4 Dashboard

Models Compare gets a column, "List-price reference", with a "Not billed" badge and a tooltip naming the tier, the verification date and the source. The known-cost column still shows "unknown" for these providers. Trace detail shows nothing new in 9B.

### 5.5 Staleness

A catalogue older than 30 days is shown with `stale: true` and a warning. It is still computed, because a reference is not a charge. For comparison, the ledger's OpenAI guard refuses after 14 days (R83).

## 6. Proposed ADR-52 (status PROPOSED; not in `ADR.md`)

**ADR-52 — Read-time list-price reference for Gemini and Groq, outside the cost ledger (complements ADR-35 and ADR-40; ADR-11 unchanged)**

- **Context:** ADR-35 rejected "computing a paid-tier list price for information" because it "could be read as a charge" and no verified catalogue existed. A verified catalogue now exists (§4), and model comparison needs a token-cost scale for free-tier traffic.
- **Decision:** a versioned, code-only reference catalogue; computation at read time in M15 only; a separate `reference_list_price` block and UI column labelled *Reference · not billed*; invariants I1–I8; Gemini paid Standard text prices and Groq on-demand prices only.
- **Alternatives rejected:** writing the reference into `usage_records` (it would become ledger data); a `reference_prices` table (a migration with no benefit, §5.1); an overview total (it would be read as spend); including OpenAI (it duplicates the ledger) or OpenRouter `:free` (the price is 0).
- **Consequences:** ledger behaviour and stored data are unchanged. The catalogue needs re-verification, and a stale flag shows when it is due. A misread risk remains and is reduced by the label, the badge and the absence of totals.

## 7. Open decisions for the owner

| # | Question | Recommendation |
|---|---|---|
| D1 | Code-only catalogue or a `reference_prices` table? | Code-only (§5.1) |
| D2 | Groq's page shows `$` with no ISO code. Record it as USD, the same reasoning `PRICING.md` §2 used for OpenAI? | Yes, with the basis noted |
| D3 | Spans do not record input modality. Apply Gemini's text price (audio is 0.50), stating the assumption in the tooltip? | Yes. Every current scenario is text-only |
| D4 | Apply the current reference to every event date (it is "today's list price", not "the price on that day")? | Yes, labelled with `verified_at` |
| D5 | Staleness window | 30 days, warn only |
| D6 | Scope: M15 / Models Compare only, nothing in the overview or experiments | Yes. Experiments can follow in a later checkpoint |

## 8. Phase 9B plan (after approval)

1. Re-read both pricing pages, including a raw-HTML read, and record the time in UTC.
2. Write the tests first:
   - catalogue checks: Decimal only, unique `(provider, model)`, HTTPS official hosts, no OpenAI or OpenRouter rows;
   - the computation: subset pricing, reasoning not added, cache write not priced, ineligible reasons, exact model match, the per-row versus summed equality;
   - the invariants I1–I3: a ledger snapshot before and after, with `cost` blocks byte-identical;
   - the API contract;
   - frontend unit tests for the column, badge and tooltip.
3. Implement the catalogue module, the `model_groups` aggregate columns, the `compare` block, the frontend types and schema, and the column.
4. Update `PRICING.md` (a new §9 "Reference list price"), `METRICS_CATALOG.md` (M15 note), `ARCHITECTURE.md` (the `/api/v1` contract) and `SECURITY.md` if needed. Add ADR-52 to `ADR.md` only after approval. Run `verify_phase0.py`.
5. Run the full backend, SDK and frontend suites, typecheck, lint, build and the bundle scan, and report exact counts.

## 9. Risks

- **Misreading (main risk):** someone may treat the reference as spend. Mitigations: the label, the badge, no totals, and the ledger column kept next to it.
- **Price drift:** Groq shows no dates. The stale flag and re-verification on each catalogue version handle this.
- **Modality:** future audio or image traffic would be under-referenced. D3's tooltip states it; a modality attribute would be a later attrmap change.
- **Scope creep:** the reference must not become a back door into ledger pricing. I1–I4 are tested invariants.
