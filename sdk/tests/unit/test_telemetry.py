"""SDK lifecycle and reliability: init, global provider, flush/shutdown, queue bounds,
timeouts, backend outages, headers and log hygiene. Telemetry is SYNTHETIC."""

from __future__ import annotations

import logging
import subprocess
import sys
import textwrap
import threading
import time

import pytest
from opentelemetry import trace

import llm_insight_sdk as lli
from llm_insight_sdk.mock import MockLLMProvider, ObservedMockClient, Scenario
from tests.conftest import DUMMY_KEY, PROMPT_SENTINEL, free_port, messages

MODEL = "mock-chat-small"


def emit_spans(t: lli.Telemetry, n: int) -> None:
    for i in range(n):
        with t.tracer.start_as_current_span(f"span-{i}"):
            pass


# ---------------------------------------------------------------- init / duplicate init
def test_init_does_not_touch_the_global_provider(stub, make_config):
    before = trace.get_tracer_provider()
    t = lli.init(make_config(stub.endpoint))
    assert trace.get_tracer_provider() is before
    assert isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider)
    assert t.is_global is False
    assert lli.get_active() is t


def test_repeated_init_with_the_same_config_reuses_one_processor(stub, make_config):
    config = make_config(stub.endpoint)
    t1 = lli.init(config)
    t2 = lli.init(make_config(stub.endpoint))  # equal, separately built config
    assert t1 is t2
    emit_spans(t1, 1)
    assert t1.force_flush(5000)
    assert len(stub.spans()) == 1  # exported once, not once per init()
    assert stub.attempts == 1
    assert t1.stats().spans_ended == 1


def test_init_with_a_different_config_is_refused_until_shutdown(stub, make_config):
    t1 = lli.init(make_config(stub.endpoint))
    with pytest.raises(lli.AlreadyInitializedError):
        lli.init(make_config(stub.endpoint, service_name="other"))
    with pytest.raises(lli.AlreadyInitializedError):
        lli.init(make_config(stub.endpoint), set_global=True)
    assert t1.shutdown()
    t2 = lli.init(make_config(stub.endpoint, service_name="other"))
    assert t2 is not t1 and lli.get_active() is t2


