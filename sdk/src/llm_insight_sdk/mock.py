"""Deterministic fake LLM provider for tests and the demo. Everything here is SYNTHETIC.

- No network, no API key, no spend. Outcomes, token counts, models and latencies are fixed
  per scenario (and per attempt for the retry scenario), so runs are reproducible.
- It never imitates a real provider: the provider name is `llminsight.mock`, models are
  `mock-*`, response IDs are `mockresp_<uuid4 hex>` (unique per successful attempt, so two
  runs never collide in the usage ledger), and every span says `llminsight.provenance =
  synthetic`.
- Message content is accepted so the call looks like a real chat API, but it is never
  inspected, stored or exported. The returned text is a fixed synthetic string.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from llm_insight_sdk.instrumentation import (
    InferenceRecorder,
    RequestParams,
    TokenUsage,
    acall_with_retries,
    call_with_retries,
    new_call_id,
)
from llm_insight_sdk.telemetry import Telemetry

MOCK_PROVIDER = "llminsight.mock"
RESPONSE_MODEL_SUFFIX = "-2026-10-01"  # a fixed synthetic snapshot: response.model != request


class Scenario(StrEnum):
    SUCCESS = "success"
    DELAYED = "delayed"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    RETRY_THEN_SUCCESS = "retry_then_success"  # attempt 1: rate limited, attempt 2+: success


class MockProviderError(Exception):
    """Base class of mock provider errors. `error_type` becomes the span's `error.type`."""

    error_type: ClassVar[str] = "_OTHER"
    retryable: ClassVar[bool] = False


class MockRateLimitError(MockProviderError):
    error_type = "429"
    retryable = True


class MockServerError(MockProviderError):
    error_type = "500"
    retryable = True


class MockTimeoutError(MockProviderError):
    error_type = "timeout"
    retryable = True


@dataclass(frozen=True)
class MockCompletion:
    text: str
    response_id: str
    response_model: str
    finish_reasons: tuple[str, ...]
    usage: TokenUsage


@dataclass(frozen=True)
class _Plan:
    latency_s: float
    error: type[MockProviderError] | None = None
    usage: TokenUsage | None = None


_SUCCESS_USAGE = TokenUsage(
    input_tokens=120,
    output_tokens=48,
    cache_read_input_tokens=32,
    cache_write_input_tokens=0,
    reasoning_output_tokens=0,
)
_DELAYED_USAGE = TokenUsage(
    input_tokens=480,
    output_tokens=160,
    cache_read_input_tokens=0,
    cache_write_input_tokens=64,
    reasoning_output_tokens=96,
)


class MockLLMProvider:
    """The fake provider itself (no telemetry). `time_scale` multiplies every latency;
    tests use 0 for speed."""

    def __init__(self, *, time_scale: float = 1.0) -> None:
        if time_scale < 0:
            raise ValueError("time_scale must be >= 0")
        self._time_scale = time_scale

    @staticmethod
    def plan(scenario: Scenario, attempt: int, timeout_s: float) -> _Plan:
        match Scenario(scenario):
            case Scenario.SUCCESS:
                return _Plan(0.02, usage=_SUCCESS_USAGE)
            case Scenario.DELAYED:
                return _Plan(0.30, usage=_DELAYED_USAGE)
            case Scenario.RATE_LIMITED:
                return _Plan(0.01, error=MockRateLimitError)
            case Scenario.SERVER_ERROR:
                return _Plan(0.01, error=MockServerError)
            case Scenario.TIMEOUT:
                return _Plan(timeout_s, error=MockTimeoutError)
            case Scenario.RETRY_THEN_SUCCESS:
                if attempt == 1:
                    return _Plan(0.01, error=MockRateLimitError)
                return _Plan(0.02, usage=_SUCCESS_USAGE)
        raise ValueError("unknown scenario")  # pragma: no cover

    def _result(self, plan: _Plan, model: str) -> MockCompletion:
        if plan.error is not None:
            raise plan.error(f"synthetic {plan.error.error_type} from the mock provider")
        if plan.usage is None:  # pragma: no cover - every success plan has usage
            raise RuntimeError("mock plan without usage")
        return MockCompletion(
            text="SYNTHETIC mock completion",
            response_id=f"mockresp_{uuid.uuid4().hex}",
            response_model=f"{model}{RESPONSE_MODEL_SUFFIX}",
            finish_reasons=("stop",),
            usage=plan.usage,
        )

    def complete(
        self, *, model: str, scenario: Scenario, attempt: int, timeout_s: float
    ) -> MockCompletion:
        plan = self.plan(scenario, attempt, timeout_s)
        time.sleep(plan.latency_s * self._time_scale)
        return self._result(plan, model)

    async def acomplete(
        self, *, model: str, scenario: Scenario, attempt: int, timeout_s: float
    ) -> MockCompletion:
        plan = self.plan(scenario, attempt, timeout_s)
        await asyncio.sleep(plan.latency_s * self._time_scale)
        return self._result(plan, model)


