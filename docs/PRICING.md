# LLM Insight — Pricing Catalogue and Cost Engine

**Status:** binding for Phase 2B onward (ADR-11). Implements `ARCHITECTURE.md` §4.6–4.7 and `METRICS_CATALOG.md` M10 without changing a column. Code: `backend/app/services/pricing.py` (engine), `backend/app/services/pricing_catalog.py` (verified data), `backend/app/repositories/pricing.py` (lookup and seed), migration `0002`.

Costs are **API cost estimates** computed from provider-reported usage and a verified price list. They are not an invoice and not billing-grade.

## 1. Principles

- **Never guess a price.** A model, tier, endpoint, modality or tool charge that the catalogue does not list exactly gives `cost_status = unknown` with `cost_unknown_reason = price_missing`, and `cost_usd` stays `NULL`. Unknown is never `0`.
- **Decimal only.** Prices are `NUMERIC(20,10)` in the database and `Decimal` in Python. A binary float is rejected before any arithmetic (`TypeError`).
- **Provider-reported usage only.** Estimated or unknown usage is never priced (`usage_missing`, T17).
- **Price once, at insert.** A usage row is priced in the ingest transaction that first writes it. Rows are never updated afterwards, so a re-export, a retry of the same batch, or a later catalogue change never alters a stored cost (§6).
- **History is append-only.** A stored price is never edited or deleted (§5).

## 2. Verified catalogue `openai-2026-10-24`

Read from the official OpenAI documentation on **2026-10-24 (UTC)**, the pricing page re-checked by 21:38:20Z: the pricing page (`Standard` tab, "Prices per 1M tokens", USD) and each model page, which repeats the same three prices and names the model's "Default snapshot". Prices are USD per 1M tokens.

| Provider | Model | Input | Cached input | Cache write | Output | Effective from | Source |
|---|---|---|---|---|---|---|---|
| openai | gpt-5.4-mini | 0.75 | 0.075 | — | 4.50 | 2026-10-24T21:40:00Z | https://developers.openai.com/api/docs/pricing |
| openai | gpt-5.4-mini-2026-03-17 | 0.75 | 0.075 | — | 4.50 | 2026-10-24T21:40:00Z | https://developers.openai.com/api/docs/models/gpt-5.4-mini |
| openai | gpt-5-mini | 0.25 | 0.025 | — | 2.00 | 2026-10-24T21:40:00Z | https://developers.openai.com/api/docs/pricing |
| openai | gpt-5-mini-2025-08-07 | 0.25 | 0.025 | — | 2.00 | 2026-10-24T21:40:00Z | https://developers.openai.com/api/docs/models/gpt-5-mini |
| openai | gpt-4.1-mini | 0.40 | 0.10 | — | 1.60 | 2026-10-24T21:40:00Z | https://developers.openai.com/api/docs/pricing |
| openai | gpt-4.1-mini-2025-04-14 | 0.40 | 0.10 | — | 1.60 | 2026-10-24T21:40:00Z | https://developers.openai.com/api/docs/models/gpt-4.1-mini |

Every row has `version = openai-2026-10-24`, `currency = USD`, `verified_at = 2026-10-24T21:40:00Z`. "—" is `NULL`: no cache-write price is listed for these models, so a reported cache write makes the call `price_missing`.

**Why these models.** A small set (master plan: no broad catalogue) whose standard text price is **flat**:
- None of them has a long-context row on the pricing page. `gpt-5.4-mini` accepts at most 272,000 input tokens (model page), so the ">272K" long-context rates of the larger GPT-5.4/5.5 models cannot apply to it.
- None of them has a cache-write price. The newest families (`gpt-6-*`, `gpt-5.6-*`, `gpt-5.5`, `gpt-5.4`) have long-context tiers and/or two cache-write durations (30 min, 12 h), which one `model_prices` row cannot represent. They are deliberately **absent**, so their calls are `price_missing` (§8).
- `gpt-5-mini` covers reasoning tokens; `gpt-4.1-mini` is a non-reasoning model.

