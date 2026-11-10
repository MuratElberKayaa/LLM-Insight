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
| `OPENAI_API_KEY` | Demo app env only, real mode only | Stays inside the application's `openai` client. The SDK adapter never reads, records or exports it, nor any request header (T15; Phase 2B tests with a fake key check the raw OTLP bodies, the DB, the API and the logs). No test uses a real key. **Phase 6A:** the live guard checks its presence only (never its value) and its presence alone enables nothing (ADR-29); the live runner passes it to the client and pins `https://api.openai.com/v1`; it is set by the operator in their own shell, never pasted into a chat, file or document. |
| `POSTGRES_PASSWORD` | Compose env | Local only. |

**Logging rules:**
- Logs never contain the `X-Project-Key` or `Authorization` header values, raw keys, the pepper, or request bodies (T44).
- Auth failures log only the key prefix (if parsable), the client address, and a reason code. `/api/v1` auth failures log the reason code and client address only.
- Error responses use generic messages, and missing, unknown and revoked keys get the same `401`. `/api/v1` errors carry a fixed code and message; a `422` names the invalid parameters but never echoes their values (FastAPI's default body would).
- The uvicorn access log records the request line, including query strings (`project`, `model`, `cursor`). The query string never carries credentials; the token travels only in the `Authorization` header, which is not logged.

**Test output (Phase 2C, R33):** a failing test must never print a local credential. Fixtures hand out credential-bearing database URLs as `RedactedUrl` (repr hidden), and report hooks in both suites (`backend/tests/redaction.py`, an identical copy in `sdk/tests/`) replace every local secret (from `.env` and the environment) and the password of any `scheme://user:<password>@` URL in failure text, skip reasons and captured stdout/stderr/log. This does not cover `pytest -s` or live logging. Never run `docker compose config` without `--quiet`: it prints the resolved `POSTGRES_PASSWORD`.

**Rotating the local PostgreSQL password** (done in Phase 2C after the R33 exposure): generate a new value, stage the new `.env` (git-ignored), set the role password with `ALTER ROLE … PASSWORD '<SCRAM-SHA-256 verifier>'` (computed client-side, so the plaintext never reaches the server or its logs), check that the new login works and the old one fails, then replace `.env` atomically and recreate the container with `docker compose up -d --wait postgres` (same named volume, so no data is lost). `POSTGRES_PASSWORD` itself only matters when the volume is first initialised. Never print the value.

Only `.env.example` with placeholders is committed. `.gitignore` excludes `.env` and `.env.*`. **Secret scan (Phase 5B):** `scripts/qa/secret_scan.py` searches every blob reachable from every ref, every commit and tag message and the working files for the exact local secret values (read from the git-ignored env files, never printed) and for credential patterns; a control repository with planted values must be detected first (`reports/phase-5b.md` §4). CI runs the pattern part as a fail-closed history gate without local values (§9). The dashboard build is scanned by `npm run check:bundle` (T42).

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
- **Experiment run (Phase 4D, ADR-26, `ARCHITECTURE.md` §5.10).** A fifth and only additional write: `submitExperiment` → `POST /api/v1/experiments`, accepted by `proxy.ts` on `/experiments/new` only with the same origin checks (its UI entry point; the page does not bind the action, ADR-27). The mode is the constant `mock` in the form parser (a crafted `mode=real` field is ignored, tested), no mode control is rendered, and an options response offering another mode is refused by the decoder; the backend refuses `mode: "real"` regardless. A run starts only from the confirmed form submission (confirmation box plus start button; a synchronous in-flight guard ignores a double click). Reads of the new pages go through the existing allowlist (`experiments`, `experiments/<id>`, `experiment-options`); ids are format-checked before any request; another project's experiment or dataset is the backend's indistinguishable `404` (live E2E). Stored strings (dataset names, prompt versions, models, evaluation details, test-case content) render as React text only.
- **Token handling (Phase 3A, T42).** `LLMI_API_TOKEN` lives in `frontend/.env.local` (git-ignored, written by `scripts/dev/init_frontend_env.py` without printing it), is read only in `lib/server/config.ts` (a `server-only` module), is sent only as a bearer header to a loopback backend origin (other hosts are refused by config validation), and never in a URL, log line, error object, HTML, RSC payload, client bundle, cookie or browser storage. The Next.js process receives only the three dashboard variables, not the pepper or the database URL. Verified by the build-output scan `npm run check:bundle` (with a mutation check), a live E2E scan of every response body, and static source tests.
- **CSP (Phase 3A).** Per-request nonce set in `frontend/proxy.ts`: `script-src 'self' 'nonce-…' 'strict-dynamic'` (dev adds `'unsafe-eval'` and the HMR websocket), `default-src 'self'`, `object-src 'none'`, `base-uri 'self'`, `form-action 'self'`, `frame-ancestors 'none'`. `style-src` allows `'unsafe-inline'` because the chart library writes inline `style` attributes, which a nonce cannot authorize; no telemetry string is placed in a style. Also `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, COOP/CORP `same-origin`, a restrictive `Permissions-Policy`, and `Cache-Control: no-store` on pages.
- **Loopback guard (Phase 3A).** The dashboard binds to `127.0.0.1:3001`. Requests whose `Host` header is not `127.0.0.1`, `localhost` or `[::1]` get `421`, so a DNS-rebinding page cannot read it through a foreign hostname; methods other than GET/HEAD get `405`. **Phase 4 final (R68):** the guard runs on every path except `/_next/static/*` and `/_next/image`, which never dispatch a Server Action; `/favicon.ico` was excluded before, and a Server Action POST there ran without these checks (fixed, static and live replay tests). Next.js dispatches any of the five action ids on any write page; the five-action set and the backend allowlist, not page binding, keep the writes narrow (R69). **Phase 5A (ADR-27):** write pages are UI entry points, not an authorization boundary; the boundary is the loopback bind and `Host` check, server-side validation in the action and the backend, the same-origin/CSRF checks, the server-side token, project scope, mock-only execution and bounded processing, none of which identifies a human. **R71:** the exclusions are whole path segments (`/_next/static/…`, exactly `/_next/image`); the previous prefix match also excluded `/_next/static`, `/_next/staticx`, `/_next/image/x` and similar paths, where a same-origin action POST ran unguarded (fixed; static test, live replay and raw-path probe).
- **Trace explorer (Phase 3B).** Backend paths pass a runtime allowlist; a trace id reaches the backend only as 32 lowercase hex, and an invalid one is neither echoed nor sent. URL filters are parsed strictly and re-serialized; the detail page's "back" link rebuilds only canonical list parameters from `?list=`, so it cannot become an open redirect or carry other parameters. Telemetry labels (span and service names, resource and span attributes, scope, `error.type`, models, providers) are rendered as React text only; integers above 2^53 are kept exact. No route handler or Server Function was added. Live E2E ingests hostile labels through `/v1/traces` and checks they render as text without executing (T41); a mutated build that renders a span name as HTML fails that test.
- **Project context (Phase 3C).** `GET /api/v1/projects` (admin token, bounded to 200, id/name/creation time only, no key material) feeds a project selector. Each page reads exactly one project from `?project=` (validated positive BIGINT) or the configured default; an invalid id is refused before any request and never replaced by another project; an unknown one is the existing indistinguishable `404`. The backend client requires an explicit project scope per call (the listing is the only unscoped path) and refuses a `project` smuggled in other parameters; overview/compare responses that echo a different project are rejected. Switching is a full page load that drops cursors and selected spans. Project names are admin-chosen free text and are rendered as text only (hostile names tested live). This adds no permission: the single admin token already reads every project (CR-06).
- The dashboard has **no user login**. That is acceptable only because it is bound to `127.0.0.1`. Any other bind needs the review in §7.
- Next.js anonymous telemetry is disabled per process (`frontend/scripts/next.mjs`), not through the global Next.js config.

## 6. Cost safety

- Tests and CI use the mock provider only (`LLMI_PROVIDER_MODE=mock`).
- **OpenAI adapter tests are offline (Phase 2B):** responses come from SYNTHETIC fixtures behind an `httpx2.MockTransport`, and each test blocks every non-loopback name resolution and connection, so a request that escaped the fake transport fails the test instead of reaching OpenAI. A test proves the guard stops the official client. `provenance="real"` is refused for a mock transport.
- **Cost figures are estimates** from provider-reported usage and the verified catalogue (`PRICING.md`), never an invoice; unknown cost is `NULL`.
- Real calls need `LLMI_ALLOW_REAL_LLM=true`, a present `OPENAI_API_KEY`, a `budget_usd` per experiment, and explicit user approval per run.
- **Live runner (Phase 6A, ADR-29/30):** the only path that can make a real call is the local CLI `python -m demo_app.live_openai --execute`; its default is a dry run. It needs `LLMI_PROVIDER_MODE=real`, the consent phrase `LLMI_LIVE_CONSENT=openai-paid-calls`, provider and model allowlists, request/token/cost/session limits under hard caps, no `OPENAI_BASE_URL`, no `OPENAI_CUSTOM_HEADERS` / `OPENAI_ORG_ID` / `OPENAI_PROJECT_ID` (Phase 6B, ADR-31: the `openai` library would let a custom `Authorization` header replace the checked key or route billing to another project), price evidence at most 14 days old and a ready backend; the real client must pass the endpoint boundary check (ADR-32: only `https://api.openai.com/v1/`, no redirect, no proxy or CA setting from the environment, verified TLS, no admin key or extra header; R87 resolved, R88 accepted); each request's worst-case `Decimal` cost is reserved before sending, failed attempts are booked at their bound (never 0), ambiguous failures are never retried. The limits are per process, not account-wide (R79): a provider-side project budget is a Phase 6B prerequisite. No dashboard route, Server Action or endpoint can trigger a real call or accept a key.
- **Gemini Free Tier runner (Phase 6C-A, ADR-34…36):** the only paths that can make a real Gemini call are two local CLIs, `python -m demo_app.live_gemini --execute` and, since ADR-39, `python -m demo_app.live_gemini_content --scenario <id> --execute`, which share one security path (`live_gemini.run_single_request`); the default of both is a dry run. It needs `LLMI_PROVIDER_MODE=real`, `LLMI_GEMINI_MODE=free-tier`, the consent phrase `LLMI_LIVE_CONSENT=gemini-free-tier-single-call` (an OpenAI consent never enables Gemini, and vice versa), a `GEMINI_API_KEY` read from the environment only (never an argument, never printed, logged, exported or stored), the operator's Free Tier attestation (`LLMI_GEMINI_BILLING_TIER=free`, `LLMI_GEMINI_TIER_CHECKED_ON` at most one day old: an **operator check, not a proof from Google's billing**, R90), the verified Free Tier model allowlist, and the hard caps 1 request / concurrency 1 / 0 retries / timeout ≤ 30 s / output ≤ 1024 tokens; it refuses `GOOGLE_API_KEY`, the Vertex AI / Enterprise switches, `GOOGLE_CLOUD_PROJECT`/`_LOCATION`, base-URL and record/replay variables, `GOOGLE_APPLICATION_CREDENTIALS` and the mTLS switches. There is no paid mode and no fallback to a paid model or project. The real client must pass the Gemini endpoint check (ADR-34: only `https://generativelanguage.googleapis.com/v1beta`, Developer API, no redirect — `httpx` would forward `x-goog-api-key` on one (R94) — no proxy or CA setting from the environment, the pinned `certifi` bundle with certificate and hostname verification (R96), one library attempt, no other credential or header), checked before telemetry, when the instrumented client is built and immediately before the send. Automatic continuation and automatic function calling are always off (R93). Only fixed SYNTHETIC text is sent (the smoke sentence, or one of the three reviewed scenarios of ADR-39); Free Tier content may be used by Google (R91). No dashboard route, Server Action or endpoint can trigger a Gemini call or accept a key.
- **Gemini content demo preview (Phase 7B, ADR-39):** telemetry stays metadata-only; the default stdout line carries no input, answer, thought, header, key or provider error text. `--preview` is opt-in, requires a real terminal outside CI (refused before any send otherwise), writes only to stderr, is bounded (input 1200 characters; output 2000 characters and 40 lines) and shows control, bidi, zero-width and surrogate characters as visible escapes. It shows only the fixed synthetic input and the model's non-thought text, read part by part (never the library's convenience text accessor or a serialized response), and nothing at all for a blocked, failed or errored outcome. **A terminal's scrollback, a screen recording or a capture tool can still keep what it shows (R106).** The preview is never exported, stored or returned by an API; it is not SDK content capture (ADR-19 unchanged).
- **Groq adapter (Phase 8A-2, ADR-42, ADR-43): offline only.** No runner exists. `provenance="real"` always raises until the free-plan guard is built and reviewed, and `synthetic` provenance needs a mock transport, so nothing in the adapter can reach Groq. One user message, no tools, streaming or extensions, exactly one attempt, no redirect, proxy or environment routing. The key is added per request and never set on the client, and failures reach callers without the library exception that holds the request's header. Groq cost stays unknown (ADR-40).
- **Groq Free Plan guard (Phase 8A-3, ADR-44): built, execution still locked.** Real requests are refused twice over: `REAL_EXECUTION_ENABLED` is false and no Free Plan evidence or model has been released. A real client cannot exist without `FreePlanSession` as its send gate. Immediately before each send the gate re-evaluates the consent phrase `groq-free-plan-single-call`, the operator's attestation (`LLMI_GROQ_PLAN=free`, checked at most one day ago: **an operator statement, not proof from Groq's billing**, R119), the evidence age, the single allowlisted model and the hard caps (1 request per session and per process, concurrency 1, 0 retries, no fallback, no paid mode). It then checks the endpoint boundary: the pinned HTTPS URL, the exact headers and body, a direct connection through exactly the audited `httpx` transport and `httpcore` connection pool (by identity, not by name, with no attribute missing, added or set), TLS trusting exactly the pinned `certifi` bundle with no key logging, and no proxy, redirect, mock transport, Unix socket or custom base URL. Any refusal sends nothing and creates no span. Only a proven-unsent request is reported `not_sent`; an ambiguous failure is `possibly_sent` and is never resent.
- **Comparison runner (Phase 8A-4, ADR-45): built, execution still locked.** `python -m demo_app.live_compare --provider groq --scenario <id>` is the only path that can make a real Groq request. Its default is a dry run that builds no client; `--execute` (complete flag only) sends at most one request with one of three fixed SYNTHETIC scenarios, and only through the Groq guard above, which refuses every request in this version. `gemini` (R123) and `openrouter` (no adapter) are refused before any setting or key is read. The run reads one copy of the environment, gives the client exactly the `GROQ_API_KEY` the policy validates, and the send gate refuses a request whose `Authorization` is not exactly that key (`credential_not_bound`). Stdout is one metadata-only JSON line: no key, input, output or provider error text, and cost is always `unknown`. Comparison data belongs in a dedicated project, which the SDK cannot verify (R125). No dashboard route, Server Action or endpoint can start a comparison run.
- **Experiments (Phase 4C, ADR-25):** only `mode: "mock"` runs (a scripted provider; the backend does not even have the `openai` package installed, and a static test forbids importing it). `mode: "real"` is validated and then refused (`409`), writing nothing. With `budget_usd`, estimated costs are checked before anything runs (unknown → refused) and a run-time guard stops further calls. Experiment costs are labelled synthetic estimates; nothing is spent and no usage-ledger row is written. An integration test runs an experiment with every non-loopback connection blocked.
- **Bounded experiment execution (Phase 4D):** at most 4 runs at once per backend process (`429 experiments_busy`, refused rather than queued), a 15 s wall-clock deadline per run checked before each simulated call (`409 experiment_time_limit`, nothing stored), at most 200 cases (400 results) per run, and an unexpected evaluation error is a fixed `500 internal_error` whose log line carries the exception type only (the message could quote content). The bounds are per process and reset on restart, like the rate limiter (CR-08).

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
| Catastrophic regex backtracking (ReDoS) in a stored rule | RE2 only (linear time), refused backtracking constructs, pattern/program/memory/input bounds; JSON Schema regex keywords refused (Phase 4C) | Worst case at the caps ~0.5 s per evaluation (NFA fallback, measured); an experiment evaluates at most 400 results and stops after 15 s (Phase 4D, R60) |
| Experiment runs exhausting the backend (many or long runs) | Phase 4D: 4 concurrent runs per process, 15 s per run, 200 cases per run, `/api/v1` rate limit; refused runs store nothing | Per-process bounds only (CR-08); a local process can still start runs one after another (R54) |
| A crafted dashboard POST requesting a real provider run | Mode constant `mock` in the Server Action's parser; backend refuses `mode: "real"` (`409`), no real executor exists, `openai` not installed in the backend (Phase 4C/4D) | — |
| A misleading experiment comparison (a winner or a cost total on incomplete evidence) | Backend names a better version only on complete paired evidence and compares cost only when fully priced (ADR-25); the dashboard decoder rejects a report violating these definitions and shows partial evidence as such (Phase 4D) | Synthetic results; small samples, no significance test (R62) |
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

## 8. Static analysis exceptions (Bandit, R104-2)

CI job `security-python` runs Bandit 1.9.4 (hash-checked tool lock) as `bandit -r backend/app sdk/src demo_app scripts -f json -o <report> -q`: the whole first-party tree of the Phase 6 assessment, including `scripts/qa/tests`, with Bandit's default profile, no `-s`/`-x`/`-l`/`-i` option, no configuration file and no `# nosec`. `scripts/qa/bandit_baseline_check.py` fails the job unless the raw report equals `security/bandit-baseline.json` exactly. Evidence and checker design: `reports/r104.md` (R104-2).

- **An entry accepts one finding, not a class.** Each entry is pinned by file, test id, severity, confidence, CWE and the SHA-256 of `path|test_id|offending line`, of the whole statement and of Bandit's issue text, plus an occurrence number for identical lines. Line numbers may move; anything else that changes is a new finding.
- **New, changed or vanished findings fail at every severity.** A finding that disappears also fails until its entry and its row below are removed in a reviewed change; a stale entry is never kept as harmless metadata.
- **Changing the baseline is a security review.** The baseline is never regenerated in CI. A change needs the baseline, this register (the checker requires both to agree row for row) and the classification evidence in the same reviewed commit. No entry may use a class outside the list below, and each class is limited to its test ids, severity and path.
- `backend/alembic` is outside the approved scope; scanned separately on 2026-11-04 it has 0 findings (not gated).
- The baseline holds the 44 findings of the released Phase 6 tree (High 1, Medium 5, Low 38), each with the earliest release tag that contains it (`reports/r104.md`, R104-2A). A B106 false positive introduced by an R104-1 self-test (`secret="x"` as a synthetic report key) was **not accepted** as an exception: the test now uses a neutral key and the entry BL-38 and its class were removed (R104-2A). Entry ids are not renumbered.

| Class | Tests (severity) | Classification | Evidence |
|---|---|---|---|
| F5 | B202 (High) | False positive: the dev-only Node installer extracts an archive whose SHA-256 was checked against `SHASUMS256.txt` from the fixed `https://nodejs.org/dist/v22.23.4/`, after checking that every member stays inside the target; `zipfile` creates no links, the tar branch uses `filter="data"` | `reports/phase-5b.md` §4.2; `reports/phase-6-final-qa.md` §9 #1 |
| F6 | B310 (Medium) | False positive: `urlopen` only on fixed `https://nodejs.org` URLs, the demo's own `http://127.0.0.1:<bound port>`, `http://127.0.0.1:<int port>`, or the validated live-runner endpoint (`http`/`https` only, plain `http` loopback-only); no `file:` or custom scheme reachable | `reports/phase-5b.md` §4.2; `reports/phase-6a.md`; `reports/phase-6-final-qa.md` §9 #2–4, #6 |
| F7 | B608 (Medium) | False positive: the QA benchmark's table name comes from a literal tuple, never from input | `reports/phase-5b.md` §4.2; `reports/phase-6-final-qa.md` §9 #5 |
| F8 | B101, B105, B404, B603, B607 (Low) | Accepted Low: asserts in the QA benchmark, fixed-argv `git`/`docker`/seeder calls without a shell, a diagnostics hypothesis text and a `CHANGE_ME` placeholder check mistaken for passwords | `reports/phase-5b.md` §4.2 (F8) |

<!-- bandit-baseline:begin -->
| ID | File | Test | Severity / confidence | Fingerprint | Class |
|---|---|---|---|---|---|
| BL-01 | `backend/app/services/diagnostics/catalog.py` | B105 | LOW / MEDIUM | `ae4e1727583d` | F8 |
| BL-02 | `demo_app/live_common.py` | B310 | MEDIUM / HIGH | `085fb004b875` | F6 |
| BL-03 | `demo_app/web.py` | B310 | MEDIUM / HIGH | `d572f779c1b8` | F6 |
| BL-04 | `scripts/dev/e2e_fixtures.py` | B404 | LOW / HIGH | `0eb26803ffa3` | F8 |
| BL-05 | `scripts/dev/e2e_fixtures.py` | B603 | LOW / HIGH | `e59d70766dea` | F8 |
| BL-06 | `scripts/dev/init_frontend_env.py` | B105 | LOW / MEDIUM | `c0622f716178` | F8 |
| BL-07 | `scripts/dev/install_node.py` | B310 | MEDIUM / HIGH | `f3c379faef32` | F6 |
| BL-08 | `scripts/dev/install_node.py` | B202 | HIGH / HIGH | `945c03dea0c0` | F5 |
| BL-09 | `scripts/qa/backend_probe.py` | B310 | MEDIUM / HIGH | `26557e2296ab` | F6 |
| BL-10 | `scripts/qa/benchmark.py` | B404 | LOW / HIGH | `d587380c4fa6` | F8 |
| BL-11 | `scripts/qa/benchmark.py` | B101 | LOW / HIGH | `18cbc8ad99db` | F8 |
| BL-12 | `scripts/qa/benchmark.py` | B603 | LOW / HIGH | `02478f6c0369` | F8 |
| BL-13 | `scripts/qa/benchmark.py` | B603 | LOW / HIGH | `024d184779e0` | F8 |
| BL-14 | `scripts/qa/benchmark.py` | B607 | LOW / HIGH | `688c3c43c06a` | F8 |
| BL-15 | `scripts/qa/benchmark.py` | B101 | LOW / HIGH | `33537e8d17a5` | F8 |
| BL-16 | `scripts/qa/benchmark.py` | B101 | LOW / HIGH | `fb77d1fc7b05` | F8 |
| BL-17 | `scripts/qa/benchmark.py` | B101 | LOW / HIGH | `888b9bb98327` | F8 |
| BL-18 | `scripts/qa/benchmark.py` | B603 | LOW / HIGH | `cfa30354a074` | F8 |
| BL-19 | `scripts/qa/benchmark.py` | B603 | LOW / HIGH | `c3ac44e4b196` | F8 |
| BL-20 | `scripts/qa/benchmark.py` | B603 | LOW / HIGH | `cfa30354a074` | F8 |
| BL-21 | `scripts/qa/benchmark.py` | B603 | LOW / HIGH | `bdd8a33393bb` | F8 |
| BL-22 | `scripts/qa/benchmark.py` | B607 | LOW / HIGH | `46d70373d2a7` | F8 |
| BL-23 | `scripts/qa/benchmark.py` | B608 | MEDIUM / MEDIUM | `884bc2e15a66` | F7 |
| BL-24 | `scripts/qa/benchmark.py` | B101 | LOW / HIGH | `d29aeecc5348` | F8 |
| BL-25 | `scripts/qa/benchmark.py` | B101 | LOW / HIGH | `d29aeecc5348` | F8 |
| BL-26 | `scripts/qa/benchmark.py` | B101 | LOW / HIGH | `d29aeecc5348` | F8 |
| BL-27 | `scripts/qa/benchmark.py` | B101 | LOW / HIGH | `d90345e4b1f6` | F8 |
| BL-28 | `scripts/qa/benchmark.py` | B101 | LOW / HIGH | `6795e02c8b50` | F8 |
| BL-29 | `scripts/qa/benchmark.py` | B101 | LOW / HIGH | `6795e02c8b50` | F8 |
| BL-30 | `scripts/qa/benchmark.py` | B101 | LOW / HIGH | `0d1bf6e37942` | F8 |
| BL-31 | `scripts/qa/benchmark.py` | B101 | LOW / HIGH | `1500e73fa24c` | F8 |
| BL-32 | `scripts/qa/benchmark.py` | B101 | LOW / HIGH | `c48d02a384a5` | F8 |
| BL-33 | `scripts/qa/secret_scan.py` | B404 | LOW / HIGH | `18f15e5a72a6` | F8 |
| BL-34 | `scripts/qa/secret_scan.py` | B603 | LOW / HIGH | `1f87d2448506` | F8 |
| BL-35 | `scripts/qa/secret_scan.py` | B607 | LOW / HIGH | `39412d3c278b` | F8 |
| BL-36 | `scripts/qa/secret_scan.py` | B603 | LOW / HIGH | `730d21ac03dc` | F8 |
| BL-37 | `scripts/qa/secret_scan.py` | B607 | LOW / HIGH | `a622ee9780a9` | F8 |
| BL-39 | `scripts/verify_phase0.py` | B404 | LOW / HIGH | `5fee7146ef6a` | F8 |
| BL-40 | `scripts/verify_phase0.py` | B603 | LOW / HIGH | `4ed2502de22b` | F8 |
| BL-41 | `scripts/verify_phase0.py` | B607 | LOW / HIGH | `57bef81899d7` | F8 |
| BL-42 | `scripts/verify_phase0.py` | B603 | LOW / HIGH | `59ce94dc1ac2` | F8 |
| BL-43 | `scripts/verify_phase0.py` | B607 | LOW / HIGH | `d611e2f7b809` | F8 |
| BL-44 | `scripts/verify_phase0.py` | B603 | LOW / HIGH | `ac3d6393febd` | F8 |
| BL-45 | `scripts/verify_phase0.py` | B607 | LOW / HIGH | `3a1f7b05d337` | F8 |
<!-- bandit-baseline:end -->

## 9. History secret-scan exceptions (R104-3)

CI job `security-history` checks out the full history and runs `scripts/qa/secret_history_gate.py`. It applies the detectors of `scripts/qa/secret_scan.py` raw, **without that scanner's `ALLOW` list**, so no global regular expression can accept a match. Evidence and design: `reports/r104.md` (R104-3).

- **Scanned:** every object reachable from every ref of the checkout (blobs, commit objects, annotated tag objects including nested tags, tree entry names) and the ref names. On a pull request the refs are `refs/remotes/origin/*`, `refs/tags/*` and `refs/remotes/pull/<n>/merge`.
- **Not scanned, and not claimed:** GitHub-managed refs of other pull requests (for example `refs/pull/1/head`, R105), unreachable objects, content inside archives or encoded data, and the exact local secret values of `secret_scan.py` (those need the git-ignored env files, so they remain a local check).
- **An entry accepts one occurrence, not a class or a count.** Each is pinned by detector, object type, full Git object id, occurrence number, byte offset, length and, for a blob, the exact set of repository paths. The object id pins the bytes, so the matched value is never stored. A new, changed, moved or vanished occurrence fails, and so does the same blob at a new path.
- **Coverage is verified, not assumed.** A shallow or partial checkout, a grafts file, a ref outside the allowed namespaces, a missing or moved release tag, a different root commit, a submodule entry, an unreachable expected commit or a difference between `git rev-list` and the gate's own object walk fails the job.
- **Changing the baseline is a security review.** CI never regenerates it. A change needs `security/secret-history-baseline.json`, the register below and the `REVIEWED_BASELINE_SHA256` constant of the gate in the same reviewed commit, with the classification evidence. Any new or edited file containing a match needs such a review before it can pass, including a future edit of an already listed fixture file.
- **Only sanitized metadata is reported:** verdict, ref categories, object counts, finding counts, entry ids, detector names, object id prefixes and error categories. Raw reports stay on the runner.
- **Detector change, Phase 8A-1 (ADR-41):** `groq_key` was added to `scripts/qa/secret_scan.py`. The baseline's `detector_digest` and `REVIEWED_BASELINE_SHA256` were updated by hand in the same change. No entry was added, removed or reclassified, so the register below is unchanged. Evidence: `reports/phase-8a-1.md`.
- **Entry SH-39, Phase 8A-3 (2026-11-10):** the Groq block added to `.env.example` created a new blob (`9b15078560e2`). Its only match is the existing `CHANGE_ME` database URL placeholder, byte-identical to SH-05 at the same offset and length; the added lines match nothing. Classified F9-CHANGE-ME-VALUE by hand; no other entry changed. Evidence: `reports/phase-8a-3.md` §15.

| Class | Detector, object | Paths | Classification | Evidence |
|---|---|---|---|---|
| F9-DOC-EXAMPLE | `url_userinfo`, blob | `docs/SECURITY.md`, `docs/reports/phase-2c.md`, `backend/tests/redaction.py`, `sdk/tests/redaction.py` | False positive: the literal placeholder URL that documents the redaction rule | `reports/phase-5b.md` §4 F9; `reports/r104.md` R104-3 |
| F9-TEST-FIXTURE | `url_userinfo`, blob | `backend/tests/unit/test_config.py`, `backend/tests/unit/test_output_redaction.py`, `sdk/tests/unit/test_redaction.py` | False positive: synthetic password or f-string template in a redaction or settings test, aimed at loopback port 1, a `.invalid` host or a rejected input | `reports/phase-5b.md` §4 F9; `reports/r104.md` R104-3 |
| F9-CHANGE-ME-VALUE | `url_userinfo`, blob | `.env.example`, `backend/tests/unit/test_config.py` | False positive: the `CHANGE_ME` placeholder; hidden by the local scanner's `ALLOW` list, listed here individually | `reports/r104.md` R104-3 |

<!-- secret-history-baseline:begin -->
| ID | Detector | Object | Id | Occurrence | Offset / length | Paths | Class |
|---|---|---|---|---|---|---|---|
| SH-01 | url_userinfo | blob | `1524786ba290` | 0 | 330 / 42 | `.env.example` | F9-CHANGE-ME-VALUE |
| SH-02 | url_userinfo | blob | `2919f41adf84` | 0 | 467 / 42 | `.env.example` | F9-CHANGE-ME-VALUE |
| SH-03 | url_userinfo | blob | `5df5016c0771` | 0 | 467 / 42 | `.env.example` | F9-CHANGE-ME-VALUE |
| SH-04 | url_userinfo | blob | `69e82d1b859b` | 0 | 330 / 42 | `.env.example` | F9-CHANGE-ME-VALUE |
| SH-05 | url_userinfo | blob | `71789f6dd7e3` | 0 | 467 / 42 | `.env.example` | F9-CHANGE-ME-VALUE |
| SH-06 | url_userinfo | blob | `90b13a683cdc` | 0 | 467 / 42 | `.env.example` | F9-CHANGE-ME-VALUE |
| SH-07 | url_userinfo | blob | `b4fb5e196fbd` | 0 | 467 / 42 | `.env.example` | F9-CHANGE-ME-VALUE |
| SH-08 | url_userinfo | blob | `db2801ff3e48` | 0 | 467 / 42 | `.env.example` | F9-CHANGE-ME-VALUE |
| SH-09 | url_userinfo | blob | `e0d1d9db2167` | 0 | 467 / 42 | `.env.example` | F9-CHANGE-ME-VALUE |
| SH-10 | url_userinfo | blob | `74c8ed1e1eaa` | 0 | 825 / 23 | `backend/tests/redaction.py`, `sdk/tests/redaction.py` | F9-DOC-EXAMPLE |
| SH-11 | url_userinfo | blob | `74c8ed1e1eaa` | 1 | 1959 / 23 | `backend/tests/redaction.py`, `sdk/tests/redaction.py` | F9-DOC-EXAMPLE |
| SH-12 | url_userinfo | blob | `c849be72bedf` | 0 | 2247 / 33 | `backend/tests/unit/test_config.py` | F9-CHANGE-ME-VALUE |
| SH-13 | url_userinfo | blob | `c849be72bedf` | 1 | 2807 / 36 | `backend/tests/unit/test_config.py` | F9-TEST-FIXTURE |
| SH-14 | url_userinfo | blob | `c849be72bedf` | 2 | 3228 / 19 | `backend/tests/unit/test_config.py` | F9-TEST-FIXTURE |
| SH-15 | url_userinfo | blob | `3eabac464c0e` | 0 | 2175 / 43 | `backend/tests/unit/test_output_redaction.py` | F9-TEST-FIXTURE |
| SH-16 | url_userinfo | blob | `3eabac464c0e` | 1 | 4698 / 39 | `backend/tests/unit/test_output_redaction.py` | F9-TEST-FIXTURE |
| SH-17 | url_userinfo | blob | `3eabac464c0e` | 2 | 5182 / 43 | `backend/tests/unit/test_output_redaction.py` | F9-TEST-FIXTURE |
| SH-18 | url_userinfo | blob | `3eabac464c0e` | 3 | 5769 / 39 | `backend/tests/unit/test_output_redaction.py` | F9-TEST-FIXTURE |
| SH-19 | url_userinfo | blob | `0494c7529b00` | 0 | 8593 / 23 | `docs/SECURITY.md` | F9-DOC-EXAMPLE |
| SH-20 | url_userinfo | blob | `0f331f2f3d09` | 0 | 8304 / 23 | `docs/SECURITY.md` | F9-DOC-EXAMPLE |
| SH-21 | url_userinfo | blob | `13acc1e1c3cb` | 0 | 8304 / 23 | `docs/SECURITY.md` | F9-DOC-EXAMPLE |
| SH-22 | url_userinfo | blob | `24a57520e662` | 0 | 8304 / 23 | `docs/SECURITY.md` | F9-DOC-EXAMPLE |
| SH-23 | url_userinfo | blob | `2ae4d3455269` | 0 | 8304 / 23 | `docs/SECURITY.md` | F9-DOC-EXAMPLE |
| SH-24 | url_userinfo | blob | `2db4e58e9701` | 0 | 5581 / 23 | `docs/SECURITY.md` | F9-DOC-EXAMPLE |
| SH-25 | url_userinfo | blob | `36c1db4d7151` | 0 | 8593 / 23 | `docs/SECURITY.md` | F9-DOC-EXAMPLE |
| SH-26 | url_userinfo | blob | `3e9542aa56f9` | 0 | 5581 / 23 | `docs/SECURITY.md` | F9-DOC-EXAMPLE |
| SH-27 | url_userinfo | blob | `4d81bddac680` | 0 | 6153 / 23 | `docs/SECURITY.md` | F9-DOC-EXAMPLE |
| SH-28 | url_userinfo | blob | `61f121f494f8` | 0 | 8593 / 23 | `docs/SECURITY.md` | F9-DOC-EXAMPLE |
| SH-29 | url_userinfo | blob | `6693735684ff` | 0 | 5581 / 23 | `docs/SECURITY.md` | F9-DOC-EXAMPLE |
| SH-30 | url_userinfo | blob | `82f955dcccd0` | 0 | 8304 / 23 | `docs/SECURITY.md` | F9-DOC-EXAMPLE |
| SH-31 | url_userinfo | blob | `889f2841f5ca` | 0 | 8593 / 23 | `docs/SECURITY.md` | F9-DOC-EXAMPLE |
| SH-32 | url_userinfo | blob | `92bc7483281e` | 0 | 8593 / 23 | `docs/SECURITY.md` | F9-DOC-EXAMPLE |
| SH-33 | url_userinfo | blob | `a532d374b327` | 0 | 8593 / 23 | `docs/SECURITY.md` | F9-DOC-EXAMPLE |
| SH-34 | url_userinfo | blob | `cfcaccc3167b` | 0 | 5581 / 23 | `docs/SECURITY.md` | F9-DOC-EXAMPLE |
| SH-35 | url_userinfo | blob | `f24f27ddb357` | 0 | 7088 / 23 | `docs/SECURITY.md` | F9-DOC-EXAMPLE |
| SH-36 | url_userinfo | blob | `b1e2f7d85360` | 0 | 4903 / 23 | `docs/reports/phase-2c.md` | F9-DOC-EXAMPLE |
| SH-37 | url_userinfo | blob | `41dd64be03d2` | 0 | 1161 / 34 | `sdk/tests/unit/test_redaction.py` | F9-TEST-FIXTURE |
| SH-38 | url_userinfo | blob | `41dd64be03d2` | 1 | 2551 / 35 | `sdk/tests/unit/test_redaction.py` | F9-TEST-FIXTURE |
| SH-39 | url_userinfo | blob | `9b15078560e2` | 0 | 467 / 42 | `.env.example` | F9-CHANGE-ME-VALUE |
<!-- secret-history-baseline:end -->
