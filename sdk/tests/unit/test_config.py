"""Configuration contract: valid and invalid settings, env parsing, no value in errors."""

from __future__ import annotations

import logging
import math

import pytest

from llm_insight_sdk import ConfigError, LLMInsightConfig
from llm_insight_sdk.config import (
    BACKEND_MAX_DECOMPRESSED_BYTES,
    BACKEND_MAX_SPANS_PER_REQUEST,
    BACKEND_MAX_WIRE_BYTES,
    MAX_REQUEST_BYTES,
)
from tests.conftest import DUMMY_KEY

ENDPOINT = "http://127.0.0.1:8000/v1/traces"


def cfg(**overrides: object) -> LLMInsightConfig:
    values: dict[str, object] = {
        "endpoint": ENDPOINT,
        "project_key": DUMMY_KEY,
        "service_name": "svc",
    }
    values.update(overrides)
    return LLMInsightConfig(**values)  # type: ignore[arg-type]


def test_defaults_respect_backend_limits_and_units():
    c = cfg()
    assert c.compression == "gzip"
    assert c.export_timeout_seconds == 5.0  # seconds (exporter)
    assert c.schedule_delay_millis == 1000  # milliseconds (processor)
    assert c.shutdown_timeout_millis == 5000  # milliseconds
    assert c.max_export_batch_size == 512 <= BACKEND_MAX_SPANS_PER_REQUEST
    assert c.max_queue_size == 2048
    assert c.max_request_bytes == MAX_REQUEST_BYTES < BACKEND_MAX_WIRE_BYTES
    assert MAX_REQUEST_BYTES < BACKEND_MAX_DECOMPRESSED_BYTES
    assert c.provenance == "synthetic"
    assert c.content_policy == "metadata_only"


def test_repr_and_str_never_contain_the_key():
    c = cfg()
    assert DUMMY_KEY not in repr(c)
    assert DUMMY_KEY not in str(c)
    assert "project_key" not in repr(c)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:8000/v1/traces",
        "http://localhost:8000/v1/traces",
        "http://[::1]:8000/v1/traces",
        "https://telemetry.example.com/v1/traces",
        "https://telemetry.example.com/otlp/v1/traces",
    ],
)
def test_valid_endpoints(endpoint):
    assert cfg(endpoint=endpoint).endpoint == endpoint


@pytest.mark.parametrize(
    ("endpoint", "rule"),
    [
        ("", "missing"),
        ("ftp://127.0.0.1/v1/traces", "http:// or https://"),
        ("127.0.0.1:8000/v1/traces", "http:// or https://"),
        ("http://127.0.0.1:8000/", "must end with /v1/traces"),
        ("http://127.0.0.1:8000/v1/logs", "must end with /v1/traces"),
        ("http://user:pw@127.0.0.1:8000/v1/traces", "user info"),
        ("http://127.0.0.1:8000/v1/traces?key=x", "query"),
        ("http://127.0.0.1:8000/v1/traces#f", "query or fragment"),
        ("http://telemetry.example.com/v1/traces", "plain http is allowed only for loopback"),
        ("http://10.0.0.5:8000/v1/traces", "plain http is allowed only for loopback"),
        ("http://127.0.0.1:99999/v1/traces", "not a valid URL"),
    ],
)
def test_invalid_endpoints(endpoint, rule):
    with pytest.raises(ConfigError, match="^endpoint: ") as exc:
        cfg(endpoint=endpoint)
    assert rule in str(exc.value)


def test_missing_project_key():
    with pytest.raises(ConfigError, match=r"^project_key: missing$"):
        cfg(project_key="")


