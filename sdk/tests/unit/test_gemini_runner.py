"""Live demo runner `demo_app.live_gemini` (Phase 6C-A, ADR-36), offline: the backend is the
local OTLP stub, the provider is an injected SYNTHETIC fake transport (`synthetic`
provenance), and every non-loopback connection is blocked."""

from __future__ import annotations

import io
import json
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from google import genai

from llm_insight_sdk import gemini_live
from tests.conftest import DUMMY_KEY, free_port
from tests.gemini_fixtures import (
    ERROR_SENTINEL,
    FAKE_GEMINI_KEY,
    MODEL,
    OUTPUT_SENTINEL,
    THOUGHT_SENTINEL,
    FakeGemini,
    GStep,
    candidate,
    g_fail,
    g_ok,
)
from tests.openai_fixtures import block_network
from tests.unit.test_gemini_live_guard import NOW, gemini_env

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))  # demo_app lives at the repository root, outside the SDK

from demo_app import live_gemini  # noqa: E402

SECRETS = (FAKE_GEMINI_KEY, DUMMY_KEY, OUTPUT_SENTINEL, THOUGHT_SENTINEL, ERROR_SENTINEL)


@pytest.fixture(autouse=True)
def offline() -> Iterator[None]:
    attempts: list[str] = []
    with block_network(attempts):
        yield
    assert attempts == [], f"a test tried to reach the network: {attempts}"


def runner_env(endpoint: str, **overrides: str | None) -> dict[str, str]:
    env = gemini_env(**overrides)
    env.update(
        {
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": endpoint,
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": f"X-Project-Key={DUMMY_KEY}",
            "OTEL_SERVICE_NAME": "live-gemini-unit-test",
            "LLMI_SDK_SCHEDULE_DELAY_MILLIS": "50",
            "LLMI_DASHBOARD_PROJECT_ID": "7",
        }
    )
    return {k: v for k, v in env.items() if v is not None}


class NeverCalled:
    calls = 0

    def __call__(self, policy: gemini_live.FreeTierPolicy) -> genai.Client:
        NeverCalled.calls += 1
        raise AssertionError("no provider client may be created")


def run(argv: list[str], env: dict[str, str], **kw) -> tuple[int, dict]:
    out = io.StringIO()
    kw.setdefault("now", NOW)
    kw.setdefault("provenance", "synthetic")
    code = live_gemini.main(argv, env, out=out, **kw)
    text = out.getvalue()
    for secret in SECRETS:
        assert secret not in text
    assert live_gemini.INPUT_TEXT not in text
    (line,) = text.splitlines()
    return code, json.loads(line)


def execute(stub, fake: FakeGemini, **overrides: str | None) -> tuple[int, dict]:
    return run(
        ["--execute"],
        runner_env(stub.endpoint, **overrides),
        client_factory=lambda policy: fake.client(),
    )


# ---------------------------------------------------------------- dry run (the default)
def test_dry_run_is_the_default_and_never_creates_a_provider_client(stub):
    never = NeverCalled()
    code, doc = run([], runner_env(stub.endpoint), client_factory=never)
    assert code == 0 and never.calls == 0
    assert doc["mode"] == "dry_run" and doc["provider_request_sent"] is False
    assert doc["execution_allowed"] is True and doc["refusals"] == []
    assert doc["backend"] == {"ready": True, "reason": "ready"}
    plan = doc["plan"]
    assert plan["provider"] == "gcp.gemini" and plan["model"] == MODEL
    assert plan["endpoint"] == (
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    )
    assert (plan["max_attempts"], plan["max_requests"], plan["max_concurrency"]) == (1, 1, 1)
    assert plan["tools"] == [] and plan["streaming"] is False
    assert plan["automatic_continuation"] is False and plan["automatic_function_calling"] is False
    assert plan["paid_fallback"] is False and plan["billing_tier"] == "free"
    assert "not verified with Google" in plan["billing_tier_basis"]
    assert plan["input_token_bound"] == len(live_gemini.INPUT_TEXT.encode()) + 64
    assert stub.spans() == []  # the readiness probe is an empty request


def test_dry_run_without_configuration_lists_every_refusal(stub):
    code, doc = run([], {}, client_factory=NeverCalled())
    assert code == 0 and doc["execution_allowed"] is False and doc["plan"] is None
    assert doc["backend"]["reason"] == "telemetry_config_invalid"
    assert {
        "provider_mode_not_real",
        "gemini_mode_not_free_tier",
        "consent_missing",
        "api_key_missing",
        "free_tier_unverified",
    } <= {r["code"] for r in doc["refusals"]}


