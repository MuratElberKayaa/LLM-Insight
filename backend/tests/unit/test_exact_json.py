"""Exact JSON (Phase 4C): Decimal numbers, refusals, lossless round trip."""

from decimal import Decimal

import pytest

from app.core import exact_json


def test_non_integers_are_exact_decimals_and_integers_stay_int():
    value = exact_json.loads('{"a": 0.1000000000000000000001, "b": 1.10, "c": 3, "d": -0.0}')
    assert value["a"] == Decimal("0.1000000000000000000001")
    assert str(value["b"]) == "1.10"  # scale kept
    assert type(value["c"]) is int and value["c"] == 3
    assert isinstance(value["d"], Decimal)
    assert not any(isinstance(v, float) for v in value.values())


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1, "a": 2}',  # duplicate key
        "[NaN]",
        "[Infinity]",
        "[-Infinity]",
        '["\\ud800"]',  # unpaired surrogate
        '{"\\udfff": 1}',
        "{'a': 1}",
        "[1,]",
        "",
    ],
)
def test_refused_inputs(text):
    with pytest.raises(exact_json.ExactJSONError):
        exact_json.loads(text)


@pytest.mark.parametrize(
    "text", ["[1e401]", "[1e-401]", "[" + "1" * 65 + "]", "[0." + "1" * 64 + "]"]
)
def test_number_limits_are_a_distinct_refusal(text):
    with pytest.raises(exact_json.ExactJSONLimit):
        exact_json.loads(text)


def test_zero_with_a_huge_exponent_is_accepted():
    assert exact_json.loads("[0e999]") == [Decimal("0e999")]


def test_round_trip_is_lossless():
    text = '{"x":[1,0.1000000000000000000001,1.10,1E+2,-0,"\\u00e9\\n",true,false,null],"y":{}}'
    value = exact_json.loads(text)
    again = exact_json.loads(exact_json.dumps(value))
    assert again == value
    assert exact_json.dumps(value) == (  # "-0" is the integer 0: the same value
        '{"x":[1,0.1000000000000000000001,1.10,1E+2,0,"é\\n",true,false,null],"y":{}}'
    )


def test_dumps_refuses_floats_and_foreign_types():
    with pytest.raises(TypeError):
        exact_json.dumps({"a": 0.1})
    with pytest.raises(TypeError):
        exact_json.dumps({1: "a"})
    with pytest.raises(ValueError):
        exact_json.dumps([Decimal("NaN")])
