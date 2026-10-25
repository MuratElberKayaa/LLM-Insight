"""Phase 4A: deterministic, metadata-only trace diagnostics (`llminsight.diagnostics.v1`)."""

from app.services.diagnostics.catalog import CONTRACT_VERSION, RULESET_VERSION
from app.services.diagnostics.engine import SpanInput, TraceInput, UsageInput, diagnose

__all__ = [
    "CONTRACT_VERSION",
    "RULESET_VERSION",
    "SpanInput",
    "TraceInput",
    "UsageInput",
    "diagnose",
]
