"""Attribute allowlist and GenAI normalization: `llminsight.attrmap.v1`.

TELEMETRY_CONTRACT.md §3 (ADR-14, ADR-15, ADR-19). Everything not allowlisted, every
always-denied key, every value of the wrong type or out of range, every duplicate key and
every alias shadowed by its standard name is dropped before persistence and counted. A
dropped value is never stored, logged or echoed.

The GenAI conventions are Development status: all mapping lives in this module, and a
change to the tables below needs a new mapping version.
"""

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue

SCHEMA_VERSION = "llminsight.attrmap.v1"

MAX_STRING = 256
MAX_ARRAY = 16

INFERENCE_OPERATIONS = frozenset({"chat", "text_completion", "generate_content", "embeddings"})
PROVENANCE_VALUES = frozenset({"real", "synthetic"})
USAGE_SOURCE_VALUES = frozenset({"provider_reported", "estimated", "unknown"})

INT32_MAX = 2**31 - 1  # usage_records.attempt_id is INT


@dataclass(frozen=True)
class Spec:
    """Expected type of an allowlisted attribute.

    `exact_max`: identity strings are dropped (not truncated) when longer, because a
    truncated identifier would be a different identifier.
    """

    kind: str  # "str" | "int" | "num" | "bool" | "str_array"
    exact_max: int | None = None
    min_value: int | None = None
    max_value: int | None = None
    choices: frozenset[str] | None = None
    non_empty: bool = False


STR = Spec("str")
INT = Spec("int")
NUM = Spec("num")
BOOL = Spec("bool")
STR_ARRAY = Spec("str_array")
TOKENS = Spec("int", min_value=0)

RESOURCE_ALLOWLIST: dict[str, Spec] = {
    k: STR
    for k in (
        "service.name",
        "service.version",
        "service.namespace",
        "deployment.environment.name",
        "telemetry.sdk.name",
        "telemetry.sdk.language",
        "telemetry.sdk.version",
        "llminsight.sdk.version",
    )
}

SPAN_ALLOWLIST: dict[str, Spec] = {
    # GenAI (Development semconv, pinned in ADR-15)
    "gen_ai.operation.name": Spec("str", non_empty=True),
    "gen_ai.provider.name": Spec("str", exact_max=64, non_empty=True),  # usage provider LEN(64)
    "gen_ai.request.model": STR,
    "gen_ai.response.model": STR,
    "gen_ai.response.id": Spec("str", exact_max=MAX_STRING, non_empty=True),
    "gen_ai.response.finish_reasons": STR_ARRAY,
    "gen_ai.request.max_tokens": INT,  # also matched by the always-deny "token" rule: see below
    "gen_ai.request.temperature": NUM,
    "gen_ai.request.top_p": NUM,
    "gen_ai.request.seed": INT,
    "gen_ai.request.stream": BOOL,
    "gen_ai.output.type": STR,
    "gen_ai.prompt.name": STR,
    "gen_ai.prompt.version": STR,
    "gen_ai.usage.input_tokens": TOKENS,
    "gen_ai.usage.output_tokens": TOKENS,
    "gen_ai.usage.cache_read.input_tokens": TOKENS,
    "gen_ai.usage.cache_write.input_tokens": TOKENS,
    "gen_ai.usage.reasoning.output_tokens": TOKENS,
    # Stable core
    "error.type": STR,
    "server.address": STR,
    "server.port": INT,
    # OpenAI conventions
    "openai.request.service_tier": STR,
    "openai.response.service_tier": STR,
    "openai.response.system_fingerprint": STR,
    "openai.api.type": STR,
    # HTTP (root span)
    "http.request.method": STR,
    "http.route": STR,
    "http.response.status_code": INT,
    "url.scheme": STR,
    # LLM Insight
    "llminsight.call_id": Spec("str", exact_max=64, non_empty=True),  # usage call_id <= 64
    "llminsight.attempt": Spec("int", min_value=1, max_value=INT32_MAX),
    "llminsight.provenance": Spec("str", choices=PROVENANCE_VALUES),
    "llminsight.usage_source": Spec("str", choices=USAGE_SOURCE_VALUES),
    "llminsight.validation.rule": STR,
    "llminsight.validation.passed": BOOL,
}

# ADR-14: legacy name -> standard name. The standard name always wins when both are present.
SPAN_ALIASES: dict[str, str] = {
    "gen_ai.system": "gen_ai.provider.name",
    "gen_ai.usage.prompt_tokens": "gen_ai.usage.input_tokens",
    "gen_ai.usage.completion_tokens": "gen_ai.usage.output_tokens",
}

# ---------------------------------------------------------------- always denied (ADR-19)
# Checked before the allowlist, so an allowlist mistake can never store these keys.
_DENY_EXACT = (
    "gen_ai.input.messages",
    "gen_ai.output.messages",
    "gen_ai.system_instructions",
    "gen_ai.tool.definitions",
    "gen_ai.prompt.variable",
    "gen_ai.conversation.id",
    "url.full",
    "url.query",
    "client.address",
    "exception.message",
    "exception.stacktrace",
)
_DENY_PREFIX = ("http.request.header.", "http.response.header.", "user.", "enduser.")
_DENY_SUBSTRING = ("authorization", "api_key", "apikey", "password", "secret", "token")
# The contract's only exception to the substring rule: the explicit gen_ai.usage.*_tokens keys
# (standard and legacy alias names). `gen_ai.request.max_tokens` is NOT one of them, so the
# literal deny rule drops it even though §3 also allowlists it (deny overrides, SECURITY.md §1).
_TOKEN_COUNT_KEYS = frozenset(
    k for k in (*SPAN_ALLOWLIST, *SPAN_ALIASES) if k.startswith("gen_ai.usage.")
)


