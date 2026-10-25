"""Phase 2C security gate for the SDK suite: no local credential in a failure report.

All values are SYNTHETIC. The full probe (every leak path, plus a control run) lives in
`backend/tests/unit/test_output_redaction.py`; this file proves the SDK copy is the same
module, that the live-backend harness hides its credentials, and that an SDK-suite failure
through a helper argument and a message is redacted.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path

from tests.integration.conftest import LiveBackend, _libpq, _with_database
from tests.redaction import URL_PLACEHOLDER, RedactedUrl

SDK_DIR = Path(__file__).resolve().parents[2]
REPO = SDK_DIR.parent


def test_the_sdk_copy_matches_the_backend_module():
    sdk_copy = (SDK_DIR / "tests" / "redaction.py").read_bytes()
    backend_copy = (REPO / "backend" / "tests" / "redaction.py").read_bytes()
    assert sdk_copy.replace(b"\r\n", b"\n") == backend_copy.replace(b"\r\n", b"\n")


def test_live_backend_repr_hides_every_credential(tmp_path):
    password = "synthpw" + secrets.token_hex(8)
    conninfo = _libpq(_with_database(f"postgresql+psycopg://u:{password}@127.0.0.1:1/x", "y_test"))
    assert isinstance(conninfo, RedactedUrl)
    assert conninfo.endswith("@127.0.0.1:1/y_test") and conninfo.startswith("postgresql://")
    backend = LiveBackend(
        base_url="http://127.0.0.1:1",
        ingest_endpoint="http://127.0.0.1:1/v1/traces",
        project_id=1,
        key="lli_AbCd1234_" + "s" * 43,
        admin_token="synthtok" + "t" * 32,
        conninfo=conninfo,
        log_path=tmp_path / "backend.log",
        python=Path(sys.executable),
        env={"LLMI_DATABASE_URL": conninfo},
    )
    text = repr(backend)
    assert password not in text and "sssss" not in text and "synthtok" not in text
    assert repr(conninfo) == URL_PLACEHOLDER


PROBE = """
import os

import pytest

from tests.redaction import RedactedUrl

URL = os.environ["PROBE_URL"]


def _connect(url):
    raise ConnectionError("synthetic")


def test_helper_argument():
    _connect(RedactedUrl(URL))


def test_message():
    pytest.fail(f"cannot reach {URL}")
"""


def test_an_sdk_suite_failure_is_redacted(tmp_path):
    password = "synthpw" + secrets.token_hex(12)
    (tmp_path / "test_probe.py").write_text(PROBE, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LLMI_", "PYTEST_"))}
    env.update(
        {
            "PYTHONPATH": str(SDK_DIR),
            "PROBE_URL": f"postgresql://llminsight:{password}@127.0.0.1:1/probe_test",
        }
    )
    result = subprocess.run(  # noqa: S603 - fixed arguments, the current interpreter
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "tests.redaction"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = result.stdout + result.stderr
    assert result.returncode == 1 and "2 failed" in out
    assert password not in out
    assert URL_PLACEHOLDER in out and "llminsight:***@" in out
