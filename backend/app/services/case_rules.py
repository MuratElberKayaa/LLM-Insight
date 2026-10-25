"""Shape of a test case's `rule` and `expected_output` (ARCHITECTURE.md §4.10, Phase 4B).

The contract fixes only `rule->>'type' IN ('json_schema','field_match','exact','regex')`. The
parameters below are the narrowest reading of the documented semantics ("JSON Schema, expected
field/value match, exact-match/regex", `CLAUDE_PHASE_PROMPTS.md` §5) and are recorded as
decision D1 in `reports/phase-4b.md` for the architectural review:

    {"type": "json_schema", "schema": <JSON Schema: object or boolean>}
    {"type": "field_match", "fields": ["<dotted.path>", ...]}   compared with expected_output
    {"type": "exact"}                                            output == expected_output
    {"type": "regex", "pattern": "<Python re pattern>"}

Nothing here evaluates anything (Phase 4C). Validation only makes sure a stored case is
well-formed for that later engine: unknown keys are refused, paths are plain, a pattern
compiles, and a schema cannot reference a remote document. Full JSON Schema meta-validation
needs `jsonschema`, which is not installed in Phase 4B (a Phase 4C item).
"""

import json
import re
from typing import Any

from app.models.dataset import RULE_TYPES

MAX_JSON_BYTES = 16 * 1024  # serialized `expected_output` and `rule`, each
MAX_JSON_DEPTH = 32
MAX_FIELDS = 50
MAX_PATTERN = 1000
_PATH = re.compile(r"[A-Za-z0-9_-]{1,64}(?:\.[A-Za-z0-9_-]{1,64}){0,9}")
_PARAMS: dict[str, frozenset[str]] = {
    "json_schema": frozenset({"type", "schema"}),
    "field_match": frozenset({"type", "fields"}),
    "exact": frozenset({"type"}),
    "regex": frozenset({"type", "pattern"}),
}


class RuleError(ValueError):
    """`field` names the request field; the message is generic and never quotes a value."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


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
    size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if size > MAX_JSON_BYTES:
        raise RuleError(field, f"{field} must be at most {MAX_JSON_BYTES} bytes as JSON")


def _remote_refs(schema: Any) -> bool:
    stack = [schema]
    while stack:
        v = stack.pop()
        if isinstance(v, dict):
            for k, item in v.items():
                if k in ("$ref", "$dynamicRef") and not (
                    isinstance(item, str) and item.startswith("#")
                ):
                    return True
                stack.append(item)
        elif isinstance(v, list):
            stack.extend(v)
    return False


def _path_present(document: Any, path: str) -> bool:
    node = document
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def validate_rule(rule: Any, expected_output: Any) -> dict[str, Any]:
    """The rule as it will be stored, or `RuleError`. `expected_output` may be None: a case
    without one is stored and later reported "not evaluated" for rules that need it (T40)."""
    if not isinstance(rule, dict):
        raise RuleError("rule", "rule must be a JSON object")
    kind = rule.get("type")
    if kind not in RULE_TYPES:
        raise RuleError("rule", f"rule.type must be one of {', '.join(RULE_TYPES)}")
    if set(rule) - _PARAMS[kind]:
        raise RuleError("rule", f"rule has fields that a {kind} rule does not take")
    if set(rule) != _PARAMS[kind]:
        raise RuleError("rule", f"a {kind} rule needs: {', '.join(sorted(_PARAMS[kind]))}")
    check_json_size("rule", rule)

    if kind == "json_schema":
        schema = rule["schema"]
        if not isinstance(schema, dict | bool):
            raise RuleError("rule", "rule.schema must be a JSON Schema (an object or a boolean)")
        if _remote_refs(schema):
            raise RuleError("rule", "rule.schema may only use local references ($ref: '#...')")
    elif kind == "field_match":
        fields = rule["fields"]
        if (
            not isinstance(fields, list)
            or not 1 <= len(fields) <= MAX_FIELDS
            or not all(isinstance(f, str) and _PATH.fullmatch(f) for f in fields)
        ):
            raise RuleError(
                "rule", f"rule.fields must be 1-{MAX_FIELDS} dotted field paths (letters, digits)"
            )
        if len(set(fields)) != len(fields):
            raise RuleError("rule", "rule.fields must not repeat a path")
        if expected_output is not None and not all(
            _path_present(expected_output, f) for f in fields
        ):
            raise RuleError("expected_output", "expected_output must contain every rule field")
    elif kind == "regex":
        pattern = rule["pattern"]
        if not isinstance(pattern, str) or not 1 <= len(pattern) <= MAX_PATTERN:
            raise RuleError("rule", f"rule.pattern must be 1-{MAX_PATTERN} characters")
        try:
            re.compile(pattern)
        except (re.error, RecursionError, OverflowError):
            raise RuleError("rule", "rule.pattern is not a valid regular expression") from None
    return rule
