"""JSON Schemas of `json_schema` rules: one dialect, local references only, bounded work
(rule contract v1, Phase 4C).

Dialect: **JSON Schema draft 2020-12** only (`jsonschema` 4.26.0). Every schema is checked
here before it is stored and again before it is evaluated:

1. **Keyword allowlist** (`KEYWORDS`), applied to every nested schema, not only the root.
   A keyword outside it is refused, because an unknown keyword would otherwise be ignored
   silently. Refused on purpose (`REFUSED`):
   - `pattern`, `patternProperties`: their regexes would run on Python's backtracking
     `re` engine inside `jsonschema` (ReDoS); there is no bounded implementation yet;
   - `format`, `content*`: annotations in 2020-12, never asserted here, so a schema relying
     on them would pass outputs it seems to reject;
   - `$id`, `$anchor`, `$dynamicRef`, `$dynamicAnchor`, `$vocabulary` and the draft-2019
     `$recursive*`: they change the base URI or resolve dynamically;
   - `unevaluated*`, legacy `definitions`/`dependencies`: outside the supported subset.
2. **References**: `$ref` must be `#/$defs/<name>` for a name defined in the root `$defs`
   (`$defs` only at the root). No remote, relative, `#` or JSON-pointer-into-anything-else
   reference. The reference graph must be acyclic (no recursive schema), and the schema
   with every reference expanded must stay under `MAX_EXPANDED_NODES` subschemas.
3. **`$schema`**: absent, or exactly the 2020-12 URI, and only at the root.
4. **Meta-validation** against the bundled 2020-12 meta-schema, with **no format checker**
   (its `regex` format would call `re.compile`) and a registry that holds only the bundled
   meta-schemas and **cannot retrieve** anything. `jsonschema.check_schema` is never used.
5. **Evaluation** with the same no-retrieval registry, no format checker, exact `Decimal`
   numbers (`integer` includes `1.0`; booleans are never numbers), `multipleOf` and
   `uniqueItems` reimplemented exactly (fractions; canonical forms instead of an O(n²)
   pairwise scan), and a budget of `KEYWORD_BUDGET` keyword applications per evaluation.

No remote document is ever fetched: `jsonschema`'s default registry would call `urlopen` for
an unknown URI, which is why a registry is always passed explicitly (tested with sockets and
`urlopen` blocked).
"""

import re
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from fractions import Fraction
from typing import Any

import referencing
import referencing.exceptions
from jsonschema import Draft202012Validator, ValidationError
from jsonschema import validators as jsonschema_validators
from jsonschema_specifications import REGISTRY as BUNDLED_METASCHEMAS

from app.services.evaluation import json_values

DIALECT = "https://json-schema.org/draft/2020-12/schema"
MAX_SCHEMA_NODES = 1000  # schemas (objects or booleans) as written
MAX_EXPANDED_NODES = 5000  # with every $ref expanded (the work done per instance value)
MAX_DEFS = 100
KEYWORD_BUDGET = 200_000  # keyword applications per evaluation
MAX_REPORTED_ERRORS = 20

_DEF_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_LOCAL_REF = re.compile(r"#/\$defs/([A-Za-z0-9_.-]{1,64})")

# Keywords whose value is one schema / a list of schemas / a map of name -> schema.
_ONE = frozenset(
    {"not", "if", "then", "else", "items", "contains", "additionalProperties", "propertyNames"}
)
_LIST = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
_MAP = frozenset({"properties", "dependentSchemas"})
_ASSERTIONS = frozenset(
    {
        "type",
        "enum",
        "const",
        "multipleOf",
        "maximum",
        "exclusiveMaximum",
        "minimum",
        "exclusiveMinimum",
        "maxLength",
        "minLength",
        "maxItems",
        "minItems",
        "uniqueItems",
        "maxContains",
        "minContains",
        "maxProperties",
        "minProperties",
        "required",
        "dependentRequired",
    }
)
# Annotations: no effect on the outcome. `default`/`examples` hold data, never schemas.
_ANNOTATIONS = frozenset(
    {"title", "description", "default", "examples", "deprecated", "readOnly", "writeOnly"}
)
_CORE = frozenset({"$schema", "$ref", "$defs", "$comment"})
KEYWORDS = _ONE | _LIST | _MAP | _ASSERTIONS | _ANNOTATIONS | _CORE

REFUSED: dict[str, str] = {
    "pattern": "regex keywords are not supported (unbounded regex engine)",
    "patternProperties": "regex keywords are not supported (unbounded regex engine)",
    "format": "format is not asserted, so it is refused rather than silently ignored",
    "contentEncoding": "content keywords are not asserted",
    "contentMediaType": "content keywords are not asserted",
    "contentSchema": "content keywords are not asserted",
    "$id": "$id would change the base URI; only local references are supported",
    "$anchor": "anchors are not supported; use #/$defs/<name>",
    "$dynamicRef": "dynamic references are not supported",
    "$dynamicAnchor": "dynamic references are not supported",
    "$recursiveRef": "dynamic references are not supported",
    "$recursiveAnchor": "dynamic references are not supported",
    "$vocabulary": "custom vocabularies are not supported",
    "definitions": "use $defs (draft 2020-12)",
    "dependencies": "use dependentRequired / dependentSchemas (draft 2020-12)",
    "unevaluatedProperties": "unevaluated keywords are not supported",
    "unevaluatedItems": "unevaluated keywords are not supported",
}