**Lifecycle (deprecations page, 2026-10-24):** `gpt-5-mini` / `gpt-5-mini-2025-08-07`: shutdown 2026-12-11. `gpt-4.1-mini` / `gpt-4.1-mini-2025-04-14`: shutdown 2027-01-14. `gpt-5.4-mini` has no deprecation notice. A shutdown does not invalidate the rows; they stay in force for historical events.

**Aliases and snapshots are separate rows.** The model is matched exactly (§4). A dated snapshot is listed only because its model page names it as the alias's *default snapshot* under the same three prices; its `source_url` is that model page. Any other snapshot (an older or newer one, a fine-tune `ft:…`) is not in the catalogue and is `price_missing`, even when the requested alias is priced.

**Effective date.** OpenAI does not publish the start date of these prices. The evidence starts at the verification, so `effective_from` is 2026-10-24T21:40:00Z (just after the last check, in UTC) and earlier events are `price_missing`. A first draft used the *local* date (UTC+3) as the start of the day, which claimed hours without evidence; the engine's event-time rule exposed it in the live tests, and the catalogue was re-verified and re-dated before any row was stored. A later price change is a **new catalogue version** with a later `effective_from` (§5); the previous row's validity ends there.

**Re-verification (Phase 2C, 2026-10-24T23:01Z, UTC).** The six rows were checked again against the pricing page, the three model pages and the deprecations page, through the page summarizer **and** raw HTML downloads (2026-10-24T23:01:21Z–23:01:24Z). The raw pricing page now carries the rows as well (first row per model: Standard input / cached input / output). Result: every price, snapshot and unit is unchanged; each model page lists one snapshot (the catalogued default), `gpt-5-mini` and `gpt-4.1-mini` are marked deprecated with unchanged shutdown dates, and the GPT-5.4 mini page repeats the 10 % regional-endpoint uplift that §3 already excludes. Tiers on the page: Standard, Batch, Flex, Fast (renamed from Priority on 2026-07-30) and Ultrafast; only Standard (`service_tier = default`) is priced. The page shows prices with `$` and no ISO currency code; `USD` is recorded because the page is the US-dollar price list. **Nothing was changed:** no new version, no re-dated row, nothing deactivated.

**Verification time is not historical validity.** `verified_at` (2026-10-24T21:40:00Z) is when the evidence was read; `effective_from` claims validity only from that instant (OpenAI publishes no start dates), and the re-check proves the same rates were still published at 23:01Z. No price before 21:40Z is claimed or guessed; earlier events stay `price_missing`.

**Re-verification (Phase 2 closure, 2026-10-25T06:38Z, UTC).** Checked again before seeding: the pricing page, the three model pages and the deprecations page through the page summarizer, and the pricing and model pages downloaded raw (2026-10-25T06:38:13Z–06:38:16Z, HTTP 200). The raw pricing page's first row per model is 0.75 / 0.075 / 4.5, 0.25 / 0.025 / 2 and 0.4 / 0.1 / 1.6; each model page lists only the catalogued default snapshot with the same three prices; no long-context row or cache-write price; shutdown dates and replacements unchanged; `gpt-5.4-mini` has no deprecation notice. **Nothing was changed.**

**Seeding:** `python -m app.cli seed-prices --dry-run`, then `python -m app.cli seed-prices` (backend). It inserts missing rows, reports identical rows as unchanged, and exits 1 writing nothing if a stored row with the same identity differs. It never reprices usage rows. **The local development database was seeded on 2026-10-25** with user approval limited to that database (`reports/phase-2-final.md` §3): 6 rows inserted, a second run inserted 0 and left 6 unchanged, the usage ledger was empty and untouched, and no backfill exists.

## 3. Applicability: what the stored price covers

