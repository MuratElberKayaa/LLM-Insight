"""Deterministic evaluation of one output against one test case (rule contract v1, Phase 4C).

`evaluate(rule, expected, output)` is pure: no I/O, no clock, no randomness, no model. The same
inputs always give the same `Outcome`. It never raises for hostile input; every situation has
an explicit outcome:

- `passed = True` (`pass`): the output satisfies the rule;
- `passed = False`: the output was checked and does not satisfy the rule:
  `schema_violation`, `field_mismatch`, `exact_mismatch`, `regex_no_match`,
  `output_not_json`, `output_not_object`, `output_invalid_text`;
- `passed = None` ("not evaluated"): the case is not evaluable or a limit was hit:
  `rule_unsupported`, `expected_output_missing` (T40), `expected_output_invalid`,
  `output_too_large`, `output_limits_exceeded`, `evaluation_limit_exceeded`.

`None` is never a failure and never enters a pass-rate denominator (T40). Malformed output
(not JSON where JSON is required, not an object where fields are matched, text that is not
valid Unicode) is a failure: the output is wrong, not unknown.

`detail` (stored as `experiment_results.evaluation_detail`, at most 1000 characters) is
`"<code>: <fixed sentence>"` plus counts, lengths, JSON types, keyword names and paths. It
never contains a value from the output or the expected output. A path segment that is an
object key is shown only when the rule or the approved expected output declares that name;
any other key is shown as `*`, because it came from the output.
"""

from dataclasses import dataclass
from typing import Any

from app.core import exact_json
from app.services.evaluation import json_values, rules, safe_regex, safe_schema

MAX_DETAIL_CHARS = 1000
_SAFE_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")


@dataclass(frozen=True)
class Outcome:
    passed: bool | None
    code: str
    detail: str | None  # None exactly when passed is True

    @staticmethod
    def ok() -> "Outcome":
        return Outcome(True, "pass", None)

    @staticmethod
    def failed(code: str, text: str) -> "Outcome":
        return Outcome(False, code, _detail(code, text))

    @staticmethod
    def not_evaluated(code: str, text: str) -> "Outcome":
        return Outcome(None, code, _detail(code, text))


def _detail(code: str, text: str) -> str:
    out = f"{code}: {text}"
    return out if len(out) <= MAX_DETAIL_CHARS else out[: MAX_DETAIL_CHARS - 3] + "..."


def _segment(part: str | int, names: frozenset[str]) -> str:
    if isinstance(part, int):
        return str(part)
    if part in names and 1 <= len(part) <= 64 and set(part) <= _SAFE_NAME_CHARS:
        return part
    return "*"


def _path(parts: list[str | int], names: frozenset[str]) -> str:
    return "/" + "/".join(_segment(p, names) for p in parts) if parts else "(root)"


def _object_keys(value: Any) -> frozenset[str]:
    names: set[str] = set()
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            names.update(item)
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return frozenset(names)


# ---------------------------------------------------------------- output parsing
def _parse_output_json(output: str) -> tuple[Any, Outcome | None]:
    try:
        value = exact_json.loads(output)
    except exact_json.ExactJSONLimit:
        return None, Outcome.not_evaluated(
            "output_limits_exceeded", "the output has a number beyond the supported size"
        )
    except exact_json.ExactJSONError as exc:
        return None, Outcome.failed("output_not_json", f"the output is not valid JSON ({exc})")
    if not json_values.within_bounds(value):
        return None, Outcome.not_evaluated(
            "output_limits_exceeded",
            f"the output JSON exceeds {json_values.MAX_DEPTH} levels or"
            f" {json_values.MAX_NODES} values",
        )
    return value, None


# ---------------------------------------------------------------- per rule
def _json_schema(rule: rules.JsonSchemaRule, value: Any) -> Outcome:
    try:
        errors = safe_schema.schema_errors(rule.validator, value)
    except (safe_schema.BudgetExceeded, RecursionError):
        return Outcome.not_evaluated(
            "evaluation_limit_exceeded",
            f"checking the output needed more than {safe_schema.KEYWORD_BUDGET} schema steps",
        )
    except safe_schema.SchemaRefused:
        return Outcome.not_evaluated("rule_unsupported", "the stored schema cannot be used")
    if not errors:
        return Outcome.ok()
    more = "+" if len(errors) > safe_schema.MAX_REPORTED_ERRORS else ""
    shown = errors[: safe_schema.MAX_REPORTED_ERRORS]
    first = "; ".join(
        f"{keyword if keyword != 'None' else 'false_schema'} at {_path(path, rule.names)}"
        for keyword, path in shown[:3]
    )
    return Outcome.failed(
        "schema_violation",
        f"the output does not satisfy the schema: {len(shown)}{more} error(s); {first}",
    )