@pytest.mark.parametrize(
    "bad_key",
    [
        "not-a-key-SECRETVALUE123",
        "lli_short_" + "s" * 43,
        "lli_AbCd1234_tooshort",
        "LLI_AbCd1234_" + "s" * 43,
        DUMMY_KEY + "\n",
        DUMMY_KEY + " ",
        "lli_AbCd1234_" + "s" * 43 + "!",
    ],
)
def test_invalid_project_key_format_is_rejected_without_echoing_it(bad_key):
    with pytest.raises(ConfigError) as exc:
        cfg(project_key=bad_key)
    assert str(exc.value) == "project_key: must have the format lli_<prefix>_<secret>"
    assert bad_key.strip() not in str(exc.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("service_name", ""),
        ("service_name", "svc\nname"),
        ("service_name", "x" * 257),
        ("service_name", "owner alice@example.com"),
        ("service_name", "svc sk-" + "a" * 20),
        ("service_version", "1.0 beta"),
        ("service_version", "v" * 129),
        ("deployment_environment", "prod‮"),
    ],
)
def test_invalid_metadata_settings(field, value):
    with pytest.raises(ConfigError, match=f"^{field}: "):
        cfg(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("export_timeout_seconds", 0),
        ("export_timeout_seconds", 0.05),
        ("export_timeout_seconds", 31),
        ("export_timeout_seconds", -1),
        ("export_timeout_seconds", math.nan),
        ("export_timeout_seconds", math.inf),
        ("export_timeout_seconds", True),
        ("export_timeout_seconds", "5"),
        ("schedule_delay_millis", 10),
        ("schedule_delay_millis", 1.5),
        ("max_queue_size", 0),
        ("max_queue_size", 40_000),
        ("max_export_batch_size", 0),
        ("max_export_batch_size", BACKEND_MAX_SPANS_PER_REQUEST + 1),
        ("max_request_bytes", MAX_REQUEST_BYTES + 1),
        ("max_request_bytes", BACKEND_MAX_WIRE_BYTES),
        ("max_request_bytes", 10),
        ("shutdown_timeout_millis", 50),
        ("shutdown_timeout_millis", 120_000),
        ("compression", "deflate"),
    ],
)
def test_invalid_bounds(field, value):
    with pytest.raises(ConfigError, match=f"^{field}: "):
        cfg(**{field: value})


def test_batch_size_cannot_exceed_queue_size():
    with pytest.raises(ConfigError, match="must not exceed max_queue_size"):
        cfg(max_queue_size=100, max_export_batch_size=200)
    assert cfg(max_queue_size=2048, max_export_batch_size=2048).max_export_batch_size == 2048


def test_real_provenance_is_rejected():
    with pytest.raises(ConfigError, match="^provenance: only 'synthetic'"):
        cfg(provenance="real")


def test_content_capture_is_not_configurable():
    with pytest.raises(ConfigError, match="^content_policy: "):
        cfg(content_policy="capture_content")


def test_configs_are_frozen_and_comparable():
    a, b = cfg(), cfg()
    assert a == b
    assert a != cfg(service_name="other")
    with pytest.raises(AttributeError):
        a.endpoint = "x"  # type: ignore[misc]


# ---------------------------------------------------------------- from_env
BASE_ENV = {
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": ENDPOINT,
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS": f"X-Project-Key={DUMMY_KEY}",
    "OTEL_SERVICE_NAME": "demo-summarizer",
}


def test_from_env_minimal():
    c = LLMInsightConfig.from_env(BASE_ENV)
    assert (c.endpoint, c.project_key, c.service_name) == (ENDPOINT, DUMMY_KEY, "demo-summarizer")


def test_from_env_all_settings():
    env = {
        **BASE_ENV,
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
        "OTEL_EXPORTER_OTLP_TRACES_COMPRESSION": "none",
        "OTEL_TRACES_SAMPLER": "always_on",
        "LLMI_SDK_SERVICE_VERSION": "1.2.3",
        "LLMI_SDK_DEPLOYMENT_ENVIRONMENT": "dev",
        "LLMI_SDK_EXPORT_TIMEOUT_SECONDS": "2.5",
        "LLMI_SDK_SCHEDULE_DELAY_MILLIS": "200",
        "LLMI_SDK_MAX_QUEUE_SIZE": "4096",
        "LLMI_SDK_MAX_EXPORT_BATCH_SIZE": "1024",
        "LLMI_SDK_MAX_REQUEST_BYTES": "1048576",
        "LLMI_SDK_SHUTDOWN_TIMEOUT_MILLIS": "8000",
        "LLMI_SDK_PROVENANCE": "synthetic",
        "LLMI_SDK_CONTENT_POLICY": "metadata_only",
    }
    c = LLMInsightConfig.from_env(env)
    assert c.compression == "none"
    assert c.service_version == "1.2.3"
    assert c.deployment_environment == "dev"
    assert c.export_timeout_seconds == 2.5
    assert (c.schedule_delay_millis, c.max_queue_size, c.max_export_batch_size) == (200, 4096, 1024)
    assert (c.max_request_bytes, c.shutdown_timeout_millis) == (1048576, 8000)