# ---------------------------------------------------------------- execute: refusals
@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"LLMI_PROVIDER_MODE": "mock"}, "provider_mode_not_real"),
        ({"LLMI_GEMINI_MODE": "paid"}, "gemini_mode_not_free_tier"),
        ({"LLMI_LIVE_CONSENT": None}, "consent_missing"),
        ({"GEMINI_API_KEY": None}, "api_key_missing"),
        ({"LLMI_GEMINI_BILLING_TIER": None}, "free_tier_unverified"),
        ({"LLMI_GEMINI_BILLING_TIER": "tier1"}, "billing_tier_not_free"),
        ({"LLMI_GEMINI_TIER_CHECKED_ON": "2026-10-01"}, "free_tier_attestation_stale"),
        ({"LLMI_LIVE_MODELS": "gemini-3.5-flash-lite"}, "model_not_free_tier_verified"),
        ({"LLMI_LIVE_MAX_REQUESTS": "2"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_RETRIES": "1"}, "setting_invalid"),
        ({"GOOGLE_GENAI_USE_VERTEXAI": "true"}, "client_env_override"),
        ({"GOOGLE_API_KEY": "SYNTHETIC"}, "client_env_override"),
        ({"GOOGLE_GEMINI_BASE_URL": "https://evil.example/"}, "client_env_override"),
    ],
)
def test_execute_is_refused_by_every_gate(stub, overrides, code):
    never = NeverCalled()
    rc, doc = run(["--execute"], runner_env(stub.endpoint, **overrides), client_factory=never)
    assert rc == 2 and never.calls == 0
    assert doc["provider_request_sent"] is False and doc["execution_allowed"] is False
    assert code in {r["code"] for r in doc["refusals"]}
    assert stub.spans() == []


def test_execute_is_refused_for_a_model_outside_the_allowlist(stub):
    rc, doc = run(
        ["--execute", "--model", "gemini-2.5-pro"],
        runner_env(stub.endpoint),
        client_factory=NeverCalled(),
    )
    assert rc == 2 and "model_not_allowed" in {r["code"] for r in doc["refusals"]}


def test_execute_is_refused_when_the_backend_is_not_ready(stub):
    endpoint = f"http://127.0.0.1:{free_port()}/v1/traces"
    rc, doc = run(["--execute"], runner_env(endpoint), client_factory=NeverCalled())
    assert rc == 2 and doc["backend"]["reason"] == "backend_unreachable"
    assert "backend_not_ready" in {r["code"] for r in doc["refusals"]}


def test_a_failing_client_construction_sends_nothing(stub):
    def broken(policy):
        raise FileNotFoundError(FAKE_GEMINI_KEY)  # the message is never printed

    rc, doc = run(["--execute"], runner_env(stub.endpoint), client_factory=broken)
    assert rc == 2 and doc["provider_request_sent"] is False
    assert doc["refusals"] == [
        {"code": "client_construction_failed", "message": "FileNotFoundError"}
    ]
    assert stub.spans() == []


def test_an_unsafe_injected_client_is_refused_in_real_mode(stub):
    fake = FakeGemini([g_ok()])
    rc, doc = run(
        ["--execute"],
        runner_env(stub.endpoint),
        client_factory=lambda policy: fake.client(),
        provenance="real",
    )
    assert rc == 2 and fake.requests == [] and stub.spans() == []
    assert {r["code"] for r in doc["refusals"]} == {"client_not_allowed"}


# ---------------------------------------------------------------- execute: one request
def test_one_free_tier_request_is_traced_flushed_and_reported(stub):
    fake = FakeGemini([g_ok(), g_ok()])
    code, doc = execute(stub, fake)
    assert code == 0 and len(fake.requests) == 1
    assert doc["provider_request_sent"] is True and doc["attempts"] == 1
    assert (doc["status"], doc["error_category"], doc["provider_reason"]) == (
        "completed",
        None,
        None,
    )
    assert (doc["provider"], doc["request_model"], doc["response_model"]) == (
        "gcp.gemini",
        MODEL,
        MODEL,
    )
    assert doc["tokens"] == {
        "input": 20,
        "output": 32,
        "cache_read_input": 4,
        "reasoning_output": 30,
    }
    assert doc["provider_usage"] == {
        "prompt": 20,
        "candidates": 2,
        "thoughts": 30,
        "cached_content": 4,
        "tool_use_prompt": None,
        "total": 52,
    }
    assert doc["usage_check"] == "consistent"
    cost = doc["cost"]
    assert cost["status"] == "unknown" and cost["usage_source"] == "provider_reported"
    assert cost["billed_amount_usd"] is None
    assert cost["paid_tier_list_price_estimate_usd"] is None
    assert (
        cost["billing_tier"] == "free" and cost["billing_tier_checked_on"] == NOW.date().isoformat()
    )
    assert "not verified with Google" in cost["billing_tier_basis"]
    assert cost["ledger_cost_status"] == "unknown (price_missing)"
    assert doc["session"] == {
        "requests_started": 1,
        "max_requests": 1,
        "physical_attempts": 1,
        "halted": None,
    }
    assert doc["telemetry"]["flushed"] is True and doc["telemetry"]["spans_exported"] == 2
    tid = doc["trace_id"]
    assert doc["dashboard_url"] == f"http://127.0.0.1:3001/traces/{tid}?project=7"
    assert doc["api_path"] == f"/api/v1/traces/{tid}"

    spans = stub.spans()
    assert len(spans) == 2
    (root,) = [s for s in spans if s["name"] == live_gemini.ROOT_SPAN]
    (child,) = [s for s in spans if s["kind"] == "CLIENT"]
    assert root["trace_id"] == child["trace_id"] == tid
    assert child["parent_span_id"] == root["span_id"]
    assert child["attributes"]["gen_ai.provider.name"] == "gcp.gemini"
    assert child["attributes"]["llminsight.provenance"] == "synthetic"
    assert fake.request_json(0)["contents"][0]["parts"] == [{"text": live_gemini.INPUT_TEXT}]
    assert fake.request_json(0)["generationConfig"] == {"maxOutputTokens": 256}


