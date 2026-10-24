# LLM Insight — Sources & Risk Register

## 1. Sources consulted (Phase 0, accessed 2026-10-24)

| Source | URL | Version / status | Used for |
|---|---|---|---|
| Master plan (project decision source) | `docs/MVP_MASTER_PLAN.md` | ADR-MVP-1.0, 2026-10-24 | All decisions |
| Phase prompts | `docs/CLAUDE_PHASE_PROMPTS.md` | — | Phase scope |
| OTLP specification | https://opentelemetry.io/docs/specs/otlp/ ; raw: https://raw.githubusercontent.com/open-telemetry/opentelemetry-proto/main/docs/specification.md | Spec 1.11.1. `opentelemetry-proto` release v1.11.1 (2026-09-29). **Stable** for traces | Content-Type, gzip, 200/400/413/429/503, partial_success, size limits, hex IDs in JSON, empty request |
| GenAI client inference semconv | https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/client-inference.md | **Development**. Repo has no releases; HEAD `9ffc7e00c639` (2026-10-21). Links core semconv v1.44.0 | Span name / kind, `gen_ai.*` attributes, usage subset notes, metric names |
| GenAI spans semconv | https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md | Development | Span scope, content-capture guidance |
| GenAI OpenAI semconv | https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/openai.md | Development | `gen_ai.provider.name=openai`, `openai.*` attributes |
| Old GenAI location | https://github.com/open-telemetry/semantic-conventions/blob/main/docs/gen-ai/README.md | Page states "Moved" | ADR-15 |
| `google.rpc` error details | https://raw.githubusercontent.com/googleapis/googleapis/master/google/rpc/error_details.proto | `BadRequest.field_violations{field, description, reason, localized_message}`, read 2026-10-24 | ADR-16/18, `BadRequest` contract |
| OTLP failure / encoding sections (re-read for the ADR gate) | raw `specification.md` §OTLP/HTTP Response, §Failures, §Binary/JSON Protobuf Encoding | 1.11.1, re-read 2026-10-24 | Response Content-Type rule, Status body rule, JSON `application/json` rule |
| PyPI JSON API | https://pypi.org/pypi/{package}/json | Read 2026-10-24 | Version pins |
| npm registry | https://registry.npmjs.org/{package} | Read 2026-10-24 | Version pins |
| endoflife.date | https://endoflife.date/api/{python,nodejs,postgresql}.json | Read 2026-10-24 | Runtime support windows |

Some content was read through an AI page summarizer, so the key statements were re-checked against the raw spec markdown by grep. Those statements are: Content-Type, the 400/413/retryable codes, the 64 MiB recommendation, empty request, and the JSON-acceptance SHOULD.

**Not available, not read:** `birlesmis_transkript.txt`, `LLM Gözlemlenebilirlik Platformu — Derin Araştırma Raporu.md`, `Proje önerilerini sunma.pdf`, and the original Architecture v1.1 Markdown. Their content is known only through the master plan's summaries. Nothing in this repository quotes them directly.

## 2. Risk register

