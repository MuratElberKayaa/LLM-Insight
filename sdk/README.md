# LLM Insight SDK (`llm-insight-sdk`)

A small Python package that records **metadata-only** OpenTelemetry GenAI client spans and
exports them over **OTLP/HTTP binary protobuf** to the LLM Insight backend
(`POST /v1/traces`). It is independent of the backend (it never imports it, ADR-01).

Status: Phase 2B (0.2.0). The SDK ships a deterministic **mock** provider (always
`synthetic`) and an **OpenAI Chat Completions adapter** (`llm_insight_sdk.openai_adapter`,
optional extra). The SDK never computes money: the backend prices usage rows from its
verified catalogue (`docs/PRICING.md`). Every adapter test is offline; no real OpenAI call
has been made by this project.

## Install

Python 3.12. From the repository root:

```bash
py -3.12 -m venv sdk/.venv
sdk/.venv/Scripts/python -m pip install -r sdk/requirements-dev.lock   # pinned dev environment
sdk/.venv/Scripts/python -m pip install --no-deps ./sdk                # the SDK itself (non-editable)
```

For use inside another application, install the wheel (`pip wheel --no-deps ./sdk`). Its
runtime dependencies are pinned exactly: `opentelemetry-api`, `opentelemetry-sdk` and
`opentelemetry-exporter-otlp-proto-http` 1.45.1. The OpenAI adapter needs the optional
extra `llm-insight-sdk[openai]` (`openai==3.28.0`); the core package never imports
`openai`.

## Quick start

Create an ingest key with the backend CLI (`python -m app.cli create-key --project demo`,
from `backend/`). The key is printed once; put it in the environment, not in code.

```bash
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:8000/v1/traces
export OTEL_EXPORTER_OTLP_TRACES_HEADERS="X-Project-Key=<key>"
export OTEL_SERVICE_NAME=demo-summarizer
```

```python
import llm_insight_sdk as lli
from llm_insight_sdk.mock import ObservedMockClient, Scenario

telemetry = lli.init(lli.LLMInsightConfig.from_env())
client = ObservedMockClient(telemetry, max_attempts=3)  # retries = separate attempts

with telemetry.tracer.start_as_current_span("handle-request"):  # your root span
    reply = client.chat(
        model="mock-chat-small",
        messages=[{"role": "user", "content": "never exported"}],
        scenario=Scenario.RETRY_THEN_SUCCESS,
        prompt_version="v1",
    )

telemetry.shutdown()  # bounded; also runs automatically at interpreter exit
```

Async code uses `await client.achat(...)`. Concurrent calls (`asyncio.gather`) each keep the
current span as their parent.

Your own instrumentation can use the same span builder, one span per physical attempt:

```python
call_id = lli.new_call_id()  # one per logical call
with telemetry.inference_span(
    operation="chat",
    provider="llminsight.mock",
    request_model="mock-chat-small",
    call_id=call_id,
    attempt=1,
) as rec:
    ...  # call the model
    rec.record_response(
        response_model="...",
        response_id="...",
        finish_reasons=["stop"],
        usage=lli.TokenUsage(input_tokens=120, output_tokens=48),
    )
```

The demo application runs every scenario against a live backend:
`sdk/.venv/Scripts/python -m demo_app` (from the repository root).

