"""R22 against the real server (uvicorn, raw sockets): slow, stalled and disconnecting
clients after authentication. All telemetry is SYNTHETIC.

The deadline is set to 0.5 s here so the tests are fast; the bounds asserted below leave
generous slack for scheduling on a loaded machine.
"""

import logging
import socket
import threading
import time

import httpx
import pytest

from tests.integration.conftest import run_live_server
from tests.integration.ingest_helpers import counts
from tests.otlp import PROTOBUF, body, load_fixture

pytestmark = pytest.mark.integration

DEADLINE_S = 0.5


@pytest.fixture
def slow_server(ingest_settings):
    settings = ingest_settings.model_copy(update={"otlp_body_read_timeout_s": DEADLINE_S})
    with run_live_server(settings) as server:
        yield server


def _port(server) -> int:
    return int(server.url.rsplit(":", 1)[1])


def open_request(server, key: str, content_length: int, first: bytes) -> socket.socket:
    sock = socket.create_connection(("127.0.0.1", _port(server)), timeout=10)
    head = (
        "POST /v1/traces HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\n"
        f"Content-Type: {PROTOBUF}\r\n"
        f"X-Project-Key: {key}\r\n"
        f"Content-Length: {content_length}\r\n\r\n"
    )
    sock.sendall(head.encode() + first)
    return sock


def read_until_closed(sock: socket.socket) -> bytes:
    """Everything the server sends until it closes the connection (or 10 s pass)."""
    data = b""
    while True:
        try:
            chunk = sock.recv(65536)
        except (ConnectionResetError, ConnectionAbortedError):
            return data
        if not chunk:
            return data
        data += chunk


def status_line(raw: bytes) -> int:
    return int(raw.split(b"\r\n", 1)[0].split()[1])


def test_stalled_body_gets_408_at_the_deadline_and_the_connection_is_closed(
    slow_server, make_key, db
):
    k = make_key()
    payload = body(load_fixture())
    started = time.monotonic()
    sock = open_request(slow_server, k.key, len(payload), payload[:10])
    raw = read_until_closed(sock)  # returns only because the server closed the socket
    elapsed = time.monotonic() - started
    sock.close()
    assert status_line(raw) == 408
    assert b"connection: close" in raw.lower()
    assert b"request body was not received within the time limit" in raw
    assert DEADLINE_S * 0.8 <= elapsed < 5
    assert counts(db) == (0, 0, 0)  # nothing was written


def test_trickling_client_cannot_stretch_the_deadline(slow_server, make_key, db):
    """One byte every 50 ms: the full body would take ~20 s; the overall deadline ends it."""
    k = make_key()
    payload = body(load_fixture())
    sock = open_request(slow_server, k.key, len(payload), payload[:1])
    stop = threading.Event()

    def trickle():
        for b in payload[1:]:
            if stop.wait(0.05):
                return
            try:
                sock.sendall(bytes([b]))
            except OSError:
                return

    sender = threading.Thread(target=trickle, daemon=True)
    started = time.monotonic()
    sender.start()
    raw = read_until_closed(sock)
    elapsed = time.monotonic() - started
    stop.set()
    sender.join(timeout=5)
    sock.close()
    assert status_line(raw) == 408
    assert elapsed < 5
    assert counts(db) == (0, 0, 0)


def test_client_disconnect_mid_body_writes_nothing_and_the_server_keeps_serving(
    slow_server, make_key, db, caplog
):
    caplog.set_level(logging.WARNING)
    k = make_key()
    payload = body(load_fixture())
    sock = open_request(slow_server, k.key, len(payload), payload[:20])
    sock.close()  # gone before the body is complete
    deadline = time.monotonic() + 5
    while "ingest body rejected status=400" not in caplog.text and time.monotonic() < deadline:
        time.sleep(0.02)
    assert "ingest body rejected status=400" in caplog.text
    assert counts(db) == (0, 0, 0)
    r = httpx.post(
        f"{slow_server.url}/v1/traces",
        content=payload,
        headers={"Content-Type": PROTOBUF, "X-Project-Key": k.key},
        timeout=10,
    )
    assert r.status_code == 200 and counts(db) == (1, 3, 1)


def test_authentication_still_happens_before_any_body_read(ingest_settings, make_key, db):
    """With a long deadline, a bad key on a stalled body is answered at once (401), not
    after the deadline: the body is never waited for."""
    make_key()
    settings = ingest_settings.model_copy(update={"otlp_body_read_timeout_s": 30})
    with run_live_server(settings) as server:
        started = time.monotonic()
        sock = open_request(server, "lli_AAAAAAAA_" + "x" * 43, 1000, b"")
        raw = read_until_closed_or_response(sock)
        sock.close()
    assert status_line(raw) == 401
    assert time.monotonic() - started < 3
    assert counts(db) == (0, 0, 0)


def read_until_closed_or_response(sock: socket.socket) -> bytes:
    """A 401 may keep the connection open (the unread body is the client's problem):
    stop once a complete response head and body have arrived."""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(65536)
        if not chunk:
            break
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    length = next(
        (
            int(line.split(b":", 1)[1])
            for line in head.split(b"\r\n")
            if line.lower().startswith(b"content-length:")
        ),
        0,
    )
    while len(rest) < length:
        chunk = sock.recv(65536)
        if not chunk:
            break
        rest += chunk
    return head + b"\r\n\r\n" + rest


def test_concurrent_stalled_clients_are_all_released_and_service_continues(
    slow_server, make_key, db
):
    k = make_key()
    payload = body(load_fixture())
    socks = [open_request(slow_server, k.key, len(payload), payload[:5]) for _ in range(8)]
    statuses = []
    started = time.monotonic()
    for s in socks:
        statuses.append(status_line(read_until_closed(s)))
        s.close()
    assert statuses == [408] * 8
    assert time.monotonic() - started < 6
    r = httpx.post(
        f"{slow_server.url}/v1/traces",
        content=payload,
        headers={"Content-Type": PROTOBUF, "X-Project-Key": k.key},
        timeout=10,
    )
    assert r.status_code == 200 and counts(db) == (1, 3, 1)
