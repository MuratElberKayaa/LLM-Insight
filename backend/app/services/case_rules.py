"""Write-side checks of a test case's `rule` and `expected_output` (ARCHITECTURE.md §4.10).

Phase 4B stored an unversioned rule payload (decision D1). The architectural review revised D1:
since Phase 4C every new rule must follow the versioned contract `llminsight.rule.v1`
(`app.services.evaluation.rules`, ADR-23), with RE2-only patterns and a bounded, local-only
JSON Schema subset, and an expected output must satisfy its own rule. This module keeps the
size bounds and hands the rest to that contract, so the API and the evaluation engine apply
exactly the same rules.
"""

from typing import Any

from app.core import exact_json
from app.services.evaluation.rules import RuleError, validate_for_storage

MAX_JSON_BYTES = 16 * 1024  # serialized `expected_output` and `rule`, each
MAX_JSON_DEPTH = 32

__all__ = ["MAX_JSON_BYTES", "MAX_JSON_DEPTH", "RuleError", "check_json_size", "validate_rule"]


def json_depth(value: Any) -> int:
    depth, stack = 0, [(value, 1)]
    while stack:
        v, d = stack.pop()
        if isinstance(v, dict | list):
            depth = max(depth, d)
            stack.extend((item, d + 1) for item in (v.values() if isinstance(v, dict) else v))
    return depth


def check_json_size(field: str, value: Any) -> None:
    if json_depth(value) > MAX_JSON_DEPTH:
        raise RuleError(field, f"{field} is nested too deeply")
    try:
        text = exact_json.dumps(value)
    except (TypeError, ValueError):
        raise RuleError(field, f"{field} must be exact JSON") from None
    if len(text.encode("utf-8")) > MAX_JSON_BYTES:
        raise RuleError(field, f"{field} must be at most {MAX_JSON_BYTES} bytes as JSON")


def validate_rule(rule: Any, expected_output: Any) -> dict[str, Any]:
    """The rule as it will be stored, or `RuleError`. `expected_output` may be None: such a
    case is stored and every evaluation reports it "not evaluated" (T40)."""
    validate_for_storage(rule, expected_output)
    return rule
