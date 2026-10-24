"""Attribute allowlist and GenAI normalization: `llminsight.attrmap.v1`.

TELEMETRY_CONTRACT.md §3 (ADR-14, ADR-15, ADR-19). Everything not allowlisted, every
always-denied key, every value of the wrong type, out of range or of the wrong format, every
duplicate key and every alias shadowed by its standard name is dropped before persistence and
counted. A dropped value is never stored, logged or echoed.

The GenAI conventions are Development status: all mapping lives in this module, and a
change to the tables below needs a new mapping version.
"""

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue

from app.services.ingestion import sensitive

SCHEMA_VERSION = "llminsight.attrmap.v1"

MAX_STRING = 256
MAX_ARRAY = 16

INFERENCE_OPERATIONS = frozenset({"chat", "text_completion", "generate_content", "embeddings"})
PROVENANCE_VALUES = frozenset({"real", "synthetic"})
USAGE_SOURCE_VALUES = frozenset({"provider_reported", "estimated", "unknown"})

INT32_MAX = 2**31 - 1  # usage_records.attempt_id is INT


@dataclass(frozen=True)
class Spec:
    """Expected type and format of an allowlisted attribute.

    `exact_max`: identity strings are dropped (not truncated) when longer, because a
    truncated identifier would be a different identifier.

    `text_class` (strings and string arrays only, R08 defence in depth):
    - "identifier": a fixed format without whitespace (`pattern`, full match); credential and
      personal-data checks apply; dropped, never truncated
    - "opaque": a provider/SDK-generated identifier (response ID, call ID, fingerprint);
      format and credential checks only, because random IDs can contain digit runs
    - "label": free text such as a service name; unsafe characters, credentials and personal
      data are dropped, otherwise truncated to MAX_STRING
    """

    kind: str  # "str" | "int" | "num" | "bool" | "str_array"
    exact_max: int | None = None
    min_value: int | None = None
    max_value: int | None = None
    choices: frozenset[str] | None = None
    non_empty: bool = False
    pattern: re.Pattern[str] | None = None  # full match; a mismatch is dropped, never repaired
    text_class: str = "identifier"


INT = Spec("int")
NUM = Spec("num")
BOOL = Spec("bool")
TOKENS = Spec("int", min_value=0)


def _identifier(pattern: str, max_len: int, *, opaque: bool = False) -> Spec:
    return Spec(
        "str",
        exact_max=max_len,
        non_empty=True,
        pattern=re.compile(pattern),
        text_class="opaque" if opaque else "identifier",
    )


LABEL = Spec("str", non_empty=True, text_class="label")

