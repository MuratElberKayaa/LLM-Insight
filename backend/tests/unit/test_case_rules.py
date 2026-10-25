"""Write-side `rule` / `expected_output` validation: contract `llminsight.rule.v1` (Phase 4C,
D1 as revised by the architectural review). The Phase 4B unversioned payload is refused."""

from decimal import Decimal

import pytest

from app.services.case_rules import MAX_JSON_BYTES, RuleError, check_json_size, validate_rule

SCHEMA = {"type": "object", "required": ["summary"], "properties": {"summary": {"type": "string"}}}


def v1(kind: str, **params):
    return {"version": 1, "type": kind, **params}


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        (v1("json_schema", schema=SCHEMA), None),
        (v1("json_schema", schema=SCHEMA), {"summary": "s"}),
        (v1("json_schema", schema=True), None),
        (v1("json_schema", schema={"$ref": "#/$defs/x", "$defs": {"x": {}}}), None),
        (
            v1(
                "json_schema",
                schema={"$schema": "https://json-schema.org/draft/2020-12/schema", **SCHEMA},
            ),
            {"summary": "s"},
        ),
        (v1("field_match", fields=["summary", "meta.lang"]), None),
        (
            v1("field_match", fields=["summary", "meta.lang"]),
            {"summary": "s", "meta": {"lang": "en"}},
        ),
        (v1("exact", output_format="json"), {"summary": "s"}),
        (v1("exact", output_format="json"), [1, Decimal("2.50"), None, True]),
        (v1("exact", output_format="text"), "ACK"),
        (v1("exact", output_format="text"), None),  # stored; never evaluated (T40)
        (v1("regex", pattern=r"^\{.*\}$", match="full"), None),
        (v1("regex", pattern=r"[A-Z]{3}-[0-9]{4}", match="full"), "TCK-0042"),
        (v1("regex", pattern=r"(?i)refund", match="search"), "Refund approved"),
    ],
)
def test_valid_v1_rules_are_returned_unchanged(rule, expected):
    assert validate_rule(rule, expected) == rule


@pytest.mark.parametrize(
    ("rule", "expected", "field"),
    [
        # shape and version
        ("exact", None, "rule"),
        ({}, None, "rule"),
        ({"type": "exact"}, None, "rule"),  # Phase 4B payload: unversioned
        ({"type": "json_schema", "schema": SCHEMA}, None, "rule"),
        ({"version": 2, "type": "exact", "output_format": "text"}, None, "rule"),
        ({"version": True, "type": "exact", "output_format": "text"}, None, "rule"),
        ({"version": Decimal("1.0"), "type": "exact", "output_format": "text"}, None, "rule"),
        ({"version": "1", "type": "exact", "output_format": "text"}, None, "rule"),
        (v1("llm_judge"), None, "rule"),
        (v1("exact", output_format="text", threshold=Decimal("0.8")), None, "rule"),
        (v1("exact"), None, "rule"),
        (v1("exact", output_format="xml"), None, "rule"),
        (v1("json_schema"), None, "rule"),
        (v1("json_schema", schema="object"), None, "rule"),
        (v1("field_match", fields=[]), None, "rule"),
        (v1("field_match", fields=["a", "a"]), None, "rule"),
        (v1("field_match", fields=["a b"]), None, "rule"),
        (v1("field_match", fields=["a..b"]), None, "rule"),
        (v1("field_match", fields=[1]), None, "rule"),
        (v1("regex", pattern="a"), None, "rule"),  # match mode required
        (v1("regex", pattern="a", match="contains"), None, "rule"),
        (v1("regex", pattern="(", match="full"), None, "rule"),
        (v1("regex", pattern="", match="full"), None, "rule"),
        (v1("regex", pattern="a" * 1001, match="full"), None, "rule"),
        (v1("regex", pattern=r"(a)\1", match="full"), None, "rule"),  # backreference
        (v1("regex", pattern=r"(?=a)a", match="full"), None, "rule"),  # lookahead
        (v1("regex", pattern=r"(?<!a)b", match="full"), None, "rule"),  # lookbehind
        (v1("regex", pattern=r"a++", match="full"), None, "rule"),  # possessive
        (v1("regex", pattern="a‮b", match="full"), None, "rule"),  # bidi override
        # expected output must satisfy its own rule
        (v1("json_schema", schema=SCHEMA), {"summary": 1}, "expected_output"),
        (v1("json_schema", schema=False), {"summary": "s"}, "expected_output"),
        (v1("field_match", fields=["summary"]), {"other": 1}, "expected_output"),
        (v1("field_match", fields=["meta.lang"]), {"meta": "en"}, "expected_output"),
        (v1("field_match", fields=["summary"]), ["summary"], "expected_output"),
        (v1("exact", output_format="text"), {"summary": "s"}, "expected_output"),
        (v1("regex", pattern="[0-9]+", match="full"), "abc", "expected_output"),
        (v1("regex", pattern="[0-9]+", match="full"), 123, "expected_output"),
    ],
)
def test_invalid_rules_are_refused_with_the_field_name(rule, expected, field):
    with pytest.raises(RuleError) as info:
        validate_rule(rule, expected)
    assert info.value.field == field
    assert "SYNTHETIC" not in info.value.message


def test_refusal_messages_never_quote_the_submitted_pattern_or_schema():
    secret_pattern = "(?P<n>leak-token-77)(?P=n)"
    with pytest.raises(RuleError) as info:
        validate_rule(v1("regex", pattern=secret_pattern, match="full"), None)
    assert "leak-token-77" not in info.value.message
    with pytest.raises(RuleError) as info:
        validate_rule(v1("json_schema", schema={"leak-keyword-77": 1}), None)
    assert "leak-keyword-77" not in info.value.message


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
    check_json_size("expected_output", {"a": "x" * 100, "n": Decimal("0.1")})
    with pytest.raises(RuleError, match="exact JSON"):
        check_json_size("expected_output", {"n": 0.1})  # a float has already lost digits
