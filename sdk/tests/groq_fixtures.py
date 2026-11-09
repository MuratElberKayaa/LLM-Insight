"""SYNTHETIC, offline Groq chat completion fixtures (Phase 8A-2).

Nothing here talks to Groq. The bodies follow the response shape in Groq's API reference (read
2026-11-09), but every ID, count and text is made up and labelled SYNTHETIC. Responses are served
by an `httpx.MockTransport`, and `tests.openai_fixtures.block_network` makes any non-loopback
connection attempt fail, so a request that escaped the fake transport would fail the test.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

# A fake key, assembled from parts and deliberately NOT in the shape of a real Groq key, so
# neither a secret scanner nor Groq could take it for one.
FAKE_GROQ_KEY = "synthetic" + "-groq-offline-" + "0" * 24
OUTPUT_SENTINEL = "GROQ-OUTPUT-SENTINEL-do-not-export-1d7c"
REASONING_SENTINEL = "GROQ-REASONING-SENTINEL-do-not-export-82fa"
ERROR_SENTINEL = "GROQ-ERROR-MESSAGE-SENTINEL-quoting-the-prompt-6e31"
GENERATION_SENTINEL = "GROQ-FAILED-GENERATION-SENTINEL-do-not-export-40bd"
MODEL = "openai/gpt-oss-20b"
RESPONSE_ID = "chatcmpl-SYNTHETIC-groq-0001"
ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

USAGE = {
    "prompt_tokens": 20,
    "completion_tokens": 32,
    "total_tokens": 52,
    "queue_time": 0.01,
    "prompt_time": 0.02,
    "completion_time": 0.03,
    "total_time": 0.06,
}


def groq_body(
    *,
    finish: str | None = "stop",
    content: str | None = OUTPUT_SENTINEL,
    usage: dict[str, Any] | None | bool = True,
    response_id: str | None = RESPONSE_ID,
    model: str | None = MODEL,
    **extra: Any,
) -> dict[str, Any]:
    choice: dict[str, Any] = {
        "index": 0,
        "message": {"role": "assistant", "content": content, "reasoning": REASONING_SENTINEL},
        "logprobs": None,
    }
    if finish is not None:
        choice["finish_reason"] = finish
    body: dict[str, Any] = {
        "object": "chat.completion",
        "created": 1_792_900_000,
        "choices": [choice],
        "service_tier": "on_demand",
        "system_fingerprint": "fp_synthetic01",
        "x_groq": {"id": "req_SYNTHETIC_groq"},
    }
    if response_id is not None:
        body["id"] = response_id
    if model is not None:
        body["model"] = model
    if usage is True:
        body["usage"] = dict(USAGE)
    elif isinstance(usage, dict):
        body["usage"] = usage
    body.update(extra)
    return body


def error_body(error_type: str = "invalid_request_error", **extra: Any) -> dict[str, Any]:
    return {
        "error": {
            "message": ERROR_SENTINEL,
            "type": error_type,
            "failed_generation": GENERATION_SENTINEL,
            **extra,
        }
    }


@dataclass
class Step:
    status: int = 200
    body: Any = None
    raise_: type[BaseException] | None = None  # raised in place of a response
    raw: bytes | None = None
    stream: httpx.SyncByteStream | None = None
    content_type: str = "application/json"
    headers: dict[str, str] = field(default_factory=dict)


def ok(**kw: Any) -> Step:
    return Step(200, groq_body(**kw))


def fail(status: int, error_type: str = "invalid_request_error", **extra: Any) -> Step:
    return Step(status, error_body(error_type, **extra))


@dataclass
class FakeGroq:
    """Scripted responses, one per physical request; records every request it receives."""

    steps: list[Step]
    requests: list[httpx.Request] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.steps:
            raise AssertionError("more physical requests than scripted responses")
        step = self.steps.pop(0)
        if step.raise_ is not None:
            if issubclass(step.raise_, httpx.RequestError):
                raise step.raise_("SYNTHETIC transport failure", request=request)
            raise step.raise_()
        headers = {"content-type": step.content_type, **step.headers}
        if step.stream is not None:
            return httpx.Response(step.status, stream=step.stream, headers=headers)
        content = step.raw if step.raw is not None else json.dumps(step.body).encode()
        return httpx.Response(step.status, content=content, headers=headers)

    def request_json(self, i: int) -> dict[str, Any]:
        return json.loads(self.requests[i].content)

    def http_client(self, **kw: Any) -> httpx.Client:
        kw.setdefault("trust_env", False)
        kw.setdefault("follow_redirects", False)
        return httpx.Client(transport=httpx.MockTransport(self.handler), **kw)
