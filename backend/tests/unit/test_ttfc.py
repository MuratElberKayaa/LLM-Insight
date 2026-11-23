"""Phase 10B (ADR-55): streaming time to first chunk, M20.

The span attribute `gen_ai.response.time_to_first_chunk` (semconv, Development, double in
seconds) is allowlisted under attrmap v2 within [0, 3600]; the metric block reports it in
milliseconds and is never estimated. SYNTHETIC values only.
"""

from types import SimpleNamespace

import pytest

from app.services import metrics
from app.services.ingestion import attrmap
from tests.otlp import kvs

KEY = "gen_ai.response.time_to_first_chunk"


@pytest.mark.parametrize(
    ("value", "kept"),
    [
        (0.25, 0.25),
        (0, 0),  # an int is accepted as a number
        (3600.0, 3600.0),
        (3600.5, None),  # more than an hour for one response: implausible, dropped
        (-0.1, None),
        (float("nan"), None),
        (float("inf"), None),
        ("0.25", None),  # a string is not a number
        (True, None),
    ],
)
def test_the_attribute_is_a_bounded_number_of_seconds(value, kept):
    m = attrmap.map_span(kvs({KEY: value}))
    if kept is None:
        assert KEY not in m.values and m.dropped == 1
    else:
        assert m.values[KEY] == kept and m.dropped == 0


def test_v1_number_attributes_keep_no_range():
    # The range check applies only where a spec sets one; temperature has none (unchanged v1).
    m = attrmap.map_span(kvs({"gen_ai.request.temperature": 7.5}))
    assert m.values["gen_ai.request.temperature"] == 7.5


def row(streaming, n, p50=None, p95=None):
    return SimpleNamespace(streaming_attempts=streaming, ttfc_n=n, ttfc_p50=p50, ttfc_p95=p95)


def test_block_with_recorded_values():
    b = metrics.ttfc(row(3, 2, 180.123456789, 420.5))
    assert b == {
        "available": True,
        "reason": None,
        "detail": None,
        "streaming_attempts": 3,
        "n": 2,
        "p50_ms": 180.123457,
        "p95_ms": 420.5,
        "low_sample": {"p95": True},
    }


def test_streaming_without_values_is_not_recorded_never_zero():
    b = metrics.ttfc(row(4, 0))
    assert (b["available"], b["reason"]) == (False, "not_recorded")
    assert (b["p50_ms"], b["p95_ms"]) == (None, None)


@pytest.mark.parametrize("r", [None, row(0, 0)])
def test_no_streaming_says_so(r):
    b = metrics.ttfc(r)
    assert (b["available"], b["reason"], b["n"], b["streaming_attempts"]) == (
        False,
        "no_streaming_attempts",
        0,
        0,
    )
    assert b["detail"]
