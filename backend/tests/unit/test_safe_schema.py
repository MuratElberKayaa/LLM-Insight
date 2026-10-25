"""JSON Schema safety (Phase 4C, A3): dialect, keyword allowlist at every depth, local
references only, no recursion, bounded size and work, and no network access at all."""

import socket
import urllib.request
from decimal import Decimal

import pytest

from app.core import exact_json
from app.services.evaluation import safe_schema
from app.services.evaluation.engine import evaluate


@pytest.fixture
def no_network(monkeypatch):
    """Any socket, DNS lookup or urlopen fails the test: a schema must never fetch."""
    calls: list = []

    def blocked(*args, **kwargs):
        calls.append(args)
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    yield calls
    assert calls == []


def refused(schema) -> str:
    with pytest.raises(safe_schema.SchemaRefused) as info:
        safe_schema.verify_schema(schema)
    return str(info.value)


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://example.invalid/schema.json"},
        {"$ref": "http://127.0.0.1:9/x"},
        {"$ref": "file:///etc/passwd"},
        {"$ref": "other.json#/a"},
        {"$ref": "#"},
        {"$ref": "#/properties/a"},
        {"$ref": "#/$defs/missing", "$defs": {}},
        {"$ref": "#/$defs/a~1b", "$defs": {"a/b": {}}},
        {"properties": {"x": {"$ref": "https://example.invalid/nested.json"}}},
        {"allOf": [{"anyOf": [{"not": {"$dynamicRef": "#meta"}}]}]},
        {"$id": "https://example.invalid/base"},
        {"properties": {"x": {"$id": "https://example.invalid/nested"}}},
        {"$anchor": "a"},
        {"$schema": "http://json-schema.org/draft-07/schema#"},
        {"$schema": "https://example.invalid/my-dialect"},
        {"properties": {"x": {"$schema": "https://json-schema.org/draft/2020-12/schema"}}},
        {"properties": {"x": {"$defs": {"y": {}}}}},
        {"$vocabulary": {}},
    ],
)
def test_non_local_dynamic_or_rebased_references_are_refused(schema, no_network):
    refused(schema)


@pytest.mark.parametrize(
    "schema",
    [
        {"pattern": "^a+$"},
        {"patternProperties": {"^x": {}}},
        {"properties": {"a": {"properties": {"b": {"pattern": "(a+)+$"}}}}},
        {"items": {"patternProperties": {".*": {"type": "string"}}}},
        {"$defs": {"d": {"pattern": "x"}}, "$ref": "#/$defs/d"},
        {"anyOf": [{"type": "string"}, {"allOf": [{"pattern": "x"}]}]},
        {"propertyNames": {"pattern": "^[a-z]+$"}},
        {"format": "regex"},
        {"properties": {"d": {"format": "date-time"}}},
    ],
)
def test_regex_and_format_keywords_are_refused_at_any_depth(schema, monkeypatch, no_network):
    import re

    def forbidden(*args, **kwargs):
        raise AssertionError("Python re used on a schema regex")

    monkeypatch.setattr(re, "compile", forbidden)
    monkeypatch.setattr(re, "search", forbidden)
    monkeypatch.setattr(re, "match", forbidden)
    refused(schema)


def test_property_names_that_look_like_keywords_are_data_not_keywords(no_network):
    schema = {
        "type": "object",
        "properties": {"pattern": {"type": "string"}, "$ref": {"type": "number"}},
        "required": ["pattern"],
        "examples": [{"pattern": "(a+)+$", "$ref": "https://example.invalid/x"}],
    }
    safe_schema.verify_schema(schema)


@pytest.mark.parametrize(
    "schema",
    [
        {"unevaluatedProperties": False},
        {"definitions": {"a": {}}},
        {"dependencies": {"a": ["b"]}},
        {"contentMediaType": "application/json"},
        {"x-custom": 1},
        {"properties": {"a": {"minimumm": 1}}},  # typo: would be ignored silently otherwise
    ],
)
def test_keywords_outside_the_subset_are_refused(schema, no_network):
    refused(schema)


