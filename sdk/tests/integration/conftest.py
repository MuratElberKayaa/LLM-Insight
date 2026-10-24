"""Live backend for SDK integration tests: real PostgreSQL, real `python -m app`.

The SDK test process never imports the backend (ADR-01). The backend runs in its own venv
(`backend/.venv`, override with LLMI_BACKEND_PYTHON) as subprocesses:

1. a disposable database `llminsight_sdk_it_test` on the docker-compose server (credentials
   from LLMI_DATABASE_URL or the repository `.env`), dropped afterwards;
2. `alembic upgrade head`, then `python -m app.cli create-key` (the printed key is parsed,
   never echoed);
3. `python -m app` on a free loopback port, with a fresh test pepper and admin token, and
   its log captured to a file so tests can check it for leaks.

Unreachable PostgreSQL skips with a reason; LLMI_TEST_REQUIRE_DB=1 turns that into a failure.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
import pytest

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
TEST_DB = "llminsight_sdk_it_test"
REQUIRE_DB = os.environ.get("LLMI_TEST_REQUIRE_DB") == "1"


def _unavailable(reason: str) -> None:
    if REQUIRE_DB:
        pytest.fail(f"live backend required (LLMI_TEST_REQUIRE_DB=1): {reason}")
    pytest.skip(f"live backend unavailable: {reason}")


def _backend_python() -> Path:
    override = os.environ.get("LLMI_BACKEND_PYTHON")
    if override:
        return Path(override)
    sub = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    return BACKEND / ".venv" / sub


def _server_url() -> str:
    url = os.environ.get("LLMI_DATABASE_URL")
    env_file = REPO / ".env"
    if not url and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("LLMI_DATABASE_URL="):
                url = line.split("=", 1)[1].strip()
    if not url or not url.startswith("postgresql+psycopg://"):
        _unavailable("LLMI_DATABASE_URL not set (repository .env missing)")
    return url  # type: ignore[return-value]


def _with_database(url: str, name: str) -> str:
    base, _, _ = url.rpartition("/")
    return f"{base}/{name}"


def _libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass(frozen=True)
class LiveBackend:
    base_url: str
    ingest_endpoint: str
    project_id: int
    key: str = field(repr=False)  # test secrets never appear in pytest output
    admin_token: str = field(repr=False)
    conninfo: str
    log_path: Path
    python: Path
    env: dict[str, str] = field(repr=False)

    def create_key(self, project: str = "sdk-it") -> tuple[str, str]:
        return _create_key(self.python, self.env, project)

    def revoke_key(self, prefix: str) -> None:
        _run_backend(self.python, self.env, ["-m", "app.cli", "revoke-key", "--prefix", prefix])

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        with psycopg.connect(self.conninfo, connect_timeout=5) as conn:
            return conn.execute(sql, params).fetchall()  # type: ignore[arg-type]

    def api(self, path: str) -> Any:
        req = urllib.request.Request(  # noqa: S310 - fixed loopback URL
            f"{self.base_url}{path}", headers={"Authorization": f"Bearer {self.admin_token}"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return json.loads(resp.read())

    def log_text(self) -> str:
        return self.log_path.read_text(encoding="utf-8", errors="replace")


def _run_backend(python: Path, env: dict[str, str], args: list[str]) -> str:
    result = subprocess.run(
        [str(python), *args], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        # stderr of these commands carries no secrets (alembic / CLI error text only)
        pytest.fail(f"backend command {args[:3]} failed: {result.stderr[-2000:]}")
    return result.stdout


def _create_key(python: Path, env: dict[str, str], project: str) -> tuple[str, str]:
    out = _run_backend(python, env, ["-m", "app.cli", "create-key", "--project", project]).split()
    key = next(t for t in out if t.startswith("lli_"))
    prefix = next(t for t in out if t.startswith("prefix=")).split("=", 1)[1]
    return key, prefix


@pytest.fixture(scope="session")
def live_backend(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveBackend]:
    python = _backend_python()
    if not python.exists():
        _unavailable(f"backend interpreter not found at {python}")
    server = _server_url()
    admin_conninfo = _libpq(_with_database(server, "postgres"))
    try:
        with psycopg.connect(admin_conninfo, autocommit=True, connect_timeout=2) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
            conn.execute(f'CREATE DATABASE "{TEST_DB}"')
    except psycopg.OperationalError as exc:
        _unavailable(type(exc).__name__)

    test_url = _with_database(server, TEST_DB)
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LLMI_", "OTEL_"))}
    env.update(
        {
            "LLMI_DATABASE_URL": test_url,
            "LLMI_KEY_PEPPER": secrets.token_urlsafe(48),
            "LLMI_ADMIN_TOKEN": secrets.token_urlsafe(48),
            "LLMI_BIND_HOST": "127.0.0.1",
            "LLMI_PORT": str(port),
            "LLMI_RATE_LIMIT_INGEST_RPS": "1000",
            "LLMI_RATE_LIMIT_INGEST_BURST": "1000",
            "LLMI_RATE_LIMIT_API_RPS": "1000",
            "LLMI_RATE_LIMIT_API_BURST": "1000",
            "LLMI_RATE_LIMIT_AUTHFAIL_PER_MIN": "1000",
            "PYTHONUNBUFFERED": "1",
        }
    )
    _run_backend(python, env, ["-m", "alembic", "upgrade", "head"])
    key, prefix = _create_key(python, env, "sdk-it")
    conninfo = _libpq(test_url)
    with psycopg.connect(conninfo) as conn:
        row = conn.execute(
            "SELECT project_id FROM project_api_keys WHERE prefix = %s", (prefix,)
        ).fetchone()
    assert row is not None
    project_id = int(row[0])

    log_path = tmp_path_factory.mktemp("backend") / "backend.log"
    log = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(python), "-m", "app"], cwd=BACKEND, env=env, stdout=log, stderr=subprocess.STDOUT
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while True:
        try:
            with urllib.request.urlopen(f"{base_url}/health/ready", timeout=1) as r:  # noqa: S310
                if r.status == 200:
                    break
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        if proc.poll() is not None or time.monotonic() > deadline:
            proc.kill()
            log.close()
            pytest.fail("backend did not become ready: " + log_path.read_text()[-2000:])
        time.sleep(0.2)

    try:
        yield LiveBackend(
            base_url=base_url,
            ingest_endpoint=f"{base_url}/v1/traces",
            project_id=project_id,
            key=key,
            admin_token=env["LLMI_ADMIN_TOKEN"],
            conninfo=conninfo,
            log_path=log_path,
            python=python,
            env=env,
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        with psycopg.connect(admin_conninfo, autocommit=True, connect_timeout=2) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
