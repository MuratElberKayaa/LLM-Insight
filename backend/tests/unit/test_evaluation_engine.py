"""Deterministic evaluation engine (Phase 4C): T36, T37, T40 at unit level, every rule type,
equality semantics, malformed output, limits, detail safety and determinism. All content is
SYNTHETIC."""

from decimal import Decimal

import pytest

from app.core import exact_json
from app.services.evaluation import rules
from app.services.evaluation.engine import MAX_DETAIL_CHARS, Outcome, evaluate

TICKET_SCHEMA = {
    "type": "object",
    "required": ["title", "priority"],
    "properties": {
        "title": {"type": "string", "minLength": 1},
        "priority": {"enum": ["low", "medium", "high"]},
    },
    "additionalProperties": False,
}
SCHEMA_RULE = {"version": 1, "type": "json_schema", "schema": TICKET_SCHEMA}
EXPECTED_TICKET = {"title": "SYNTHETIC printer offline", "priority": "high"}
FIELD_RULE = {"version": 1, "type": "field_match", "fields": ["category", "meta.lang"]}
EXPECTED_FIELDS = {"category": "billing", "meta": {"lang": "en"}, "note": "ignored"}


def stored(value):
    """As the engine receives stored JSON: exact text parsed back (Decimals, no floats)."""
    return exact_json.loads(exact_json.dumps(value))


# ---------------------------------------------------------------- T36 / T37
def test_t36_valid_output_passes_the_json_schema_rule():
    out = evaluate(stored(SCHEMA_RULE), stored(EXPECTED_TICKET), '{"priority":"low","title":"x"}')
    assert out == Outcome(True, "pass", None)


def test_t36_valid_output_passes_the_field_rule():
    out = evaluate(
        stored(FIELD_RULE),
        stored(EXPECTED_FIELDS),
        '{"meta": {"lang": "en", "extra": 1}, "category": "billing", "note": "other"}',
    )
    assert out == Outcome(True, "pass", None)


@pytest.mark.parametrize(
    ("output", "keywords"),
    [
        ('{"title": "x"}', ["required"]),
        ('{"title": "", "priority": "high"}', ["minLength"]),
        ('{"title": "x", "priority": "urgent"}', ["enum"]),
        ('{"title": "x", "priority": 3}', ["enum"]),
        ('{"title": "x", "priority": "low", "extra": true}', ["additionalProperties"]),
        ('["title", "priority"]', ["type"]),
    ],
)
def test_t37_schema_violation_fails_with_evaluation_detail(output, keywords):
    out = evaluate(stored(SCHEMA_RULE), stored(EXPECTED_TICKET), output)
    assert out.passed is False and out.code == "schema_violation"
    assert out.detail.startswith("schema_violation: the output does not satisfy the schema")
    for keyword in keywords:
        assert keyword in out.detail
    assert len(out.detail) <= MAX_DETAIL_CHARS


# ---------------------------------------------------------------- T40
@pytest.mark.parametrize(
    "rule",
    [
        SCHEMA_RULE,
        FIELD_RULE,
        {"version": 1, "type": "exact", "output_format": "text"},
        {"version": 1, "type": "exact", "output_format": "json"},
        {"version": 1, "type": "regex", "pattern": "[a-z]+", "match": "full"},
    ],
    ids=lambda r: f"{r['type']}-{r.get('output_format', '')}",
)
@pytest.mark.parametrize("output", ['{"title":"x","priority":"low"}', "not json", "abc"])
def test_t40_missing_expected_output_is_not_evaluated_for_every_rule(rule, output):
    out = evaluate(stored(rule), None, output)
    assert out.passed is None and out.code == "expected_output_missing"
    assert out.detail == "expected_output_missing: the case has no approved expected output"


# ---------------------------------------------------------------- exact
def test_exact_text_is_code_point_equality():
    rule = stored({"version": 1, "type": "exact", "output_format": "text"})
    assert evaluate(rule, "ACK", "ACK").passed is True
    for other in ("ACK.", "ack", " ACK", "ACK\n", "ÁCK"):
        out = evaluate(rule, "ACK", other)
        assert out.passed is False and out.code == "exact_mismatch"
    nfc, nfd = "é", "é"  # same glyph, different code points: not equal
    assert evaluate(rule, nfc, nfd).passed is False


