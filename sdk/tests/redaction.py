"""Keep local credentials out of pytest output (Phase 2C security gate, SECURITY.md §2).

A Phase 2B test failure printed the local PostgreSQL password: pytest shows the arguments of
every traceback frame (funcargs), the repr of values in assertion explanations and, with
`--showlocals`, every local variable. A database URL handed around as a plain `str`
therefore appeared in the failure report. Two layers stop that:

1. `RedactedUrl` is a `str` whose repr is a placeholder. Fixtures that hand out
   credential-bearing URLs return it, so funcargs and assertion reprs never show them.
2. The report hooks below rewrite every test and collection report before it is printed or
   written to a file: each known local secret (`local_secrets()`: the repository `.env` and
   the process environment) and the password of any `scheme://user:password@` URL become
   `***`. That covers what layer 1 cannot: f-strings, `pytest.fail` messages, skip reasons,
   `--showlocals`, and captured stdout, stderr and log sections.

Neither layer applies to `-s` (uncaptured output goes straight to the terminal) or to the
live-log option, so code under test must still never print secrets.

This file is kept identical in `backend/tests/` and `sdk/tests/` (the SDK never imports the
backend, ADR-01); `sdk/tests/unit/test_redaction.py` fails when the copies drift. Standard
library and pytest only.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER = "***"
URL_PLACEHOLDER = "<redacted database url>"
MIN_SECRET_LEN = 8  # shorter values are not generated secrets; replacing them garbles output

# Environment / .env keys whose values are credentials (SECURITY.md §2).
_SECRET_KEY = re.compile(r"(PASSWORD|PEPPER|TOKEN|SECRET|API_KEY|DATABASE_URL|_HEADERS)$")
# `scheme://user:password@`: the password part, whatever the host.
_URL_PASSWORD = re.compile(r"(?P<head>\b[A-Za-z][A-Za-z0-9+.-]*://[^\s:/@'\"]*:)[^\s@'\"]+@")


class RedactedUrl(str):
    """A credential-bearing URL whose repr never shows it. `str()` and f-strings still give
    the real value, which the database drivers need; derived strings are plain `str` again,
    so wrap them too."""

    __slots__ = ()

    def __repr__(self) -> str:
        return URL_PLACEHOLDER


def _env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#"):
            values[key.strip()] = value.strip()
    return values


def _variants(key: str, value: str) -> Iterable[str]:
    yield value
    if "://" in value:
        password = urlsplit(value).password
        if password:
            yield password
            yield unquote(password)
            yield quote(password, safe="")
    if key.endswith("_HEADERS"):  # `X-Project-Key=<key>,Other=<value>`
        for item in value.split(","):
            yield item.partition("=")[2]


def local_secrets(
    environ: Mapping[str, str] | None = None, env_file: Path | None = None
) -> frozenset[str]:
    """Credential values of this machine: from the repository `.env` and the environment."""
    sources = [_env_file(env_file or REPO_ROOT / ".env"), environ or os.environ]
    found = set()
    for source in sources:
        for key, value in source.items():
            if _SECRET_KEY.search(key.upper()):
                found.update(v for v in _variants(key.upper(), value) if v)
    return frozenset(v for v in found if len(v) >= MIN_SECRET_LEN and v != "CHANGE_ME")


def scrub(text: str, secrets: Iterable[str]) -> str:
    # Longest first, so a whole URL is replaced before the password inside it.
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, PLACEHOLDER)
    return _URL_PASSWORD.sub(lambda m: m.group("head") + PLACEHOLDER + "@", text)


def scrub_report(report: pytest.CollectReport | pytest.TestReport, secrets: Iterable[str]) -> None:
    """Rewrite one report in place. A failure text that needs no change keeps its structure
    (and colours); one that does is replaced by its scrubbed plain text."""
    secrets = tuple(secrets)
    longrepr = report.longrepr
    if isinstance(longrepr, tuple) and len(longrepr) == 3:  # skip: (path, lineno, reason)
        report.longrepr = (longrepr[0], longrepr[1], scrub(longrepr[2], secrets))
    elif longrepr is not None:
        text = str(longrepr)
        clean = scrub(text, secrets)
        if clean != text:
            report.longrepr = clean
    report.sections = [(title, scrub(content, secrets)) for title, content in report.sections]


_SECRETS = local_secrets()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    scrub_report(report, _SECRETS)
    return report


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(collector):
    report = yield
    scrub_report(report, _SECRETS)
    return report
