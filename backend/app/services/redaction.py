"""Server-side checks for human-entered text (Phase 4B; SECURITY.md §1, T35).

Three kinds of human input reach the database, and each gets its own treatment:
- **free text** (`sanitized_input`, `evidence`, `hypothesis`): credentials and personal data
  are *masked* before insert (contract: "redaction is applied before insert"), and the caller
  reports what was masked;
- **structured assertions** (`expected_output`, `rule`): masking would silently change what a
  test asserts, so a string value that looks like a credential or personal data is *refused*;
- **labels** (`created_by`, `approved_by`, dataset names): single-line, and contact data or a
  secret is refused instead of stored as someone's name.

Unsafe characters (controls other than tab and newline, bidi overrides, zero-width, private
use, separators) are refused everywhere: they hide or spoof text and have no use here.

The patterns are the ingest heuristics (`services/ingestion/sensitive.py`), so they catch
obvious formats only. Redaction is best effort, not a guarantee: content must still be
synthetic (SECURITY.md §1). No value is ever logged or echoed in an error.
"""

import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from app.services.ingestion.sensitive import (
    CREDENTIAL_PATTERNS,
    EMAIL_PATTERN,
    GROUPED_DIGITS_PATTERN,
    INTL_PHONE_PATTERN,
    SPACED_IBAN_PATTERN,
    UNSAFE_CATEGORIES,
    contains_credential,
    contains_personal_data,
)

MASK = "[REDACTED:{}]"
_MARK = "[REDACTED:"
CATEGORIES = ("credential", "email", "iban", "phone", "number")

# Free text: 10+ digits are masked (phones, national IDs, card and account numbers). The
# ingest rule for identifiers uses 11+, because model snapshot dates are 8-digit runs there.
_LONG_DIGITS = re.compile(r"(?<!\d)\d{10,}(?!\d)")
_COMPACT_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_MIN_PHONE_DIGITS = 9
_MIN_NUMBER_DIGITS = 10
_ALLOWED_CONTROLS = frozenset("\n\t")


def _digits(s: str) -> int:
    return sum(c.isdigit() for c in s)


@dataclass(frozen=True)
class Redacted:
    text: str
    masked: dict[str, int] = field(default_factory=dict)  # category -> occurrences masked

    @property
    def masked_total(self) -> int:
        return sum(self.masked.values())


def normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def has_unsafe_text_characters(value: str) -> bool:
    """Like the ingest rule, but tab and newline are legitimate in multi-line free text."""
    return any(
        c not in _ALLOWED_CONTROLS and unicodedata.category(c) in UNSAFE_CATEGORIES for c in value
    )


def has_unsafe_label_characters(value: str) -> bool:
    return any(unicodedata.category(c) in UNSAFE_CATEGORIES for c in value)


def _sub(
    pattern: re.Pattern[str],
    category: str,
    text: str,
    counts: Counter[str],
    accept: Callable[[str], bool] = lambda _m: True,
) -> str:
    def repl(m: re.Match[str]) -> str:
        if not accept(m.group(0)):
            return m.group(0)
        counts[category] += 1
        return MASK.format(category)

    return pattern.sub(repl, text)


def redact_free_text(value: str) -> Redacted:
    """Mask credentials, e-mail addresses, IBAN-like, phone-like and long numbers.

    Order matters: credentials first (a `password=...` assignment is masked whole), then
    e-mail, IBAN, international phone numbers, grouped digit runs, and plain long digit runs.
    A mask contains no digit, so a later pass never re-matches an earlier mask.
    """
    counts: Counter[str] = Counter()
    text = value
    for pattern in CREDENTIAL_PATTERNS:
        # `secret: sk-…` is masked by the key pattern first; the assignment pattern must not
        # then count the remaining `secret: [REDACTED:credential]` a second time.
        text = _sub(pattern, "credential", text, counts, lambda s: _MARK not in s)
    text = _sub(EMAIL_PATTERN, "email", text, counts)
    text = _sub(SPACED_IBAN_PATTERN, "iban", text, counts)
    text = _sub(_COMPACT_IBAN, "iban", text, counts, lambda s: _digits(s) >= _MIN_NUMBER_DIGITS)
    text = _sub(
        INTL_PHONE_PATTERN, "phone", text, counts, lambda s: _digits(s) >= _MIN_PHONE_DIGITS
    )
    text = _sub(
        GROUPED_DIGITS_PATTERN,
        "number",
        text,
        counts,
        lambda s: _digits(s) >= _MIN_NUMBER_DIGITS,
    )
    text = _sub(_LONG_DIGITS, "number", text, counts)
    return Redacted(text, {c: counts[c] for c in CATEGORIES if counts[c]})


def label_is_sensitive(value: str) -> bool:
    """A label (a person's self-declared name, a dataset name) carrying a secret or contact
    data. Labels are refused, never masked: a masked name would no longer identify anyone."""
    return contains_credential(value) or contains_personal_data(value, free_text=True)


def assertion_string_is_sensitive(value: str) -> bool:
    return redact_free_text(value).masked_total > 0 or has_unsafe_text_characters(value)


def iter_json_strings(value: Any) -> Iterator[str]:
    """Every string in a decoded JSON value, keys included (iterative: no recursion limit)."""
    stack = [value]
    while stack:
        v = stack.pop()
        if isinstance(v, str):
            yield v
        elif isinstance(v, dict):
            for k, item in v.items():
                yield k
                stack.append(item)
        elif isinstance(v, list):
            stack.extend(v)
