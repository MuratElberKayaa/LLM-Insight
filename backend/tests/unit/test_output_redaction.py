"""Phase 2C security gate: a failing test never prints a local credential.

Every value here is SYNTHETIC (random per run). The probe suite below is run by a real inner
pytest process and fails on purpose through each path that can print a credential: helper
arguments (funcargs), assertion reprs, `pytest.fail` messages, `--showlocals`, captured
stdout/stderr/log, skip reasons and a collection error. A control run without the
protections proves the probe would see a leak.
"""

import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

from tests.redaction import (
    PLACEHOLDER,
    URL_PLACEHOLDER,
    RedactedUrl,
    local_secrets,
    scrub,
)

BACKEND_DIR = Path(__file__).resolve().parents[2]

PROBE_CONFTEST = """
import os

import pytest

from tests.redaction import RedactedUrl

URL = os.environ["PROBE_URL"]


@pytest.fixture
def url():
    return URL if os.environ.get("PROBE_PLAIN") == "1" else RedactedUrl(URL)
"""

PROBE_TESTS = """
import logging
import os
import sys

import pytest

URL = os.environ["PROBE_URL"]
TOKEN = os.environ["LLMI_ADMIN_TOKEN"]


def _connect(url):
    raise ConnectionError("synthetic connection failure")


def test_helper_argument(url):
    _connect(url)


def test_assertion_repr(url):
    assert url.endswith("/another_test")


def test_fail_message():
    pytest.fail(f"cannot reach {URL}")


def test_locals():
    raw = URL
    token = TOKEN
    raise RuntimeError(f"{len(raw)} {len(token)}")


def test_captured_output():
    print(f"url={URL}")
    print(f"token={TOKEN}", file=sys.stderr)
    logging.getLogger("probe").warning("token=%s", TOKEN)
    assert False


def test_skip_reason():
    pytest.skip(f"database unavailable at {URL}")
"""

PROBE_COLLECTION_ERROR = """
import os

raise RuntimeError("cannot import: " + os.environ["PROBE_URL"])
"""


def _run_probe(
    tmp_path: Path, *, protected: bool, collection_error: bool = True
) -> tuple[str, str, str, int]:
    """Run the probe suite in a fresh pytest process; returns (output, password, token, exit)."""
    password = "synthpw" + secrets.token_hex(12)
    token = "synthtok" + secrets.token_hex(16)
    url = f"postgresql+psycopg://llminsight:{password}@127.0.0.1:1/probe_test"
    (tmp_path / "conftest.py").write_text(PROBE_CONFTEST, encoding="utf-8")
    (tmp_path / "test_probe.py").write_text(PROBE_TESTS, encoding="utf-8")
    if collection_error:
        (tmp_path / "test_probe_collection.py").write_text(PROBE_COLLECTION_ERROR, "utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LLMI_", "PYTEST_"))}
    env.update(
        {
            "PYTHONPATH": str(BACKEND_DIR),
            "PROBE_URL": url,
            "LLMI_DATABASE_URL": url,
            "LLMI_ADMIN_TOKEN": token,
            "PROBE_PLAIN": "0" if protected else "1",
        }
    )
    args = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rA", "--showlocals"]
    if protected:
        args += ["-p", "tests.redaction"]
    result = subprocess.run(  # noqa: S603 - fixed arguments, the current interpreter
        args, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120
    )
    return result.stdout + result.stderr, password, token, result.returncode


def test_a_collection_error_never_prints_the_credentials(tmp_path):
    out, password, token, code = _run_probe(tmp_path, protected=True)
    assert code == 2  # interrupted by the collection error
    assert "1 error" in out
    assert password not in out
    assert token not in out
    assert PLACEHOLDER in out


def test_failing_tests_never_print_the_credentials(tmp_path):
    out, password, token, code = _run_probe(tmp_path, protected=True, collection_error=False)
    assert code == 1
    assert "5 failed, 1 skipped" in out  # every probe path ran
    for section in ("Captured stdout call", "Captured stderr call", "Captured log call"):
        assert section in out
    assert password not in out
    assert token not in out
    assert URL_PLACEHOLDER in out  # layer 1 was exercised (funcargs / assertion repr)
    assert PLACEHOLDER in out  # layer 2 was exercised (messages, locals, captured output)


def test_control_without_protection_the_probe_sees_the_leak(tmp_path):
    """Mutation check: plain `str` URLs and no report hook print both credentials, so the
    assertions above would fail if a protection stopped working."""
    out, password, token, _ = _run_probe(tmp_path, protected=False, collection_error=False)
    assert password in out
    assert token in out


# ---------------------------------------------------------------- the building blocks
def test_redacted_url_hides_only_its_repr():
    url = RedactedUrl("postgresql+psycopg://u:synthpw-xyz-123@127.0.0.1:1/db_test")
    assert repr(url) == URL_PLACEHOLDER
    assert "synthpw" not in repr([url]) and "synthpw" not in repr({"u": url})
    assert str(url).endswith("@127.0.0.1:1/db_test")  # drivers still get the real value
    assert f"{url}" == str.__str__(url)


def test_scrub_replaces_known_secrets_and_any_url_password():
    known = {"synthetic-token-0123456789"}
    text = (
        "token=synthetic-token-0123456789 "
        "db=postgresql+psycopg://user:other-unknown-pw@db.example.invalid:5432/x "
        "plain=https://example.invalid/path"
    )
    clean = scrub(text, known)
    assert "synthetic-token" not in clean and "other-unknown-pw" not in clean
    assert "postgresql+psycopg://user:***@db.example.invalid:5432/x" in clean
    assert "https://example.invalid/path" in clean  # no credentials: unchanged


def test_local_secrets_reads_env_file_and_environment(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "POSTGRES_PASSWORD=synthfilepw0123\n"
        "LLMI_DATABASE_URL=postgresql+psycopg://u:p%40ss-synth-42@127.0.0.1:1/db\n"
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS=X-Project-Key=lli_ABCDEFGH_synthkeyvalue\n"
        "LLMI_KEY_PEPPER=CHANGE_ME\n"
        "LLMI_PORT=8000\n"
        "# LLMI_ADMIN_TOKEN=commented-out-value\n",
        encoding="utf-8",
    )
    found = local_secrets({"LLMI_ADMIN_TOKEN": "synthenvtoken99", "HOME": "/home/x"}, env_file)
    assert {"synthfilepw0123", "p@ss-synth-42", "p%40ss-synth-42"} <= found
    assert "lli_ABCDEFGH_synthkeyvalue" in found and "synthenvtoken99" in found
    assert "CHANGE_ME" not in found and "8000" not in found and "/home/x" not in found
    assert "commented-out-value" not in found


def test_short_values_are_not_treated_as_secrets(tmp_path):
    assert local_secrets({"LLMI_ADMIN_TOKEN": "short"}, tmp_path / "missing.env") == frozenset()


@pytest.mark.parametrize("key", ["POSTGRES_PASSWORD", "LLMI_KEY_PEPPER", "LLMI_API_TOKEN"])
def test_secret_keys_are_recognised(key, tmp_path):
    assert local_secrets({key: "synthetic-value-1"}, tmp_path / "x") == {"synthetic-value-1"}