class SchemaRefused(ValueError):
    """The schema is not accepted. The message never quotes user text: it names a rule, or
    a keyword from the constant tables above."""


class BudgetExceeded(Exception):
    """An evaluation needed more than `KEYWORD_BUDGET` keyword applications."""


# ---------------------------------------------------------------- 1-3: structure
def _children(node: dict[str, Any], root: bool) -> Iterator[Any]:
    for key, value in node.items():
        if key not in KEYWORDS:
            if key in REFUSED:
                raise SchemaRefused(f"{key}: {REFUSED[key]}")
            raise SchemaRefused("the schema uses a keyword outside the supported subset")
        if key == "$schema":
            if not root or value != DIALECT:
                raise SchemaRefused(f"$schema must be absent or {DIALECT}, and only at the root")
        elif key == "$defs":
            if not root:
                raise SchemaRefused("$defs is accepted only at the root")
            # handled by `check_structure`
        elif key == "$ref":
            # validated against the root's $defs by `check_structure`
            pass
        elif key in _ONE:
            yield value
        elif key in _LIST:
            if not isinstance(value, list):
                raise SchemaRefused(f"{key} must be a list of schemas")
            yield from value
        elif key in _MAP:
            if not isinstance(value, dict):
                raise SchemaRefused(f"{key} must map names to schemas")
            yield from value.values()


def _walk(start: Any, root_node: bool, def_names: frozenset[str]) -> tuple[int, list[str]]:
    """(schema count, referenced def names) of one schema, not entering the root's $defs."""
    count, refs = 0, []
    stack: list[tuple[Any, bool]] = [(start, root_node)]
    while stack:
        node, is_root = stack.pop()
        count += 1
        if count > MAX_SCHEMA_NODES:
            raise SchemaRefused(f"the schema has more than {MAX_SCHEMA_NODES} subschemas")
        if isinstance(node, bool):
            continue
        if not isinstance(node, dict):
            raise SchemaRefused("every subschema must be an object or a boolean")
        if "$ref" in node:
            ref = node["$ref"]
            m = _LOCAL_REF.fullmatch(ref) if isinstance(ref, str) else None
            if m is None:
                raise SchemaRefused("$ref must be a local reference of the form #/$defs/<name>")
            if m.group(1) not in def_names:
                raise SchemaRefused("$ref names a definition that does not exist")
            refs.append(m.group(1))
        stack.extend((child, False) for child in _children(node, is_root))
    return count, refs


def check_structure(schema: Any) -> None:
    """Steps 1-3 of the module docstring. Raises `SchemaRefused`."""
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        raise SchemaRefused("the schema must be an object or a boolean")
    defs = schema.get("$defs", {})
    if not isinstance(defs, dict):
        raise SchemaRefused("$defs must map names to schemas")
    if len(defs) > MAX_DEFS:
        raise SchemaRefused(f"at most {MAX_DEFS} definitions")
    if not all(_DEF_NAME.fullmatch(name) for name in defs):
        raise SchemaRefused("definition names must be 1-64 letters, digits, '_', '.' or '-'")
    names = frozenset(defs)
    own: dict[str | None, int] = {}
    edges: dict[str | None, list[str]] = {}
    own[None], edges[None] = _walk(schema, True, names)
    total = own[None]
    for name, sub in defs.items():
        own[name], edges[name] = _walk(sub, False, names)
        total += own[name]
        if total > MAX_SCHEMA_NODES:
            raise SchemaRefused(f"the schema has more than {MAX_SCHEMA_NODES} subschemas")

    # Acyclic reference graph, and the expanded size (each $ref counts its target's size).
    expanded: dict[str | None, int] = {}
    visiting: set[str | None] = set()

    def size(node: str | None) -> int:
        if node in expanded:
            return expanded[node]
        if node in visiting:
            raise SchemaRefused("recursive references are not supported")
        visiting.add(node)
        n = own[node]
        for target in edges[node]:
            n += size(target)
            if n > MAX_EXPANDED_NODES:
                raise SchemaRefused("the schema is too large once its references are expanded")
        visiting.discard(node)
        expanded[node] = n
        return n

    size(None)  # depth <= MAX_DEFS + 1, well below the recursion limit


