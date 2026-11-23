"""Pseudonymous session identity (Phase 10C, ADR-56). Pure apart from the key it is given.

The OTel GenAI attribute `gen_ai.conversation.id` stays on the ADR-19 deny list for storage: its
raw value is never stored, logged or echoed. At ingest, a valid raw value is replaced by

    llminsight.session.hash = "s1_" + hex(HMAC-SHA256(K_session, project_id || 0x00 || raw))[:32]

where K_session = HMAC-SHA256(LLMI_KEY_PEPPER, "llminsight.session.v1") is a purpose-bound key.
Consequences:
- the same conversation ID maps to the same hash within one project and to unrelated hashes in
  other projects (no cross-project linkage);
- the hash cannot be reversed without the pepper; a guessable ID (a counter, an email) can still
  be confirmed by someone who holds the pepper, so the hash is a pseudonym, not anonymous data;
- rotating the pepper changes every future hash (old and new sessions are not linked).
A client cannot supply the hash itself: `llminsight.session.hash` is not allowlisted, so a sent
value is dropped and counted like any unknown key.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Iterable
from typing import Any

from opentelemetry.proto.common.v1.common_pb2 import KeyValue

SOURCE_KEY = "gen_ai.conversation.id"
HASH_KEY = "llminsight.session.hash"
PREFIX = "s1_"
HEX_CHARS = 32  # 128 bits
MAX_RAW = 256  # a longer "ID" is treated as content and dropped
HASH_PATTERN = r"^s1_[0-9a-f]{32}$"

Pseudonymizer = Callable[[str], str]


def session_key(pepper: bytes) -> bytes:
    return hmac.new(pepper, b"llminsight.session.v1", hashlib.sha256).digest()


def pseudonymizer(pepper: bytes, project_id: int) -> Pseudonymizer:
    key = session_key(pepper)
    prefix = str(project_id).encode("ascii") + b"\x00"

    def apply(raw: str) -> str:
        digest = hmac.new(key, prefix + raw.encode("utf-8"), hashlib.sha256).hexdigest()
        return PREFIX + digest[:HEX_CHARS]

    return apply


def raw_session_id(kvs: Iterable[KeyValue]) -> tuple[str | None, bool]:
    """(raw value, present). The value is usable only when the key occurs exactly once with a
    non-empty string of at most MAX_RAW characters and no control characters."""
    values: list[Any] = [kv.value for kv in kvs if kv.key == SOURCE_KEY]
    if not values:
        return None, False
    if len(values) != 1 or values[0].WhichOneof("value") != "string_value":
        return None, True
    raw = values[0].string_value
    if not raw or len(raw) > MAX_RAW or any(ord(c) < 32 or ord(c) == 127 for c in raw):
        return None, True
    return raw, True
