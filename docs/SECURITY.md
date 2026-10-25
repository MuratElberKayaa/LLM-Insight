# LLM Insight — Security & Privacy (MVP)

Scope: a local, single-project demo (ADR-08). Nothing here is a claim of production-grade security.

## 1. Data minimization (ADR-07, ADR-19)

- **Metadata only.** Prompts, completions, system instructions, tool definitions and prompt variables are never persisted from telemetry. This holds even when a client sends them.
- **Allowlist.** Only the attributes listed in `TELEMETRY_CONTRACT.md` §3 are stored. Everything else is dropped before the DB write and only counted. An always-deny list overrides any allowlist mistake.
- **No events stored.** Exception messages and stack traces are dropped; only `exception.type` is kept, and only when it is a type identifier.
- **No status descriptions stored** (Phase 1C, R21). OTLP `Status.message` is never persisted (`spans.status_message` is always `NULL`), and `error.type` / `exception.type` are kept only when they match an identifier pattern (no spaces or control characters). Errors are described by the span status plus that identifier.
- **Credential keys** are recognised by explicit classification (credential stems, credential words such as singular `token`, and qualified `tokens`/`key`), not by substring. Token *counters* such as `gen_ai.request.max_tokens` are kept (`TELEMETRY_CONTRACT.md` §3).
- **String-value safety (R08, Phase 1 final).** Every persisted untrusted string must pass `TELEMETRY_CONTRACT.md` §3.1, checked on the full value before any truncation. That covers span name, scope name/version, model, provider, operation, error type, prompt name/version, route, host, versions, the resource labels and the opaque IDs.
  - Fields with a known format have an explicit pattern and maximum length.
  - Unsafe characters (control, bidi/zero-width, separators) and obvious credential formats are refused everywhere: `sk-`/`lli_` keys, cloud and VCS tokens, JWTs, private keys, `Bearer`/`Basic` values, URL user-info and `secret=` assignments.
  - E-mail addresses and phone/ID/card/IBAN-like numbers are refused in identifiers and labels.
  - A refused value is dropped, or the span name becomes `redacted_span`; it is counted, and it never appears in the DB, logs, error bodies or API responses.
  - **This is a heuristic.** It catches obvious formats only and cannot guarantee that no secret or personal data is ever stored. Instrumentations must keep secrets and content out of metadata.
- **Truncation.** Free-text labels (span name, scope name, service name/namespace, environment) are capped at 256 chars (scope version 64) after the checks; arrays keep 16 items. Identifiers are dropped instead of truncated when over their bound.
- **Test-case content.** Test-case input is the only free text stored. It is entered by a human, must be synthetic or explicitly redacted, and passes server-side redaction before insert (Phase 4, T35). Redaction targets at minimum email addresses, phone numbers, IBAN/card-like digit runs and API-key-like tokens. Redaction is best effort and not a guarantee, so content must still be synthetic.
- **Review text.** Failure Review `evidence` and `hypothesis` are also human-written free text, and pass the same redaction before insert. Reviewers are told not to paste raw prompts.

## 2. Credentials

| Secret | Holder | Storage / handling |
|---|---|---|
| Project ingest key `X-Project-Key` | Instrumented app (`OTEL_EXPORTER_OTLP_TRACES_HEADERS`) | Format `lli_<prefix>_<secret>`, at least 256-bit random secret. DB stores `prefix` plus `key_hash = HMAC-SHA256(LLMI_KEY_PEPPER, key)`. Lookup is by prefix with a constant-time compare (`hmac.compare_digest`). It can be revoked with `revoked_at`. The raw key is printed once by the CLI init command. |
| `LLMI_KEY_PEPPER` | Backend env only | Random value, at least 32 bytes. Rotating it invalidates all keys (documented). |
| `LLMI_ADMIN_TOKEN` (CR-06) | Backend env plus Next.js **server** env (`LLMI_API_TOKEN`) | Sent as a Bearer token on `/api/v1/*` and compared in constant time. It is never in a `NEXT_PUBLIC_*` variable, the client bundle, cookies, `localStorage` or HTML (T42). |
| `OPENAI_API_KEY` | Demo app env only, real mode only | Stays inside the application's `openai` client. The SDK adapter never reads, records or exports it, nor any request header (T15; Phase 2B tests with a fake key check the raw OTLP bodies, the DB, the API and the logs). No test uses a real key. |
| `POSTGRES_PASSWORD` | Compose env | Local only. |

