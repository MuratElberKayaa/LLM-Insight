"""Small-sample statistics for A/B reports (Phase 9D, ADR-53). Pure and deterministic.

- `wilson_interval(k, n)`: the 95 % Wilson score interval for a pass rate `k / n`. It stays
  inside [0, 1] and is informative at the small `n` this project has (6–12 cases), where the
  normal ("Wald") interval collapses to a point at 0 or n passes.
- `exact_mcnemar(a_only, b_only)`: the exact two-sided McNemar test on paired binary outcomes.
  Only the discordant pairs (a case passed by one version and failed by the other) carry
  information. Under "no difference" each discordant pair is a fair coin, so
  p = min(1, 2 · P(X ≤ min(a_only, b_only))) for X ~ Binomial(a_only + b_only, 1/2), computed
  exactly with integers. No discordant pair gives p = 1: there is no evidence either way.

No randomness (no bootstrap), no floats in the results: values are `Decimal`, rounded once,
half-even, to 6 places, the report's precision. A p-value above 0.05 is not evidence of "no
difference"; with few discordant pairs no result can reach 0.05 (`min_discordant_for_0_05`).
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Context, Decimal
from fractions import Fraction
from math import comb

Z_95 = Decimal("1.959963984540054")  # the 0.975 standard normal quantile
ALPHA = Fraction(5, 100)
_SIX = Decimal("0.000001")
_CTX = Context(prec=50, rounding=ROUND_HALF_EVEN)


def _six(value: Decimal) -> Decimal:
    return value.quantize(_SIX, rounding=ROUND_HALF_EVEN, context=_CTX)


def _check_counts(*counts: int) -> None:
    if any(type(c) is not int or c < 0 for c in counts):
        raise ValueError("counts must be non-negative integers")


def wilson_interval(successes: int, n: int) -> tuple[Decimal, Decimal] | None:
    """95 % Wilson score interval of `successes / n`; `None` when `n` is 0."""
    _check_counts(successes, n)
    if successes > n:
        raise ValueError("successes exceed n")
    if n == 0:
        return None
    p = _CTX.divide(Decimal(successes), Decimal(n))
    z2 = _CTX.multiply(Z_95, Z_95)
    denominator = _CTX.add(Decimal(1), _CTX.divide(z2, Decimal(n)))
    centre = _CTX.add(p, _CTX.divide(z2, Decimal(2 * n)))
    spread_sq = _CTX.add(
        _CTX.divide(_CTX.multiply(p, Decimal(1) - p), Decimal(n)),
        _CTX.divide(z2, Decimal(4 * n * n)),
    )
    spread = _CTX.multiply(Z_95, _CTX.sqrt(spread_sq))
    low = max(Decimal(0), _CTX.divide(_CTX.subtract(centre, spread), denominator))
    high = min(Decimal(1), _CTX.divide(_CTX.add(centre, spread), denominator))
    # Exact ends: 0 passes has a lower bound of exactly 0, n passes an upper bound of exactly 1.
    if successes == 0:
        low = Decimal(0)
    if successes == n:
        high = Decimal(1)
    return _six(low), _six(high)


def mcnemar_p_fraction(a_only: int, b_only: int) -> Fraction:
    """Exact two-sided p-value as a fraction (see the module docstring)."""
    _check_counts(a_only, b_only)
    n = a_only + b_only
    if n == 0:
        return Fraction(1)
    tail = sum(comb(n, i) for i in range(min(a_only, b_only) + 1))
    return min(Fraction(1), Fraction(2 * tail, 2**n))


def exact_mcnemar(a_only: int, b_only: int) -> dict[str, object]:
    """The report block of the paired test: counts, p-value and the 0.05 decision."""
    p = mcnemar_p_fraction(a_only, b_only)
    return {
        "method": "exact_mcnemar",
        "a_only": a_only,
        "b_only": b_only,
        "p_value": _six(_CTX.divide(Decimal(p.numerator), Decimal(p.denominator))),
        "significant_at_0_05": p < ALPHA,  # decided on the exact fraction, not the rounded value
        "min_discordant_for_0_05": min_discordant_for(ALPHA),
    }


def min_discordant_for(alpha: Fraction) -> int:
    """The fewest discordant pairs for which a one-sided split can reach `p < alpha`."""
    n = 1
    while mcnemar_p_fraction(n, 0) >= alpha:
        n += 1
    return n
