"""Project ingest keys (SECURITY.md §2): format, HMAC-SHA256 hash, constant-time check.

Key format: `lli_<prefix>_<secret>`. The prefix (8 alphanumerics) is stored in clear for
lookup; the secret is 256-bit random. The DB stores only HMAC-SHA256(LLMI_KEY_PEPPER, key).
"""

import hashlib
import hmac
import re
import secrets
import string
from dataclasses import dataclass

PREFIX_ALPHABET = string.ascii_letters + string.digits
PREFIX_LEN = 8
SECRET_BYTES = 32  # 256 bits; token_urlsafe(32) gives 43 chars

# The secret part is urlsafe base64 (may contain '_' and '-'); the prefix never does.
KEY_RE = re.compile(r"^lli_([A-Za-z0-9]{8})_([A-Za-z0-9_-]{43,128})$")


@dataclass(frozen=True)
class GeneratedKey:
    prefix: str
    key: str  # shown once, never stored


def generate_key() -> GeneratedKey:
    prefix = "".join(secrets.choice(PREFIX_ALPHABET) for _ in range(PREFIX_LEN))
    return GeneratedKey(prefix=prefix, key=f"lli_{prefix}_{secrets.token_urlsafe(SECRET_BYTES)}")


def hash_key(pepper: bytes, key: str) -> str:
    return hmac.new(pepper, key.encode("utf-8"), hashlib.sha256).hexdigest()


def parse_prefix(presented: str) -> str | None:
    """Prefix of a well-formed key, else None. Never returns any part of the secret."""
    m = KEY_RE.match(presented)
    return m.group(1) if m else None


# Compared against when the prefix is unknown, so every lookup path does one HMAC + compare.
_DUMMY_HASH = "0" * 64


def hash_matches(pepper: bytes, presented: str, stored_hash: str | None) -> bool:
    computed = hash_key(pepper, presented)
    return hmac.compare_digest(computed, stored_hash or _DUMMY_HASH) and stored_hash is not None