def _retryable(exc: BaseException) -> bool:
    return isinstance(exc, MockProviderError) and exc.retryable


class ObservedMockClient:
    """A chat client over `MockLLMProvider` that emits one SYNTHETIC CLIENT span per physical
    attempt through `telemetry`. Provider errors reach the caller after the last attempt;
    telemetry problems never do."""

    def __init__(
        self,
        telemetry: Telemetry,
        provider: MockLLMProvider | None = None,
        *,
        max_attempts: int = 1,
        backoff_seconds: float = 0.0,
        timeout_seconds: float = 1.0,
    ) -> None:
        self._telemetry = telemetry
        self._provider = provider or MockLLMProvider()
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._timeout_seconds = timeout_seconds

    def _span(
        self,
        model: str,
        call_id: str,
        attempt: int,
        prompt_name: str | None,
        prompt_version: str | None,
        params: RequestParams | None,
    ) -> AbstractContextManager[InferenceRecorder]:
        return self._telemetry.inference_span(
            operation="chat",
            provider=MOCK_PROVIDER,
            request_model=model,
            call_id=call_id,
            attempt=attempt,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            params=params,
        )

    def chat(
        self,
        *,
        model: str,
        messages: Sequence[object],
        scenario: Scenario = Scenario.SUCCESS,
        prompt_name: str | None = None,
        prompt_version: str | None = None,
        params: RequestParams | None = None,
        max_attempts: int | None = None,
    ) -> MockCompletion:
        """One logical call (one `call_id`), retried as separate attempts. `messages` is
        never read or exported."""
        del messages
        call_id = new_call_id()

        def attempt_fn(attempt: int) -> MockCompletion:
            with self._span(model, call_id, attempt, prompt_name, prompt_version, params) as rec:
                completion = self._provider.complete(
                    model=model, scenario=scenario, attempt=attempt, timeout_s=self._timeout_seconds
                )
                rec.record_response(
                    response_model=completion.response_model,
                    response_id=completion.response_id,
                    finish_reasons=completion.finish_reasons,
                    usage=completion.usage,
                )
                return completion

        return call_with_retries(
            attempt_fn,
            max_attempts=max_attempts or self._max_attempts,
            is_retryable=_retryable,
            backoff_seconds=self._backoff_seconds,
        )

    async def achat(
        self,
        *,
        model: str,
        messages: Sequence[object],
        scenario: Scenario = Scenario.SUCCESS,
        prompt_name: str | None = None,
        prompt_version: str | None = None,
        params: RequestParams | None = None,
        max_attempts: int | None = None,
    ) -> MockCompletion:
        """Async variant of `chat`; concurrent calls keep their own parent context."""
        del messages
        call_id = new_call_id()

        async def attempt_fn(attempt: int) -> MockCompletion:
            with self._span(model, call_id, attempt, prompt_name, prompt_version, params) as rec:
                completion = await self._provider.acomplete(
                    model=model, scenario=scenario, attempt=attempt, timeout_s=self._timeout_seconds
                )
                rec.record_response(
                    response_model=completion.response_model,
                    response_id=completion.response_id,
                    finish_reasons=completion.finish_reasons,
                    usage=completion.usage,
                )
                return completion

        return await acall_with_retries(
            attempt_fn,
            max_attempts=max_attempts or self._max_attempts,
            is_retryable=_retryable,
            backoff_seconds=self._backoff_seconds,
        )