A stored row is the **Standard-tier text-token price on the global endpoint**. The engine applies it only when the span shows that this is how the call was billed. For `gen_ai.provider.name = openai` (rule table `APPLICABILITY` in `pricing.py`):

| Condition (span attributes, `llminsight.attrmap.v1`) | Otherwise |
|---|---|
| `openai.response.service_tier = default` (the tier that actually served the call; the request tier alone is not proof) | `price_missing` (Flex, Batch, Fast/`priority`, `scale`, `auto`, or not reported) |
| `server.address = api.openai.com` | `price_missing` (regional `us.`/`eu.`/`ae.api.openai.com` endpoints carry a 10 % uplift for models released on or after 2026-03-05; FedRAMP endpoints too) |

Not representable, therefore `price_missing` by construction (no matching row): long-context tiers, cache-write durations, audio/realtime/image-generation models, search-preview models and other tool-call charges, fine-tuned models. The catalogue models are text models; image input to them is billed as input tokens (the pricing page lists no separate image rate for them). OpenAI's Chat Completions response reports `service_tier` reliably only when the request sets it, so the SDK adapter documents that callers who want a computed cost send `service_tier="default"`.

A provider without an entry in `APPLICABILITY` has no billing mode beyond its stored price; today that is only the synthetic test providers. Adding a real provider requires adding its rule.

## 4. Cost equation, selection and rounding

For one usage row (`app.services.pricing.assess`), in this order:

