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
- **Phase 4B implementation (`backend/app/services/redaction.py`).** Free text (`sanitized_input`, `evidence`, `hypothesis`) is **masked** with the ingest heuristics (`[REDACTED:credential|email|iban|phone|number]`; 10+ digit runs count as numbers) and the response reports the masked counts per field; length limits apply to the masked text. Structured assertions (`expected_output`, `rule`) are **refused** (`422`) when a string looks like a credential or personal data, or an integer has 10+ digits, because masking would change what a test asserts. Labels (`created_by`, `approved_by`, dataset names) must be single-line and are refused when they carry contact data or a secret. Invisible/bidi/control characters (except tab and newline in free text) are refused everywhere. Nothing is copied from telemetry into a review or test case; the person types the content. Redaction remains best effort (it catches obvious formats only).
- **Deterministic evaluation (Phase 4C, ADR-23, `ARCHITECTURE.md` §5.9).** User-supplied rule content is untrusted. Regex rules run only on RE2 (`google-re2`: linear time; backreferences, lookaround and possessive/atomic groups refused, never handed to another engine; pattern ≤ 1000 characters, program ≤ 1500 instructions, 2 MiB; RE2's own error logging is off so a pattern never reaches a log). JSON Schema rules use draft 2020-12 only, with a keyword allowlist applied at every depth: `pattern`/`patternProperties` (which `jsonschema` would run on Python's backtracking `re`), `format` and `content*` (not asserted), `$id`, anchors and dynamic references are refused; `$ref` must be `#/$defs/<name>`, acyclic and expansion-bounded; schemas are meta-validated without a format checker; every validator gets an explicit registry that cannot retrieve, so no remote document is ever fetched (tested with sockets and `urlopen` blocked); each evaluation has a keyword budget. Outputs are bounded (32 KiB, depth 32, 10,000 values, bounded numbers). No `eval`/`exec`/dynamic import anywhere in the backend (static test). Evaluation details never contain output or expected values, and model output is never stored.
- **Trace diagnostics (Phase 4A, `ARCHITECTURE.md` §5.6).** Derived on request from stored metadata only and never persisted. A result echoes a fixed set of allowlisted, already-validated fields (IDs, status, `error.type`, models, provider, usage counts, validation rule); it never returns span names, raw attribute objects, response-ID values or anything outside that set, so even a malformed row could not leak content through it. All prose is constant catalogue text: no stored value is interpolated into a sentence. No model is called and no prompt is reconstructed.

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
  - at most 2000 spans and 2000 usage rows per trace detail, and the same bound for trace diagnostics (Phase 4A: four statements per request, at most 100 findings with 25 evidence spans each)
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
- **Dashboard writes (Phase 4B, `ARCHITECTURE.md` §5.8; recorded as ADR-22 in Phase 4C).** ADR-22 supersedes the GET-only restriction for exactly these four writes; any other write needs a new ADR. There is no authenticated identity: `created_by`/`approved_by` are self-declared and must not be presented as verified. Four Server Actions (review, decision, dataset, test case), each re-validating its form and calling one allowlisted backend write with exactly one method; still no route handlers and no generic proxy. CSRF defence without cookies: `proxy.ts` accepts `POST` only on the three form pages and only with an `Origin` equal to the loopback host, `Sec-Fetch-Site: same-origin` when sent, and the `Next-Action` header (which a cross-site form cannot send); Next.js also checks `Origin` against `Host`. Action bodies are capped at 128 KB, backend write bodies at 64 KiB. Submit buttons are disabled while pending; nothing writes on page load. Verified by unit, static and live E2E tests (cross-site, origin-less and header-less POSTs get `403`).
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
- **Experiments (Phase 4C, ADR-25):** only `mode: "mock"` runs (a scripted provider; the backend does not even have the `openai` package installed, and a static test forbids importing it). `mode: "real"` is validated and then refused (`409`), writing nothing. With `budget_usd`, estimated costs are checked before anything runs (unknown → refused) and a run-time guard stops further calls. Experiment costs are labelled synthetic estimates; nothing is spent and no usage-ledger row is written. An integration test runs an experiment with every non-loopback connection blocked.

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
| Dashboard used as a proxy or read via DNS rebinding | No route handlers; fixed backend paths and validated parameters; loopback `Host` check (`421`); GET/HEAD, plus Phase 4B Server Action POSTs only on three pages and four fixed writes | A local process on the same machine can still read the dashboard (no login; loopback trust model) |
| Accidental public exposure | Bind to 127.0.0.1, no public ingress | Changing the bind is a manual act and needs a new review |
| Cross-site request forgery against the Phase 4B forms | `Origin` required and equal to the loopback host, `Sec-Fetch-Site` same-origin, `Next-Action` header required, Next.js Origin/Host check; POST only on the form pages | A process on the same machine can still submit (loopback trust model, no login, R54) |
| Accidental or automatic creation of reviews/test cases | Only the explicit POST/PATCH handlers write; static test that ingest, diagnostics and reads never touch these tables; approval tick plus `approved: true`; one case per (dataset, review); duplicate open review `409` | `created_by` / `approved_by` are self-declared, not authenticated (R55) |
| Catastrophic regex backtracking (ReDoS) in a stored rule | RE2 only (linear time), refused backtracking constructs, pattern/program/memory/input bounds; JSON Schema regex keywords refused (Phase 4C) | Worst case at the caps ~0.5 s per evaluation (NFA fallback, measured); an experiment evaluates at most 400 results (R60) |
| A schema used to fetch remote documents (SSRF) or loop | Local `#/$defs` references only, no `$id`, explicit no-retrieval registry, acyclic and expansion-bounded references, keyword budget (Phase 4C) | Recursive schemas are not expressible (R61) |
| A review decision changed after a golden case was built on it | Decision frozen once cited (API row locks plus trigger), conversion only for supported/rejected hypotheses, one case per (dataset, review) in the database (ADR-24) | Actors are self-declared; who changed what is not recorded (R55, D8 deferred) |
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
