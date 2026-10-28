"""SYNTHETIC, offline Gemini Developer API fixtures (Phase 6C-A).

Nothing here talks to Google. The bodies follow the `generateContent` REST response shape
that google-genai 2.30.1 parses (`GenerateContentResponse`), but every ID, count and text is
made up and labelled SYNTHETIC. Responses are served by an `httpx.MockTransport`, and
`tests.openai_fixtures.block_network` makes any non-loopback connection attempt fail, so a
request that escaped the fake transport would fail the test instead of reaching the internet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
from google import genai
from google.genai import types as genai_types

# A fake key, assembled from parts and deliberately NOT in the `AIza...` shape of a real
# Google API key, so neither a secret scanner nor Google could take it for one.
FAKE_GEMINI_KEY = "synthetic" + "-gemini-offline-" + "0" * 24
OUTPUT_SENTINEL = "GEMINI-OUTPUT-SENTINEL-do-not-export-3b9e"
THOUGHT_SENTINEL = "GEMINI-THOUGHT-SENTINEL-do-not-export-a41c"
ERROR_SENTINEL = "GEMINI-ERROR-MESSAGE-SENTINEL-quoting-the-prompt-5c0d"
MODEL = "gemini-3.1-flash-lite"
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

# prompt 20 (4 cached), candidates 2, thoughts 30, total 52 = 20 + 2 + 30
USAGE = {
    "promptTokenCount": 20,
    "candidatesTokenCount": 2,
    "thoughtsTokenCount": 30,
    "cachedContentTokenCount": 4,
    "totalTokenCount": 52,
}


def candidate(finish: str | None = "STOP", **extra: Any) -> dict[str, Any]:
    c: dict[str, Any] = {
        "content": {
            "parts": [{"text": THOUGHT_SENTINEL, "thought": True}, {"text": OUTPUT_SENTINEL}],
            "role": "model",
        },
        "index": 0,
        "safetyRatings": [{"category": "HARM_CATEGORY_HARASSMENT", "probability": "NEGLIGIBLE"}],
    }
    if finish is not None:
        c["finishReason"] = finish
    c.update(extra)
    return c


def gemini_body(
    *,
    finish: str | None = "STOP",
    usage: dict[str, Any] | None | bool = True,
    response_id: str | None = "SYNTHETICgeminiResp01",
    model_version: str | None = MODEL,
    candidates: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "candidates": candidates if candidates is not None else [candidate(finish)]
    }
    if usage is True:
        body["usageMetadata"] = dict(USAGE)
    elif isinstance(usage, dict):
        body["usageMetadata"] = usage
    if response_id is not None:
        body["responseId"] = response_id
    if model_version is not None:
        body["modelVersion"] = model_version
    body.update(extra)
    return body


def error_body(code: int, status: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": ERROR_SENTINEL, "status": status}}


@dataclass
class GStep:
    status: int = 200
    body: dict[str, Any] | None = None
    raise_: type[Exception] | None = None  # an httpx transport error class
    raw: bytes | None = None
    content_type: str = "application/json"
    headers: dict[str, str] = field(default_factory=dict)


def g_ok(**kw: Any) -> GStep:
    return GStep(200, gemini_body(**kw))


def g_fail(code: int, status: str) -> GStep:
    return GStep(code, error_body(code, status))


@dataclass
class FakeGemini:
    """Scripted responses, one per physical request; records every request it receives."""

    steps: list[GStep]
    requests: list[httpx.Request] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.steps:
            raise AssertionError("more physical requests than scripted responses")
        step = self.steps.pop(0)
        if step.raise_ is not None:
            raise step.raise_("SYNTHETIC transport failure", request=request)
        if step.raw is not None:
            headers = {"content-type": step.content_type, **step.headers}
            return httpx.Response(step.status, content=step.raw, headers=headers)
        return httpx.Response(step.status, json=step.body or {}, headers=step.headers)

    def request_json(self, i: int) -> dict[str, Any]:
        return json.loads(self.requests[i].content)

    def client(self, **http: Any) -> genai.Client:
        return genai.Client(
            vertexai=False,
            api_key=FAKE_GEMINI_KEY,
            http_options=genai_types.HttpOptions(
                httpx_client=httpx.Client(transport=httpx.MockTransport(self.handler)), **http
            ),
        )
