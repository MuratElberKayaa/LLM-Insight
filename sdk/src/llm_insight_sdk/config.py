"""SDK configuration contract (sdk/README.md "Configuration").

Every value is validated when the config is created. `ConfigError` messages name the
setting and the rule, never the value, so a mistyped key cannot leak into a log.

Timeout units are explicit in every name: the OTLP exporter timeout is in **seconds**,
the BatchSpanProcessor and shutdown timeouts are in **milliseconds**.
"""

from __future__ import annotations

import ipaddress
import logging
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import unquote, urlsplit

from llm_insight_sdk import _guards

logger = logging.getLogger("llm_insight_sdk")

# Backend ingest limits (TELEMETRY_CONTRACT.md §1, ADR-17).
BACKEND_MAX_SPANS_PER_REQUEST = 2048
BACKEND_MAX_WIRE_BYTES = 4 * 1024 * 1024
BACKEND_MAX_DECOMPRESSED_BYTES = 16 * 1024 * 1024
# The exporter checks the serialized request *before* compression and drops a larger batch
# without sending it. Keeping the uncompressed body 64 KiB under the 4 MiB wire limit bounds
# both backend limits: gzip adds only a few bytes per 32 KiB block even for incompressible
# input, so the wire body stays < 4 MiB, and the decompressed size is <= 4 MiB < 16 MiB.
MAX_REQUEST_BYTES = BACKEND_MAX_WIRE_BYTES - 64 * 1024

TRACES_PATH = "/v1/traces"
PROJECT_KEY_HEADER = "X-Project-Key"
# Backend key format (SECURITY.md §2): lli_<8 alphanumerics>_<urlsafe secret>.
_PROJECT_KEY_RE = re.compile(r"lli_[A-Za-z0-9]{8}_[A-Za-z0-9_-]{43,128}")


