"""Media type, content encoding, bounded read and bounded gunzip (§1.1 steps 2, 3, 7, 8)."""

import asyncio
import gzip
import os
import time

import pytest

from app.services.ingestion import body
from app.services.ingestion.errors import IngestError


@pytest.mark.parametrize(
    "header,ok",
    [
        ("application/x-protobuf", True),
        ("Application/X-Protobuf", True),
        ("application/x-protobuf; charset=binary", True),
        ("application/json", False),
        ("text/plain", False),
        ("application/protobuf", False),
        ("", False),
        (None, False),
    ],
)
def test_check_media_type(header, ok):
    if ok:
        body.check_media_type(header)
    else:
        with pytest.raises(IngestError) as e:
            body.check_media_type(header)
        assert e.value.status_code == 415


@pytest.mark.parametrize(
    "header,expected",
    [
        ("application/json", "application/json"),
        ("application/json; charset=utf-8", "application/json"),
        ("application/x-protobuf", "application/x-protobuf"),
        ("text/plain", "application/x-protobuf"),
        (None, "application/x-protobuf"),
    ],
)
def test_response_media_type(header, expected):
    assert body.response_media_type(header) == expected


@pytest.mark.parametrize(
    "header,expected",
    [(None, False), ("", False), ("identity", False), ("gzip", True), ("GZIP", True)],
)
def test_supported_encodings(header, expected):
    assert body.is_gzip(header) is expected


@pytest.mark.parametrize("header", ["br", "deflate", "zstd", "gzip, br", "x-gzip"])
def test_unsupported_encodings_are_415(header):
    with pytest.raises(IngestError) as e:
        body.is_gzip(header)
    assert e.value.status_code == 415


async def _chunks(parts):
    for p in parts:
        yield p


def read(content_length, parts, limit):
    return asyncio.run(body.read_bounded(content_length, _chunks(parts), limit))


def test_read_bounded_accepts_exactly_the_limit():
    assert read("10", [b"12345", b"67890"], 10) == b"1234567890"


def test_declared_content_length_over_limit_is_413_before_reading():
    consumed = []

    async def tracking():
        consumed.append(1)
        yield b"x"

    with pytest.raises(IngestError) as e:
        asyncio.run(body.read_bounded("11", tracking(), 10))
    assert e.value.status_code == 413 and consumed == []


def test_streamed_body_over_limit_is_413_even_without_content_length():
    with pytest.raises(IngestError) as e:
        read(None, [b"x" * 6, b"x" * 5], 10)
    assert e.value.status_code == 413


def test_gunzip_roundtrip_and_multi_member():
    assert body.gunzip_bounded(gzip.compress(b"hello"), 100) == b"hello"
    assert body.gunzip_bounded(gzip.compress(b"ab") + gzip.compress(b"cd"), 100) == b"abcd"
    assert body.gunzip_bounded(gzip.compress(b""), 100) == b""


def test_gunzip_exactly_at_limit_passes_and_one_over_fails():
    data = os.urandom(1000)
    assert body.gunzip_bounded(gzip.compress(data), 1000) == data
    with pytest.raises(IngestError) as e:
        body.gunzip_bounded(gzip.compress(data), 999)
    assert e.value.status_code == 413


@pytest.mark.parametrize(
    "payload",
    [
        b"not gzip at all",
        gzip.compress(b"hello world" * 10)[:-6],  # truncated
        gzip.compress(b"x")[:5],
        b"",
        gzip.compress(b"ok") + b"trailing garbage",
    ],
)
def test_corrupt_gzip_is_400_invalid_gzip(payload):
    with pytest.raises(IngestError) as e:
        body.gunzip_bounded(payload, 1 << 20)
    assert e.value.status_code == 400
    assert [v.reason for v in e.value.violations] == ["INVALID_GZIP"]


def test_gzip_bomb_is_cut_off_at_the_limit():
    """64 MiB of zeros compresses to about 64 KiB; inflation must stop just past 16 MiB."""
    bomb = gzip.compress(b"\x00" * (64 * 1024 * 1024), compresslevel=9)
    started = time.monotonic()
    with pytest.raises(IngestError) as e:
        body.gunzip_bounded(bomb, 16 * 1024 * 1024)
    assert e.value.status_code == 413
    assert time.monotonic() - started < 5
