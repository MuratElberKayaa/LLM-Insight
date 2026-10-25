"""Phase 2C demo: a FastAPI application whose request spans are the parents of LLM spans.

Every request gets a SERVER span (`{method} {route}`), created with the LLM Insight SDK's
tracer. The handlers call LLMs inside it, so every inference span (one per physical attempt)
is a CLIENT child in the same trace. An incoming W3C `traceparent` header is honoured: the
request span then continues the caller's trace.

All LLM calls are SYNTHETIC and offline:
- the deterministic mock provider (`llminsight.mock`, never priced, so its cost is unknown);
- the real `openai` client wired to an in-process fake transport (`httpx2.MockTransport`).
  No request leaves the process and no API key is used; the fake answers like
  `gpt-5.4-mini-2026-03-17` on the standard tier, so the backend can price it from the
  verified catalogue when that is seeded. Provenance is `synthetic`.

Routes (each answers with the request's `trace_id`, never with telemetry content):
  POST /v1/summarize          one successful mock call
  POST /v1/summarize/multi    two async mock calls and one async fake-OpenAI call
  POST /v1/summarize/failure  a mock provider 500, handled: the app answers with a fallback
  POST /v1/summarize/retry    a 429 retried as a second physical attempt that succeeds
  POST /v1/summarize/openai   one fake-OpenAI call (the adapter's attribute set and cost)
  GET  /health                liveness (not traced)

Run from the repository root with the SDK venv (the backend must be running):

    sdk/.venv/Scripts/python -m demo_app.web            # serve on 127.0.0.1:8100
    sdk/.venv/Scripts/python -m demo_app.web --drive    # one request per route, JSON summary

Configuration as for `python -m demo_app` (sdk/README.md); `LLMI_DEMO_PORT` sets the port.
Telemetry is best effort: with the backend down or the key rejected every route still
answers (ADR-09).
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import sys
import threading
import time
import urllib.request
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2
import openai
import uvicorn
from fastapi import FastAPI, Request, Response
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from pydantic import BaseModel, Field

import llm_insight_sdk as lli
from llm_insight_sdk.mock import MockLLMProvider, MockProviderError, ObservedMockClient, Scenario
from llm_insight_sdk.openai_adapter import ObservedAsyncOpenAIClient, ObservedOpenAIClient

MOCK_MODEL = "mock-chat-small"
OPENAI_MODEL = "gpt-5.4-mini"
OPENAI_SNAPSHOT = "gpt-5.4-mini-2026-03-17"
PROMPT_NAME = "summarize"
PROMPT_VERSION = "v1"
SYSTEM_PROMPT = "SYNTHETIC: summarize the text in one sentence."
# A fake key the fake transport never checks, assembled so no secret scanner sees a literal.
OFFLINE_KEY = "sk" + "-synthetic-offline-demo-" + "0" * 24
HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
UNTRACED = frozenset({"/health"})
PROPAGATOR = TraceContextTextMapPropagator()


class SummarizeRequest(BaseModel):
    """Synthetic input. It is passed to the provider and never recorded or exported."""

    text: str = Field(min_length=1, max_length=4000)


# ---------------------------------------------------------------- offline OpenAI
def fake_openai_response(request: httpx2.Request) -> httpx2.Response:
    """A SYNTHETIC Chat Completions answer; the token counts are made up."""
    if request.url.host != "api.openai.com" or request.url.path != "/v1/chat/completions":
        return httpx2.Response(404, json={"error": {"message": "not found", "type": "invalid"}})
    return httpx2.Response(
        200,
        json={
            "id": f"chatcmpl-SYNTHETIC-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": OPENAI_SNAPSHOT,
            "service_tier": "default",
            "system_fingerprint": "fp_synthetic01",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "SYNTHETIC fake completion"},
                    "finish_reason": "stop",
                    "logprobs": None,
                }
            ],
            "usage": {
                "prompt_tokens": 1200,
                "completion_tokens": 300,
                "total_tokens": 1500,
                "prompt_tokens_details": {"cached_tokens": 1000},
                "completion_tokens_details": {"reasoning_tokens": 200},
            },
        },
    )


def offline_openai() -> openai.OpenAI:
    transport = httpx2.MockTransport(fake_openai_response)
    return openai.OpenAI(api_key=OFFLINE_KEY, http_client=httpx2.Client(transport=transport))


def offline_async_openai() -> openai.AsyncOpenAI:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return fake_openai_response(request)

    transport = httpx2.MockTransport(handler)
    return openai.AsyncOpenAI(
        api_key=OFFLINE_KEY, http_client=httpx2.AsyncClient(transport=transport)
    )


# ---------------------------------------------------------------- the application
def create_app(telemetry: lli.Telemetry, *, mock_time_scale: float = 1.0) -> FastAPI:
    tracer = telemetry.tracer
    mock = ObservedMockClient(
        telemetry, MockLLMProvider(time_scale=mock_time_scale), max_attempts=3
    )
    sync_openai = ObservedOpenAIClient(
        telemetry, offline_openai(), provenance="synthetic", backoff_seconds=0.0
    )
    async_openai = ObservedAsyncOpenAIClient(
        telemetry, offline_async_openai(), provenance="synthetic", backoff_seconds=0.0
    )
    app = FastAPI(
        title="LLM Insight demo (SYNTHETIC)", docs_url=None, redoc_url=None, openapi_url=None
    )

    @app.middleware("http")
    async def request_span(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """The SERVER span of one incoming request. Metadata only: method, route template,
        scheme and status code; never the URL, query, headers or body. No exception events
        and no status description (both would carry exception text)."""
        if request.url.path in UNTRACED:
            return await call_next(request)
        method = request.method if request.method in HTTP_METHODS else "_OTHER"
        parent = PROPAGATOR.extract({"traceparent": request.headers.get("traceparent", "")})
        with tracer.start_as_current_span(
            method,
            context=parent,
            kind=SpanKind.SERVER,
            attributes={
                "http.request.method": method,
                "url.scheme": request.url.scheme,
                "llminsight.provenance": "synthetic",
            },
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                response = await call_next(request)
            except Exception as exc:
                span.set_attribute("error.type", type(exc).__qualname__)
                span.set_status(Status(StatusCode.ERROR))
                raise
            finally:
                route = request.scope.get("route")
                if route is not None and hasattr(route, "path"):
                    span.update_name(f"{method} {route.path}")
                    span.set_attribute("http.route", route.path)
            span.set_attribute("http.response.status_code", response.status_code)
            if response.status_code >= 500:
                span.set_attribute("error.type", str(response.status_code))
                span.set_status(Status(StatusCode.ERROR))
            return response

    def messages(body: SummarizeRequest) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": body.text},
        ]

    def answer(**fields: Any) -> dict[str, Any]:
        trace_id = format(trace.get_current_span().get_span_context().trace_id, "032x")
        return {"trace_id": trace_id, "provenance": "synthetic", **fields}

    def mock_call(body: SummarizeRequest, scenario: Scenario, **kw: Any) -> str:
        completion = mock.chat(
            model=MOCK_MODEL,
            messages=messages(body),
            scenario=scenario,
            prompt_name=PROMPT_NAME,
            prompt_version=PROMPT_VERSION,
            **kw,
        )
        return completion.text

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "live"}

    @app.post("/v1/summarize")
    def summarize(body: SummarizeRequest) -> dict[str, Any]:
        return answer(summary=mock_call(body, Scenario.SUCCESS), calls=1)

    @app.post("/v1/summarize/retry")
    def summarize_retry(body: SummarizeRequest) -> dict[str, Any]:
        return answer(summary=mock_call(body, Scenario.RETRY_THEN_SUCCESS), calls=1)

    @app.post("/v1/summarize/failure")
    def summarize_failure(body: SummarizeRequest) -> dict[str, Any]:
        try:
            summary: str | None = mock_call(body, Scenario.SERVER_ERROR, max_attempts=1)
        except MockProviderError:
            summary = None  # the provider failed; the application still answers
        return answer(summary=summary, fallback=summary is None, calls=1)

    @app.post("/v1/summarize/openai")
    def summarize_openai(body: SummarizeRequest) -> dict[str, Any]:
        completion = sync_openai.create_chat_completion(
            model=OPENAI_MODEL,
            messages=messages(body),  # type: ignore[arg-type]
            prompt_name=PROMPT_NAME,
            prompt_version=PROMPT_VERSION,
            service_tier="default",
        )
        return answer(summary=completion.choices[0].message.content, calls=1)

    @app.post("/v1/summarize/multi")
    async def summarize_multi(body: SummarizeRequest) -> dict[str, Any]:
        results = await asyncio.gather(
            mock.achat(model=MOCK_MODEL, messages=messages(body), prompt_version=PROMPT_VERSION),
            mock.achat(
                model=MOCK_MODEL,
                messages=messages(body),
                scenario=Scenario.DELAYED,
                prompt_version=PROMPT_VERSION,
            ),
            async_openai.create_chat_completion(
                model=OPENAI_MODEL,
                messages=messages(body),  # type: ignore[arg-type]
                prompt_version=PROMPT_VERSION,
                service_tier="default",
            ),
        )
        return answer(summaries=len(results), calls=len(results))

    return app


# ---------------------------------------------------------------- serve / drive
ROUTES = ("/v1/summarize", "/v1/summarize/multi", "/v1/summarize/failure",
          "/v1/summarize/retry", "/v1/summarize/openai")  # fmt: skip


@dataclasses.dataclass
class RunningServer:
    server: uvicorn.Server
    thread: threading.Thread
    url: str

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=15)


def serve_in_thread(app: FastAPI, port: int = 0) -> RunningServer:
    """The demo under uvicorn on a loopback port (0 = any free port)."""
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("demo server did not start")
        time.sleep(0.02)
    bound = server.servers[0].sockets[0].getsockname()[1]
    return RunningServer(server, thread, f"http://127.0.0.1:{bound}")


def call(base_url: str, route: str, text: str = "SYNTHETIC: LLM Insight demo input.") -> Any:
    req = urllib.request.Request(  # noqa: S310 - loopback demo server
        base_url + route,
        data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return {"http_status": resp.status, **json.loads(resp.read())}


def drive(telemetry: lli.Telemetry) -> dict[str, Any]:
    running = serve_in_thread(create_app(telemetry))
    try:
        results = {route: call(running.url, route) for route in ROUTES}
    finally:
        running.stop()
    delivered = telemetry.force_flush()
    return {
        "provenance": "synthetic",
        "requests": {
            r: {k: v[k] for k in ("http_status", "trace_id") if k in v} for r, v in results.items()
        },
        "export": {"delivered": delivered, "stats": dataclasses.asdict(telemetry.stats())},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m demo_app.web")
    parser.add_argument("--drive", action="store_true", help="one request per route, then exit")
    args = parser.parse_args(argv)
    if os.environ.get("LLMI_PROVIDER_MODE", "mock") != "mock":
        print("error: the demo is offline only (LLMI_PROVIDER_MODE=mock)", file=sys.stderr)
        return 2
    try:
        config = lli.LLMInsightConfig.from_env()
    except lli.ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)  # names the setting, never its value
        return 2
    telemetry = lli.init(config)
    try:
        if args.drive:
            print(json.dumps(drive(telemetry), sort_keys=True))
        else:
            port = int(os.environ.get("LLMI_DEMO_PORT", "8100"))
            uvicorn.run(create_app(telemetry), host="127.0.0.1", port=port, log_level="info")
    finally:
        telemetry.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