class ConfigError(ValueError):
    """Invalid SDK configuration. The message names the setting, never its value."""


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _int_in(name: str, value: object, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ConfigError(f"{name}: must be an integer between {low} and {high}")
    return value


@dataclass(frozen=True, kw_only=True)
class LLMInsightConfig:
    """Settings for one SDK instance. `project_key` is excluded from `repr()`."""

    endpoint: str
    project_key: str = field(repr=False)
    service_name: str
    service_version: str | None = None
    deployment_environment: str | None = None
    compression: Literal["gzip", "none"] = "gzip"
    # OTLPSpanExporter timeout in SECONDS: one export request including the exporter's own
    # retries of 429/502/503/504 and connection errors.
    export_timeout_seconds: float = 5.0
    # BatchSpanProcessor settings in MILLISECONDS / span counts.
    schedule_delay_millis: int = 1000
    max_queue_size: int = 2048
    max_export_batch_size: int = 512
    # Upper bound of one uncompressed export request (bytes); see MAX_REQUEST_BYTES.
    max_request_bytes: int = MAX_REQUEST_BYTES
    # Bound on shutdown() and the default bound on force_flush(), in MILLISECONDS.
    shutdown_timeout_millis: int = 5000
    # Phase 2A emits only synthetic telemetry; "real" is reserved for the OpenAI adapter.
    provenance: Literal["synthetic"] = "synthetic"
    # ADR-19: the SDK never captures prompts, completions, instructions or tool definitions.
    content_policy: Literal["metadata_only"] = "metadata_only"

    def __post_init__(self) -> None:
        self._validate_endpoint()
        if not isinstance(self.project_key, str) or not self.project_key:
            raise ConfigError("project_key: missing")
        if not _PROJECT_KEY_RE.fullmatch(self.project_key):
            raise ConfigError("project_key: must have the format lli_<prefix>_<secret>")
        if not isinstance(self.service_name, str) or not self.service_name:
            raise ConfigError("service_name: missing")
        for name, value, rule in (
            ("service_name", self.service_name, _guards.LABEL),
            ("service_version", self.service_version, _guards.VERSION),
            ("deployment_environment", self.deployment_environment, _guards.LABEL),
        ):
            if value is not None and _guards.check(value, rule) != value:
                raise ConfigError(
                    f"{name}: must be a non-empty metadata value without control characters, "
                    "credentials or personal data, at most "
                    f"{rule.max_len} characters"
                )
        if self.compression not in ("gzip", "none"):
            raise ConfigError("compression: must be 'gzip' or 'none'")
        t = self.export_timeout_seconds
        if (
            isinstance(t, bool)
            or not isinstance(t, int | float)
            or not math.isfinite(t)
            or not 0.1 <= t <= 30
        ):
            raise ConfigError("export_timeout_seconds: must be a number of seconds in [0.1, 30]")
        _int_in("schedule_delay_millis", self.schedule_delay_millis, 50, 60_000)
        _int_in("max_queue_size", self.max_queue_size, 1, 32_768)
        _int_in(
            "max_export_batch_size", self.max_export_batch_size, 1, BACKEND_MAX_SPANS_PER_REQUEST
        )
        if self.max_export_batch_size > self.max_queue_size:
            raise ConfigError("max_export_batch_size: must not exceed max_queue_size")
        _int_in("max_request_bytes", self.max_request_bytes, 1024, MAX_REQUEST_BYTES)
        _int_in("shutdown_timeout_millis", self.shutdown_timeout_millis, 100, 60_000)
        if self.provenance != "synthetic":
            raise ConfigError(
                "provenance: only 'synthetic' is supported in this release; 'real' is "
                "reserved for the provider adapter, which is not implemented yet"
            )
        if self.content_policy != "metadata_only":
            raise ConfigError("content_policy: only 'metadata_only' is supported (ADR-19)")

    def _validate_endpoint(self) -> None:
        if not isinstance(self.endpoint, str) or not self.endpoint:
            raise ConfigError("endpoint: missing")
        try:
            parts = urlsplit(self.endpoint)
            port = parts.port
        except ValueError:
            raise ConfigError("endpoint: not a valid URL") from None
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ConfigError("endpoint: must be an http:// or https:// URL with a host")
        if parts.username is not None or parts.password is not None:
            raise ConfigError("endpoint: must not contain user info (send the key as a header)")
        if parts.query or parts.fragment:
            raise ConfigError("endpoint: must not contain a query or fragment")
        if not parts.path.endswith(TRACES_PATH):
            raise ConfigError(f"endpoint: path must end with {TRACES_PATH}")
        if port == 0:
            raise ConfigError("endpoint: port must be 1-65535")
        # The project key travels in a header: plain HTTP is accepted only on loopback.
        if parts.scheme == "http" and not _is_loopback(parts.hostname):
            raise ConfigError("endpoint: plain http is allowed only for loopback hosts; use https")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> LLMInsightConfig:
        """Build a config from environment variables (sdk/README.md lists them)."""
        env = os.environ if environ is None else environ

        protocol = env.get("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL") or env.get(
            "OTEL_EXPORTER_OTLP_PROTOCOL"
        )
        if protocol and protocol.strip() != "http/protobuf":
            raise ConfigError("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL: only http/protobuf is supported")
        sampler = env.get("OTEL_TRACES_SAMPLER")
        if sampler and sampler.strip() != "always_on":
            raise ConfigError("OTEL_TRACES_SAMPLER: only always_on is supported (ADR-06)")
        if env.get("OTEL_EXPORTER_OTLP_TRACES_TIMEOUT") or env.get("OTEL_EXPORTER_OTLP_TIMEOUT"):
            # The spec defines these in milliseconds; the Python exporter 1.45.1 reads them as
            # seconds. The SDK always passes an explicit timeout, so they have no effect.
            logger.warning(
                "OTEL_EXPORTER_OTLP_TRACES_TIMEOUT is ignored (ambiguous unit); "
                "use LLMI_SDK_EXPORT_TIMEOUT_SECONDS"
            )

        endpoint = env.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")
        if not endpoint and env.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
            endpoint = env["OTEL_EXPORTER_OTLP_ENDPOINT"].rstrip("/") + TRACES_PATH
        headers = _parse_headers(
            env.get("OTEL_EXPORTER_OTLP_TRACES_HEADERS") or env.get("OTEL_EXPORTER_OTLP_HEADERS")
        )

        values: dict[str, object] = {
            "endpoint": endpoint,
            "project_key": headers.get(PROJECT_KEY_HEADER.lower(), ""),
            "service_name": env.get("OTEL_SERVICE_NAME", ""),
        }
        optional: dict[str, tuple[str, type]] = {
            "service_version": ("LLMI_SDK_SERVICE_VERSION", str),
            "deployment_environment": ("LLMI_SDK_DEPLOYMENT_ENVIRONMENT", str),
            "export_timeout_seconds": ("LLMI_SDK_EXPORT_TIMEOUT_SECONDS", float),
            "schedule_delay_millis": ("LLMI_SDK_SCHEDULE_DELAY_MILLIS", int),
            "max_queue_size": ("LLMI_SDK_MAX_QUEUE_SIZE", int),
            "max_export_batch_size": ("LLMI_SDK_MAX_EXPORT_BATCH_SIZE", int),
            "max_request_bytes": ("LLMI_SDK_MAX_REQUEST_BYTES", int),
            "shutdown_timeout_millis": ("LLMI_SDK_SHUTDOWN_TIMEOUT_MILLIS", int),
            "provenance": ("LLMI_SDK_PROVENANCE", str),
            "content_policy": ("LLMI_SDK_CONTENT_POLICY", str),
        }
        for name, (var, kind) in optional.items():
            raw = env.get(var)
            if raw is None or raw == "":
                continue
            try:
                values[name] = kind(raw.strip())
            except ValueError:
                raise ConfigError(f"{var}: must be a valid {kind.__name__}") from None
        compression = env.get("OTEL_EXPORTER_OTLP_TRACES_COMPRESSION") or env.get(
            "OTEL_EXPORTER_OTLP_COMPRESSION"
        )
        if compression:
            values["compression"] = compression.strip().lower()
        return cls(**values)  # type: ignore[arg-type]


def _parse_headers(raw: str | None) -> dict[str, str]:
    """OTel header list: comma-separated `name=value` pairs, values URL-encoded."""
    result: dict[str, str] = {}
    for item in (raw or "").split(","):
        name, sep, value = item.partition("=")
        if sep and name.strip():
            result[name.strip().lower()] = unquote(value.strip())
    return result