def _run_isolated(code: str) -> str:
    """Global TracerProvider state is set-once per process: test it in a fresh interpreter."""
    result = subprocess.run(
        [sys.executable, "-I", "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout + result.stderr


GLOBAL_PRELUDE = f"""
import logging
logging.basicConfig(level=logging.WARNING)  # the SDK logger itself only has a NullHandler
import llm_insight_sdk as lli
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
cfg = lli.LLMInsightConfig(
    endpoint="http://127.0.0.1:{free_port()}/v1/traces",
    project_key="{DUMMY_KEY}",
    service_name="global-test",
    shutdown_timeout_millis=500,
    export_timeout_seconds=0.2,
)
"""


def test_set_global_installs_only_when_no_global_provider_exists():
    out = _run_isolated(
        GLOBAL_PRELUDE
        + """
t = lli.init(cfg, set_global=True)
assert t.is_global and trace.get_tracer_provider() is t.tracer_provider
print("OK")
"""
    )
    assert "OK" in out


def test_set_global_never_replaces_an_existing_provider():
    out = _run_isolated(
        GLOBAL_PRELUDE
        + """
app_provider = TracerProvider()
trace.set_tracer_provider(app_provider)
t = lli.init(cfg, set_global=True)
assert t.is_global is False
assert trace.get_tracer_provider() is app_provider
assert len(app_provider._active_span_processor._span_processors) == 0
print("OK")
"""
    )
    assert "OK" in out
    assert "a global TracerProvider already exists and was not replaced" in out


def test_process_exit_runs_a_bounded_shutdown_without_errors():
    started = time.monotonic()
    out = _run_isolated(
        GLOBAL_PRELUDE
        + """
t = lli.init(cfg)
with t.tracer.start_as_current_span("pending"):
    pass
print("EXITING")
"""
    )
    assert "EXITING" in out
    assert "Traceback" not in out
    assert time.monotonic() - started < 20


def test_process_exit_delivers_pending_spans(stub):
    _run_isolated(
        f"""
import llm_insight_sdk as lli
t = lli.init(lli.LLMInsightConfig(
    endpoint="{stub.endpoint}", project_key="{DUMMY_KEY}", service_name="exit-test",
    schedule_delay_millis=60000,
))
with t.tracer.start_as_current_span("pending-at-exit"):
    pass
"""
    )
    assert [s["name"] for s in stub.spans()] == ["pending-at-exit"]


# ---------------------------------------------------------------- flush / shutdown
def test_force_flush_delivers_and_reports_success(telemetry, stub):
    emit_spans(telemetry, 3)
    assert telemetry.force_flush(5000) is True
    assert len(stub.spans()) == 3
    s = telemetry.stats()
    assert (s.spans_ended, s.spans_exported, s.spans_export_failed) == (3, 3, 0)


def test_shutdown_drains_the_queue_and_is_idempotent(stub, make_config):
    t = lli.init(make_config(stub.endpoint, schedule_delay_millis=60_000))
    emit_spans(t, 5)
    assert stub.spans() == []  # nothing exported yet: long schedule delay
    assert t.shutdown() is True
    assert len(stub.spans()) == 5
    assert t.shutdown() is True  # second call is a no-op
    assert t.is_shutdown and lli.get_active() is None
    assert t.force_flush() is False


def test_the_application_keeps_working_after_shutdown(stub, make_config):
    t = lli.init(make_config(stub.endpoint))
    client = ObservedMockClient(t, MockLLMProvider(time_scale=0.0))
    t.shutdown()
    result = client.chat(model=MODEL, messages=messages())  # no exception, no export
    assert result.text
    time.sleep(0.2)
    assert stub.spans() == []


def test_module_level_shutdown_without_instance_is_a_noop():
    assert lli.shutdown() is True


def test_shutdown_is_bounded_when_the_backend_hangs(stub, make_config):
    stub.behaviour.gate = threading.Event()  # requests block until released
    t = lli.init(make_config(stub.endpoint, export_timeout_seconds=10, shutdown_timeout_millis=500))
    emit_spans(t, 3)
    started = time.monotonic()
    assert t.shutdown() is False  # could not drain in time: reported, not raised
    assert time.monotonic() - started < 2.0


def test_force_flush_is_bounded_when_the_backend_hangs(stub, make_config):
    stub.behaviour.gate = threading.Event()
    t = lli.init(make_config(stub.endpoint, export_timeout_seconds=10))
    emit_spans(t, 1)
    started = time.monotonic()
    assert t.force_flush(300) is False
    assert time.monotonic() - started < 1.5


# ---------------------------------------------------------------- queue bounds
def test_queue_is_bounded_and_emitting_never_blocks(stub, make_config):
    gate = threading.Event()
    stub.behaviour.gate = gate  # the first export blocks the worker thread
    t = lli.init(
        make_config(
            stub.endpoint,
            max_queue_size=8,
            max_export_batch_size=4,
            export_timeout_seconds=10,
            shutdown_timeout_millis=5000,
        )
    )
    emit_spans(t, 4)  # fills one batch; the worker picks it up and blocks on the stub
    deadline = time.monotonic() + 5
    while stub.attempts == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert stub.attempts == 1

    started = time.monotonic()
    emit_spans(t, 200)  # far beyond the queue bound
    assert time.monotonic() - started < 1.0  # the application thread never waits on export

    gate.set()
    assert t.shutdown() is True
    s = t.stats()
    assert s.spans_ended == 204
    delivered = len(stub.spans())
    assert delivered == s.spans_exported
    assert delivered <= 4 + 8  # in-flight batch + bounded queue; the rest was dropped
    assert s.spans_ended - s.spans_exported - s.spans_export_failed >= 204 - 12


def test_batches_never_exceed_the_configured_batch_size(stub, make_config):
    t = lli.init(make_config(stub.endpoint, max_queue_size=4096, max_export_batch_size=2048))
    emit_spans(t, 2100)
    assert t.force_flush(10_000)
    sizes = sorted(len(r.spans()) for r in stub.received)
    assert sum(sizes) == 2100
    assert max(sizes) <= 2048


def test_oversized_request_is_dropped_client_side(stub, make_config):
    t = lli.init(make_config(stub.endpoint, max_request_bytes=2048))
    for i in range(40):
        with t.tracer.start_as_current_span(f"span-{i}", attributes={"pad": "x" * 200}):
            pass
    assert t.force_flush(5000) is False
    assert stub.attempts == 0  # never sent: the backend would answer 413
    assert t.stats().spans_export_failed == 40


# ---------------------------------------------------------------- timeouts and outages
def test_export_timeout_is_in_seconds_and_bounds_a_slow_backend(stub, make_config, monkeypatch):
    # 10000 would mean 10000 s to exporter 1.45.1 if it were read; the SDK passes 0.5 s.
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_TIMEOUT", "10000")
    stub.behaviour.delay_s = 3.0
    t = lli.init(make_config(stub.endpoint, export_timeout_seconds=0.5))
    emit_spans(t, 1)
    started = time.monotonic()
    assert t.force_flush(5000) is False
    assert time.monotonic() - started < 2.5
    assert t.stats().export_batches_failed == 1


def test_backend_unavailable_fails_open(make_config, caplog):
    t = lli.init(
        make_config(f"http://127.0.0.1:{free_port()}/v1/traces", export_timeout_seconds=0.5)
    )
    client = ObservedMockClient(t, MockLLMProvider(time_scale=0.0))
    started = time.monotonic()
    with caplog.at_level(logging.DEBUG):
        with t.tracer.start_as_current_span("demo.request"):
            result = client.chat(model=MODEL, messages=messages())
        app_latency = time.monotonic() - started
        assert result.text  # the application got its answer
        assert app_latency < 0.5  # it did not wait for the export
        assert t.force_flush(5000) is False
        assert t.shutdown() in (True, False)
    s = t.stats()
    assert s.spans_exported == 0 and s.spans_export_failed == 2
    assert DUMMY_KEY not in caplog.text


@pytest.mark.parametrize("status", [401, 400, 413])
def test_rejected_export_is_not_retried_and_fails_open(stub, make_config, status, caplog):
    stub.behaviour.status = status
    t = lli.init(make_config(stub.endpoint))
    client = ObservedMockClient(t, MockLLMProvider(time_scale=0.0))
    with caplog.at_level(logging.DEBUG):
        client.chat(model=MODEL, messages=messages())
        assert t.force_flush(5000) is False
    assert stub.attempts == 1  # non-retryable status: one request
    assert t.stats().export_batches_failed == 1
    assert DUMMY_KEY not in caplog.text


def test_unavailable_503_is_retried_by_the_exporter_then_fails_open(stub, make_config):
    stub.behaviour.status = 503
    t = lli.init(make_config(stub.endpoint, export_timeout_seconds=1.5))
    emit_spans(t, 1)
    assert t.force_flush(5000) is False
    assert stub.attempts >= 2  # retried within the export timeout


def test_backend_recovery_after_an_outage(stub, make_config):
    stub.behaviour.status = 401
    t = lli.init(make_config(stub.endpoint))
    emit_spans(t, 1)
    assert t.force_flush(5000) is False
    stub.behaviour.status = 200
    emit_spans(t, 2)
    assert t.force_flush(5000) is True
    assert len(stub.spans()) == 2  # the failed batch is lost (no persistent queue)


# ---------------------------------------------------------------- wire and secrets
@pytest.mark.parametrize("compression", ["gzip", "none"])
def test_wire_format_and_project_key_header(stub, make_config, compression):
    t = lli.init(make_config(stub.endpoint, compression=compression))
    emit_spans(t, 1)
    assert t.force_flush(5000)
    (req,) = stub.received
    assert req.headers["content-type"] == "application/x-protobuf"
    assert req.headers["x-project-key"] == DUMMY_KEY
    assert req.headers.get("content-encoding") == ("gzip" if compression == "gzip" else None)
    assert "authorization" not in req.headers
    assert DUMMY_KEY.encode() not in req.body  # the key is a header, never span data


def test_project_key_never_appears_in_logs_reprs_or_stats(stub, make_config, caplog):
    with caplog.at_level(logging.DEBUG):
        t = lli.init(make_config(stub.endpoint))
        client = ObservedMockClient(t, MockLLMProvider(time_scale=0.0), max_attempts=2)
        client.chat(model=MODEL, messages=messages(), scenario=Scenario.RETRY_THEN_SUCCESS)
        t.force_flush(5000)
        stub.behaviour.status = 401
        emit_spans(t, 1)
        t.force_flush(5000)
        logging.getLogger("diag").debug("telemetry=%r config=%s", t, t.config)
        t.shutdown()
    for text in (caplog.text, repr(t), str(t.config), repr(t.stats())):
        assert DUMMY_KEY not in text
        assert DUMMY_KEY[13:] not in text  # not even the secret part
        assert PROMPT_SENTINEL not in text


def test_no_secret_reaches_stored_attributes_via_metadata(stub, make_config):
    t = lli.init(make_config(stub.endpoint))
    client = ObservedMockClient(t, MockLLMProvider(time_scale=0.0))
    client.chat(model=MODEL, messages=messages(), prompt_version=DUMMY_KEY)
    assert t.force_flush(5000)
    for req in stub.received:
        assert DUMMY_KEY.encode() not in req.body
