"""SYNTHETIC, offline OpenRouter chat completion fixtures (ADR-46).

Nothing here talks to OpenRouter. The bodies follow the non-streaming response shape in
OpenRouter's API reference (read 2026-11-11), but every ID, count and text is made up and labelled
SYNTHETIC. Responses are served by a fake transport, and `tests.openai_fixtures.block_network`
makes any non-loopback connection attempt fail.
"""

from __future__ import annotations

import json
from typing import Any

from tests.groq_fixtures import FakeGroq, Step

# Assembled from parts and deliberately NOT in OpenRouter's `sk-or-` shape, so neither a secret
# scanner nor OpenRouter could take it for a key.
FAKE_OPENROUTER_KEY = "synthetic" + "-openrouter-offline-" + "0" * 24
OUTPUT_SENTINEL = "OPENROUTER-OUTPUT-SENTINEL-do-not-export-5a1e"
REASONING_SENTINEL = "OPENROUTER-REASONING-SENTINEL-do-not-export-c03b"
ERROR_SENTINEL = "OPENROUTER-ERROR-MESSAGE-SENTINEL-quoting-the-prompt-9d24"
UPSTREAM_SENTINEL = "OPENROUTER-UPSTREAM-PROVIDER-SENTINEL-7f10"
NATIVE_FINISH_SENTINEL = "OPENROUTER-NATIVE-FINISH-SENTINEL-2b6c"
RAW_SENTINEL = "OPENROUTER-METADATA-RAW-SENTINEL-e85a"
MODEL = "synthetic-org/synthetic-model-7b:free"
PAID_MODEL = "synthetic-org/synthetic-model-7b"
RESPONSE_ID = "gen-SYNTHETIC-openrouter-0001"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
SENTINELS = (
    FAKE_OPENROUTER_KEY,
    OUTPUT_SENTINEL,
    REASONING_SENTINEL,
    ERROR_SENTINEL,
    UPSTREAM_SENTINEL,
    NATIVE_FINISH_SENTINEL,
    RAW_SENTINEL,
)

USAGE: dict[str, Any] = {
    "prompt_tokens": 24,
    "completion_tokens": 40,
    "total_tokens": 64,
    "prompt_tokens_details": {"cached_tokens": 4, "cache_write_tokens": 2},
    "completion_tokens_details": {"reasoning_tokens": 8},
    "cost": 0,
    "is_byok": False,
    "cost_details": {"upstream_inference_cost": 0},
}


def openrouter_body(
    *,
    finish: str | None = "stop",
    content: str | None = OUTPUT_SENTINEL,
    usage: dict[str, Any] | None | bool = True,
    response_id: str | None = RESPONSE_ID,
    model: str | None = MODEL,
    choice_extra: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    choice: dict[str, Any] = {
        "index": 0,
        "message": {"role": "assistant", "content": content, "reasoning": REASONING_SENTINEL},
        "native_finish_reason": NATIVE_FINISH_SENTINEL,
        "logprobs": None,
    }
    if finish is not None:
        choice["finish_reason"] = finish
    choice.update(choice_extra or {})
    body: dict[str, Any] = {
        "object": "chat.completion",
        "created": 1_793_000_000,
        "choices": [choice],
        "provider": UPSTREAM_SENTINEL,
        "system_fingerprint": None,
    }
    if response_id is not None:
        body["id"] = response_id
    if model is not None:
        body["model"] = model
    if usage is True:
        body["usage"] = json.loads(json.dumps(USAGE))
    elif isinstance(usage, dict):
        body["usage"] = usage
    body.update(extra)
    return body


def usage_with(**changes: Any) -> dict[str, Any]:
    usage = json.loads(json.dumps(USAGE))
    for key, value in changes.items():
        if value is None:
            usage.pop(key, None)
        else:
            usage[key] = value
    return usage


def error_body(code: Any = 400, error_type: str | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = {"raw": RAW_SENTINEL, "provider_name": UPSTREAM_SENTINEL}
    if error_type is not None:
        metadata["error_type"] = error_type
    return {"error": {"code": code, "message": ERROR_SENTINEL, "metadata": metadata}}


def ok(**kw: Any) -> Step:
    return Step(200, openrouter_body(**kw))


def fail(status: int, error_type: str | None = None) -> Step:
    return Step(status, error_body(status, error_type))


class FakeOpenRouter(FakeGroq):
    """Scripted responses, one per physical request (the Groq fake; only the bodies differ)."""