The FastAPI demo (Phase 2C) shows request spans as the parents of inference spans:
`sdk/.venv/Scripts/python -m demo_app.web [--drive]` (offline: mock provider and the `openai`
client on an in-process fake transport; see `demo_app/web.py`). It needs the `dev` extra
(`fastapi`, `uvicorn`, pinned to the backend's versions).

## Configuration

`LLMInsightConfig(...)` takes keyword arguments; `LLMInsightConfig.from_env()` reads the
variables below. Invalid values raise `ConfigError` when the config is built. The message
names the setting, never its value.

| Setting | Environment variable | Default | Rule |
|---|---|---|---|
| `endpoint` | `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` (else `OTEL_EXPORTER_OTLP_ENDPOINT` + `/v1/traces`) | required | `http(s)://host[:port]/…/v1/traces`; no user info, query or fragment; **plain `http` only for loopback** (the key travels in a header) |
| `project_key` | `X-Project-Key` inside `OTEL_EXPORTER_OTLP_TRACES_HEADERS` (else `OTEL_EXPORTER_OTLP_HEADERS`) | required | `lli_<8 alphanumerics>_<secret>`. Sent only as the `X-Project-Key` header. Hidden from `repr()` |
| `service_name` | `OTEL_SERVICE_NAME` | required | metadata label, ≤ 256 chars, no control characters, credentials or personal data |
| `service_version` | `LLMI_SDK_SERVICE_VERSION` | none | `[A-Za-z0-9][A-Za-z0-9._+-]*`, ≤ 128 |
| `deployment_environment` | `LLMI_SDK_DEPLOYMENT_ENVIRONMENT` | none | label, ≤ 256 |
| `compression` | `OTEL_EXPORTER_OTLP_TRACES_COMPRESSION` | `gzip` | `gzip` or `none` |
| `export_timeout_seconds` | `LLMI_SDK_EXPORT_TIMEOUT_SECONDS` | `5.0` | **seconds**, 0.1–30. One export request, including the exporter's own retries of `429`/`502`/`503`/`504` and connection errors |
| `schedule_delay_millis` | `LLMI_SDK_SCHEDULE_DELAY_MILLIS` | `1000` | **milliseconds**, 50–60000. BatchSpanProcessor export interval |
| `max_queue_size` | `LLMI_SDK_MAX_QUEUE_SIZE` | `2048` | spans, 1–32768. When full, the **oldest** span is dropped |
| `max_export_batch_size` | `LLMI_SDK_MAX_EXPORT_BATCH_SIZE` | `512` | spans, 1–2048 (backend limit), ≤ `max_queue_size` |
| `max_request_bytes` | `LLMI_SDK_MAX_REQUEST_BYTES` | 4 MiB − 64 KiB | uncompressed bytes, 1024–4 MiB − 64 KiB. A larger batch is dropped before sending |
| `shutdown_timeout_millis` | `LLMI_SDK_SHUTDOWN_TIMEOUT_MILLIS` | `5000` | **milliseconds**, 100–60000. Bounds `shutdown()` and the default `force_flush()` |
| `provenance` | `LLMI_SDK_PROVENANCE` | `synthetic` | only `synthetic` in this release; `real` is reserved for the provider adapter and rejected |
| `content_policy` | `LLMI_SDK_CONTENT_POLICY` | `metadata_only` | only `metadata_only` (ADR-19) |

Also checked by `from_env()`: `OTEL_EXPORTER_OTLP_TRACES_PROTOCOL` must be `http/protobuf`
if set, and `OTEL_TRACES_SAMPLER` must be `always_on` if set (ADR-06).

**Timeout units.** The OTLP specification defines `OTEL_EXPORTER_OTLP_TRACES_TIMEOUT` in
milliseconds, but the Python exporter 1.45.1 reads it as seconds. The SDK therefore ignores
it (with a warning) and always passes `export_timeout_seconds` to the exporter explicitly.
The BatchSpanProcessor's own `export_timeout_millis` is set to the same value, but version
1.45.1 does not enforce it; the exporter timeout is what bounds an export.
`OTEL_BSP_*` variables have no effect either, because every processor setting is passed
explicitly.

**Backend limits** (`TELEMETRY_CONTRACT.md` §1): 4 MiB wire body, 16 MiB decompressed,
2048 spans per request. The exporter measures a request *before* compression, so
`max_request_bytes` ≤ 4 MiB − 64 KiB keeps both the gzip body and the decompressed body
under the limits, and `max_export_batch_size` ≤ 2048 keeps the span count under its limit.

## Lifecycle

- `lli.init(config)` builds a private `TracerProvider` (sampler `always_on`, an explicit
  resource) with one BatchSpanProcessor and one OTLP exporter. It **does not** set the
  global provider. `init(config, set_global=True)` installs it globally only if no global
  SDK provider exists yet; an existing one is never replaced (`telemetry.is_global` says
  which happened).
- Calling `init()` again with an equal config returns the same instance, so no processor is
  registered twice. A different config raises `AlreadyInitializedError` until `shutdown()`.
- `telemetry.force_flush(timeout_millis=None) -> bool` and
  `telemetry.shutdown(timeout_millis=None) -> bool` are bounded by the timeout (an
  OpenTelemetry `force_flush` alone can block for the whole export). `True` means
  "finished in time and no export failed meanwhile". `False` is a report, never an
  exception.
- `telemetry.stats()` returns process-local counters: spans ended, exported and failed,
  batches succeeded and failed, and attributes withheld by the metadata rules.
- `telemetry.tracer` is for your own spans; `telemetry.tracer_provider` lets you add
  processors (for example an in-memory exporter in tests).

The resource contains exactly `service.name`, `service.version`,
`deployment.environment.name`, `telemetry.sdk.{name,language,version}` and
`llminsight.sdk.version`. `OTEL_RESOURCE_ATTRIBUTES` and resource detectors are not used,
so host, process and user attributes cannot slip in.

## Delivery guarantees (best effort)

The application thread only appends ended spans to a bounded in-memory queue; export runs on
the processor's worker thread. **Telemetry never raises into the application**: a backend
that is down, slow, or rejecting the key costs at most the export timeout on the worker
thread, which is logged by OpenTelemetry and counted in `stats()`.

**Delivery is not guaranteed.** Spans are lost when:
- the process crashes or is killed;
- `shutdown()` exceeds its timeout;
- the queue is full (the oldest span is dropped);
- the backend is unavailable for longer than the export timeout;
- the backend rejects the batch (`400`, `401`, `408`, `413`: not retried).

There is no persistent queue (ADR-06) and no billing-grade reliability.

## OpenAI adapter (Phase 2B)

```python
import openai

import llm_insight_sdk as lli
from llm_insight_sdk.openai_adapter import ObservedOpenAIClient

telemetry = lli.init(lli.LLMInsightConfig.from_env())
llm = ObservedOpenAIClient(telemetry, openai.OpenAI(), provenance="real")
completion = llm.create_chat_completion(
    model="gpt-5.4-mini",
    messages=[{"role": "user", "content": "..."}],
    service_tier="default",  # lets the backend prove standard-tier pricing
    prompt_version="v1",
)
```

- **Surface:** `chat.completions.create` without streaming (`openai.api.type =
  chat_completions`), sync (`ObservedOpenAIClient`) and async (`ObservedAsyncOpenAIClient`).
  `stream=True`, `stream_options`, Azure clients and any base URL other than
  `https://api.openai.com` are refused with `UnsupportedRequestError` / `ConfigError`
  before a request is sent.
- **Provenance is declared**, never guessed: `real` for a client that talks to OpenAI,
  `synthetic` for fake transports and fixtures. `real` with an `httpx2.MockTransport` is
  refused. The backend still downgrades a successful `real` span without a response ID.
- **One span per physical request.** The wrapped client's own retries are disabled
  (`with_options(max_retries=0)`; your client object is not modified). The adapter retries
  connection errors, timeouts, 408, 409, 429 and 5xx (default 3 attempts, linear back-off
  of 0.5 s, `Retry-After` not honoured), each attempt with its own span, sharing one
  UUIDv4 `llminsight.call_id`.
- **Usage** is copied from the provider's `usage` object only: `prompt_tokens`,
  `completion_tokens`, `prompt_tokens_details.cached_tokens` / `.cache_write_tokens`,
  `completion_tokens_details.reasoning_tokens`. A field the provider did not report stays
  absent; nothing is estimated from text.
- **Errors** reach your code unchanged after the last attempt. The span gets `ERROR` and
  `error.type` = `timeout`, the HTTP status (`429`, `500`, …) or `openai.<ExceptionClass>`.
  The provider's error message and request ID are never recorded.
- **Fail-open:** if telemetry itself fails, the request still runs and its result or error
  is returned exactly as without the adapter.
- **Cost** is computed by the backend only for responses that report
  `service_tier = default` on `api.openai.com` with a model in the verified catalogue;
  everything else is stored as unknown (`price_missing`), never as 0.
- **Logging caveat:** the `openai` library's own DEBUG logging prints request details,
  including OpenAI request IDs. That is the application's logging configuration; the SDK's
  logger (`llm_insight_sdk`) never logs content, keys or provider error text.

## What is recorded

One `CLIENT` span per physical attempt, named `{gen_ai.operation.name} {gen_ai.request.model}`
(semconv `semantic-conventions-genai` @ `9ffc7e00c639`, mapping `llminsight.attrmap.v1`). It
records only these attributes:
- `gen_ai.operation.name`, `gen_ai.provider.name`, and `gen_ai.request.model` /
  `gen_ai.response.model`, kept separate;
- `gen_ai.response.id`, `gen_ai.response.finish_reasons`;
- the numeric request settings `max_tokens`, `temperature`, `top_p`, `seed`;
- `gen_ai.prompt.name` / `.version`;
- the provider-reported `gen_ai.usage.*` counts, with cache and reasoning subsets reported
  separately and a reported `0` emitted explicitly;
- `error.type` on failure;
- `llminsight.call_id` (UUIDv4 per logical call), `llminsight.attempt` (1, 2, …),
  `llminsight.provenance` and `llminsight.usage_source`;
- OpenAI adapter only: `openai.api.type`, `openai.request.service_tier` (when set and not
  `auto`), `openai.response.service_tier`, `openai.response.system_fingerprint`,
  `server.address` / `server.port`, and `gen_ai.output.type` for a JSON `response_format`.

**Never recorded:**
- prompts, completions, system instructions, tool definitions or prompt variables;
- exception messages and stack traces: exceptions are not added as span events, and an
  error span carries status `ERROR` with no description, plus a low-cardinality
  `error.type` such as `429`, `timeout` or a class name;
- the OpenAI API key, request headers, `user`/`metadata` request fields or any other
  request argument besides the numeric settings above;
- any cost or price.

Every string value passes a client-side copy of the backend's metadata rules
(`TELEMETRY_CONTRACT.md` §3.1: format, length, unsafe characters, obvious credentials and
personal-data shapes) before it is set. A rejected value is withheld and counted, never
logged. If the request model is withheld, the span is named after the operation alone.
These rules are heuristics for obvious formats; do not put secrets or content into
metadata.

The mock provider never imitates a real one: provider `llminsight.mock`, models `mock-*`,
response IDs `mockresp_<uuid4 hex>`.

## Tests

From `sdk/`:

```bash
.venv/Scripts/python -m pytest tests/unit                          # no network, local OTLP stub
LLMI_TEST_REQUIRE_DB=1 .venv/Scripts/python -m pytest tests/integration
.venv/Scripts/ruff check . && .venv/Scripts/ruff format --check . && .venv/Scripts/python -m mypy src
```

The integration tests start the real backend (`backend/.venv`, `python -m app`) against a
disposable `llminsight_sdk_it_test` database. The database credentials come from the
repository `.env`, and the database is dropped afterwards. Without PostgreSQL they skip,
unless `LLMI_TEST_REQUIRE_DB=1` is set.

The OpenAI adapter tests (`tests/unit/test_openai_adapter.py`,
`tests/integration/test_openai_live.py`) use SYNTHETIC response fixtures
(`tests/openai_fixtures.py`) behind an `httpx2.MockTransport`, and block every
non-loopback name resolution and connection for the duration of each test, so no request
can reach OpenAI. The live tests seed the backend's verified price catalogue and check
the stored cost.