**Logging rules:**
- Logs never contain the `X-Project-Key` or `Authorization` header values, raw keys, the pepper, or request bodies (T44).
- Auth failures log only the key prefix (if parsable), the client address, and a reason code. `/api/v1` auth failures log the reason code and client address only.
- Error responses use generic messages, and missing, unknown and revoked keys get the same `401`. `/api/v1` errors carry a fixed code and message; a `422` names the invalid parameters but never echoes their values (FastAPI's default body would).
- The uvicorn access log records the request line, including query strings (`project`, `model`, `cursor`). The query string never carries credentials; the token travels only in the `Authorization` header, which is not logged.

**Test output (Phase 2C, R33):** a failing test must never print a local credential. Fixtures hand out credential-bearing database URLs as `RedactedUrl` (repr hidden), and report hooks in both suites (`backend/tests/redaction.py`, an identical copy in `sdk/tests/`) replace every local secret (from `.env` and the environment) and the password of any `scheme://user:password@` URL in failure text, skip reasons and captured stdout/stderr/log. This does not cover `pytest -s` or live logging. Never run `docker compose config` without `--quiet`: it prints the resolved `POSTGRES_PASSWORD`.

**Rotating the local PostgreSQL password** (done in Phase 2C after the R33 exposure): generate a new value, stage the new `.env` (git-ignored), set the role password with `ALTER ROLE … PASSWORD '<SCRAM-SHA-256 verifier>'` (computed client-side, so the plaintext never reaches the server or its logs), check that the new login works and the old one fails, then replace `.env` atomically and recreate the container with `docker compose up -d --wait postgres` (same named volume, so no data is lost). `POSTGRES_PASSWORD` itself only matters when the volume is first initialised. Never print the value.

Only `.env.example` with placeholders is committed. `.gitignore` excludes `.env` and `.env.*`. Phase 5 adds a secret scan.

## 3. Request limits

- `/v1/traces` limits, all configurable:
  - wire body 4 MiB
  - decompressed 16 MiB, enforced incrementally during gzip decompression (zip-bomb guard)
  - 2048 spans per request
  - all three return `413`
  - body-read deadline 10 s after authentication (`LLMI_OTLP_BODY_READ_TIMEOUT_S`, Phase 1C, R22): `408` and the connection is closed. The deadline covers the body only; slow request headers and connection exhaustion are server-level concerns that uvicorn does not bound here (`reports/phase-1c.md` §5)
- `/api/v1/*`:
  - `limit` ≤ 200
  - date window ≤ 31 days (default: the last 31 days)
  - at most 2000 spans and 2000 usage rows per trace detail
  - read-only `REPEATABLE READ` transactions
  - ORM-parameterized SQL only, with no string-built SQL

## 4. Rate limiting (CR-08)

- There are three in-process token buckets. Over-limit requests get `429` with `Retry-After` (T43).
  - **Auth-failure:** keyed by client address, default 30 failures/min (`LLMI_RATE_LIMIT_AUTHFAIL_PER_MIN`). It is checked before key verification, which slows key guessing.
  - **Ingest:** keyed by the **verified** key ID after auth (default 20 req/s, burst 40). An unauthenticated caller cannot drain a legitimate key's bucket.
  - **`/api/v1`:** keyed by token and client address (default 10 req/s, burst 20). Implemented in Phase 1C: with one verified admin token it is keyed by client address after authentication. `/api/v1` auth failures consume the auth-failure bucket shared with ingest.
- **Limit of this design:** the state is per process. With N workers the effective limit is N× the configured value, and it resets on restart. The MVP runs a **single uvicorn worker**. This control is not a defense against distributed abuse.

## 5. Web / UI

- React escaping only; no `dangerouslySetInnerHTML` for telemetry-derived strings (T41). Enforced by ESLint (`react/no-danger`) and a static test.
- All backend calls are made from Next.js server components (Phase 3A: `frontend/lib/server/backend.ts` only). There are no route handlers and no Server Functions, so the dashboard is not a proxy to the backend (`ARCHITECTURE.md` §5.3).
- **Token handling (Phase 3A, T42).** `LLMI_API_TOKEN` lives in `frontend/.env.local` (git-ignored, written by `scripts/dev/init_frontend_env.py` without printing it), is read only in `lib/server/config.ts` (a `server-only` module), is sent only as a bearer header to a loopback backend origin (other hosts are refused by config validation), and never in a URL, log line, error object, HTML, RSC payload, client bundle, cookie or browser storage. The Next.js process receives only the three dashboard variables, not the pepper or the database URL. Verified by the build-output scan `npm run check:bundle` (with a mutation check), a live E2E scan of every response body, and static source tests.
- **CSP (Phase 3A).** Per-request nonce set in `frontend/proxy.ts`: `script-src 'self' 'nonce-…' 'strict-dynamic'` (dev adds `'unsafe-eval'` and the HMR websocket), `default-src 'self'`, `object-src 'none'`, `base-uri 'self'`, `form-action 'self'`, `frame-ancestors 'none'`. `style-src` allows `'unsafe-inline'` because the chart library writes inline `style` attributes, which a nonce cannot authorize; no telemetry string is placed in a style. Also `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, COOP/CORP `same-origin`, a restrictive `Permissions-Policy`, and `Cache-Control: no-store` on pages.
- **Loopback guard (Phase 3A).** The dashboard binds to `127.0.0.1:3001`. Requests whose `Host` header is not `127.0.0.1`, `localhost` or `[::1]` get `421`, so a DNS-rebinding page cannot read it through a foreign hostname; methods other than GET/HEAD get `405`.
- **Trace explorer (Phase 3B).** Backend paths pass a runtime allowlist; a trace id reaches the backend only as 32 lowercase hex, and an invalid one is neither echoed nor sent. URL filters are parsed strictly and re-serialized; the detail page's "back" link rebuilds only canonical list parameters from `?list=`, so it cannot become an open redirect or carry other parameters. Telemetry labels (span and service names, resource and span attributes, scope, `error.type`, models, providers) are rendered as React text only; integers above 2^53 are kept exact. No route handler or Server Function was added. Live E2E ingests hostile labels through `/v1/traces` and checks they render as text without executing (T41); a mutated build that renders a span name as HTML fails that test.
- **Project context (Phase 3C).** `GET /api/v1/projects` (admin token, bounded to 200, id/name/creation time only, no key material) feeds a project selector. Each page reads exactly one project from `?project=` (validated positive BIGINT) or the configured default; an invalid id is refused before any request and never replaced by another project; an unknown one is the existing indistinguishable `404`. The backend client requires an explicit project scope per call (the listing is the only unscoped path) and refuses a `project` smuggled in other parameters; overview/compare responses that echo a different project are rejected. Switching is a full page load that drops cursors and selected spans. Project names are admin-chosen free text and are rendered as text only (hostile names tested live). This adds no permission: the single admin token already reads every project (CR-06).
- The dashboard has **no user login**. That is acceptable only because it is bound to `127.0.0.1`. Any other bind needs the review in §7.
- Next.js anonymous telemetry is disabled per process (`frontend/scripts/next.mjs`), not through the global Next.js config.

## 6. Cost safety

- Tests and CI use the mock provider only (`LLMI_PROVIDER_MODE=mock`).
- **OpenAI adapter tests are offline (Phase 2B):** responses come from SYNTHETIC fixtures behind an `httpx2.MockTransport`, and each test blocks every non-loopback name resolution and connection, so a request that escaped the fake transport fails the test instead of reaching OpenAI. A test proves the guard stops the official client. `provenance="real"` is refused for a mock transport.
- **Cost figures are estimates** from provider-reported usage and the verified catalogue (`PRICING.md`), never an invoice; unknown cost is `NULL`.
- Real calls need `LLMI_ALLOW_REAL_LLM=true`, a present `OPENAI_API_KEY`, a `budget_usd` per experiment, and explicit user approval per run.

## 7. Threat notes (local demo)

| Threat | Mitigation in MVP | Residual risk |
|---|---|---|
| Sensitive prompt / PII leaks into the DB | Allowlist, always-deny list, no events, no status messages, per-field formats, credential and personal-data value checks, unsafe-character rejection (R08) | Heuristics miss non-obvious secrets (an unprefixed random token in a model-shaped value) and names or free-form personal data in labels (R08, mitigated not eliminated) |
| Slow or stalled request bodies | Body-read deadline (`408`, connection closed) after auth; nothing is written before the body is complete | Slow headers and connection exhaustion are not bounded by the app (R24); loopback bind |
| Key theft from DB dump | HMAC with an env pepper | DB plus env both leaked compromises the keys; revocation is manual |
| Cross-project read | Mandatory `project` scope; another project's trace, an unknown trace and an unknown project get one byte-identical `404` (no `403` that would confirm existence) (T28) | Single admin token can read all projects (single-project MVP) |
| Ingest flooding / oversized payload | Body, decompression and span limits plus the rate limiter | Per-process limiter only |
| Duplicate / replayed exports inflating cost | `UNIQUE(project_id, canonical_call_key)`, one usage row per span, `ON CONFLICT DO NOTHING`, and response-ID-first keys | A failed attempt reported by two instrumentations is not deduplicated (no response ID). Its cost is unknown, so the known total is unaffected |
| Ingest key guessing | Auth-failure limiter per client address, generic `401`, 256-bit keys | Per-process limiter only |
| Mislabelled provenance (fake "real") | Server downgrade of `real` without a response ID (a response ID never upgrades a claim); mixed and unknown never counted as real; the API returns `provenance_verified: false` | Provenance is client-asserted, not cryptographically verified |
| Error responses leaking data | `Status.message` / `BadRequest` carry field paths and rule text only, never values | — |
| Secret in client bundle | Server-only env, `server-only` modules, T42 bundle and live-response scans (Phase 3A) | Developer error with `NEXT_PUBLIC_*`, caught by ESLint, the static test and the scan |
| Dashboard used as a proxy or read via DNS rebinding | No route handlers or Server Functions; fixed backend paths and validated parameters; loopback `Host` check (`421`); GET/HEAD only (Phase 3A) | A local process on the same machine can still read the dashboard (no login; loopback trust model) |
| Accidental public exposure | Bind to 127.0.0.1, no public ingress | Changing the bind is a manual act and needs a new review |
| Malicious attribute content (XSS) | Escaped rendering only; identifier fields (models, providers, `error.type`) are format-checked at ingest, free-text labels are escaped in the UI; live hostile-label E2E (T41, Phase 3B) | `style-src 'unsafe-inline'` remains (R38); no telemetry string is placed in a style |

**Standing limitations (Phase 1 closure):**
- **Public deployment is NOT authorized.** The backend is loopback-only; any other bind needs the review below.
- Client-supplied provenance is an assertion, not proof of provider origin (`provenance_verified: false`).
- Unknown cost stays `NULL`, never `0` (ADR-11).
- In-process counters and rate limits reset on restart (R23, CR-08).
- Slow request headers and connection exhaustion are not fully mitigated (R24).
- Protocol: `408` for an incomplete body after the deadline (OTLP exporters may not retry it), `413` for size/span limits, `503` for temporary unavailability. There is no `403` with the local all-project admin token, and a cross-project trace lookup returns the same `404` as a missing trace.

**Before any public deployment**, the following are mandatory and out of MVP scope:
- real user auth (session/cookie plus CSRF review)
- TLS
- per-project authorization
- a distributed rate limiter
- audit logging
- a KVKK/GDPR review of retention and deletion

Retention defaults to 14 days (`LLMI_RETENTION_DAYS`). The retention job is P1; until then, deletion is a manual SQL/CLI step, documented in Phase 5.