# Formats (Phase 1 final, R08). Model IDs cover snapshots ("gpt-4o-mini-2024-07-18"),
# fine-tunes ("ft:gpt-4o-mini:org:x:id"), paths ("models/gemini-1.5-pro",
# "meta-llama/Llama-3-8B") and versions ("claude-3-sonnet@20240229").
MODEL = _identifier(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]*", MAX_STRING)
PROVIDER = _identifier(r"[A-Za-z0-9][A-Za-z0-9._-]*", 64)  # usage provider LEN(64)
OPERATION = _identifier(r"[a-z][a-z0-9_.]*", 64)
KEYWORD = _identifier(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", 64)
VERSION = _identifier(r"[A-Za-z0-9][A-Za-z0-9._+-]*", 128)
LABEL_ID = _identifier(r"[A-Za-z0-9][A-Za-z0-9._:+/-]*", 128)
HOST = _identifier(r"[A-Za-z0-9\[][A-Za-z0-9._:\[\]-]*", 253)
SCHEME = _identifier(r"[A-Za-z][A-Za-z0-9+.-]*", 16)
# A route template: printable ASCII from '/', never a query ('?') or fragment ('#').
ROUTE = _identifier(r"/[\x21\x22\x24-\x3e\x40-\x7e]*", MAX_STRING)
RESPONSE_ID = _identifier(r"[A-Za-z0-9_][A-Za-z0-9._:/+=-]*", MAX_STRING, opaque=True)
# The contract defines llminsight.call_id as a UUID string (usage call_id <= 64).
CALL_ID = _identifier(r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}", 64, opaque=True)
FINGERPRINT = _identifier(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", 128, opaque=True)
FINISH_REASONS = Spec(
    "str_array", exact_max=64, non_empty=True, pattern=KEYWORD.pattern, text_class="identifier"
)

# Bounded identifiers instead of free text (Phase 1C). A prompt version is a label that
# Phase 4 compares with experiment versions (LEN(64)); truncating it would merge versions.
PROMPT_VERSION = _identifier(r"[A-Za-z0-9][A-Za-z0-9._:+/-]*", 64)
# semconv `error.type` is a low-cardinality identifier ("timeout", "500",
# "openai.RateLimitError", "_OTHER"); anything with spaces or control characters is a
# description, not a type, and is dropped (R21).
ERROR_TYPE_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:$/-]*")
ERROR_TYPE = Spec("str", exact_max=MAX_STRING, non_empty=True, pattern=ERROR_TYPE_RE)

RESOURCE_ALLOWLIST: dict[str, Spec] = {
    "service.name": LABEL,
    "service.version": VERSION,
    "service.namespace": LABEL,
    "deployment.environment.name": LABEL,
    "telemetry.sdk.name": KEYWORD,
    "telemetry.sdk.language": KEYWORD,
    "telemetry.sdk.version": VERSION,
    "llminsight.sdk.version": VERSION,
}

SPAN_ALLOWLIST: dict[str, Spec] = {
    # GenAI (Development semconv, pinned in ADR-15)
    "gen_ai.operation.name": OPERATION,
    "gen_ai.provider.name": PROVIDER,
    "gen_ai.request.model": MODEL,
    "gen_ai.response.model": MODEL,
    "gen_ai.response.id": RESPONSE_ID,
    "gen_ai.response.finish_reasons": FINISH_REASONS,
    "gen_ai.request.max_tokens": Spec("int", min_value=0),  # a count, not a credential
    "gen_ai.request.temperature": NUM,
    "gen_ai.request.top_p": NUM,
    "gen_ai.request.seed": INT,
    "gen_ai.request.stream": BOOL,
    "gen_ai.output.type": KEYWORD,
    "gen_ai.prompt.name": LABEL_ID,
    "gen_ai.prompt.version": PROMPT_VERSION,
    "gen_ai.usage.input_tokens": TOKENS,
    "gen_ai.usage.output_tokens": TOKENS,
    "gen_ai.usage.cache_read.input_tokens": TOKENS,
    "gen_ai.usage.cache_write.input_tokens": TOKENS,
    "gen_ai.usage.reasoning.output_tokens": TOKENS,
    # Stable core
    "error.type": ERROR_TYPE,
    "server.address": HOST,
    "server.port": INT,
    # OpenAI conventions
    "openai.request.service_tier": KEYWORD,
    "openai.response.service_tier": KEYWORD,
    "openai.response.system_fingerprint": FINGERPRINT,
    "openai.api.type": KEYWORD,
    # HTTP (root span)
    "http.request.method": KEYWORD,
    "http.route": ROUTE,
    "http.response.status_code": INT,
    "url.scheme": SCHEME,
    # LLM Insight
    "llminsight.call_id": CALL_ID,
    "llminsight.attempt": Spec("int", min_value=1, max_value=INT32_MAX),
    "llminsight.provenance": Spec("str", choices=PROVENANCE_VALUES),
    "llminsight.usage_source": Spec("str", choices=USAGE_SOURCE_VALUES),
    "llminsight.validation.rule": LABEL_ID,
    "llminsight.validation.passed": BOOL,
}

# Span and scope fields outside the attribute map (normalize.py). Unsafe values are replaced
# by these documented fallbacks (name) or NULL (scope), never stored.
SPAN_NAME = LABEL
SCOPE_NAME = LABEL
SCOPE_VERSION = Spec("str", non_empty=True, text_class="label")
REDACTED_SPAN_NAME = "redacted_span"

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

# Credential classification (Phase 1C), replacing the substring rule that also matched
# token *counters* such as `gen_ai.request.max_tokens`. A key is split into lower-case words
# at '.', '_', '-' and camelCase boundaries, then:
# - a credential stem anywhere in the key (no counter or metadata key contains one);
# - a credential word, e.g. `token` (singular: `auth.token`, `access_token`);
# - `tokens` / `key(s)` qualified by a credential word (`access_tokens`, `signing_key`).
# Plural `tokens` after any other word is a count (`input_tokens`, `max_tokens`) and passes.
_CREDENTIAL_STEMS = (
    "password",
    "passwd",
    "secret",
    "authorization",
    "apikey",
    "api_key",
    "credential",
    "cookie",
    "privatekey",
    "private_key",
)
_CREDENTIAL_WORDS = frozenset({"token", "auth", "bearer", "jwt", "pwd"})
_CREDENTIAL_QUALIFIERS = frozenset(
    {"access", "refresh", "id", "session", "auth", "bearer", "api", "oauth", "csrf", "xsrf"}
    | {"secret", "private", "signing", "encryption", "client", "master", "security"}
)
_QUALIFIED_WORDS = frozenset({"tokens", "key", "keys"})
_CAMEL = re.compile(r"([a-z0-9])([A-Z])")
_WORD_SPLIT = re.compile(r"[._\-\s/:]+")


def _words(key: str) -> list[str]:
    return [w for w in _WORD_SPLIT.split(_CAMEL.sub(r"\1_\2", key).lower()) if w]


def is_credential_key(key: str) -> bool:
    flat = key.lower().replace("-", "_")
    if any(stem in flat for stem in _CREDENTIAL_STEMS):
        return True
    words = _words(key)
    for i, w in enumerate(words):
        if w in _CREDENTIAL_WORDS:
            return True
        if w in _QUALIFIED_WORDS and i > 0 and words[i - 1] in _CREDENTIAL_QUALIFIERS:
            return True
    return False


def is_denied(key: str) -> bool:
    k = key.lower()
    if any(k == d or k.startswith(d + ".") for d in _DENY_EXACT):
        return True
    if k.startswith(_DENY_PREFIX):
        return True
    return is_credential_key(key)


# ---------------------------------------------------------------- value handling
# U+0000 (which PostgreSQL TEXT/JSONB cannot hold) is a control character, so `safe_string`
# drops any value containing it instead of storing a repaired copy.
_MISSING = object()


def safe_string(spec: Spec, s: str, limit: int = MAX_STRING) -> str | None:
    """The value to store for an untrusted string, or None if it must be dropped.

    Every check runs on the complete original value, before any truncation, so a secret can
    never survive as a truncated prefix. Rejected values are returned to nobody."""
    if spec.non_empty and not s:
        return None
    if spec.choices is not None:
        return s if s in spec.choices else None  # fixed vocabulary: nothing else to check
    if sensitive.has_unsafe_characters(s) or sensitive.contains_credential(s):
        return None
    if spec.text_class != "opaque" and sensitive.contains_personal_data(
        s, free_text=spec.text_class == "label"
    ):
        return None
    if spec.exact_max is not None and len(s) > spec.exact_max:
        return None
    if spec.pattern is not None and not spec.pattern.fullmatch(s):
        return None
    return s if spec.exact_max is not None else s[:limit]


def _coerce(spec: Spec, v: AnyValue) -> Any:
    which = v.WhichOneof("value")
    if spec.kind == "str":
        if which != "string_value":
            return _MISSING
        value = safe_string(spec, v.string_value)
        return _MISSING if value is None else value
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
        item_spec = replace(spec, kind="str")
        kept = [safe_string(item_spec, i.string_value) for i in items[:MAX_ARRAY]]
        return _MISSING if any(k is None for k in kept) else kept
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