@pytest.mark.parametrize(
    ("output", "passed"),
    [
        ('{"b": [1, 2], "a": 1}', True),  # key order does not matter
        ('{"a": 1.0, "b": [1, 2.00]}', True),  # numbers by exact value
        ('{"a": 1E0, "b": [1, 2]}', True),
        ('{"a": 1, "b": [2, 1]}', False),  # array order matters
        ('{"a": true, "b": [1, 2]}', False),  # boolean is not a number
        ('{"a": "1", "b": [1, 2]}', False),  # string is not a number
        ('{"a": 1, "b": [1, 2], "c": null}', False),  # extra key
        ('{"a": 1}', False),  # missing key
        ('{"a": 1.0000000000000000000001, "b": [1, 2]}', False),  # no float rounding
    ],
)
def test_exact_json_structural_semantics(output, passed):
    rule = stored({"version": 1, "type": "exact", "output_format": "json"})
    assert evaluate(rule, stored({"a": 1, "b": [1, 2]}), output).passed is passed


def test_booleans_and_numbers_never_compare_equal():
    rule = stored({"version": 1, "type": "exact", "output_format": "json"})
    assert evaluate(rule, [True], "[1]").passed is False
    assert evaluate(rule, [0], "[false]").passed is False
    assert evaluate(rule, [None], "[0]").passed is False


def test_exact_numeric_precision_is_preserved():
    rule = stored({"version": 1, "type": "exact", "output_format": "json"})
    expected = stored({"p": Decimal("0.1000000000000000000001")})
    assert evaluate(rule, expected, '{"p": 0.1000000000000000000001}').passed is True
    assert evaluate(rule, expected, '{"p": 0.1}').passed is False


# ---------------------------------------------------------------- field_match
@pytest.mark.parametrize(
    ("output", "detail_part"),
    [
        ('{"category": "refund", "meta": {"lang": "en"}}', "category (the string values differ)"),
        ('{"category": "billing", "meta": {"lang": 1}}', "meta.lang (expected string, got number)"),
        ('{"category": "billing"}', "meta.lang (missing in the output)"),
        ('{"category": "billing", "meta": "en"}', "meta.lang (missing in the output)"),
    ],
)
def test_field_match_failures_name_the_field_never_the_value(output, detail_part):
    out = evaluate(stored(FIELD_RULE), stored(EXPECTED_FIELDS), output)
    assert out.passed is False and out.code == "field_mismatch"
    assert detail_part in out.detail
    assert "refund" not in out.detail and "billing" not in out.detail


def test_field_match_needs_an_object_output():
    out = evaluate(stored(FIELD_RULE), stored(EXPECTED_FIELDS), '["category"]')
    assert out == Outcome(
        False, "output_not_object", "output_not_object: field_match needs a JSON object, got array"
    )


# ---------------------------------------------------------------- regex
def test_regex_full_and_search():
    full = stored({"version": 1, "type": "regex", "pattern": "[A-Z]{3}-[0-9]{4}", "match": "full"})
    assert evaluate(full, "ABC-0001", "TCK-0042").passed is True
    assert evaluate(full, "ABC-0001", "Ticket TCK-0042").code == "regex_no_match"
    search = stored({"version": 1, "type": "regex", "pattern": "(?i)refund", "match": "search"})
    assert evaluate(search, "Refund", "We approved the REFUND.").passed is True


# ---------------------------------------------------------------- malformed output
@pytest.mark.parametrize(
    "output",
    ["", "SYNTHETIC prose, not JSON", '{"title": "x",', '{"a": 1, "a": 2}', "[NaN]", '"\\ud800"'],
)
def test_malformed_output_fails_as_not_json(output):
    out = evaluate(stored(SCHEMA_RULE), stored(EXPECTED_TICKET), output)
    assert out.passed is False and out.code == "output_not_json"


