"""Request headers and body: media type, content encoding, bounded read and bounded gunzip.

TELEMETRY_CONTRACT.md §1.1 steps 2, 3, 7 and 8. Size limits come from ADR-17; the body-read
deadline is the Phase 1C control for R22.
"""

import asyncio
import zlib
from collections.abc import AsyncIterable

from starlette.requests import ClientDisconnect

from app.services.ingestion import errors
from app.services.ingestion.errors import JSON, PROTOBUF


def media_type(content_type: str | None) -> str | None:
    """Media type without parameters, lower-cased; None when the header is absent or empty."""
    if content_type is None:
        return None
    mt = content_type.split(";", 1)[0].strip().lower()
    return mt or None


def response_media_type(content_type: str | None) -> str:
    """§1.2: JSON only for a JSON request; binary protobuf for everything else."""
    return JSON if media_type(content_type) == JSON else PROTOBUF


def check_media_type(content_type: str | None) -> None:
    if media_type(content_type) != PROTOBUF:
        raise errors.unsupported_media_type()


def is_gzip(content_encoding: str | None) -> bool:
    """True for gzip, False for absent/identity; anything else is a 415 (step 3)."""
    enc = (content_encoding or "").strip().lower()
    if enc in ("", "identity"):
        return False
    if enc == "gzip":
        return True
    raise errors.unsupported_encoding()


async def read_bounded(
    content_length: str | None, chunks: AsyncIterable[bytes], max_bytes: int, timeout_s: float
) -> bytes:
    """Read the wire body (step 7): 413 as soon as it exceeds `max_bytes`, 408 if the whole
    body has not arrived within `timeout_s` (one overall deadline, so a client trickling one
    byte at a time cannot extend it), 400 if the client disconnects first.

    Every exit closes the body stream and drops the partial buffer. Nothing has touched the
    database yet, so no exit can leave a partial write."""
    declared = (content_length or "").strip()
    if declared.isascii() and declared.isdigit() and int(declared) > max_bytes:
        raise errors.body_too_large()
    buf = bytearray()
    stream = aiter(chunks)
    try:
        async with asyncio.timeout(timeout_s):
            async for chunk in stream:
                buf += chunk
                if len(buf) > max_bytes:
                    raise errors.body_too_large()
    except TimeoutError:
        raise errors.body_read_timeout() from None
    except ClientDisconnect:
        raise errors.client_disconnected() from None
    finally:
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            await aclose()
    return bytes(buf)


def gunzip_bounded(data: bytes, max_bytes: int) -> bytes:
    """Inflate gzip (one or more members) without ever holding more than `max_bytes + 1`
    output bytes: a decompression bomb is cut off at the limit (step 8)."""
    out = bytearray()
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    pending = data
    while True:
        try:
            # max_length is always >= 1 here (0 would mean "unlimited" to zlib).
            chunk = d.decompress(pending, max_bytes + 1 - len(out))
        except zlib.error:
            raise errors.invalid_gzip() from None
        out += chunk
        if len(out) > max_bytes:
            raise errors.decompressed_too_large()
        if d.eof:
            if not d.unused_data:
                return bytes(out)
            # Concatenated gzip members are valid gzip (RFC 1952 §2.2).
            pending = d.unused_data
            d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif d.unconsumed_tail:
            pending = d.unconsumed_tail
        elif chunk:
            pending = b""  # input consumed; drain output zlib may still hold
        else:
            raise errors.invalid_gzip()  # truncated stream
