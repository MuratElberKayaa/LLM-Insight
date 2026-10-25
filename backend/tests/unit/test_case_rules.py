"""Phase 4B `rule` / `expected_output` validation (decision D1). Nothing is evaluated here."""

import pytest

from app.services.case_rules import MAX_JSON_BYTES, RuleError, check_json_size, validate_rule

SCHEMA = {"type": "object", "required": ["summary"], "properties": {"summary": {"type": "string"}}}


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        ({"type": "json_schema", "schema": SCHEMA}, None),
        ({"type": "json_schema", "schema": True}, None),
        ({"type": "json_schema", "schema": {"$ref": "#/$defs/x", "$defs": {"x": {}}}}, None),
        ({"type": "field_match", "fields": ["summary", "meta.lang"]}, None),
        (
            {"type": "field_match", "fields": ["summary", "meta.lang"]},
            {"summary": "s", "meta": {"lang": "en"}},
        ),
        ({"type": "exact"}, {"summary": "s"}),
        ({"type": "exact"}, None),  # stored; reported "not evaluated" later (T40)
        ({"type": "regex", "pattern": r"^\{.*\}$"}, None),
    ],
)
def test_valid_rules_are_returned_unchanged(rule, expected):
    assert validate_rule(rule, expected) == rule


@pytest.mark.parametrize(
    ("rule", "expected", "field"),
    [
        ("exact", None, "rule"),
        ({}, None, "rule"),
        ({"type": "llm_judge"}, None, "rule"),
        ({"type": "exact", "threshold": 0.8}, None, "rule"),
        ({"type": "json_schema"}, None, "rule"),
        ({"type": "json_schema", "schema": "object"}, None, "rule"),
        (
            {"type": "json_schema", "schema": {"$ref": "https://example.invalid/s.json"}},
            None,
            "rule",
        ),
        ({"type": "json_schema", "schema": {"a": {"$dynamicRef": "file:///etc/x"}}}, None, "rule"),
        ({"type": "field_match", "fields": []}, None, "rule"),
        ({"type": "field_match", "fields": ["a", "a"]}, None, "rule"),
        ({"type": "field_match", "fields": ["a b"]}, None, "rule"),
        ({"type": "field_match", "fields": ["a..b"]}, None, "rule"),
        ({"type": "field_match", "fields": [1]}, None, "rule"),
        ({"type": "field_match", "fields": ["summary"]}, {"other": 1}, "expected_output"),
        ({"type": "field_match", "fields": ["meta.lang"]}, {"meta": "en"}, "expected_output"),
        ({"type": "regex", "pattern": "("}, None, "rule"),
        ({"type": "regex", "pattern": ""}, None, "rule"),
        ({"type": "regex", "pattern": "a" * 1001}, None, "rule"),
        ({"type": "regex"}, None, "rule"),
    ],
)
def test_invalid_rules_are_refused_with_the_field_name(rule, expected, field):
    with pytest.raises(RuleError) as info:
        validate_rule(rule, expected)
    assert info.value.field == field
    assert "SYNTHETIC" not in info.value.message


def test_size_and_depth_bounds():
    deep: list = []
    node = deep
    for _ in range(40):
        node.append([])
        node = node[0]
    with pytest.raises(RuleError, match="nested too deeply"):
        check_json_size("expected_output", deep)
    with pytest.raises(RuleError, match="at most"):
        check_json_size("expected_output", {"a": "x" * MAX_JSON_BYTES})
    check_json_size("expected_output", {"a": "x" * 100})
