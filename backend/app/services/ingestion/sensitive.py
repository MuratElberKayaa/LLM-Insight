"""Heuristic detection of credentials and personal data in permitted string values (R08).

Defence in depth behind the allowlist and the per-field format rules in `attrmap`: a value
that matches here is dropped (or replaced by a documented fallback) before persistence. It is
never stored, logged or echoed, and the caller only counts it.

These are deliberately *obvious* patterns: well-known key prefixes, bearer/basic credentials,
JWTs, private-key blocks, URL user-info, `secret=value` assignments, e-mail addresses, phone /
card / IBAN / national-ID-like digit runs. Heuristics cannot find every secret or every piece
of personal data; a miss is possible and documented (SECURITY.md §1). No entropy test is used,
because opaque provider identifiers (response IDs, fingerprints) would be false positives.
"""

import re
import unicodedata

# ---------------------------------------------------------------- credentials (every field)
_CREDENTIAL_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{16,}"),  # OpenAI / Anthropic style keys
    re.compile(r"(?<![A-Za-z0-9])lli_[A-Za-z0-9]{8}_[A-Za-z0-9_-]{16,}"),  # LLM Insight keys
    re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Z])"),  # AWS access key id
    re.compile(r"(?<![A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    re.compile(r"(?<![A-Za-z0-9])xox[abprs]-[A-Za-z0-9-]{10,}"),  # Slack
    re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{30,}"),  # Google API key
    re.compile(r"(?<![A-Za-z0-9])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),  # JWT / JWS
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY"),
    # Authorization header values: the credential must look like one (12+ chars with a digit),
    # so prose such as "basic authentication flow" is not matched.
    re.compile(r"(?i)\b(?:bearer|basic)\s+(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@"),  # URL with user:password@
    re.compile(
        r"(?i)(?<![a-z0-9])(?:api[_-]?key|apikey|secret|client[_-]?secret|password|passwd|pwd"
        r"|token|access[_-]?token|refresh[_-]?token|authorization|credentials?)"
        r"\s*[=:]\s*[^\s,;&]{3,}"
    ),
)

# ---------------------------------------------------------------- personal data
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_LONG_DIGIT_RUN = re.compile(r"\d{11,}")  # national IDs (TCKN 11), phones, card numbers
_INTL_PHONE = re.compile(r"\+\d[\d ().-]{7,}\d")
_GROUPED_DIGITS = re.compile(r"(?<![\d.])\d{2,4}(?:[ .-]\d{2,4}){2,6}(?![\d])")
_SPACED_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: [A-Z0-9]{4}){3,8}(?: [A-Z0-9]{1,4})?\b")


def _digits(s: str) -> int:
    return sum(c.isdigit() for c in s)


def contains_credential(value: str) -> bool:
    return any(p.search(value) for p in _CREDENTIAL_PATTERNS)


def contains_personal_data(value: str, *, free_text: bool) -> bool:
    """E-mail and long digit runs everywhere; formatted phone / card / IBAN numbers only in
    free-text fields, where spaces and separators are expected (identifier formats such as
    model snapshot dates would otherwise be false positives)."""
    if _EMAIL.search(value) or _LONG_DIGIT_RUN.search(value):
        return True
    if not free_text:
        return False
    if _SPACED_IBAN.search(value):
        return True
    for pattern in (_INTL_PHONE, _GROUPED_DIGITS):
        if any(_digits(m.group(0)) >= 10 for m in pattern.finditer(value)):
            return True
    return False


# Control, format (bidi overrides, zero-width), private-use, surrogate and line/paragraph
# separator characters: never legitimate in metadata, and used to hide or spoof content.
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cs", "Zl", "Zp"})


def has_unsafe_characters(value: str) -> bool:
    return any(unicodedata.category(c) in _UNSAFE_CATEGORIES for c in value)