| ID | Risk | Likelihood | Impact | Mitigation | Owner phase |
|---|---|---|---|---|---|
| R01 | The enclosing home-directory Git repo (`C:/Users/PC`, DailyDietAPP remote, 0-byte index) receives project files by mistake | Med | High | The project has its own `.git`. Check `git rev-parse --show-toplevel` before every commit. The home repo is never touched | All |
| R02 | GenAI semconv (Development) renames attributes during the MVP | Med | Med | Versioned mapping adapter, `attributes_schema_version`, alias rows (ADR-14/15) | 1–2 |
| R03 | The OTel Python exporter's timeout env-var units are ambiguous (spec says ms; the Python SDK historically used seconds) | Med | Med | Verify against the pinned SDK in Phase 2 before setting it, and test fail-open timing (T13) | 2 |
| R04 | No verified OpenAI price available, so every cost shows `unknown` | Med | Med (demo) | Seed only verified official prices with source URL and date. `unknown` is a correct outcome, not a bug | 2 |
| R05 | Real OpenAI spend during development | Low | Med | Mock by default, `LLMI_ALLOW_REAL_LLM` flag, per-run approval and budget | 2, 4 |
| R06 | Docker Desktop resource limits (disk / memory for `postgres:17`) | Unknown | Med | Check `docker info` and `docker system df` at the start of Phase 1. Daemon state is tracked as R18 | 1 |
| R07 | TypeScript 7 / ESLint 10 incompatibility with Next 16 tooling | Med | Low | Decide in Phase 3, falling back to TS 5.9 / 6.0 | 3 |
| R08 | Allowlisted free-form strings carry PII or secrets (e.g. a non-template `http.route`, a span name, a model name, or an identifier-shaped secret with no spaces in `error.type`) | Low | Med | Truncation, credential-key classification, identifier patterns for `error.type` / `gen_ai.prompt.version` (Phase 1C), T16 / variants. Values are not scanned for secrets | 1–2 |
| R09 | In-process rate limiter ineffective with more than one worker | Low (single worker) | Low | Document it and run one worker (CR-08) | 3 |
| R10 | Schedule: 14-day window, single developer | Med | Med | Gate-driven scope. Drop P1 items first (master plan §H) | All |
| R11 | Percentile disagreement between SQL and the UI or NumPy | Low | Med | One definition (`percentile_cont`), T24 cross-check | 2–3 |
| R12 | Installed Node 22.11.0 is an old patch level | Med | Low | Update to the latest 22.x before Phase 3 (needs user action) | 3 |
| R13 | A protobuf JSON parser misreads hex IDs in the fixture as base64 | Med | Low | Hex-decode in the fixture loader (`docs/fixtures/README.md`) | 1 |
| R14 | Exact-match pricing: provider snapshot names (e.g. `gpt-4o-mini-2024-07-18`) don't match a seeded base name, so costs show `unknown` | High | Med (demo) | Seed verified prices per exact model name. No fuzzy matching, because a wrong price is worse than an unknown one | 2 |
| R15 | Limited OTLP interoperability (no JSON/gRPC, atomic 400, smaller limits) surprises third-party clients | Med | Low (MVP) | Support statement in `TELEMETRY_CONTRACT.md` §0; T01-S3 exporter smoke | 1 |
| R16 | Provenance is client-asserted; a misconfigured client could label synthetic data `real` | Low | Med | Server consistency downgrade, "reported real" wording, mixed/unknown never real | 2–3 |
| R17 | Failed attempts reported by two instrumentations are not deduplicated | Low | Low | Documented; unknown cost doesn't affect the known total | 2 |
| R18 | Docker Engine was not running on 2026-10-24 (`docker info`: pipe `dockerDesktopLinuxEngine` not found) | **Resolved** in Phase 1A: Docker Desktop 4.49.0, Engine 28.5.1 running | — | The user started Docker Desktop | 1 |
| R19 | Host port conflicts on the dev machine: a native `postgres.exe` service listens on `0.0.0.0:5432`, and an unrelated `flowise` container publishes `0.0.0.0:3000` (the planned frontend port) | Confirmed | Low | Compose PostgreSQL uses `127.0.0.1:55432` (`POSTGRES_HOST_PORT`). Neither service was touched. Phase 3 must pick a free frontend port or the user stops `flowise` | 1, 3 |
| R21 | OTLP `Status.message` is stored (`spans.status_message`) and may carry credential or content fragments, e.g. an exception text quoting an API key | **Resolved** in Phase 1C | — | The message is no longer stored (column kept, always `NULL`); `error.type` / `exception.type` must be identifiers. Adversarial test with credentials, PII, multiline, RTL / zero-width / NUL and 100 kB text: 0 occurrences in a full DB dump, in DEBUG logs and in API responses (`reports/phase-1c.md` §5) | 1 |
| R22 | No server-side timeout on reading a request body: a slow client can hold a worker thread and a connection | **Mitigated** in Phase 1C (body phase only) | Low | `LLMI_OTLP_BODY_READ_TIMEOUT_S` (10 s): one overall deadline after auth → `408` + `Connection: close`; disconnects and cancellation close the stream and write nothing. The request-header phase and connection counts are covered by R24 | 1 |
| R23 | Span content conflicts and usage call-key collisions are counted in process memory only (reset on restart, not exposed by any API) | Med | Low | Warning log with counts; the first write is always kept intact. Exposing the counters needs an endpoint or a column (Phase 3 decision) | 3 |
| R24 | Slow request headers and connection exhaustion: uvicorn bounds idle keep-alive (5 s) but not the time to send headers, and the app sets no connection limit | Low (local bind) | Low | Loopback-only bind; body deadline after auth. A reverse proxy or uvicorn `limit_concurrency` is required before any non-local exposure (`SECURITY.md` §7) | 5 |
| R20 | Starlette 1.x deprecates the `httpx`-based `TestClient` (warning: "install `httpx2` instead"). The pinned `httpx` 0.28.1 still works | Confirmed (warning only) | Low | Re-check at the next pin review. Switching the test client needs a new pin and evidence | 1–5 |
