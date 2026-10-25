"""Rule contract `llminsight.rule.v1` (Phase 4C; supersedes the unversioned Phase 4B payload,
decision D1 as revised by the architectural review, ADR-23).

A test case's `rule` (ARCHITECTURE.md §4.10, JSONB) is one of exactly these objects. Every key
is required, no other key is accepted, and `version` is the JSON integer `1`:

    {"version": 1, "type": "json_schema", "schema": <draft 2020-12 schema: object | boolean>}
    {"version": 1, "type": "field_match", "fields": ["<dotted.path>", ...]}
    {"version": 1, "type": "exact", "output_format": "text" | "json"}
    {"version": 1, "type": "regex", "pattern": "<RE2 pattern>", "match": "full" | "search"}

**Output representation.** The evaluated output is the model's raw text, as a Unicode string
that must encode to UTF-8 (at most `MAX_OUTPUT_BYTES` bytes). `json_schema`, `field_match` and
`exact` with `output_format: json` first parse that text as strict, exact JSON
(`app.core.exact_json`); `regex` and `exact` with `output_format: text` use the text itself.

**Expected output.** `expected_output` is the human-approved expected result. Every rule type
needs one: a case without it is never evaluated (`passed = NULL`, T40). When present it must
satisfy its own rule, which proves the case is coherent before anything is run:
- `json_schema`: it validates against the schema;
- `field_match`: it is an object that has every listed path;
- `exact`/`text`: it is a string; `exact`/`json`: any JSON value;
- `regex`: it is a string the pattern matches with the stated `match` mode.

**Comparison semantics** (`json_values`): exact structural equality; key order ignored,
array order significant, booleans never numbers, numbers by exact value, strings by code
points. `field_match` compares the value at each path of the output with the value at the
same path of the expected output; a missing path in the output is a mismatch.

**Unknown versions and unversioned rules** (the Phase 4B payload) are refused for storage
and reported `rule_unsupported` (not evaluated) by the engine; stored data is never
reinterpreted. The development database held no test case when this contract was introduced.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from app.services.evaluation import json_values, safe_regex, safe_schema

RULE_CONTRACT = "llminsight.rule.v1"
RULE_VERSION = 1
RULE_TYPES = ("json_schema", "field_match", "exact", "regex")
OUTPUT_FORMATS = ("text", "json")
MAX_FIELDS = 50
MAX_OUTPUT_BYTES = 32 * 1024  # evaluated output, UTF-8

_PATH = re.compile(r"[A-Za-z0-9_-]{1,64}(?:\.[A-Za-z0-9_-]{1,64}){0,9}")
_KEYS: dict[str, frozenset[str]] = {
    "json_schema": frozenset({"version", "type", "schema"}),
    "field_match": frozenset({"version", "type", "fields"}),
    "exact": frozenset({"version", "type", "output_format"}),
    "regex": frozenset({"version", "type", "pattern", "match"}),
}


class RuleError(ValueError):
    """`field` names the request field (`rule` or `expected_output`); the message is fixed
    text and never quotes a submitted value."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


@dataclass(frozen=True)
class JsonSchemaRule:
    schema: Any
    validator: Any = field(compare=False, repr=False)
    names: frozenset[str] = field(compare=False, repr=False)
    type: Literal["json_schema"] = "json_schema"


@dataclass(frozen=True)
class FieldMatchRule:
    fields: tuple[str, ...]
    type: Literal["field_match"] = "field_match"


@dataclass(frozen=True)
class ExactRule:
    output_format: Literal["text", "json"]
    type: Literal["exact"] = "exact"


@dataclass(frozen=True)
class RegexRule:
    pattern: str
    match: safe_regex.MatchMode
    compiled: Any = field(compare=False, repr=False)
    type: Literal["regex"] = "regex"


Rule = JsonSchemaRule | FieldMatchRule | ExactRule | RegexRule