1. **Usage** — `cached_input_tokens + cache_write_input_tokens > input_tokens`, `reasoning_output_tokens > output_tokens`, or a malformed count → `usage_inconsistent`. Missing `input_tokens`/`output_tokens`, or `usage_source ≠ provider_reported` → `usage_missing`.
2. **Applicability** — §3, else `price_missing`.
3. **Selection** — model = `response_model`, or `requested_model` only when the response model is absent. The price is the row with exactly that `(provider, model)` and the **latest `effective_from ≤ started_at`** (the span's event time, never the ingest time). No alias, prefix, case-folding or fuzzy match. None → `price_missing`.
4. **Cost** (USD):

   `cost = ((in − cache_read − cache_write) · p_in + cache_read · p_cached + cache_write · p_cache_write + out · p_out) / 1 000 000`

   - Cache read and cache write are **subsets of input** and are priced once, at their own rates (T20).
   - Reasoning tokens are **part of output** and are priced once, at the output rate (T21). They are never added to `out`.
   - An absent subset counts as 0 inside the formula only; the stored column stays `NULL`.
   - A non-zero term whose price is `NULL` makes the row `price_missing`. Cached tokens are never priced at the full input rate.

**Rounding policy.** The sum is computed exactly in `Decimal` (60-digit context, so BIGINT counts times `NUMERIC(20,10)` prices never round), divided by 10^6, and rounded **once**, **half-even** (banker's rounding), to the 10 decimal places of `cost_usd NUMERIC(20,10)`. Terms are never rounded individually. With the verified catalogue every per-token price has at most 10 decimals, so real costs are exact; rounding only affects synthetic test prices. A cost above the column maximum (9 999 999 999.9999999999) can only come from implausible counts and is `usage_inconsistent`, never an ingest error. A computed cost of exactly 0 (all reported counts are 0) is a known zero, not unknown.

**Stored on a computed row:** `cost_usd`, `cost_status = computed`, `price_version` (= the row's `version`) and `model_price_id` (the exact row applied, FK `ON DELETE RESTRICT`, T22). The table checks of `ARCHITECTURE.md` §4.6 hold for every outcome.

**Worked example (T19):** `gpt-5.4-mini-2026-03-17`, 1 200 input of which 1 000 cached, 300 output of which 200 reasoning: (200 · 0.75 + 1 000 · 0.075 + 300 · 4.50) / 10^6 = (150 + 75 + 1 350) / 10^6 = **0.0015750000** USD.

## 5. Validity periods and append-only history

- A price is valid from its `effective_from` until the next `effective_from` of the same `(provider, model)`. `UNIQUE(provider, model, effective_from)` leaves no instant with two candidates, so there is no ambiguous overlap. `UNIQUE(provider, model, version)` keeps versions distinct.
- Migration `0002` installs `model_prices_append_only()`:
  - `UPDATE`, `DELETE` and `TRUNCATE … CASCADE` on `model_prices` fail (`restrict_violation`); a plain `TRUNCATE` is already refused by PostgreSQL because `usage_records` references the table;
  - an `INSERT` fails (`check_violation`) when the same `(provider, model)` already has a row starting **later**: a new row may only close the latest period, never rewrite a past one. Inserts per `(provider, model)` are serialized with a transaction advisory lock.
- `usage_records.model_price_id` → `model_prices(id) ON DELETE RESTRICT`.
- Downgrading `0002` is refused while any usage row references a price.
- The table owner can still disable the triggers. Doing so is a deliberate, separately approved administrative action (§6), never part of normal operation.

## 6. Idempotency, no silent repricing, and the future backfill workflow

- A usage row is written once with `ON CONFLICT DO NOTHING` and never updated. Re-exporting a span changes nothing (T04, T23). A retry attempt is a new physical call and gets its own row and its own price (T14).
- Adding a catalogue version **does not reprice** existing rows: neither computed rows (their price is kept) nor `unknown` rows (they stay unknown). Only spans first ingested after the new row exists are priced with it (T22).
- `seed-prices` never touches `usage_records`.

**Repricing / backfill (designed, not implemented, needs separate authorization).** If historical rows must ever be repriced — for example `price_missing` rows from before a verified price, or a corrected price — the workflow must be:

1. **Authorization:** an explicit user approval naming the catalogue version, the project, the event-time window and the reason; plus an ADR and a migration for the audit table below. The ingest path never does this.
2. **Scope:** only rows whose outcome can change for the stated reason. `usage_missing` and `usage_inconsistent` rows are never repriced (their usage, not the price, is the problem).
3. **Dry run first:** compute the proposed outcome for every selected row with the same engine and write a report only: row counts, per-model sums before and after, and rows that would stay unknown. Nothing is written to the ledger.
4. **Audit, append-only:** each applied change is a row in a new revision table (usage row id, previous and new cost columns, catalogue version, run id, approver, timestamp, reason) in the same transaction as the change. Preferably `usage_records` keeps its original values and the API shows the latest revision labelled "repriced"; the original first-write cost stays visible.
5. **Rollback:** a run is reverted by its run id (its revisions are superseded or removed), which restores the original values exactly. No run can delete a price row.

## 7. Synthetic pricing fixtures

Tests insert their own prices with provider `synthetic.test` (or a per-test variant), models `synthetic-*`, versions `synthetic-*` and `source_url = https://example.invalid/synthetic`. They exist only in disposable `*_test` databases, are never seeded by the CLI, and never use a real provider name. Offline OpenAI response fixtures in the SDK tests use the verified catalogue rows and are labelled `synthetic` provenance: their usage numbers are made up, the prices are real.

## 8. Limitations

- **Staleness (R28):** the catalogue is correct as of its verification date only. If OpenAI changes a price before a new version is added, new calls are priced at the old rate. Re-verify before any cost-relevant demo.
- **Coverage (R29):** most current OpenAI models, every non-standard tier and every regional endpoint are `price_missing`. This is deliberate (no guessing), and the dashboard must show unknown-cost counts next to every known cost (M11).
- **Not an invoice:** discounts, credits, taxes, minimums, data-sharing programmes and organisation-specific pricing are not modelled.
- **Client-asserted attributes:** tier, endpoint, model and usage come from the instrumentation (R16). A misreporting client gets a wrong estimate; provenance and `usage_source` are stored next to every cost.
