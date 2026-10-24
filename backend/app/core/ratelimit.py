"""In-process token buckets (SECURITY.md §4, CR-08).

State lives in this process only: with N workers the effective limit is N x the configured
value, and it resets on restart. The MVP runs one uvicorn worker.
"""

import math
import threading
import time
from collections.abc import Callable, Hashable
from dataclasses import dataclass

Clock = Callable[[], float]

# Bound on tracked keys; buckets that have refilled completely carry no state and are pruned.
MAX_TRACKED_KEYS = 10_000


@dataclass
class _Bucket:
    tokens: float
    updated: float


class KeyedTokenBucket:
    """One bucket per key: capacity `burst`, refilled at `rate` tokens per second."""

    def __init__(self, rate: float, burst: int, clock: Clock = time.monotonic) -> None:
        if rate <= 0 or burst <= 0:
            raise ValueError("rate and burst must be positive")
        self._rate = rate
        self._burst = float(burst)
        self._clock = clock
        self._buckets: dict[Hashable, _Bucket] = {}
        self._lock = threading.Lock()

    def _refilled(self, key: Hashable, now: float) -> _Bucket:
        b = self._buckets.get(key)
        if b is None:
            if len(self._buckets) >= MAX_TRACKED_KEYS:
                self._prune(now)
            b = self._buckets[key] = _Bucket(self._burst, now)
        else:
            b.tokens = min(self._burst, b.tokens + (now - b.updated) * self._rate)
            b.updated = now
        return b

    def _prune(self, now: float) -> None:
        full = [
            k
            for k, b in self._buckets.items()
            if b.tokens + (now - b.updated) * self._rate >= self._burst
        ]
        for k in full:
            del self._buckets[k]
        if len(self._buckets) >= MAX_TRACKED_KEYS:  # still full: drop the oldest entries
            for k in sorted(self._buckets, key=lambda k: self._buckets[k].updated)[
                : MAX_TRACKED_KEYS // 10
            ]:
                del self._buckets[k]

    def _retry_after(self, b: _Bucket) -> int:
        return max(1, math.ceil((1.0 - b.tokens) / self._rate))

    def acquire(self, key: Hashable) -> int | None:
        """Take one token. Returns None on success, else Retry-After seconds."""
        with self._lock:
            b = self._refilled(key, self._clock())
            if b.tokens >= 1.0:
                b.tokens -= 1.0
                return None
            return self._retry_after(b)

    def blocked(self, key: Hashable) -> int | None:
        """Peek without consuming: Retry-After seconds if no token is available, else None."""
        with self._lock:
            b = self._refilled(key, self._clock())
            return None if b.tokens >= 1.0 else self._retry_after(b)

    def consume(self, key: Hashable) -> None:
        """Record one event (e.g. an auth failure); never goes below zero."""
        with self._lock:
            b = self._refilled(key, self._clock())
            b.tokens = max(0.0, b.tokens - 1.0)
