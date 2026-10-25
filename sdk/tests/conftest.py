"""Shared fixtures. Tests import the *installed* package (src layout, non-editable install)."""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterator

import pytest

import llm_insight_sdk as lli
from llm_insight_sdk.mock import MockLLMProvider, ObservedMockClient
from tests.otlp_stub import OTLPStub, start_stub

# Report hooks that keep local credentials out of every failure report (tests/redaction.py).
from tests.redaction import pytest_make_collect_report, pytest_runtest_makereport  # noqa: F401

# Well-formed dummy key (format lli_<8>_<43+>); never a real credential.
DUMMY_KEY = "lli_AbCd1234_" + "k" * 43
# Strings that must never leave the process (content and credentials).
PROMPT_SENTINEL = "PROMPT-SENTINEL-do-not-export-7f3a"
SYSTEM_SENTINEL = "SYSTEM-INSTRUCTION-SENTINEL-91c2"


def free_port() -> int:
    """A loopback port with nothing listening (connection refused)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(autouse=True)
def _no_active_instance() -> Iterator[None]:
    """Every test starts and ends without an active SDK instance."""
    lli.shutdown(timeout_millis=2000)
    yield
    lli.shutdown(timeout_millis=2000)


@pytest.fixture
def stub() -> Iterator[OTLPStub]:
    s = start_stub()
    yield s
    s.stop()


@pytest.fixture
def make_config() -> Callable[..., lli.LLMInsightConfig]:
    def _make(endpoint: str, **overrides: object) -> lli.LLMInsightConfig:
        values: dict[str, object] = {
            "endpoint": endpoint,
            "project_key": DUMMY_KEY,
            "service_name": "sdk-unit-test",
            "service_version": "0.1.0",
            "deployment_environment": "test",
            "schedule_delay_millis": 50,
            "export_timeout_seconds": 2.0,
            "shutdown_timeout_millis": 3000,
        }
        values.update(overrides)
        return lli.LLMInsightConfig(**values)  # type: ignore[arg-type]

    return _make


@pytest.fixture
def telemetry(stub, make_config) -> Iterator[lli.Telemetry]:
    t = lli.init(make_config(stub.endpoint))
    yield t
    t.shutdown(timeout_millis=3000)


@pytest.fixture
def client(telemetry) -> ObservedMockClient:
    return ObservedMockClient(telemetry, MockLLMProvider(time_scale=0.0), max_attempts=3)


def messages() -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_SENTINEL},
        {"role": "user", "content": PROMPT_SENTINEL},
    ]