def test_from_env_header_parsing():
    env = {
        **BASE_ENV,
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS": f"other=1, x-project-key = {DUMMY_KEY} ,y=2",
    }
    assert LLMInsightConfig.from_env(env).project_key == DUMMY_KEY
    encoded = {**BASE_ENV, "OTEL_EXPORTER_OTLP_TRACES_HEADERS": "X-Project-Key=" + DUMMY_KEY}
    assert LLMInsightConfig.from_env(encoded).project_key == DUMMY_KEY


def test_from_env_generic_fallbacks():
    env = {
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:8000/",
        "OTEL_EXPORTER_OTLP_HEADERS": f"X-Project-Key={DUMMY_KEY}",
        "OTEL_SERVICE_NAME": "svc",
    }
    c = LLMInsightConfig.from_env(env)
    assert c.endpoint == ENDPOINT
    assert c.project_key == DUMMY_KEY


@pytest.mark.parametrize(
    ("drop", "message"),
    [
        ("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "endpoint: missing"),
        ("OTEL_EXPORTER_OTLP_TRACES_HEADERS", "project_key: missing"),
        ("OTEL_SERVICE_NAME", "service_name: missing"),
    ],
)
def test_from_env_missing_required(drop, message):
    env = {k: v for k, v in BASE_ENV.items() if k != drop}
    with pytest.raises(ConfigError, match=f"^{message}$"):
        LLMInsightConfig.from_env(env)


def test_from_env_header_without_project_key():
    env = {**BASE_ENV, "OTEL_EXPORTER_OTLP_TRACES_HEADERS": "Authorization=Bearer abc"}
    with pytest.raises(ConfigError, match="^project_key: missing$"):
        LLMInsightConfig.from_env(env)


@pytest.mark.parametrize(
    ("var", "value", "message"),
    [
        ("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "grpc", "only http/protobuf"),
        ("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "http/json", "only http/protobuf"),
        ("OTEL_TRACES_SAMPLER", "traceidratio", "only always_on"),
        ("LLMI_SDK_EXPORT_TIMEOUT_SECONDS", "five", "must be a valid float"),
        ("LLMI_SDK_MAX_QUEUE_SIZE", "1e3", "must be a valid int"),
        ("LLMI_SDK_PROVENANCE", "real", "only 'synthetic'"),
    ],
)
def test_from_env_invalid_values(var, value, message):
    with pytest.raises(ConfigError, match=message):
        LLMInsightConfig.from_env({**BASE_ENV, var: value})


def test_from_env_invalid_key_is_not_echoed():
    secretish = "lli_AbCd1234_SECRET"
    env = {**BASE_ENV, "OTEL_EXPORTER_OTLP_TRACES_HEADERS": f"X-Project-Key={secretish}"}
    with pytest.raises(ConfigError) as exc:
        LLMInsightConfig.from_env(env)
    assert secretish not in str(exc.value)
    assert "SECRET" not in repr(exc.value)


def test_from_env_ignores_ambiguous_otel_timeout_with_a_warning(caplog):
    env = {**BASE_ENV, "OTEL_EXPORTER_OTLP_TRACES_TIMEOUT": "10000"}
    with caplog.at_level(logging.WARNING, logger="llm_insight_sdk"):
        c = LLMInsightConfig.from_env(env)
    assert c.export_timeout_seconds == 5.0
    assert "OTEL_EXPORTER_OTLP_TRACES_TIMEOUT is ignored" in caplog.text