def declared_names(schema: Any) -> frozenset[str]:
    """Property names a schema declares (`properties`, `required`, `dependentRequired`).
    Only these may appear in an evaluation detail: other keys come from the evaluated
    output and could carry its content."""
    names: set[str] = set()
    stack = [schema]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("properties", "dependentSchemas") and isinstance(value, dict):
                    names.update(value)
                    stack.extend(value.values())
                elif key == "required" and isinstance(value, list):
                    names.update(v for v in value if isinstance(v, str))
                elif key == "dependentRequired" and isinstance(value, dict):
                    names.update(value)
                    for v in value.values():
                        if isinstance(v, list):
                            names.update(x for x in v if isinstance(x, str))
                elif key == "$defs" and isinstance(value, dict):
                    stack.extend(value.values())
                elif key in _ONE:
                    stack.append(value)
                elif key in _LIST and isinstance(value, list):
                    stack.extend(value)
    return frozenset(names)


# ---------------------------------------------------------------- 4: meta-validation
# Only the bundled meta-schemas, and `Registry`'s default `retrieve`, which raises instead
# of fetching: an unknown URI can never become a network request.
_NO_RETRIEVAL = referencing.Registry().combine(BUNDLED_METASCHEMAS)
_META_VALIDATOR = Draft202012Validator(
    Draft202012Validator.META_SCHEMA, registry=_NO_RETRIEVAL, format_checker=None
)


def verify_schema(schema: Any) -> None:
    """Steps 1-4. Raises `SchemaRefused`."""
    check_structure(schema)
    with _budget():
        try:
            valid = _META_VALIDATOR.is_valid(schema)
        except (BudgetExceeded, RecursionError):
            raise SchemaRefused("the schema is too complex to check") from None
    if not valid:
        raise SchemaRefused("the schema is not a valid JSON Schema (draft 2020-12)")


# ---------------------------------------------------------------- 5: evaluation
_BUDGET: ContextVar[list[int] | None] = ContextVar("llminsight_schema_budget", default=None)


class _budget:  # noqa: N801 - used as a context manager
    def __enter__(self) -> None:
        self._token = _BUDGET.set([KEYWORD_BUDGET])

    def __exit__(self, *exc: object) -> None:
        _BUDGET.reset(self._token)


def _counted(fn: Callable[..., Any]) -> Callable[..., Any]:
    def keyword(validator: Any, value: Any, instance: Any, schema: Any) -> Any:
        remaining = _BUDGET.get()
        if remaining is not None:
            remaining[0] -= 1
            if remaining[0] < 0:
                raise BudgetExceeded
        return fn(validator, value, instance, schema)

    return keyword


def _is_number(_checker: Any, instance: Any) -> bool:
    return json_values.is_number(instance)


def _is_integer(_checker: Any, instance: Any) -> bool:
    if type(instance) is int:
        return True
    return json_values.is_number(instance) and instance == instance.to_integral_value()


def _multiple_of(validator: Any, divisor: Any, instance: Any, _schema: Any) -> Iterator[Any]:
    if not validator.is_type(instance, "number"):
        return
    # Exact: both are int or Decimal with bounded digits/exponent (exact_json limits).
    if (Fraction(instance) / Fraction(divisor)).denominator != 1:
        yield ValidationError("the value is not a multiple of the divisor")


def _unique_items(validator: Any, unique: Any, instance: Any, _schema: Any) -> Iterator[Any]:
    if not unique or not validator.is_type(instance, "array"):
        return
    keys = sorted(json_values.canonical(v) for v in instance)
    if any(a == b for a, b in zip(keys, keys[1:], strict=False)):
        yield ValidationError("the array has duplicate items")


_KEYWORD_FUNCTIONS = {
    **Draft202012Validator.VALIDATORS,
    "multipleOf": _multiple_of,
    "uniqueItems": _unique_items,
}
_ExactValidator = jsonschema_validators.extend(
    Draft202012Validator,
    validators={k: _counted(fn) for k, fn in _KEYWORD_FUNCTIONS.items()},
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine_many(
        {"number": _is_number, "integer": _is_integer}
    ),
)


def validator_for(schema: Any) -> Any:
    """An instance validator for a schema that passed `verify_schema`."""
    return _ExactValidator(schema, registry=_NO_RETRIEVAL, format_checker=None)


def schema_errors(validator: Any, instance: Any) -> list[tuple[str, list[str | int]]]:
    """Up to `MAX_REPORTED_ERRORS + 1` (keyword, instance path) pairs, in the validator's
    deterministic order; empty when the instance is valid. Error *messages* are never used:
    they quote the instance. Raises `BudgetExceeded` (or `RecursionError`), or
    `SchemaRefused` for a schema that did not pass `verify_schema`."""
    out: list[tuple[str, list[str | int]]] = []
    with _budget():
        try:
            for error in validator.iter_errors(instance):
                out.append((str(error.validator), list(error.absolute_path)))
                if len(out) > MAX_REPORTED_ERRORS:
                    break
        except referencing.exceptions.Unresolvable:
            # Unreachable for a schema that passed `check_structure` (local, existing
            # references only); kept so that no reference error can escape as a crash.
            raise SchemaRefused("the schema has an unresolvable reference") from None
    return out
