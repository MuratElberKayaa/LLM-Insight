"""Exact JSON for human-entered assertions and evaluated outputs (Phase 4C, rule contract v1).

Python's `json` turns every non-integer number into a binary float, so `0.1000000000000000001`
would be stored, compared and returned as `0.1`. Test-case assertions (`expected_output`,
`rule`) and model outputs are therefore parsed here instead:

- integers stay `int`, every other number becomes an exact `Decimal` (never a float);
- `NaN`, `Infinity` and `-Infinity` are refused (not JSON, RFC 8259 §6);
- duplicate object keys are refused (ambiguous: which value counts?);
- a number literal longer than `MAX_NUMBER_CHARS` or with an exponent beyond
  `MAX_ABS_EXPONENT` is refused, so later exact arithmetic (`multipleOf`, comparisons)
  stays cheap.

`dumps` writes such values back without loss. PostgreSQL `jsonb` stores numbers as `numeric`
(exact), so a value written with `dumps` and read back as `jsonb::text` round-trips exactly.
"""

import json
from decimal import Decimal, InvalidOperation
from typing import Any

MAX_NUMBER_CHARS = 64
MAX_ABS_EXPONENT = 400


class ExactJSONError(ValueError):
    """The text is not acceptable JSON. The message names the rule only, never a value."""


class ExactJSONLimit(ExactJSONError):
    """Valid JSON syntax, but a number beyond the supported size (a resource limit, not a
    syntax error: callers may report it differently)."""


def _number(text: str) -> Decimal:
    if len(text) > MAX_NUMBER_CHARS:
        raise ExactJSONLimit("a number literal is too long")
    try:
        value = Decimal(text)
    except InvalidOperation:  # pragma: no cover - the json scanner only passes numbers
        raise ExactJSONError("not a number") from None
    if value.is_zero():
        return value
    if abs(value.adjusted()) > MAX_ABS_EXPONENT:
        raise ExactJSONLimit("a number is out of the supported range")
    return value


def _integer(text: str) -> int:
    if len(text) > MAX_NUMBER_CHARS:
        raise ExactJSONLimit("a number literal is too long")
    return int(text)


def _constant(_name: str) -> Any:
    raise ExactJSONError("NaN and Infinity are not JSON")


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ExactJSONError("an object has a duplicate key")
        out[key] = value
    return out


def _has_lone_surrogate(s: str) -> bool:
    return any("\ud800" <= ch <= "\udfff" for ch in s)


def _check_strings(value: Any) -> None:
    """`"\\ud800"` is valid JSON syntax but no Unicode text: it cannot be encoded as UTF-8
    (or stored in `jsonb`), so it is refused like any other malformed input."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if _has_lone_surrogate(item):
                raise ExactJSONError("a string contains an unpaired surrogate")
        elif isinstance(item, dict):
            for key, element in item.items():
                if _has_lone_surrogate(key):
                    raise ExactJSONError("a string contains an unpaired surrogate")
                stack.append(element)
        elif isinstance(item, list):
            stack.extend(item)


def loads(text: str) -> Any:
    """Strict, exact JSON. Raises `ExactJSONError` (a `ValueError`) on any refusal."""
    try:
        value = json.loads(
            text,
            parse_float=_number,
            parse_int=_integer,
            parse_constant=_constant,
            object_pairs_hook=_unique,
        )
    except ExactJSONError:
        raise
    except (ValueError, RecursionError):
        raise ExactJSONError("not valid JSON") from None
    _check_strings(value)
    return value


def _encode_number(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("non-finite numbers are not JSON")
    return str(value)  # "1E+2", "-0", "0.10": all valid JSON number syntax


def dumps(value: Any) -> str:
    """Compact JSON text; `Decimal` is written with its exact digits. Floats are refused:
    a value that reaches here as a float has already lost precision."""
    parts: list[str] = []
    stack: list[Any] = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, _Raw):
            parts.append(item.text)
        elif item is None:
            parts.append("null")
        elif item is True:
            parts.append("true")
        elif item is False:
            parts.append("false")
        elif type(item) is int:
            parts.append(str(item))
        elif isinstance(item, Decimal):
            parts.append(_encode_number(item))
        elif isinstance(item, str):
            parts.append(json.dumps(item, ensure_ascii=False))
        elif isinstance(item, list | tuple):
            seq: list[Any] = [_Raw("[")]
            for i, element in enumerate(item):
                if i:
                    seq.append(_Raw(","))
                seq.append(element)
            seq.append(_Raw("]"))
            stack.extend(reversed(seq))
        elif isinstance(item, dict):
            seq = [_Raw("{")]
            for i, (key, element) in enumerate(item.items()):
                if not isinstance(key, str):
                    raise TypeError("JSON object keys must be strings")
                if i:
                    seq.append(_Raw(","))
                seq.append(_Raw(json.dumps(key, ensure_ascii=False) + ":"))
                seq.append(element)
            seq.append(_Raw("}"))
            stack.extend(reversed(seq))
        else:
            raise TypeError(f"not an exact JSON value: {type(item).__name__}")
    return "".join(parts)


class _Raw:
    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text
