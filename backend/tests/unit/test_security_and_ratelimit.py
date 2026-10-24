"""Ingest key format/hash (SECURITY.md §2) and the token buckets (SECURITY.md §4)."""

import pytest

from app.core import ratelimit, security
from app.core.ratelimit import KeyedTokenBucket

PEPPER = b"p" * 40


def test_generated_key_format_and_entropy():
    keys = {security.generate_key().key for _ in range(200)}
    assert len(keys) == 200
    g = security.generate_key()
    assert security.KEY_RE.match(g.key)
    assert g.key.startswith(f"lli_{g.prefix}_")
    assert len(g.prefix) == 8 and g.prefix.isalnum()
    assert len(g.key.split("_", 2)[2]) >= 43  # >= 256-bit secret


def test_hash_is_hmac_sha256_hex_and_depends_on_pepper():
    k = security.generate_key().key
    h = security.hash_key(PEPPER, k)
    assert len(h) == 64 and int(h, 16) >= 0
    assert h == security.hash_key(PEPPER, k)
    assert h != security.hash_key(b"q" * 40, k)


def test_hash_matches():
    k = security.generate_key().key
    h = security.hash_key(PEPPER, k)
    assert security.hash_matches(PEPPER, k, h)
    assert not security.hash_matches(PEPPER, k + "x", h)
    assert not security.hash_matches(PEPPER, k, None)
    assert not security.hash_matches(PEPPER, k, "0" * 64)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "lli_",
        "lli_short_x",
        "Bearer abc",
        "lli_AbCd1234_" + "x" * 10,
        "LLI_AbCd1234_" + "x" * 43,
    ],
)
def test_malformed_keys_have_no_prefix(value):
    assert security.parse_prefix(value) is None


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_bucket_burst_then_retry_after_then_refill():
    clock = FakeClock()
    b = KeyedTokenBucket(rate=2, burst=3, clock=clock)
    assert [b.acquire("k") for _ in range(3)] == [None, None, None]
    assert b.acquire("k") == 1  # ceil(1 token / 2 per s)
    clock.now += 0.5
    assert b.acquire("k") is None
    assert b.acquire("other") is None  # keys are independent


def test_failure_counter_blocks_after_limit_and_recovers():
    clock = FakeClock()
    b = KeyedTokenBucket(rate=30 / 60, burst=30, clock=clock)
    for _ in range(30):
        assert b.blocked("1.2.3.4") is None
        b.consume("1.2.3.4")
    assert b.blocked("1.2.3.4") == 2  # one token per 2 s
    clock.now += 2
    assert b.blocked("1.2.3.4") is None


def test_tracked_keys_are_bounded(monkeypatch):
    monkeypatch.setattr(ratelimit, "MAX_TRACKED_KEYS", 50)
    clock = FakeClock()
    b = KeyedTokenBucket(rate=1, burst=1, clock=clock)
    for i in range(500):
        b.consume(f"addr-{i}")
    assert len(b._buckets) <= 50


def test_invalid_bucket_parameters():
    with pytest.raises(ValueError):
        KeyedTokenBucket(rate=0, burst=1)
