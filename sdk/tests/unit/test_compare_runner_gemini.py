"""Comparison runner, Gemini path (ADR-49), fully offline.

The runner's Gemini path is the shared `live_gemini.run_single_request`: the real Free Tier
policy, session and runner client (`_default_client`) with its ADR-34 checks run, and only
`httpx.HTTPTransport.handle_request` is replaced by a SYNTHETIC responder (`wire`). The Gemini
guard has no execution flag: its lock is the Free Tier evidence window, so the tests use a clock
inside the window (`NOW`) and one after it (`STALE`). No test sends a provider request.
"""

from __future__ import annotations

import io
import json
import sys
import threading
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from llm_insight_sdk import gemini_live, groq_live
from tests.conftest import DUMMY_KEY, PROMPT_SENTINEL
from tests.gemini_fixtures import (
    ERROR_SENTINEL,
    FAKE_GEMINI_KEY,
    MODEL,
    OUTPUT_SENTINEL,
    THOUGHT_SENTINEL,
    error_body,
    gemini_body,
)
from tests.openai_fixtures import block_network
from tests.unit.test_compare_runner import GOOD, SUMMARY, TICKET, USAGE
from tests.unit.test_gemini_live_guard import gemini_env

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))  # demo_app lives at the repository root, outside the SDK

from demo_app import comparison_scenarios as cs  # noqa: E402
from demo_app import live_compare as runner  # noqa: E402

NOW = gemini_live.FREE_TIER_VERIFIED_AT + timedelta(hours=2)
STALE = gemini_live.FREE_TIER_VERIFIED_AT + gemini_live.FREE_TIER_MAX_AGE + timedelta(minutes=1)
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
SECRETS = (FAKE_GEMINI_KEY, DUMMY_KEY, PROMPT_SENTINEL, THOUGHT_SENTINEL, ERROR_SENTINEL)


@pytest.fixture(autouse=True)
def offline() -> Iterator[None]:
    attempts: list[str] = []
    with block_network(attempts):
        yield
    assert attempts == [], f"a test tried to reach the network: {attempts}"


@pytest.fixture(autouse=True)
def own_slot(monkeypatch) -> None:
    monkeypatch.setattr(groq_live, "_PROCESS_SLOT", threading.Lock())


class Wire(list[httpx.Request]):
    """Requests that reached the transport, and scripted replies (default: none, so a request
    without a reply fails the test)."""

    def __init__(self) -> None:
        super().__init__()
        self.replies: list[httpx.Response | type[BaseException]] = []


@pytest.fixture
def wire(monkeypatch) -> Wire:
    sent = Wire()

    def handle_request(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        request.read()
        sent.append(request)
        if not sent.replies:
            raise AssertionError("more physical requests than scripted replies")
        reply = sent.replies.pop(0)
        if isinstance(reply, type):
            raise reply("SYNTHETIC transport failure", request=request)
        return reply

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle_request)
    return sent


def answer(text: str, finish: str = "STOP", **kw: Any) -> httpx.Response:
    parts = [{"text": THOUGHT_SENTINEL, "thought": True}, {"text": text}]
    body = gemini_body(
        candidates=[
            {"content": {"parts": parts, "role": "model"}, "index": 0, "finishReason": finish}
        ],
        **kw,
    )
    return httpx.Response(200, json=body)


def compare_env(endpoint: str, at=NOW, **overrides: str | None) -> dict[str, str]:
    env = gemini_env(
        LLMI_LIVE_MAX_OUTPUT_TOKENS="1024",
        LLMI_LIVE_MAX_INPUT_TOKENS="1024",
        LLMI_GEMINI_TIER_CHECKED_ON=at.date().isoformat(),
    )
    env.update(
        {
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": endpoint,
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": f"X-Project-Key={DUMMY_KEY}",
            "OTEL_SERVICE_NAME": "live-compare-gemini-unit-test",
            "LLMI_SDK_SCHEDULE_DELAY_MILLIS": "50",
            "LLMI_DASHBOARD_PROJECT_ID": "7",
        }
    )
    for name, value in overrides.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return env


def run(argv: list[str], env: dict[str, str], now=NOW) -> tuple[int, dict[str, Any]]:
    out = io.StringIO()
    code = runner.main(argv, env, now=now, out=out)
    text = out.getvalue()
    for secret in (*SECRETS, *GOOD.values(), OUTPUT_SENTINEL):
        assert secret not in text
    for scenario in cs.SCENARIOS.values():
        assert scenario.fixture not in text and scenario.task not in text
    (line,) = text.splitlines()
    doc = json.loads(line)
    assert doc["schema"] == "llminsight.demo.compare.v1"
    return code, doc


