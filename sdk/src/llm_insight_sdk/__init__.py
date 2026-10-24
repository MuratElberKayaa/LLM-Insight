"""LLM Insight telemetry SDK: metadata-only OpenTelemetry GenAI spans over OTLP/HTTP protobuf.

    import llm_insight_sdk as lli
    from llm_insight_sdk.mock import ObservedMockClient

    telemetry = lli.init(lli.LLMInsightConfig.from_env())
    with telemetry.tracer.start_as_current_span("handle-request"):
        ObservedMockClient(telemetry).chat(model="mock-chat-small", messages=[...])
    telemetry.shutdown()

See sdk/README.md for the configuration contract and delivery guarantees (best effort).
"""

import logging

from llm_insight_sdk._version import __version__
from llm_insight_sdk.config import ConfigError, LLMInsightConfig
from llm_insight_sdk.instrumentation import (
    InferenceRecorder,
    RequestParams,
    TokenUsage,
    acall_with_retries,
    call_with_retries,
    classify_error,
    new_call_id,
)
from llm_insight_sdk.telemetry import (
    AlreadyInitializedError,
    Telemetry,
    TelemetryStats,
    get_active,
    init,
    shutdown,
)

logging.getLogger("llm_insight_sdk").addHandler(logging.NullHandler())

__all__ = [
    "AlreadyInitializedError",
    "ConfigError",
    "InferenceRecorder",
    "LLMInsightConfig",
    "RequestParams",
    "Telemetry",
    "TelemetryStats",
    "TokenUsage",
    "__version__",
    "acall_with_retries",
    "call_with_retries",
    "classify_error",
    "get_active",
    "init",
    "new_call_id",
    "shutdown",
]
