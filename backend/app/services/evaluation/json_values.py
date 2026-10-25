"""JSON value semantics of rule contract v1 (Phase 4C).

Values are those produced by `app.core.exact_json.loads`: `None`, `bool`, `int`, `Decimal`,
`str`, `list`, `dict`. Equality is structural and exact:

- object key order never matters; array order always does;
- `true`/`false` are never equal to a number (Python's `True == 1` is not used);
- numbers compare by exact mathematical value: `1`, `1.0` and `1E0` are equal, `0.1` and
  `0.10000000000000001` are not (no binary floats anywhere);
- strings compare code point by code point: no trimming, case folding or Unicode
  normalization;
- `null` equals only `null`.
"""

from decimal import Context, Decimal
from typing import Any

MAX_DEPTH = 32  # nesting of arrays/objects in an evaluated output
MAX_NODES = 10_000  # values (scalars and containers) in an evaluated output

# Exact normalization: literals have at most 64 digits (exact_json.MAX_NUMBER_CHARS).
_NORMALIZE = Context(prec=200)


def is_number(value: Any) -> bool:
    return (type(value) is int) or isinstance(value, Decimal)


def type_name(value: Any) -> str:
    """The JSON type, for messages: never the value itself."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if is_number(value):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    raise TypeError("not a JSON value")


def equal(a: Any, b: Any) -> bool:
    """Exact structural equality (module docstring). Depth is bounded by `MAX_DEPTH`."""
    stack = [(a, b)]
    while stack:
        x, y = stack.pop()
        tx, ty = type_name(x), type_name(y)
        if tx != ty:
            return False
        if tx == "number":
            if Decimal(x) != Decimal(y):  # exact: int -> Decimal is lossless
                return False
        elif tx in ("array",):
            if len(x) != len(y):
                return False
            stack.extend(zip(x, y, strict=True))
        elif tx == "object":
            if x.keys() != y.keys():
                return False
            stack.extend((x[k], y[k]) for k in x)
        elif x != y:  # null, boolean, string
            return False
    return True


def within_bounds(value: Any) -> bool:
    """At most `MAX_DEPTH` levels and `MAX_NODES` values."""
    nodes, stack = 0, [(value, 1)]
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES:
            return False
        if isinstance(item, dict | list):
            if depth > MAX_DEPTH:
                return False
            children = item.values() if isinstance(item, dict) else item
            stack.extend((child, depth + 1) for child in children)
    return True


def canonical(value: Any) -> str:
    """A string that is equal for two values exactly when `equal` holds (used to find
    duplicates in O(n log n) instead of comparing every pair)."""
    tn = type_name(value)
    if tn == "null":
        return "n"
    if tn == "boolean":
        return "t" if value else "f"
    if tn == "number":
        d = Decimal(value)
        return "0" if d.is_zero() else "#" + str(_NORMALIZE.normalize(d))
    if tn == "string":
        return "s" + str(len(value)) + ":" + value
    if tn == "array":
        return "[" + ",".join(canonical(v) for v in value) + "]"
    items = sorted((k, canonical(v)) for k, v in value.items())
    return "{" + ",".join(f"{len(k)}:{k}={v}" for k, v in items) + "}"


def get_path(document: Any, path: str) -> tuple[bool, Any]:
    """(found, value) for a dotted path of object keys, as `field_match` defines it."""
    node = document
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node