@pytest.mark.parametrize(
    ("step", "exit_code", "status", "category", "reason", "tokens", "check"),
    [
        (g_ok(finish="MAX_TOKENS"), 0, "incomplete", None, "MAX_TOKENS", True, "consistent"),
        (g_ok(candidates=[candidate("CONTINUATION", continuationToken="U1lO")]), 0,
         "incomplete", None, "CONTINUATION", True, "consistent"),
        (g_ok(finish="SAFETY"), 1, "blocked", "content_blocked", "SAFETY", True, "consistent"),
        (GStep(200, {"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"},
                     "usageMetadata": {"promptTokenCount": 9, "totalTokenCount": 9}}),
         1, "blocked", "content_blocked", "PROHIBITED_CONTENT", True, "consistent"),
        (GStep(200, {}), 1, "failed", "empty_response", None, False, "missing"),
        (g_ok(usage=None), 0, "completed", None, None, False, "missing"),
        (g_ok(usage={"promptTokenCount": 3, "totalTokenCount": 77}), 0, "completed", None,
         None, False, "inconsistent"),
        (g_fail(429, "RESOURCE_EXHAUSTED"), 1, "error", "rate_limited", "RESOURCE_EXHAUSTED",
         False, "missing"),
        (g_fail(402, "PAYMENT_REQUIRED"), 1, "error", "payment_required", "PAYMENT_REQUIRED",
         False, "missing"),
        (g_fail(403, "PERMISSION_DENIED"), 1, "error", "permission_denied",
         "PERMISSION_DENIED", False, "missing"),
        (g_fail(400, "FAILED_PRECONDITION"), 1, "error", "precondition_failed",
         "FAILED_PRECONDITION", False, "missing"),
        (g_fail(401, "UNAUTHENTICATED"), 1, "error", "authentication_failed",
         "UNAUTHENTICATED", False, "missing"),
        (g_fail(500, "INTERNAL"), 1, "error", "provider_error", "INTERNAL", False, "missing"),
        (GStep(raise_=httpx.ReadTimeout), 1, "error", "timeout", None, False, "missing"),
        (GStep(raise_=httpx.ConnectError), 1, "error", "connection_error", None, False,
         "missing"),
        (GStep(200, raw=b"<html>SYNTHETIC</html>", content_type="text/html"), 1, "error",
         "malformed_response", None, False, "missing"),
    ],
)  # fmt: skip
def test_outcomes_are_reported_as_they_are_and_never_resubmitted(
    stub, step, exit_code, status, category, reason, tokens, check
):
    """No retry, no automatic continuation, no fallback to another model or a paid
    project: whatever happens, exactly one request was sent (the spare scripted answers
    are never requested)."""
    fake = FakeGemini([step, g_ok(), g_ok()])
    code, doc = execute(stub, fake)
    assert len(fake.requests) == 1
    assert {str(r.url) for r in fake.requests} == {live_gemini.ENDPOINT.format(model=MODEL)}
    assert code == exit_code
    assert (doc["status"], doc["error_category"], doc["provider_reason"]) == (
        status,
        category,
        reason,
    )
    assert (doc["tokens"] is not None) is tokens and doc["usage_check"] == check
    assert doc["cost"]["status"] == "unknown" and doc["cost"]["billed_amount_usd"] is None
    assert doc["provider_request_sent"] is True and doc["attempts"] == 1
    spans = stub.spans()
    assert len(spans) == 2 and len({s["trace_id"] for s in spans}) == 1


def test_the_runner_has_no_retry_fallback_or_openai_path():
    source = Path(live_gemini.__file__).read_text(encoding="utf-8")
    assert "import openai" not in source and "chat.completions" not in source
    for word in ("while ", "for attempt", "retry_options=", "fallback_model"):
        assert word not in source