def test_output_that_is_not_valid_unicode_fails():
    out = evaluate(stored(SCHEMA_RULE), stored(EXPECTED_TICKET), "\ud800")
    assert out.passed is False and out.code == "output_invalid_text"
    assert evaluate(stored(SCHEMA_RULE), stored(EXPECTED_TICKET), b"{}").code == (
        "output_invalid_text"
    )


# ---------------------------------------------------------------- not evaluated
def test_output_limits_are_not_failures():
    big = '"' + "x" * rules.MAX_OUTPUT_BYTES + '"'
    out = evaluate(stored(SCHEMA_RULE), stored(EXPECTED_TICKET), big)
    assert out.passed is None and out.code == "output_too_large"
    deep = "[" * 40 + "]" * 40
    assert evaluate(stored(SCHEMA_RULE), stored(EXPECTED_TICKET), deep).code == (
        "output_limits_exceeded"
    )
    assert evaluate(stored(SCHEMA_RULE), stored(EXPECTED_TICKET), "[1e999]").code == (
        "output_limits_exceeded"
    )
    many = "[" + ",".join("0" for _ in range(10_001)) + "]"
    assert evaluate(stored(SCHEMA_RULE), stored(EXPECTED_TICKET), many).passed is None


@pytest.mark.parametrize(
    "rule",
    [
        {"type": "exact"},  # Phase 4B unversioned payload: never reinterpreted
        {"type": "regex", "pattern": "a"},
        {"version": 2, "type": "exact", "output_format": "text"},
        {"version": 1, "type": "regex", "pattern": r"(a)\1", "match": "full"},
        {"version": 1, "type": "json_schema", "schema": {"pattern": "a"}},
        "exact",
    ],
)
def test_unsupported_stored_rules_are_not_evaluated(rule):
    out = evaluate(rule, "a", "a")
    assert out.passed is None and out.code == "rule_unsupported"


def test_incoherent_stored_expected_output_is_not_evaluated():
    out = evaluate(stored(SCHEMA_RULE), stored({"title": 1}), '{"title":"x","priority":"low"}')
    assert out.passed is None and out.code == "expected_output_invalid"


# ---------------------------------------------------------------- safety and determinism
def test_details_never_contain_output_or_expected_values():
    secret_out = (
        '{"title": "jane.roe@example.invalid", "priority": "sk-SYNTHETICleak",'
        ' "jane.roe@example.invalid": 1, "contact-key-77": 2}'
    )
    out = evaluate(stored(SCHEMA_RULE), stored(EXPECTED_TICKET), secret_out)
    assert out.passed is False
    for leaked in ("jane.roe", "sk-SYNTHETIC", "contact-key-77", "SYNTHETIC printer"):
        assert leaked not in out.detail
    exact = stored({"version": 1, "type": "exact", "output_format": "json"})
    out = evaluate(exact, stored({"a": "SYNTHETIC expected"}), '{"a": "SYNTHETIC-leak-99"}')
    assert "SYNTHETIC-leak-99" not in out.detail and "SYNTHETIC expected" not in out.detail
    out = evaluate(exact, stored({"a": 1}), '{"a": 1, "leaky-key-55": 2}')
    assert "leaky-key-55" not in out.detail and "unexpected key" in out.detail


def test_evaluation_is_deterministic_and_pure():
    rule, expected = stored(SCHEMA_RULE), stored(EXPECTED_TICKET)
    rule_copy, expected_copy = exact_json.dumps(rule), exact_json.dumps(expected)
    outputs = ['{"title":"x"}', '{"title":"x","priority":"low"}', "nope"]
    first = [evaluate(rule, expected, o) for o in outputs]
    for _ in range(20):
        assert [evaluate(rule, expected, o) for o in outputs] == first
    assert exact_json.dumps(rule) == rule_copy and exact_json.dumps(expected) == expected_copy


def test_every_detail_is_prefixed_with_its_code_and_bounded():
    many_errors = (
        '{"title": "", "priority": "x", ' + ", ".join(f'"e{i}": {i}' for i in range(200)) + "}"
    )
    out = evaluate(stored(SCHEMA_RULE), stored(EXPECTED_TICKET), many_errors)
    assert out.detail.startswith(out.code + ": ")
    assert len(out.detail) <= MAX_DETAIL_CHARS and "error(s)" in out.detail
