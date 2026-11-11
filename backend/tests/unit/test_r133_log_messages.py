"""R133: the backend log lines that the CI E2E gate (`scripts/qa/e2e_check.py`) relies on.

The gate reads the main E2E backend's log. It requires uvicorn's startup lines and fails on
any rate-limit rejection, while counting authentication failures apart. These tests pin the
exact texts, so changing a message (or upgrading uvicorn) cannot silently blind the gate.
No database, network or provider is used.
"""

import inspect
import logging
import subprocess
import sys
from types import SimpleNamespace

import pytest
import uvicorn
import uvicorn.lifespan.on
import uvicorn.server
from pydantic import SecretStr

from app.api import admin
from app.core.ratelimit import KeyedTokenBucket
from app.services.ingestion import service as ingestion

ADMIN_VALUE = "r133-" + "a" * 40
CLIENT = "127.0.0.1"


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _guard(api_burst: int = 2, authfail_burst: int = 30) -> admin.AdminGuard:
    clock = _Clock()
    settings = SimpleNamespace(admin_token=SecretStr(ADMIN_VALUE))
    return admin.AdminGuard(
        settings,  # type: ignore[arg-type]  # AdminGuard reads only `admin_token`
        authfail_limiter=KeyedTokenBucket(authfail_burst / 60.0, authfail_burst, clock),
        api_limiter=KeyedTokenBucket(10, api_burst, clock),
    )


LOGGER = "app.api"  # admin.py: logging.getLogger("app.api")


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage() for r in caplog.records if r.name == LOGGER and r.levelno == logging.WARNING
    ]


def test_api_bucket_rejection_is_logged_with_the_gate_reason(caplog):
    guard = _guard(api_burst=2)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        guard.check([f"Bearer {ADMIN_VALUE}"], CLIENT)
        guard.check([f"Bearer {ADMIN_VALUE}"], CLIENT)
        with pytest.raises(admin.APIError) as raised:
            guard.check([f"Bearer {ADMIN_VALUE}"], CLIENT)
    assert raised.value.status_code == 429
    assert _warnings(caplog) == [f"api rejected reason=api_rate_limited client={CLIENT}"]


def test_auth_failures_and_the_auth_failure_bucket_are_logged_apart(caplog):
    guard = _guard(authfail_burst=2)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        for _ in range(2):
            with pytest.raises(admin.APIError) as raised:
                guard.check(["Bearer wrong"], CLIENT)
            assert raised.value.status_code == 401
        with pytest.raises(admin.APIError) as raised:
            guard.check([f"Bearer {ADMIN_VALUE}"], CLIENT)  # valid, but the bucket is empty
    assert raised.value.status_code == 429
    assert _warnings(caplog) == [
        f"api auth failed reason=mismatch client={CLIENT}",
        f"api auth failed reason=mismatch client={CLIENT}",
        f"api rejected reason=authfail_rate_limited client={CLIENT}",
    ]


def test_ingest_rejection_and_auth_failure_messages_are_unchanged():
    source = inspect.getsource(ingestion)
    for literal in (
        '"ingest rejected reason=authfail_rate_limited client=%s"',
        '"ingest rejected reason=key_rate_limited key_id=%s"',
        '"ingest auth failed reason=%s prefix=%s client=%s"',
    ):
        assert source.count(literal) == 1, literal


def test_a_rejection_reaches_stderr_under_uvicorns_logging_config():
    """The gate reads the process's stderr (redirected to backend.log). uvicorn configures only
    its own loggers, so the app's warnings reach stderr through Python's last-resort handler.
    Run in a child process so this test's logging configuration stays untouched."""
    code = f"""
import logging.config
from types import SimpleNamespace
from pydantic import SecretStr
import uvicorn.config
logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)
from app.api import admin
from app.core.ratelimit import KeyedTokenBucket
clock = lambda: 0.0
guard = admin.AdminGuard(SimpleNamespace(admin_token=SecretStr({ADMIN_VALUE!r})),
    authfail_limiter=KeyedTokenBucket(0.5, 30, clock), api_limiter=KeyedTokenBucket(10, 1, clock))
guard.check(["Bearer " + {ADMIN_VALUE!r}], "127.0.0.1")
try:
    guard.check(["Bearer " + {ADMIN_VALUE!r}], "127.0.0.1")
except admin.APIError:
    pass
"""
    done = subprocess.run(  # noqa: S603 - fixed arguments, the current interpreter
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False
    )
    assert done.returncode == 0, done.stderr[-500:]
    rejected = f"api rejected reason=api_rate_limited client={CLIENT}"
    assert done.stderr.splitlines().count(rejected) == 1


def test_uvicorn_startup_lines_are_the_pinned_version():
    # The gate requires "Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)" and
    # "Application startup complete.". An upgrade must re-check both texts.
    assert uvicorn.__version__ == "0.54.0"
    server = inspect.getsource(uvicorn.server)
    assert 'addr_format = "%s://%s:%d"' in server
    assert 'f"Uvicorn running on {addr_format} (Press CTRL+C to quit)"' in server
    rendered = "Uvicorn running on {} (Press CTRL+C to quit)".format(
        "%s://%s:%d" % ("http", "127.0.0.1", 8000)  # noqa: UP031 - uvicorn's own format
    )
    assert rendered == "Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)"
    assert '"Application startup complete."' in inspect.getsource(uvicorn.lifespan.on)
