"""Planning and executing one mock A/B experiment (Phase 4C, B4). Pure: no database, clock or
network; the caller loads the cases and prices and stores what this returns.

Order and identity: the cases are taken in id order and each runs under prompt version A and
then B, so both versions see exactly the same case ids (T38). Each (case, version) is one
simulated attempt (no retries) and becomes one result row.

Budget (`budget_usd`, optional for mock runs): checked **before** anything runs, from the
provider's estimates. A run is refused when any estimated cost is unknown (a budget cannot be
shown to hold) or the estimated total exceeds the budget. During the run a guard stops issuing
calls once the recorded known cost exceeds the budget; the remaining (case, version) pairs are
recorded as not executed (`budget_exhausted`). For the deterministic mock the estimate equals
the outcome, so the guard is a safety net.

Cost per result: synthetic token counts priced at the stored price of
(`openai`, experiment model) in force at the experiment's start, with the ledger's rules
(`services/pricing.py`): no usage -> `usage_missing`, no price -> `price_missing`, never 0.
Every mock result is `synthetic` (T39).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.services import pricing
from app.services.evaluation import engine
from app.services.experiments.golden import Scripted
from app.services.experiments.mock_provider import SIMULATED_PROVIDER, ScriptedMockProvider

BUDGET_EXHAUSTED = "budget_exhausted"
_CALL_ERROR_TEXT = {
    "provider_timeout": "the simulated call timed out; there is no output to evaluate",
    "provider_error": "the simulated call failed; there is no output to evaluate",
    "mock_no_scripted_output": (
        "the mock provider has no scripted output for this input and prompt version"
    ),
}


@dataclass(frozen=True)
class CaseRecord:
    id: int
    sanitized_input: str
    expected_output: Any  # exact JSON, None when the case has none
    rule: Any  # exact JSON as stored


@dataclass(frozen=True)
class PlannedResult:
    test_case_id: int
    prompt_version: str
    passed: bool | None
    code: str
    evaluation_detail: str | None
    duration_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    cost: pricing.CostOutcome
    provenance: str

    def as_row(self, experiment_id: int) -> dict[str, Any]:
        return {
            "experiment_id": experiment_id,
            "test_case_id": self.test_case_id,
            "prompt_version": self.prompt_version,
            "passed": self.passed,
            "evaluation_detail": self.evaluation_detail,
            "duration_ms": self.duration_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost.cost_usd,
            "cost_status": self.cost.cost_status,
            "cost_unknown_reason": self.cost.cost_unknown_reason,
            "provenance": self.provenance,
        }


class BudgetRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def result_cost(
    answer: Scripted, model: str, prices: Iterable[pricing.Price], at: datetime
) -> pricing.CostOutcome:
    tokens = pricing.Tokens(answer.input_tokens, answer.output_tokens, None, None, None)
    reason = pricing.usage_unknown_reason(tokens, "provider_reported")
    if reason is not None:
        return pricing.unknown(reason)
    price = pricing.select_price(prices, SIMULATED_PROVIDER, model, at)
    if price is None:
        return pricing.unknown("price_missing")
    cost = pricing.compute_cost(tokens, price)
    if cost is None:
        return pricing.unknown("price_missing")
    if cost > pricing.MAX_COST:
        return pricing.unknown("usage_inconsistent")
    return pricing.CostOutcome(cost, "computed", None, price.version, price.id)


def check_budget(
    cases: Sequence[CaseRecord],
    versions: tuple[str, str],
    model: str,
    provider: ScriptedMockProvider,
    prices: Sequence[pricing.Price],
    at: datetime,
    budget: Decimal,
) -> Decimal:
    """The estimated total, or `BudgetRefused`. Nothing is executed."""
    total = Decimal(0)
    for case in cases:
        for version in versions:
            estimate = provider.estimate(
                model=model, prompt_version=version, sanitized_input=case.sanitized_input
            )
            cost = result_cost(estimate, model, prices, at)
            if cost.cost_usd is None:
                raise BudgetRefused(
                    "budget_unverifiable",
                    "the estimated cost of at least one call is unknown, so the budget cannot"
                    " be shown to hold; nothing was run",
                )
            total += cost.cost_usd
    if total > budget:
        raise BudgetRefused(
            "budget_exceeded", "the estimated cost exceeds budget_usd; nothing was run"
        )
    return total


def _evaluate(case: CaseRecord, answer: Scripted) -> tuple[bool | None, str, str]:
    if answer.error is not None or answer.output is None:
        error = answer.error or "provider_error"
        return None, error, f"{error}: {_CALL_ERROR_TEXT.get(error, 'no output')}"
    outcome = engine.evaluate(case.rule, case.expected_output, answer.output)
    return outcome.passed, outcome.code, outcome.detail or ""


def run(
    cases: Sequence[CaseRecord],
    versions: tuple[str, str],
    model: str,
    provider: ScriptedMockProvider,
    prices: Sequence[pricing.Price],
    at: datetime,
    budget: Decimal | None,
) -> list[PlannedResult]:
    """Every (case, version) result in execution order. Raises `BudgetRefused` before any
    call when a budget is given and cannot be shown to hold."""
    if budget is not None:
        check_budget(cases, versions, model, provider, prices, at, budget)
    results: list[PlannedResult] = []
    spent = Decimal(0)
    stopped = False
    for case in sorted(cases, key=lambda c: c.id):
        for version in versions:
            if stopped:
                results.append(
                    PlannedResult(
                        case.id,
                        version,
                        None,
                        BUDGET_EXHAUSTED,
                        f"{BUDGET_EXHAUSTED}: not executed; the budget was reached earlier in"
                        " the run",
                        None,
                        None,
                        None,
                        pricing.unknown("usage_missing"),
                        "synthetic",
                    )
                )
                continue
            answer = provider.call(
                model=model, prompt_version=version, sanitized_input=case.sanitized_input
            )
            passed, code, detail = _evaluate(case, answer)
            cost = result_cost(answer, model, prices, at)
            results.append(
                PlannedResult(
                    case.id,
                    version,
                    passed,
                    code,
                    None if passed is True else detail,
                    answer.duration_ms if answer.output is not None else None,
                    answer.input_tokens,
                    answer.output_tokens,
                    cost,
                    "synthetic",
                )
            )
            if cost.cost_usd is not None:
                spent += cost.cost_usd
            if budget is not None and spent > budget:
                stopped = True
    return results