def is_denied(key: str) -> bool:
    k = key.lower()
    if any(k == d or k.startswith(d + ".") for d in _DENY_EXACT):
        return True
    if k.startswith(_DENY_PREFIX):
        return True
    if key in _TOKEN_COUNT_KEYS:
        return False
    return any(s in k for s in _DENY_SUBSTRING)


# ---------------------------------------------------------------- value handling
def clean_str(s: str, limit: int = MAX_STRING) -> str:
    """Truncate to `limit` characters; PostgreSQL TEXT/JSONB cannot hold U+0000."""
    return s[:limit].replace("\x00", "�")


_SECRETISH = re.compile(
    r"(?i)(bearer\s+)[^\s,;]+"
    r"|\bsk-[A-Za-z0-9_\-]{8,}"
    r"|\blli_[A-Za-z0-9]{8}_[A-Za-z0-9_\-]+"
    r"|((?:api[_-]?key|authorization|password|secret|token)\s*[=:]\s*(?:bearer\s+|basic\s+)?)"
    r"[^\s,;]+"
)


def scrub_free_text(s: str, limit: int = MAX_STRING) -> str:
    """Best-effort masking of credential-like substrings in free text (span status messages).
    Not a guarantee: SECURITY.md §1 treats redaction as best effort."""

    def _mask(m: re.Match[str]) -> str:
        keep = m.group(1) or m.group(2) or ""
        return f"{keep}[REDACTED]"

    return clean_str(_SECRETISH.sub(_mask, s), limit)


_MISSING = object()


def _coerce(spec: Spec, v: AnyValue) -> Any:
    which = v.WhichOneof("value")
    if spec.kind == "str":
        if which != "string_value":
            return _MISSING
        s = v.string_value
        if spec.non_empty and not s:
            return _MISSING
        if spec.exact_max is not None:
            if len(s) > spec.exact_max or "\x00" in s:
                return _MISSING
            return s
        if spec.choices is not None and s not in spec.choices:
            return _MISSING
        return clean_str(s)
    if spec.kind == "int":
        if which != "int_value":
            return _MISSING
        n = v.int_value
        if spec.min_value is not None and n < spec.min_value:
            return _MISSING
        if spec.max_value is not None and n > spec.max_value:
            return _MISSING
        return n
    if spec.kind == "num":
        if which == "int_value":
            return v.int_value
        if which == "double_value" and math.isfinite(v.double_value):
            return v.double_value  # JSONB has no NaN/Infinity
        return _MISSING
    if spec.kind == "bool":
        return v.bool_value if which == "bool_value" else _MISSING
    if spec.kind == "str_array":
        if which != "array_value":
            return _MISSING
        items = v.array_value.values
        if any(i.WhichOneof("value") != "string_value" for i in items):
            return _MISSING
        return [clean_str(i.string_value) for i in items[:MAX_ARRAY]]
    raise AssertionError(f"unknown spec kind {spec.kind}")


@dataclass(frozen=True)
class Mapped:
    values: dict[str, Any]
    dropped: int


def map_attributes(
    kvs: Iterable[KeyValue], allowlist: dict[str, Spec], aliases: dict[str, str] | None = None
) -> Mapped:
    aliases = aliases or {}
    chosen: dict[str, tuple[bool, Any]] = {}  # normalized key -> (from standard name, value)
    dropped = 0
    for kv in kvs:
        if is_denied(kv.key):
            dropped += 1
            continue
        standard = aliases.get(kv.key)
        target = standard or kv.key
        spec = allowlist.get(target)
        if spec is None:
            dropped += 1
            continue
        value = _coerce(spec, kv.value)
        if value is _MISSING:
            dropped += 1
            continue
        is_standard = standard is None
        prev = chosen.get(target)
        if prev is None:
            chosen[target] = (is_standard, value)
        elif is_standard and not prev[0]:
            chosen[target] = (True, value)  # standard name overrides an earlier alias
            dropped += 1
        else:
            dropped += 1  # duplicate key, or alias shadowed by the standard name
    return Mapped({k: v for k, (_, v) in chosen.items()}, dropped)


def map_resource(kvs: Iterable[KeyValue]) -> Mapped:
    return map_attributes(kvs, RESOURCE_ALLOWLIST)


def map_span(kvs: Iterable[KeyValue]) -> Mapped:
    return map_attributes(kvs, SPAN_ALLOWLIST, SPAN_ALIASES)


def is_inference(attributes: dict[str, Any]) -> bool:
    """ADR-13: detection by `gen_ai.operation.name` only, never by span name or kind."""
    return attributes.get("gen_ai.operation.name") in INFERENCE_OPERATIONS
