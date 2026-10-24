"""SYNTHETIC, offline OpenAI Chat Completions fixtures for the adapter tests.

Nothing here talks to OpenAI. The response bodies follow the Chat Completions response
shape of `openai` 3.28.0 (`openai.types.chat.ChatCompletion`), but every ID, count and text
is made up and labelled SYNTHETIC. Responses are served by an `httpx2.MockTransport`, and
`block_network` makes any non-loopback connection attempt fail, so a request that escaped
the fake transport would fail the test instead of reaching the internet.
"""

from __future__ import annotations

import json
import socket
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx2
import openai

# A syntactically plausible but fake key, assembled so no secret scanner sees a literal.
FAKE_API_KEY = "sk" + "-synthetic-offline-" + "0" * 32
COMPLETION_SENTINEL = "COMPLETION-SENTINEL-do-not-export-5d1e"
TOOL_SENTINEL = "TOOL-DEFINITION-SENTINEL-c0ffee"
ERROR_SENTINEL = "PROVIDER-ERROR-MESSAGE-SENTINEL-quoting-the-prompt-77aa"
REQUEST_ID_SENTINEL = "req_SYNTHETIC_request_id_sentinel"


def completion_body(
    *,
    model: str = "gpt-5.4-mini-2026-03-17",
    usage: dict[str, Any] | None | bool = True,
    service_tier: str | None = "default",
    response_id: str | None = None,
    finish_reason: str = "stop",
) -> dict[str, Any]:
    """A successful completion. `usage=True` gives the T19 example: 1200 input tokens (1000
    cached), 300 output tokens (200 reasoning)."""
    body: dict[str, Any] = {
        "id": response_id or f"chatcmpl-SYNTHETIC-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": 1_792_900_000,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": COMPLETION_SENTINEL},
                "finish_reason": finish_reason,
                "logprobs": None,
            }
        ],
        "system_fingerprint": "fp_synthetic01",
    }
    if service_tier is not None:
        body["service_tier"] = service_tier
    if usage is True:
        body["usage"] = {
            "prompt_tokens": 1200,
            "completion_tokens": 300,
            "total_tokens": 1500,
            "prompt_tokens_details": {"cached_tokens": 1000, "audio_tokens": 0},
            "completion_tokens_details": {
                "reasoning_tokens": 200,
                "audio_tokens": 0,
                "accepted_prediction_tokens": 0,
                "rejected_prediction_tokens": 0,
            },
        }
    elif isinstance(usage, dict):
        body["usage"] = usage
    return body


def error_body(kind: str = "rate_limit_error") -> dict[str, Any]:
    """Provider error bodies can quote the request; the adapter must never export them."""
    return {"error": {"message": ERROR_SENTINEL, "type": kind, "code": kind, "param": None}}


@dataclass
class Step:
    status: int = 200
    body: dict[str, Any] | None = None
    raise_: type[Exception] | None = None  # an httpx2 transport error class


@dataclass
class FakeOpenAI:
    """Scripted responses, one per physical request; records every request it receives."""

    steps: list[Step]
    requests: list[httpx2.Request] = field(default_factory=list)

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if not self.steps:
            raise AssertionError("more physical requests than scripted responses")
        step = self.steps.pop(0)
        if step.raise_ is not None:
            raise step.raise_("SYNTHETIC transport failure", request=request)
        headers = {"x-request-id": REQUEST_ID_SENTINEL}
        return httpx2.Response(step.status, json=step.body or {}, headers=headers)

    def request_json(self, i: int) -> dict[str, Any]:
        return json.loads(self.requests[i].content)

    def client(self) -> openai.OpenAI:
        return openai.OpenAI(
            api_key=FAKE_API_KEY,
            http_client=httpx2.Client(transport=httpx2.MockTransport(self.handler)),
        )

    def async_client(self) -> openai.AsyncOpenAI:
        async def handler(request: httpx2.Request) -> httpx2.Response:
            return self.handler(request)

        return openai.AsyncOpenAI(
            api_key=FAKE_API_KEY,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        )


def ok(**kw: Any) -> Step:
    return Step(200, completion_body(**kw))


def fail(status: int, kind: str = "api_error") -> Step:
    return Step(status, error_body(kind))


# ---------------------------------------------------------------- network prohibition
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class NetworkBlocked(OSError):
    pass


@contextmanager
def block_network(attempts: list[str]) -> Iterator[None]:
    """Refuse every non-loopback name resolution and connection; record what was tried."""
    real_getaddrinfo: Callable[..., Any] = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if str(host) not in LOOPBACK:
            attempts.append(str(host))
            raise NetworkBlocked(f"offline test: resolving {host} is not allowed")
        return real_getaddrinfo(host, *args, **kwargs)

    def guard(address: Any) -> None:
        host = address[0] if isinstance(address, tuple) else address
        if str(host) not in LOOPBACK:
            attempts.append(str(host))
            raise NetworkBlocked(f"offline test: connecting to {host} is not allowed")

    def connect(self: socket.socket, address: Any) -> None:
        guard(address)
        real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        guard(address)
        return real_connect_ex(self, address)

    socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    try:
        yield
    finally:
        socket.getaddrinfo = real_getaddrinfo
        socket.socket.connect = real_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = real_connect_ex  # type: ignore[method-assign]
