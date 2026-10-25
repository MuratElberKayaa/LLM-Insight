"""R32 (Phase 2C): clients that reset their connection never stop the server from accepting.

With uvicorn's default Windows loop (Proactor), a client that resets a connection before it
is accepted makes CPython 3.12 close the listening socket: every later connection is refused
and asyncio logs "Accept failed on a socket" with a traceback (`app/core/eventloop.py`). The
race needs a separate server process (an in-process server sharing the GIL with the client
rarely hits it), so these tests drive the real `python -m app` as a subprocess. Mutation
check (`reports/phase-2c.md` §2.3): with uvicorn's default loop they fail on Windows.
"""

import os
import socket
import struct
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import httpx
import pytest

from tests import otlp
from tests.helpers import BACKEND_DIR, DUMMY_PEPPER, DUMMY_TOKEN

pytestmark = pytest.mark.integration

REQUEST = b"GET /health/live HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
ROUNDS = 300


class DevServer(NamedTuple):
    url: str
    port: int
    log: Path


@pytest.fixture
def dev_server(ingest_url, db, tmp_path) -> Iterator[DevServer]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith("LLMI_")}
    env.update(
        {
            "LLMI_DATABASE_URL": str(ingest_url),
            "LLMI_KEY_PEPPER": DUMMY_PEPPER,  # so keys from `make_key` verify
            "LLMI_ADMIN_TOKEN": DUMMY_TOKEN,
            "LLMI_PORT": str(port),
            "LLMI_RATE_LIMIT_INGEST_RPS": "1000",
            "LLMI_RATE_LIMIT_INGEST_BURST": "1000",
            "PYTHONUNBUFFERED": "1",
        }
    )
    log = tmp_path / "server.log"
    with log.open("w", encoding="utf-8") as out:
        proc = subprocess.Popen(  # noqa: S603 - the current interpreter, fixed arguments
            [sys.executable, "-m", "app"], cwd=BACKEND_DIR, env=env, stdout=out, stderr=out
        )
        url = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 30
            while True:
                try:
                    if httpx.get(f"{url}/health/live", timeout=1).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                if proc.poll() is not None or time.monotonic() > deadline:
                    pytest.fail("python -m app did not start")
                time.sleep(0.2)
            yield DevServer(url, port, log)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()


def _reset(sock: socket.socket) -> None:
    """Close with RST instead of FIN (SO_LINGER on, timeout 0)."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    sock.close()


def _abort_connections(port: int, send: bytes) -> int:
    """Open and reset ROUNDS connections; returns how many could not even connect."""
    refused = 0
    for _ in range(ROUNDS):
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=2)
        except OSError:
            refused += 1
            break  # the listener is gone; further connects would only time out
        if send:
            sock.sendall(send)
        _reset(sock)
    return refused


@pytest.mark.parametrize(
    "send",
    [b"", REQUEST[:20], REQUEST],
    ids=["reset-after-connect", "reset-mid-request", "reset-with-unread-response"],
)
def test_the_server_keeps_accepting_after_client_resets(dev_server, send):
    assert _abort_connections(dev_server.port, send) == 0
    time.sleep(0.5)
    assert httpx.get(f"{dev_server.url}/health/ready", timeout=5).status_code == 200
    log = dev_server.log.read_text(encoding="utf-8", errors="replace")
    assert "Accept failed" not in log
    assert "Traceback" not in log


def test_ingest_still_works_after_resets(dev_server, make_key, db):
    assert _abort_connections(dev_server.port, REQUEST[:20]) == 0
    key = make_key()
    r = httpx.post(
        f"{dev_server.url}/v1/traces",
        content=otlp.body(otlp.request(otlp.span())),
        headers={"Content-Type": "application/x-protobuf", "X-Project-Key": key.key},
        timeout=10,
    )
    assert r.status_code == 200
    with db.connect() as c:
        assert c.exec_driver_sql("SELECT count(*) FROM spans").scalar_one() == 1
    assert "Traceback" not in dev_server.log.read_text(encoding="utf-8", errors="replace")
