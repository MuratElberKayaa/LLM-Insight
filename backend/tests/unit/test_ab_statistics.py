"""Phase 9D (ADR-53): Wilson intervals and the exact McNemar test.

The Wilson reference values were computed independently with binary floats (`math.sqrt`) from
the closed form and pinned to 6 decimal places; the McNemar values are exact fractions worked
by hand from the binomial sums.
"""

from decimal import Decimal
from fractions import Fraction

import pytest

from app.services import ab_statistics as st


@pytest.mark.parametrize(
    ("k", "n", "low", "high"),
    [
        (0, 6, "0.000000", "0.390334"),
        (6, 6, "0.609666", "1.000000"),
        (3, 6, "0.187616", "0.812384"),
        (5, 6, "0.436497", "0.969947"),
        (1, 12, "0.014865", "0.353880"),
        (10, 12, "0.551969", "0.953035"),
        (50, 100, "0.403832", "0.596168"),
    ],
)
def test_wilson_matches_the_closed_form(k, n, low, high):
    assert st.wilson_interval(k, n) == (Decimal(low), Decimal(high))


def test_wilson_is_symmetric_and_brackets_the_rate():
    for n in range(1, 25):
        for k in range(n + 1):
            lo, hi = st.wilson_interval(k, n)
            assert Decimal(0) <= lo <= Decimal(k) / n <= hi <= Decimal(1)
            mirror = st.wilson_interval(n - k, n)
            assert (Decimal(1) - hi, Decimal(1) - lo) == mirror


def test_wilson_without_cases_is_none_and_bad_counts_are_refused():
    assert st.wilson_interval(0, 0) is None
    for args in ((7, 6), (-1, 6), (1.0, 6)):
        with pytest.raises(ValueError):
            st.wilson_interval(*args)


@pytest.mark.parametrize(
    ("a_only", "b_only", "p"),
    [
        (0, 0, Fraction(1)),
        (1, 0, Fraction(1)),  # 2 · 1/2, capped at 1
        (3, 0, Fraction(1, 4)),
        (5, 0, Fraction(1, 16)),
        (6, 0, Fraction(1, 32)),
        (0, 6, Fraction(1, 32)),
        (6, 1, Fraction(1, 8)),  # 2 · (1 + 7) / 128
        (2, 2, Fraction(1)),
        (10, 2, Fraction(79, 2048)),  # 2 · (1 + 12 + 66) / 4096
    ],
)
def test_exact_mcnemar_p_values(a_only, b_only, p):
    assert st.mcnemar_p_fraction(a_only, b_only) == p


def test_the_block_reports_counts_rounded_p_and_an_exact_decision():
    block = st.exact_mcnemar(6, 0)
    assert block == {
        "method": "exact_mcnemar",
        "a_only": 6,
        "b_only": 0,
        "p_value": Decimal("0.031250"),
        "significant_at_0_05": True,
        "min_discordant_for_0_05": 6,
    }
    assert st.exact_mcnemar(5, 0)["significant_at_0_05"] is False  # 0.0625
    assert st.exact_mcnemar(0, 0)["p_value"] == Decimal("1.000000")


def test_six_discordant_pairs_are_the_minimum_for_significance():
    # With five or fewer discordant pairs no split can reach p < 0.05: the six-case live
    # A/B can only ever show a significant difference if every case disagrees one way.
    assert st.min_discordant_for(st.ALPHA) == 6
    assert all(st.mcnemar_p_fraction(n, 0) >= st.ALPHA for n in range(6))


def test_mcnemar_refuses_bad_counts():
    for args in ((-1, 0), (1, 0.5)):
        with pytest.raises(ValueError):
            st.exact_mcnemar(*args)