def _difference(expected: Any, actual: Any) -> tuple[list[str | int], str] | None:
    """The first difference (path, what differs) in a deterministic walk, or None."""
    stack: list[tuple[Any, Any, list[str | int]]] = [(expected, actual, [])]
    while stack:
        e, a, path = stack.pop()
        te, ta = json_values.type_name(e), json_values.type_name(a)
        if te != ta:
            return path, f"expected {te}, got {ta}"
        if te == "object":
            for key in sorted(e):
                if key not in a:
                    return [*path, key], "missing in the output"
            if len(a) > len(e):
                return path, f"{len(a) - len(e)} unexpected key(s) in the output"
            stack.extend((e[k], a[k], [*path, k]) for k in sorted(e, reverse=True))
        elif te == "array":
            if len(e) != len(a):
                return path, f"expected {len(e)} items, got {len(a)}"
            stack.extend((e[i], a[i], [*path, i]) for i in reversed(range(len(e))))
        elif not json_values.equal(e, a):
            return path, f"the {te} values differ"
    return None


def _field_match(rule: rules.FieldMatchRule, expected: Any, value: Any) -> Outcome:
    if not isinstance(value, dict):
        return Outcome.failed(
            "output_not_object",
            f"field_match needs a JSON object, got {json_values.type_name(value)}",
        )
    names = frozenset(part for f in rule.fields for part in f.split("."))
    differing: list[str] = []
    for path in rule.fields:
        _, want = json_values.get_path(expected, path)
        found, got = json_values.get_path(value, path)
        if not found:
            differing.append(f"{path} (missing in the output)")
        elif not json_values.equal(want, got):
            sub, what = _difference(want, got) or ([], "the values differ")
            label = path + (_path(sub, names) if sub else "")
            differing.append(f"{label} ({what})")
    if not differing:
        return Outcome.ok()
    return Outcome.failed(
        "field_mismatch",
        f"{len(differing)} of {len(rule.fields)} field(s) differ; " + "; ".join(differing[:5]),
    )


def _exact(rule: rules.ExactRule, expected: Any, output: str) -> Outcome:
    if rule.output_format == "text":
        if output == expected:
            return Outcome.ok()
        first = next(
            (i for i, (x, y) in enumerate(zip(expected, output, strict=False)) if x != y),
            min(len(expected), len(output)),
        )
        return Outcome.failed(
            "exact_mismatch",
            f"the output text differs from the expected text (expected {len(expected)}"
            f" characters, got {len(output)}; first difference at character {first})",
        )
    value, problem = _parse_output_json(output)
    if problem is not None:
        return problem
    where = _difference(expected, value)
    if where is None:
        return Outcome.ok()
    path, what = where
    return Outcome.failed(
        "exact_mismatch",
        f"the output JSON differs from the expected JSON at"
        f" {_path(path, _object_keys(expected))} ({what})",
    )


def _regex(rule: rules.RegexRule, output: str) -> Outcome:
    if safe_regex.matches(rule.compiled, output, rule.match):
        return Outcome.ok()
    mode = "the whole output" if rule.match == "full" else "anywhere in the output"
    return Outcome.failed("regex_no_match", f"the pattern does not match {mode}")


# ---------------------------------------------------------------- entry point
def evaluate(rule_raw: Any, expected: Any, output: Any) -> Outcome:
    """`rule_raw` and `expected` as stored (exact JSON; `expected` None = missing), `output`
    the raw output text."""
    try:
        rule = rules.parse_rule(rule_raw)
    except rules.RuleError:
        return Outcome.not_evaluated(
            "rule_unsupported", f"the stored rule is not a valid {rules.RULE_CONTRACT} rule"
        )
    if expected is None:
        return Outcome.not_evaluated(
            "expected_output_missing", "the case has no approved expected output"
        )
    try:
        rules.check_expected(rule, expected)
    except rules.RuleError:
        return Outcome.not_evaluated(
            "expected_output_invalid", "the stored expected output does not satisfy its rule"
        )
    if not isinstance(output, str):
        return Outcome.failed("output_invalid_text", "the output is not text")
    try:
        size = len(output.encode("utf-8"))
    except UnicodeEncodeError:
        return Outcome.failed("output_invalid_text", "the output is not valid Unicode text")
    if size > rules.MAX_OUTPUT_BYTES:
        return Outcome.not_evaluated(
            "output_too_large",
            f"the output has {size} bytes; at most {rules.MAX_OUTPUT_BYTES} are evaluated",
        )

    if isinstance(rule, rules.RegexRule):
        return _regex(rule, output)
    if isinstance(rule, rules.ExactRule):
        return _exact(rule, expected, output)
    value, problem = _parse_output_json(output)
    if problem is not None:
        return problem
    if isinstance(rule, rules.FieldMatchRule):
        return _field_match(rule, expected, value)
    return _json_schema(rule, value)