def execute(stub, scenario: str = TICKET, **kw: Any) -> tuple[int, dict[str, Any]]:
    env = kw.pop("env", None) or compare_env(stub.endpoint)
    return run(["--provider", "gemini", "--scenario", scenario, "--execute"], env, **kw)


def codes(doc: dict[str, Any]) -> set[str]:
    return {r["code"] for r in doc["refusals"]}


# ---------------------------------------------------------------- as shipped: the evidence lock
def test_as_shipped_the_expired_free_tier_evidence_refuses_every_execution(stub, wire):
    code, doc = execute(stub, env=compare_env(stub.endpoint, at=STALE), now=STALE)
    assert code == 2 and wire == [] and stub.spans() == []
    assert codes(doc) == {"free_tier_evidence_stale"}
    assert doc["provider_request_state"] == "not_sent"


def test_the_shipped_evidence_is_the_one_read_on_2026_10_28():
    assert gemini_live.FREE_TIER_VERIFIED_AT.isoformat() == "2026-10-28T13:00:00+00:00"
    assert gemini_live.FREE_TIER_MAX_AGE.days == 14
    assert list(gemini_live.FREE_TIER_MODELS) == [MODEL]


# ---------------------------------------------------------------- dry run
def test_the_registry_now_executes_gemini(stub):
    code, doc = run(["--provider", "gemini", "--scenario", USAGE], compare_env(stub.endpoint))
    assert code == 0 and doc["provider_selection"] == {
        "name": "gemini",
        "provider": "gcp.gemini",
        "available": True,
    }
    assert doc["execution_allowed"] is True and doc["refusals"] == []
    plan = doc["plan"]
    assert (plan["provider"], plan["model"]) == ("gcp.gemini", MODEL)
    assert plan["billing_tier"] == "free" and "not verified" in plan["billing_tier_basis"]
    assert plan["max_output_tokens"] == 1024 and plan["paid_fallback"] is False
    constraints = doc["constraints"]
    assert constraints["max_output_tokens"] == 1024 and constraints["retries"] == 0
    assert constraints["free_tier_evidence_max_age_days"] == 14
    for flag in ("model_fallback", "provider_fallback", "paid_plan", "streaming"):
        assert constraints[flag] is False


# ---------------------------------------------------------------- one request
@pytest.mark.parametrize("scenario", [TICKET, USAGE, SUMMARY])
def test_one_request_with_the_shared_prompt_and_metadata_only_output(stub, wire, scenario):
    wire.replies = [answer(GOOD[scenario])]
    code, doc = execute(stub, scenario)
    assert code == 0, doc
    (request,) = wire
    assert str(request.url) == URL and request.method == "POST"
    assert request.headers["x-goog-api-key"] == FAKE_GEMINI_KEY
    body = json.loads(request.content)
    reviewed = cs.SCENARIOS[scenario]
    assert body["contents"] == [{"parts": [{"text": cs.compose_input(reviewed)}], "role": "user"}]
    assert body["generationConfig"]["maxOutputTokens"] == cs.UNIFORM_OUTPUT_TOKENS == 1024
    assert "tools" not in body
    assert doc["provider_request_state"] == "sent" and doc["provider_request_sent"] is True
    provider = doc["provider"]
    assert (provider["provider"], provider["outcome"], provider["attempts"]) == (
        "gcp.gemini",
        "completed",
        1,
    )
    assert doc["usage"]["tokens"]["input"] == 20
    cost = doc["usage"]["cost"]
    assert cost["status"] == "unknown" and cost["billed_amount_usd"] is None
    assert cost["billing_tier"] == "free"
    assert doc["validation"]["status"] == "passed"
    assert doc["export"]["outcome"] == "confirmed" and doc["free_plan_violation"] is None
    assert doc["comparison"]["prompt_version"] == reviewed.prompt_version


def test_the_exported_trace_is_grouped_by_provider_model_and_scenario(stub, wire):
    wire.replies = [answer(GOOD[TICKET])]
    code, doc = execute(stub)
    assert code == 0
    spans = stub.spans()
    (client,) = [s for s in spans if s["kind"] == "CLIENT"]
    (root,) = [s for s in spans if s["name"] == runner.ROOT_SPAN]
    attrs = client["attributes"]
    assert attrs["gen_ai.provider.name"] == "gcp.gemini"
    assert attrs["gen_ai.request.model"] == MODEL
    assert attrs["gen_ai.prompt.version"] == "cmp-v1.ticket-triage"
    assert client["parent_span_id"] == root["span_id"]
    assert client["trace_id"] == doc["correlation"]["trace_id"]
    text = json.dumps(spans)
    for secret in (*SECRETS, OUTPUT_SENTINEL, cs.compose_input(cs.SCENARIOS[TICKET])):
        assert secret not in text