def parse_rule(raw: Any) -> Rule:
    """The rule of contract v1, or `RuleError("rule", ...)`. Pure; safe on hostile input."""
    if not isinstance(raw, dict):
        raise RuleError("rule", "rule must be a JSON object")
    if "version" not in raw:
        raise RuleError("rule", f"rule.version is required ({RULE_CONTRACT}: version 1)")
    version = raw["version"]
    if type(version) is not int or version != RULE_VERSION:
        raise RuleError("rule", "rule.version is not supported (supported: 1)")
    kind = raw.get("type")
    if not isinstance(kind, str) or kind not in RULE_TYPES:
        raise RuleError("rule", f"rule.type must be one of {', '.join(RULE_TYPES)}")
    allowed = _KEYS[kind]
    if set(raw) - allowed:
        raise RuleError("rule", f"rule has fields that a {kind} rule does not take")
    if set(raw) != allowed:
        raise RuleError("rule", f"a {kind} rule needs: {', '.join(sorted(allowed))}")

    if kind == "json_schema":
        schema = raw["schema"]
        try:
            safe_schema.verify_schema(schema)
        except safe_schema.SchemaRefused as exc:
            raise RuleError("rule", f"rule.schema: {exc}") from None
        return JsonSchemaRule(
            schema, safe_schema.validator_for(schema), safe_schema.declared_names(schema)
        )
    if kind == "field_match":
        fields = raw["fields"]
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
        return FieldMatchRule(tuple(fields))
    if kind == "exact":
        fmt = raw["output_format"]
        if not isinstance(fmt, str) or fmt not in OUTPUT_FORMATS:
            raise RuleError("rule", "rule.output_format must be text or json")
        return ExactRule(fmt)  # type: ignore[arg-type]
    match = raw["match"]
    if not isinstance(match, str) or match not in safe_regex.MATCH_MODES:
        raise RuleError("rule", "rule.match must be full or search")
    try:
        compiled = safe_regex.compile_pattern(raw["pattern"])
    except safe_regex.RegexRefused as exc:
        raise RuleError("rule", f"rule.pattern: {exc}") from None
    return RegexRule(raw["pattern"], match, compiled)  # type: ignore[arg-type]


def check_expected(rule: Rule, expected: Any) -> None:
    """`expected` (not None) satisfies its own rule, or `RuleError("expected_output", ...)`."""
    if isinstance(rule, JsonSchemaRule):
        try:
            errors = safe_schema.schema_errors(rule.validator, expected)
        except (safe_schema.BudgetExceeded, RecursionError, safe_schema.SchemaRefused):
            raise RuleError(
                "expected_output", "expected_output is too complex to check against the schema"
            ) from None
        if errors:
            raise RuleError("expected_output", "expected_output does not satisfy rule.schema")
    elif isinstance(rule, FieldMatchRule):
        if not isinstance(expected, dict):
            raise RuleError("expected_output", "a field_match rule needs an object")
        if not all(json_values.get_path(expected, f)[0] for f in rule.fields):
            raise RuleError("expected_output", "expected_output must contain every rule field")
    elif isinstance(rule, ExactRule):
        if rule.output_format == "text":
            if not isinstance(expected, str):
                raise RuleError("expected_output", "an exact text rule needs a JSON string")
            if len(expected.encode("utf-8")) > MAX_OUTPUT_BYTES:
                raise RuleError("expected_output", "expected_output is longer than an output")
    elif isinstance(rule, RegexRule):
        if not isinstance(expected, str):
            raise RuleError("expected_output", "a regex rule needs a JSON string")
        if len(expected.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise RuleError("expected_output", "expected_output is longer than an output")
        if not safe_regex.matches(rule.compiled, expected, rule.match):
            raise RuleError("expected_output", "expected_output does not match rule.pattern")


def validate_for_storage(raw_rule: Any, expected: Any) -> Rule:
    """Write-time check of a case: the rule is v1, and an expected output (when given)
    satisfies it."""
    rule = parse_rule(raw_rule)
    if expected is not None:
        check_expected(rule, expected)
    return rule
