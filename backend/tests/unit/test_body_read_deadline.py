"""R22: overall body-read deadline, client disconnect and resource release (§1.1 step 7).

Deterministic slow streams: each fake body is an async generator whose pacing is controlled
by the test, and whose `finally` records that it was closed.
"""

import asyncio
import time

import pytest
from starlette.requests import ClientDisconnect

from app.services.ingestion import body
from app.services.ingestion.errors import IngestError


class Stream:
    """Async-generator body with explicit pacing and a close flag."""

    def __init__(self) -> None:
        self.closed = False
        self.yielded = 0

    async def paced(self, parts: list[bytes], delay_s: float):
        try:
            for p in parts:
                await asyncio.sleep(delay_s)
                self.yielded += 1
                yield p
        finally:
            self.closed = True

    async def stalls_after(self, first: bytes):
        try:
            yield first
            self.yielded += 1
            await asyncio.Event().wait()  # never set: the client stops sending
            yield b"unreachable"
        finally:
            self.closed = True

    async def disconnects_after(self, first: bytes):
        try:
            yield first
            raise ClientDisconnect()
        finally:
            self.closed = True


def run(coro):
    return asyncio.run(coro)


def test_stalled_body_is_408_at_the_deadline_and_the_stream_is_closed():
    s = Stream()
    started = time.monotonic()
    with pytest.raises(IngestError) as e:
        run(body.read_bounded("100", s.stalls_after(b"x" * 10), 1000, 0.2))
    elapsed = time.monotonic() - started
    assert e.value.status_code == 408
    assert e.value.message == "request body was not received within the time limit"
    assert e.value.headers == {"Connection": "close"}
    assert 0.15 <= elapsed < 2.0
    assert s.closed


def test_trickling_body_cannot_extend_the_overall_deadline():
    """Every chunk arrives quickly (50 ms) but the whole body would take 2 s: a per-chunk
    timeout would never fire; the overall deadline does."""
    s = Stream()
    started = time.monotonic()
    with pytest.raises(IngestError) as e:
        run(body.read_bounded(None, s.paced([b"x"] * 40, 0.05), 1000, 0.3))
    assert e.value.status_code == 408
    assert time.monotonic() - started < 1.5
    assert 2 <= s.yielded < 40 and s.closed


def test_body_completing_within_the_deadline_is_returned():
    s = Stream()
    assert run(body.read_bounded("6", s.paced([b"ab", b"cd", b"ef"], 0.01), 100, 2.0)) == (
        b"abcdef"
    )
    assert s.closed


def test_size_limit_still_applies_inside_the_deadline_and_closes_the_stream():
    s = Stream()
    with pytest.raises(IngestError) as e:
        run(body.read_bounded(None, s.paced([b"x" * 6, b"x" * 6, b"x" * 6], 0.0), 10, 5.0))
    assert e.value.status_code == 413
    assert e.value.headers == {"Connection": "close"}
    assert s.yielded == 2 and s.closed  # stopped as soon as the limit was crossed


def test_declared_oversize_is_413_before_the_deadline_starts():
    s = Stream()
    started = time.monotonic()
    with pytest.raises(IngestError) as e:
        run(body.read_bounded(str(4 * 1024 * 1024 + 1), s.stalls_after(b"x"), 4 * 1024 * 1024, 5))
    assert e.value.status_code == 413
    assert time.monotonic() - started < 0.5
    assert s.yielded == 0


def test_client_disconnect_is_a_value_free_400_and_the_stream_is_closed():
    s = Stream()
    with pytest.raises(IngestError) as e:
        run(body.read_bounded("100", s.disconnects_after(b"partial"), 1000, 5.0))
    assert e.value.status_code == 400
    assert [v.reason for v in e.value.violations] == ["INCOMPLETE_BODY"]
    assert "partial" not in repr(e.value)
    assert s.closed


def test_cancellation_propagates_and_releases_the_stream():
    """Server shutdown cancels the request task: CancelledError is not swallowed or turned
    into a response, and the body stream is still closed."""
    s = Stream()

    async def scenario():
        task = asyncio.create_task(body.read_bounded("100", s.stalls_after(b"x"), 1000, 30.0))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())
    assert s.closed


def test_timeout_setting_is_bounded_and_defaults_to_ten_seconds():
    from pydantic import ValidationError

    from tests.helpers import make_settings

    assert make_settings().otlp_body_read_timeout_s == 10.0
    for bad in (0, -1, 301):
        with pytest.raises(ValidationError):
            make_settings(otlp_body_read_timeout_s=bad)