def test_a_second_execution_in_the_process_is_refused_and_the_slot_is_shared(stub, wire):
    wire.replies = [answer(GOOD[TICKET]), answer(GOOD[TICKET])]
    assert execute(stub)[0] == 0
    code, doc = execute(stub)
    assert code == 2 and "request_limit_reached" in codes(doc) and len(wire) == 1
    with pytest.raises(groq_live.LiveRefusal):  # Groq and OpenRouter cannot send either
        groq_live.FreePlanSession({})._reserve()


@pytest.mark.parametrize(
    ("reply", "category", "state"),
    [
        (httpx.Response(429, json=error_body(429, "RESOURCE_EXHAUSTED")), "rate_limited", "sent"),
        (httpx.Response(503, json=error_body(503, "UNAVAILABLE")), "provider_error", "sent"),
        (httpx.ReadTimeout, "timeout", "possibly_sent"),
        (httpx.ConnectError, "connection_error", "possibly_sent"),
    ],
    ids=["429", "503", "timeout", "connect"],
)
def test_a_failure_is_reported_once_never_retried_and_never_claimed_unsent(
    stub, wire, reply, category, state
):
    wire.replies = [reply, answer(GOOD[TICKET])]
    code, doc = execute(stub)
    assert code == 1 and len(wire) == 1
    assert doc["provider"]["error_category"] == category
    assert doc["provider_request_state"] == state
    assert doc["usage"]["tokens"] is None and doc["usage"]["cost"]["status"] == "unknown"


def test_an_incomplete_answer_is_not_a_success(stub, wire):
    wire.replies = [answer(GOOD[TICKET], finish="MAX_TOKENS")]
    code, doc = execute(stub)
    assert code == 3 and doc["provider"]["outcome"] == "incomplete"
    assert doc["validation"]["status"] == "not_evaluated"


# ---------------------------------------------------------------- refusals before any send
@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"LLMI_LIVE_MODELS": f"{MODEL},{MODEL}-other"}, "model_not_free_tier_verified"),
        ({"SSLKEYLOGFILE": "SYNTHETIC-keys.log"}, "client_env_override"),  # R123 (2)
        ({"SSL_CERT_FILE": "SYNTHETIC.pem"}, "client_env_override"),
        ({"LLMI_GEMINI_TIER_CHECKED_ON": "2026-W44-3"}, "free_tier_unverified"),  # R123 (1)
        ({"LLMI_GEMINI_BILLING_TIER": "paid"}, "billing_tier_not_free"),
        ({"GOOGLE_API_KEY": "SYNTHETIC"}, "client_env_override"),
        ({"LLMI_LIVE_MAX_OUTPUT_TOKENS": "512"}, "output_limit_exceeded"),
        ({"LLMI_LIVE_MAX_RETRIES": "1"}, "setting_invalid"),
        ({"LLMI_LIVE_CONSENT": "groq-free-plan-single-call"}, "consent_missing"),
    ],
)
def test_each_refusal_blocks_execution_and_sends_nothing(stub, wire, overrides, code):
    env = compare_env(stub.endpoint, **overrides)
    dry, dry_doc = run(["--provider", "gemini", "--scenario", USAGE], env)
    rc, doc = run(["--provider", "gemini", "--scenario", USAGE, "--execute"], env)
    assert dry == 0 and code in codes(dry_doc) and dry_doc["execution_allowed"] is False
    assert rc == 2 and code in codes(doc) and wire == []


def test_two_allowlisted_models_are_refused_as_a_fallback_list(stub, wire, monkeypatch):
    other = gemini_live.FreeTierModel(f"{MODEL}-other", "stable", ("x",))
    models = {**gemini_live.FREE_TIER_MODELS, other.model: other}
    monkeypatch.setattr(gemini_live, "FREE_TIER_MODELS", models)
    code, doc = execute(
        stub, env=compare_env(stub.endpoint, LLMI_LIVE_MODELS=f"{MODEL},{other.model}")
    )
    assert code == 2 and codes(doc) == {"model_list_not_single"} and wire == []


def test_the_policy_is_read_again_immediately_before_the_send(stub, wire, monkeypatch):
    """A clock that passes the evidence window between the plan and the send refuses."""
    times = iter([NOW, STALE])
    monkeypatch.setattr(runner, "_fixed_clock", lambda now: lambda: next(times))
    code, doc = execute(stub)
    assert code == 2 and "free_tier_evidence_stale" in codes(doc) and wire == []


def test_a_groq_or_openrouter_run_never_loads_google_genai():
    import subprocess

    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import demo_app.live_compare as r; "
        "r.constraints_doc(r.GROQ); r.constraints_doc(r.OPENROUTER); "
        "print([m for m in sys.modules if m.startswith('google.genai')])"
    )
    result = subprocess.run(  # noqa: S603 - this interpreter, a fixed script
        [sys.executable, "-c", code, str(REPO)], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
