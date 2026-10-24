"""SDK lifecycle: TracerProvider, BatchSpanProcessor and the OTLP/HTTP protobuf exporter.

- `init()` builds a **private** TracerProvider. It never replaces the global provider; with
  `set_global=True` it installs itself only if no global SDK provider exists yet.
- `init()` is idempotent: the same config returns the same instance (one processor, one
  exporter). A different config raises `AlreadyInitializedError` until `shutdown()`.
- Export runs on the BatchSpanProcessor worker thread. The application thread only appends
  to a bounded in-memory queue (oldest span dropped when full). Export failures are logged
  by OpenTelemetry and counted here; they never raise into the application (ADR-09).
- Delivery is **best effort**: queued spans are lost on a crash, a kill, a shutdown that
  exceeds its timeout, or a backend outage longer than the export timeout. There is no
  persistent queue (ADR-06).
"""

from __future__ import annotations

import atexit
import logging
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.sdk.version import __version__ as otel_sdk_version

from llm_insight_sdk import instrumentation
from llm_insight_sdk._version import __version__
from llm_insight_sdk.config import PROJECT_KEY_HEADER, LLMInsightConfig

logger = logging.getLogger("llm_insight_sdk")

SCOPE_NAME = "llm_insight_sdk"


class AlreadyInitializedError(RuntimeError):
    """`init()` was called again with a different configuration before `shutdown()`."""


@dataclass(frozen=True)
class TelemetryStats:
    """Process-local counters. `spans_ended - spans_exported - spans_export_failed` spans
    are still queued or were dropped (queue full, or shutdown before export)."""

    spans_ended: int
    spans_exported: int
    spans_export_failed: int
    export_batches_succeeded: int
    export_batches_failed: int
    attributes_withheld: int


