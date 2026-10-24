"""Client-side metadata value rules (a mirror of TELEMETRY_CONTRACT.md §3.1, R08).

The backend is authoritative and re-checks every value. This copy runs *before* a value
leaves the process, so that:

- a value the backend would withhold is never sent (no `redacted_span`, no silent drops);
- an obvious credential or personal-data shape in metadata never reaches the network.

A rejected value is withheld and counted by the caller; it is never logged or echoed.
The SDK cannot import the backend (ADR-01), so the rules are duplicated here. The
live-backend integration tests check that SDK-emitted spans have
`server_dropped_attributes_count = 0`, which catches drift between the two copies.

These are heuristics for obvious formats only. They cannot detect every secret.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------- credentials (every field)
_CREDENTIAL_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?<![A-Za-z0-9])lli_[A-Za-z0-9]{8}_[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Z])"),
    re.compile(r"(?<![A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    re.compile(r"(?<![A-Za-z0-9])xox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"(?<![A-Za-z0-9])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY"),
    re.compile(r"(?i)\b(?:bearer|basic)\s+(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@"),
    re.compile(
        r"(?i)(?<![a-z0-9])(?:api[_-]?key|apikey|secret|client[_-]?secret|password|passwd|pwd"
        r"|token|access[_-]?token|refresh[_-]?token|authorization|credentials?)"
        r"\s*[=:]\s*[^\s,;&]{3,}"
    ),
)

# ---------------------------------------------------------------- personal data
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_LONG_DIGIT_RUN = re.compile(r"\d{11,}")
_INTL_PHONE = re.compile(r"\+\d[\d ().-]{7,}\d")
_GROUPED_DIGITS = re.compile(r"(?<![\d.])\d{2,4}(?:[ .-]\d{2,4}){2,6}(?![\d])")
_SPACED_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: [A-Z0-9]{4}){3,8}(?: [A-Z0-9]{1,4})?\b")

_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cs", "Zl", "Zp"})


def contains_credential(value: str) -> bool:
    return any(p.search(value) for p in _CREDENTIAL_PATTERNS)


def contains_personal_data(value: str, *, free_text: bool) -> bool:
    if _EMAIL.search(value) or _LONG_DIGIT_RUN.search(value):
        return True
    if not free_text:
        return False
    if _SPACED_IBAN.search(value):
        return True
    for pattern in (_INTL_PHONE, _GROUPED_DIGITS):
        if any(sum(c.isdigit() for c in m.group(0)) >= 10 for m in pattern.finditer(value)):
            return True
    return False


def has_unsafe_characters(value: str) -> bool:
    return any(unicodedata.category(c) in _UNSAFE_CATEGORIES for c in value)


# ---------------------------------------------------------------- per-field rules (§3.1 table)
@dataclass(frozen=True)
class Rule:
    """`identifier`: full-match format, dropped when too long; `opaque`: generated IDs, which
    skip the personal-data layer only; `label`: free text, truncated after the checks."""

    text_class: Literal["identifier", "opaque", "label"]
    max_len: int
    pattern: re.Pattern[str] | None = None


def _identifier(pattern: str, max_len: int) -> Rule:
    return Rule("identifier", max_len, re.compile(pattern))


def _opaque(pattern: str, max_len: int) -> Rule:
    return Rule("opaque", max_len, re.compile(pattern))


MODEL = _identifier(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]*", 256)
PROVIDER = _identifier(r"[A-Za-z0-9][A-Za-z0-9._-]*", 64)
OPERATION = _identifier(r"[a-z][a-z0-9_.]*", 64)
KEYWORD = _identifier(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", 64)  # finish reasons, output type
VERSION = _identifier(r"[A-Za-z0-9][A-Za-z0-9._+-]*", 128)  # service / sdk versions
PROMPT_NAME = _identifier(r"[A-Za-z0-9][A-Za-z0-9._:+/-]*", 128)
PROMPT_VERSION = _identifier(r"[A-Za-z0-9][A-Za-z0-9._:+/-]*", 64)
ERROR_TYPE = _identifier(r"[A-Za-z0-9_][A-Za-z0-9_.:$/-]*", 256)
RESPONSE_ID = _opaque(r"[A-Za-z0-9_][A-Za-z0-9._:/+=-]*", 256)
CALL_ID = _opaque(r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}", 64)
LABEL = Rule("label", 256)  # service name, environment, span name


def check(value: object, rule: Rule) -> str | None:
    """The value to emit, or None when it must be withheld. Checks the complete value
    before any truncation, so a secret cannot survive as a truncated prefix."""
    if not isinstance(value, str) or not value:
        return None
    if has_unsafe_characters(value) or contains_credential(value):
        return None
    if rule.text_class != "opaque" and contains_personal_data(
        value, free_text=rule.text_class == "label"
    ):
        return None
    if rule.text_class == "label":
        return value[: rule.max_len]
    if len(value) > rule.max_len or rule.pattern is None or not rule.pattern.fullmatch(value):
        return None
    return value