def test_recursive_and_mutually_recursive_references_are_refused(no_network):
    assert "recursive" in refused({"$defs": {"a": {"$ref": "#/$defs/a"}}, "$ref": "#/$defs/a"})
    assert "recursive" in refused(
        {
            "$defs": {"a": {"items": {"$ref": "#/$defs/b"}}, "b": {"$ref": "#/$defs/a"}},
            "$ref": "#/$defs/a",
        }
    )


def test_reference_expansion_is_bounded(no_network):
    """Each definition uses the previous one twice: 2^n subschemas once expanded."""
    defs = {"d0": {"type": "string"}}
    for i in range(1, 20):
        defs[f"d{i}"] = {"allOf": [{"$ref": f"#/$defs/d{i - 1}"}, {"$ref": f"#/$defs/d{i - 1}"}]}
    assert "expanded" in refused({"$defs": defs, "$ref": "#/$defs/d19"})


def test_size_limits(no_network):
    wide = {
        "properties": {f"p{i}": {"type": "string"} for i in range(safe_schema.MAX_SCHEMA_NODES)}
    }
    refused(wide)
    many_defs = {"$defs": {f"d{i}": {} for i in range(safe_schema.MAX_DEFS + 1)}}
    refused(many_defs)


def test_meta_validation_without_format_checking_or_network(no_network):
    for schema in ({"type": "strng"}, {"minimum": "1"}, {"required": "a"}, {"maxLength": -1}):
        assert "not a valid JSON Schema" in refused(schema)
    assert "object or a boolean" in refused({"items": 3})
    safe_schema.verify_schema({"type": ["string", "null"], "minLength": 1})


def test_a_remote_reference_that_slipped_past_the_checks_is_unresolvable_not_fetched(
    no_network,
):
    """Defence in depth: even a validator built directly (bypassing `verify_schema`) has no
    way to fetch; the reference error surfaces as a refusal, never as a request."""
    validator = safe_schema.validator_for({"$ref": "https://example.invalid/x.json"})
    with pytest.raises(safe_schema.SchemaRefused):
        safe_schema.schema_errors(validator, {})


def test_decimal_numbers_and_integer_semantics(no_network):
    v = safe_schema.validator_for(
        {"type": "integer", "multipleOf": Decimal("0.5"), "minimum": Decimal("0.1")}
    )
    assert safe_schema.schema_errors(v, Decimal("1.0")) == []  # 1.0 is an integer in 2020-12
    assert [k for k, _ in safe_schema.schema_errors(v, Decimal("1.25"))] == ["type", "multipleOf"]
    assert [k for k, _ in safe_schema.schema_errors(v, True)] == ["type"]  # bool is not a number
    exact = safe_schema.validator_for({"minimum": Decimal("0.1000000000000000000001")})
    assert [k for k, _ in safe_schema.schema_errors(exact, Decimal("0.1"))] == ["minimum"]


def test_unique_items_uses_exact_equality_in_linear_log_time(no_network):
    v = safe_schema.validator_for({"uniqueItems": True})
    assert safe_schema.schema_errors(v, [1, True, "1", Decimal("1.5")]) == []
    assert safe_schema.schema_errors(v, [1, Decimal("1.0")]) != []
    assert safe_schema.schema_errors(v, [{"a": 1, "b": 2}, {"b": 2, "a": 1}]) != []
    big = [{"k": i} for i in range(5000)]
    assert safe_schema.schema_errors(v, big) == []  # O(n log n), not 12.5 M comparisons


def test_keyword_budget_bounds_the_work(monkeypatch, no_network):
    monkeypatch.setattr(safe_schema, "KEYWORD_BUDGET", 50)
    v = safe_schema.validator_for({"items": {"type": "integer", "minimum": 0}})
    with pytest.raises(safe_schema.BudgetExceeded):
        safe_schema.schema_errors(v, list(range(100)))
    rule = {"version": 1, "type": "json_schema", "schema": {"items": {"minimum": 0}}}
    outcome = evaluate(rule, [1], exact_json.dumps(list(range(100))))
    assert outcome.passed is None and outcome.code == "evaluation_limit_exceeded"


def test_error_report_is_bounded_and_deterministic(no_network):
    v = safe_schema.validator_for({"items": {"type": "string"}})
    errors = safe_schema.schema_errors(v, list(range(100)))
    assert len(errors) == safe_schema.MAX_REPORTED_ERRORS + 1
    assert errors == safe_schema.schema_errors(v, list(range(100)))