class _Counters:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values = dict.fromkeys(TelemetryStats.__dataclass_fields__, 0)

    def add(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._values[name] += n

    def snapshot(self) -> TelemetryStats:
        with self._lock:
            return TelemetryStats(**self._values)


class _CountingExporter(SpanExporter):
    """Wraps the OTLP exporter: counts outcomes and turns any exception into FAILURE."""

    def __init__(self, inner: SpanExporter, counters: _Counters) -> None:
        self._inner = inner
        self._counters = counters
        self._shutdown_lock = threading.Lock()
        self._is_shutdown = False

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            result = self._inner.export(spans)
        except Exception as exc:
            logger.warning("llm_insight_sdk: span export raised %s", type(exc).__name__)
            result = SpanExportResult.FAILURE
        if result is SpanExportResult.SUCCESS:
            self._counters.add("export_batches_succeeded")
            self._counters.add("spans_exported", len(spans))
        else:
            self._counters.add("export_batches_failed")
            self._counters.add("spans_export_failed", len(spans))
        return result

    def shutdown(self) -> None:
        with self._shutdown_lock:
            if self._is_shutdown:
                return
            self._is_shutdown = True
        try:
            self._inner.shutdown()
        except Exception as exc:
            logger.warning("llm_insight_sdk: exporter shutdown raised %s", type(exc).__name__)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


class _EndedSpanCounter(SpanProcessor):
    def __init__(self, counters: _Counters) -> None:
        self._counters = counters

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        pass

    def on_end(self, span: ReadableSpan) -> None:
        self._counters.add("spans_ended")


def _run_bounded(fn: Callable[[], object], timeout_s: float, name: str) -> bool:
    """Run `fn` on a daemon thread; True if it finished within `timeout_s`. OpenTelemetry's
    own force_flush/shutdown do not enforce their timeout on a blocked export."""
    thread = threading.Thread(target=fn, name=name, daemon=True)
    try:
        thread.start()
    except RuntimeError:  # no new threads during interpreter finalization: run inline
        fn()
        return True
    thread.join(timeout_s)
    return not thread.is_alive()


def _resource(config: LLMInsightConfig) -> Resource:
    # Built explicitly (not Resource.create) so OTEL_RESOURCE_ATTRIBUTES and resource
    # detectors cannot add host, process or user attributes behind the application's back.
    attributes: dict[str, str] = {
        "service.name": config.service_name,
        "telemetry.sdk.name": "opentelemetry",
        "telemetry.sdk.language": "python",
        "telemetry.sdk.version": otel_sdk_version,
        "llminsight.sdk.version": __version__,
    }
    if config.service_version is not None:
        attributes["service.version"] = config.service_version
    if config.deployment_environment is not None:
        attributes["deployment.environment.name"] = config.deployment_environment
    return Resource(attributes)


def build_span_exporter(config: LLMInsightConfig) -> OTLPSpanExporter:
    """The official OTLP/HTTP protobuf exporter, every setting passed explicitly. The
    timeout is in seconds and is passed explicitly, so OTEL_EXPORTER_OTLP_TRACES_TIMEOUT
    (milliseconds in the spec, seconds in exporter 1.45.1) has no effect."""
    return OTLPSpanExporter(
        endpoint=config.endpoint,
        headers={PROJECT_KEY_HEADER: config.project_key},
        timeout=config.export_timeout_seconds,
        compression=Compression.Gzip if config.compression == "gzip" else Compression.NoCompression,
        max_request_size=config.max_request_bytes,
    )


class Telemetry:
    """One SDK instance. Create it with `llm_insight_sdk.init()`."""

    def __init__(self, config: LLMInsightConfig) -> None:
        self.config = config
        self._counters = _Counters()
        self._lock = threading.Lock()
        self._is_shutdown = False
        self.is_global = False
        self._exporter = _CountingExporter(build_span_exporter(config), self._counters)
        self._provider = TracerProvider(
            sampler=ALWAYS_ON,  # ADR-06
            resource=_resource(config),
            shutdown_on_exit=False,  # the module's atexit hook applies the bounded shutdown
        )
        self._provider.add_span_processor(_EndedSpanCounter(self._counters))
        self._provider.add_span_processor(
            BatchSpanProcessor(
                self._exporter,
                max_queue_size=config.max_queue_size,
                schedule_delay_millis=config.schedule_delay_millis,
                max_export_batch_size=config.max_export_batch_size,
                # Milliseconds. BatchSpanProcessor 1.45.1 accepts but does not enforce it;
                # each export is bounded by the exporter's timeout (seconds) instead.
                export_timeout_millis=config.export_timeout_seconds * 1000,
            )
        )
        self.tracer = self._provider.get_tracer(SCOPE_NAME, __version__)

    @property
    def tracer_provider(self) -> TracerProvider:
        return self._provider

    @property
    def is_shutdown(self) -> bool:
        return self._is_shutdown

    def __repr__(self) -> str:
        return f"Telemetry(config={self.config!r}, is_global={self.is_global})"

    def stats(self) -> TelemetryStats:
        return self._counters.snapshot()

    def _withheld(self, key: str) -> None:
        self._counters.add("attributes_withheld")
        logger.debug("llm_insight_sdk: withheld attribute %s (metadata rule)", key)

    @contextmanager
    def inference_span(
        self,
        *,
        operation: str,
        provider: str,
        request_model: str | None,
        call_id: str,
        attempt: int,
        prompt_name: str | None = None,
        prompt_version: str | None = None,
        params: instrumentation.RequestParams | None = None,
    ) -> Iterator[instrumentation.InferenceRecorder]:
        """A CLIENT span for one physical inference attempt, labelled with the configured
        provenance. See `instrumentation.inference_span`."""
        with instrumentation.inference_span(
            self.tracer,
            on_withheld=self._withheld,
            provenance=self.config.provenance,
            operation=operation,
            provider=provider,
            request_model=request_model,
            call_id=call_id,
            attempt=attempt,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            params=params,
        ) as recorder:
            yield recorder

    def force_flush(self, timeout_millis: int | None = None) -> bool:
        """Export everything queued now. True only if that finished within the timeout
        (default: `shutdown_timeout_millis`) and no export failed meanwhile. False never
        means an exception: the application keeps running either way."""
        if self._is_shutdown:
            return False
        timeout = self.config.shutdown_timeout_millis if timeout_millis is None else timeout_millis
        before = self._counters.snapshot().export_batches_failed
        finished = _run_bounded(self._provider.force_flush, timeout / 1000, "llm-insight-flush")
        return finished and self._counters.snapshot().export_batches_failed == before

    def shutdown(self, timeout_millis: int | None = None) -> bool:
        """Flush what is queued and stop the worker, bounded by the timeout (default:
        `shutdown_timeout_millis`). Idempotent. True if the drain finished in time and no
        export failed during it; spans still queued after the timeout are lost."""
        with self._lock:
            if self._is_shutdown:
                return True
            self._is_shutdown = True
        _forget(self)
        timeout = self.config.shutdown_timeout_millis if timeout_millis is None else timeout_millis
        before = self._counters.snapshot().export_batches_failed
        finished = _run_bounded(self._provider.shutdown, timeout / 1000, "llm-insight-shutdown")
        if not finished:
            # Stop the exporter's retry backoff; an in-flight request still ends within
            # export_timeout_seconds on the (daemon) worker thread.
            self._exporter.shutdown()
            logger.warning("llm_insight_sdk: shutdown timed out; queued spans may be lost")
        return finished and self._counters.snapshot().export_batches_failed == before

    def _try_set_global(self) -> None:
        current = trace.get_tracer_provider()
        if isinstance(current, trace.ProxyTracerProvider):
            trace.set_tracer_provider(self._provider)
        self.is_global = trace.get_tracer_provider() is self._provider
        if not self.is_global:
            logger.warning(
                "llm_insight_sdk: a global TracerProvider already exists and was not replaced; "
                "use Telemetry.tracer for LLM Insight spans"
            )


_registry_lock = threading.Lock()
_active: Telemetry | None = None


def _forget(instance: Telemetry) -> None:
    global _active
    with _registry_lock:
        if _active is instance:
            _active = None


def init(config: LLMInsightConfig, *, set_global: bool = False) -> Telemetry:
    """Start the SDK, or return the running instance if it has the same configuration."""
    global _active
    with _registry_lock:
        if _active is not None:
            if _active.config == config and (_active.is_global or not set_global):
                return _active
            raise AlreadyInitializedError(
                "llm_insight_sdk is already initialized with a different configuration; "
                "call shutdown() first"
            )
        instance = Telemetry(config)
        if set_global:
            instance._try_set_global()
        _active = instance
        return instance


def get_active() -> Telemetry | None:
    with _registry_lock:
        return _active


def shutdown(timeout_millis: int | None = None) -> bool:
    """Shut down the active instance, if any."""
    instance = get_active()
    return True if instance is None else instance.shutdown(timeout_millis)


@atexit.register
def _shutdown_at_exit() -> None:
    try:
        shutdown()
    except Exception:  # never fail interpreter exit
        logger.debug("llm_insight_sdk: shutdown at exit failed", exc_info=False)
