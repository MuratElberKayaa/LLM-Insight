"""Comparison runner `demo_app.live_compare` (Phase 8A-4), fully offline.

The backend is the local OTLP stub. The provider is SYNTHETIC: the real audited client and the real
Free Plan guard run, and only `httpx.HTTPTransport.handle_request` is replaced (`wire`), as in the
guard's own tests. `offline` fails any test that attempts a non-loopback connection. The execution
lock and the evidence are lifted only by the `released` fixture; tests without it run the module
exactly as shipped. No test sends a provider request.
"""

from __future__ import annotations

import dataclasses
import io
import json
import logging
import os
import re
import subprocess
import sys
import threading
from collections.abc import Iterator, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from llm_insight_sdk import groq_adapter, groq_live
from llm_insight_sdk.groq_live import FreePlanModel
from tests.conftest import DUMMY_KEY, PROMPT_SENTINEL, free_port
from tests.groq_fixtures import (
    ENDPOINT,
    ERROR_SENTINEL,
    FAKE_GROQ_KEY,
    GENERATION_SENTINEL,
    MODEL,
    OUTPUT_SENTINEL,
    REASONING_SENTINEL,
    FakeGroq,
    Step,
    fail,
    ok,
)
from tests.openai_fixtures import block_network
from tests.unit.test_groq_live_guard import ENV as GUARD_ENV
from tests.unit.test_groq_live_guard import NOW

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))  # demo_app lives at the repository root, outside the SDK

from demo_app import comparison_scenarios as cs  # noqa: E402
from demo_app import live_compare as runner  # noqa: E402

TICKET, USAGE, SUMMARY = "cmp-ticket-triage", "cmp-json-aggregate", "cmp-release-summary"
SECRETS = (
    FAKE_GROQ_KEY,
    DUMMY_KEY,
    PROMPT_SENTINEL,
    OUTPUT_SENTINEL,
    REASONING_SENTINEL,
    ERROR_SENTINEL,
    GENERATION_SENTINEL,
)
GOOD = {
    TICKET: json.dumps({"category": "login", "priority": "high", "reason": "Sign-in fails."}),
    USAGE: json.dumps(
        {
            "total_requests": 1000,
            "highest_error_day": "Wed",
            "error_rate_percent": 3.0,
            "observation": "Errors peaked on Wednesday.",
        }
    ),
    # The output sentinel inside a passing answer: it must still never leave the process.
    SUMMARY: f"{OUTPUT_SENTINEL} Boards load faster. Known issue: large imports can stall.",
}
TRACE_ID = re.compile(r"[0-9a-f]{32}")
# Since the Phase 8G evidence-only change the shipped Groq evidence and model are released; the
# execution lock still refuses every request.
SHIPPED_REFUSALS = {"real_execution_disabled"}


@pytest.fixture(autouse=True)
def offline() -> Iterator[None]:
    attempts: list[str] = []
    with block_network(attempts):
        yield
    assert attempts == [], f"a test tried to reach the network: {attempts}"


@pytest.fixture(autouse=True)
def own_slot(monkeypatch) -> None:
    """Each test gets a process slot of its own; the shipped slot is never touched."""
    monkeypatch.setattr(groq_live, "_PROCESS_SLOT", threading.Lock())


@pytest.fixture
def released(monkeypatch) -> None:
    """Test-only release of both locks, exactly as the guard's tests do."""
    monkeypatch.setattr(groq_adapter, "REAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(groq_live, "FREE_PLAN_EVIDENCE_VERIFIED_AT", NOW - timedelta(days=1))
    monkeypatch.setattr(
        groq_live,
        "FREE_PLAN_MODELS",
        {MODEL: FreePlanModel(MODEL, ("https://example.invalid/synthetic",))},
    )


@pytest.fixture
def wire(monkeypatch) -> FakeGroq:
    fake = FakeGroq([])
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", lambda self, request: fake.handler(request)
    )
    return fake


class NeverBuilt:
    """An HTTP client factory that must not be reached."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> httpx.Client:
        self.calls += 1
        raise AssertionError("no provider client may be built")


class Recording(Mapping[str, str]):
    """An environment that records every read."""

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)
        self.reads = 0

    def __getitem__(self, key: str) -> str:
        self.reads += 1
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        self.reads += 1
        return iter(self._values)

    def __len__(self) -> int:
        self.reads += 1
        return len(self._values)


def runner_env(endpoint: str, **changes: str | None) -> dict[str, str]:
    env = dict(GUARD_ENV)
    env.update(
        {
            "LLMI_LIVE_MAX_OUTPUT_TOKENS": "1024",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": endpoint,
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": f"X-Project-Key={DUMMY_KEY}",
            "OTEL_SERVICE_NAME": "live-compare-unit-test",
            "LLMI_SDK_SCHEDULE_DELAY_MILLIS": "50",
            "LLMI_DASHBOARD_PROJECT_ID": "7",
        }
    )
    for name, value in changes.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return env


def run(argv: list[str], env: Mapping[str, str], **kw: Any) -> tuple[int, dict[str, Any], str]:
    out = io.StringIO()
    kw.setdefault("now", NOW)
    code = runner.main(argv, env, out=out, **kw)
    text = out.getvalue()
    for secret in SECRETS:
        assert secret not in text
    for scenario in cs.SCENARIOS.values():
        assert scenario.fixture not in text and scenario.task not in text
    for answer in GOOD.values():
        assert answer not in text
    (line,) = text.splitlines()  # exactly one JSON line
    doc = json.loads(line)
    assert doc["schema"] == "llminsight.demo.compare.v1"
    return code, doc, text


def execute(stub, scenario: str = SUMMARY, **kw: Any) -> tuple[int, dict[str, Any], str]:
    env = kw.pop("env", None) or runner_env(stub.endpoint)
    return run(["--provider", "groq", "--scenario", scenario, "--execute"], env, **kw)


def codes(doc: dict[str, Any]) -> set[str]:
    return {r["code"] for r in doc["refusals"]}


def assert_exported_without_content(stub) -> None:
    for received in stub.received:
        for secret in (*SECRETS, *(cs.compose_input(s) for s in cs.SCENARIOS.values())):
            assert secret.encode() not in received.body
            assert secret.encode() not in received.raw_body
        assert all(secret not in json.dumps(received.headers) for secret in SECRETS[:1])


# ---------------------------------------------------------------- as shipped: nothing is possible
@pytest.mark.parametrize("scenario", [TICKET, USAGE, SUMMARY])
def test_as_shipped_the_dry_run_reports_the_locks_and_sends_nothing(stub, wire, scenario):
    never = NeverBuilt()
    code, doc, _ = run(
        ["--provider", "groq", "--scenario", scenario],
        runner_env(stub.endpoint),
        http_factory=never,
    )
    assert code == 0 and never.calls == 0 and wire.requests == []
    assert doc["mode"] == "dry_run" and doc["provider_request_state"] == "not_sent"
    assert doc["provider_request_sent"] is False
    assert (doc["policy_eligible"], doc["execution_allowed"]) == (False, False)
    assert doc["execution_ready"] is True
    assert codes(doc) >= SHIPPED_REFUSALS
    assert doc["constraints"]["real_execution_enabled"] is False
    assert doc["constraints"]["free_plan_models_released"] == 1  # evidence only (8G)
    assert doc["session"] is None and doc["provider"] is None and doc["export"] is None
    assert stub.spans() == []  # the readiness probe is an empty request


def test_as_shipped_execute_is_refused_before_any_client_exists(stub, wire, monkeypatch):
    def forbidden() -> httpx.Client:
        raise AssertionError("the audited client must not be built")

    monkeypatch.setattr(groq_adapter, "build_groq_http_client", forbidden)
    code, doc, _ = execute(stub, TICKET, http_factory=None)
    assert code == 2 and wire.requests == [] and stub.spans() == []
    assert doc["mode"] == "execute" and codes(doc) >= SHIPPED_REFUSALS
    assert doc["provider_request_state"] == "not_sent" and doc["session"] is None


def test_as_shipped_the_constants_are_still_locked():
    assert groq_adapter.REAL_EXECUTION_ENABLED is False
    assert list(groq_live.FREE_PLAN_MODELS) == [MODEL]
    assert groq_live.FREE_PLAN_EVIDENCE_VERIFIED_AT is not None


# ---------------------------------------------------------------- released: the dry run
@pytest.mark.usefixtures("released")
def test_a_released_dry_run_is_ready_and_still_sends_nothing(stub, wire):
    never = NeverBuilt()
    code, doc, _ = run(
        ["--provider", "groq", "--scenario", USAGE], runner_env(stub.endpoint), http_factory=never
    )
    assert code == 0 and never.calls == 0 and wire.requests == []
    assert (doc["policy_eligible"], doc["execution_ready"], doc["execution_allowed"]) == (
        True,
        True,
        True,
    )
    assert doc["refusals"] == []
    plan = doc["plan"]
    assert plan["model"] == MODEL and plan["endpoint"] == ENDPOINT
    assert (plan["max_requests"], plan["max_retries"], plan["max_concurrency"]) == (1, 0, 1)
    assert plan["max_output_tokens"] == 1024 and plan["paid_fallback"] is False
    assert plan["plan"] == "free" and "not verified" in plan["plan_basis"]
    scenario = doc["scenario"]
    assert scenario["prompt_version"] == "cmp-v1.json-aggregate"
    assert scenario["input_utf8_bytes"] == 661 and scenario["input_token_bound"] == 725
    assert scenario["input_sha256"] == cs.SCENARIOS[USAGE].input_sha256
    comparison = doc["comparison"]
    assert comparison["root_span"] == "demo.live-compare" and comparison["project_id"] == 7
    assert comparison["compare_group_key"] == [
        "provider",
        "requested_model",
        "response_model",
        "prompt_version",
    ]
    assert "create-key --project llm-insight-compare" in comparison["project_requirement"]


# ---------------------------------------------------------------- released: one request
@pytest.mark.usefixtures("released")
@pytest.mark.parametrize("scenario", [TICKET, USAGE, SUMMARY])
def test_one_completed_request_with_metadata_only_output(stub, wire, scenario):
    wire.steps = [ok(content=GOOD[scenario])]
    code, doc, _ = execute(stub, scenario)
    assert code == 0, doc
    assert len(wire.requests) == 1
    request = wire.requests[0]
    assert str(request.url) == ENDPOINT and request.method == "POST"
    assert request.headers["authorization"] == f"Bearer {FAKE_GROQ_KEY}"
    assert json.loads(request.content) == {
        "model": MODEL,
        "messages": [{"role": "user", "content": cs.compose_input(cs.SCENARIOS[scenario])}],
        "max_completion_tokens": 1024,
        "stream": False,
        "include_reasoning": False,
    }
    assert doc["provider_request_sent"] is True and doc["provider_request_state"] == "sent"
    assert doc["provider"]["outcome"] == "completed" and doc["provider"]["attempts"] == 1
    assert doc["provider"]["response_model"] == MODEL
    assert doc["validation"]["status"] == "passed"
    assert doc["session"] == {
        "requests_started": 1,
        "max_requests": 1,
        "physical_attempts": 1,
        "provider_request_state": "sent",
        "halted": None,
    }
    assert doc["free_plan_violation"] is None
    assert doc["usage"]["tokens"] == {
        "input": 20,
        "output": 32,
        "cache_read_input": None,
        "cache_write_input": None,  # one token schema for every provider; unknown, never 0
        "reasoning_output": None,
    }
    cost = doc["usage"]["cost"]
    assert cost["status"] == "unknown" and cost["billed_amount_usd"] is None
    assert cost["list_price_estimate_usd"] is None
    assert cost["ledger_cost_status"] == "unknown (price_missing)"
    assert doc["export"]["outcome"] == "confirmed" and doc["export"]["spans_exported"] == 2
    tid = doc["correlation"]["trace_id"]
    assert TRACE_ID.fullmatch(tid)
    assert doc["correlation"]["dashboard_url"] == f"http://127.0.0.1:3001/traces/{tid}?project=7"
    assert doc["correlation"]["api_path"] == f"/api/v1/traces/{tid}?project=7"


@pytest.mark.usefixtures("released")
def test_the_exported_trace_is_the_one_the_dashboard_groups_and_holds_no_content(stub, wire):
    wire.steps = [ok(content=GOOD[SUMMARY])]
    code, doc, _ = execute(stub, SUMMARY)
    assert code == 0
    spans = stub.spans()
    assert len(spans) == 2 and {s["trace_id"] for s in spans} == {doc["correlation"]["trace_id"]}
    (root,) = [s for s in spans if s["name"] == "demo.live-compare"]
    (child,) = [s for s in spans if s["kind"] == "CLIENT"]
    assert root["kind"] == "INTERNAL" and child["parent_span_id"] == root["span_id"]
    assert not any(key.startswith("gen_ai.") for key in root["attributes"])
    attrs = child["attributes"]
    assert attrs["gen_ai.provider.name"] == "groq"
    assert attrs["gen_ai.request.model"] == MODEL and attrs["gen_ai.response.model"] == MODEL
    assert attrs["gen_ai.prompt.name"] == "cmp.release-summary"
    assert attrs["gen_ai.prompt.version"] == "cmp-v1.release-summary"
    assert attrs["gen_ai.request.max_tokens"] == 1024
    assert attrs["llminsight.provenance"] == "real"
    assert attrs["server.address"] == "api.groq.com"
    assert child["resource"]["service.name"] == "live-compare-unit-test"
    # The backend contract test (backend/tests/unit/test_comparison_contract.py) ingests this key
    # set: the shared fixture's attributes with the scenario's prompt identity.
    fixture = json.loads(
        (REPO / "docs" / "fixtures" / "groq_client_span.v1.json").read_text(encoding="utf-8")
    )
    assert set(attrs) == set(fixture["attributes"])
    assert_exported_without_content(stub)


@pytest.mark.usefixtures("released")
@pytest.mark.parametrize(
    ("step", "status", "category", "state", "sent"),
    [
        (fail(429), "error", "rate_limited", "sent", True),
        (fail(500), "error", "provider_error", "sent", True),
        (fail(401), "error", "authentication_failed", "sent", True),
        (Step(raise_=httpx.ReadTimeout), "error", "timeout", "possibly_sent", None),
        (
            Step(raise_=httpx.RemoteProtocolError),
            "error",
            "connection_error",
            "possibly_sent",
            None,
        ),
        (Step(raise_=httpx.ConnectError), "error", "connection_error", "not_sent", False),
        (Step(raise_=httpx.ConnectTimeout), "error", "timeout", "not_sent", False),
        (ok(content=""), "failed", "empty_response", "sent", True),
        (ok(finish="content_filter"), "blocked", "content_blocked", "sent", True),
    ],
)
def test_a_failure_is_reported_once_and_never_retried(
    stub, wire, step, status, category, state, sent
):
    wire.steps = [step]
    code, doc, _ = execute(stub, TICKET)
    assert code == 1 and len(wire.requests) == 1  # no retry, no fallback
    assert doc["provider"]["outcome"] == status and doc["provider"]["error_category"] == category
    assert doc["provider_request_state"] == state and doc["provider_request_sent"] is sent
    assert doc["session"]["physical_attempts"] == 1 and doc["session"]["requests_started"] == 1
    assert doc["validation"]["status"] == "not_evaluated"
    # A response that arrived keeps its reported usage; an HTTP or transport error has none.
    assert (doc["usage"]["tokens"] is None) is (status == "error")
    assert doc["usage"]["cost"]["status"] == "unknown"
    assert doc["usage"]["cost"]["billed_amount_usd"] is None


@pytest.mark.usefixtures("released")
def test_an_incomplete_answer_is_not_a_success_and_is_not_continued(stub, wire):
    wire.steps = [ok(finish="length", content="partial")]
    code, doc, _ = execute(stub, USAGE)
    assert code == 3 and len(wire.requests) == 1
    assert doc["provider"]["outcome"] == "incomplete"
    assert doc["validation"] == {
        "status": "not_evaluated",
        "reason": "provider_incomplete",
        "rules": [],
        "fence_stripped": False,
    }


@pytest.mark.usefixtures("released")
def test_a_completed_answer_that_fails_validation_exits_3(stub, wire):
    wire.steps = [ok(content='{"category": "billing", "priority": "low", "reason": "x"}')]
    code, doc, _ = execute(stub, TICKET)
    assert code == 3 and doc["provider"]["outcome"] == "completed"
    assert doc["validation"]["status"] == "failed"
    assert {"rule": "expected_category", "passed": False} in doc["validation"]["rules"]


@pytest.mark.usefixtures("released")
def test_an_unconfirmed_export_exits_3(stub, wire):
    wire.steps = [ok(content=GOOD[SUMMARY])]

    def rejecting_backend() -> httpx.Client:
        stub.behaviour.status = 401  # the readiness probe passed; the export is now rejected
        return groq_adapter.build_groq_http_client()

    code, doc, _ = execute(stub, SUMMARY, http_factory=rejecting_backend)
    assert code == 3 and doc["provider"]["outcome"] == "completed"
    assert doc["export"]["outcome"] == "failed"


# ---------------------------------------------------------------- post-call halts
@pytest.mark.usefixtures("released")
@pytest.mark.parametrize(
    ("step", "halt"),
    [
        (ok(content=GOOD[TICKET], service_tier="flex"), "service_tier_not_free_plan"),
        (ok(content=GOOD[TICKET], model="other-org/other-model"), "response_model_mismatch"),
        (fail(402), "paid_plan_indicator"),
        (fail(400, "blocked_api_access"), "paid_plan_indicator"),
        (
            ok(
                content=GOOD[TICKET],
                usage={"prompt_tokens": 2000, "completion_tokens": 1, "total_tokens": 2001},
            ),
            "usage_exceeded_bound",
        ),
    ],
)
def test_a_free_plan_violation_indicator_halts_with_exit_3_and_sends_nothing_more(
    stub, wire, step, halt
):
    wire.steps = [step]
    code, doc, _ = execute(stub, TICKET)
    assert code == 3 and len(wire.requests) == 1
    assert doc["free_plan_violation"] == halt and doc["session"]["halted"] == halt


# ---------------------------------------------------------------- one request per process
@pytest.mark.usefixtures("released")
def test_a_second_execution_in_the_same_process_is_refused(stub, wire):
    wire.steps = [ok(content=GOOD[TICKET]), ok(content=GOOD[TICKET])]
    first, _, _ = execute(stub, TICKET)
    second, doc, _ = execute(stub, TICKET)
    assert (first, second) == (0, 2) and len(wire.requests) == 1
    assert "request_limit_reached" in codes(doc)
    assert doc["provider_request_state"] == "not_sent"


@pytest.mark.usefixtures("released")
def test_a_failed_request_does_not_free_the_process_for_another(stub, wire):
    wire.steps = [Step(raise_=httpx.ConnectError), ok(content=GOOD[TICKET])]
    first, _, _ = execute(stub, TICKET)
    second, doc, _ = execute(stub, TICKET)
    assert (first, second) == (1, 2) and len(wire.requests) == 1
    assert "request_limit_reached" in codes(doc)


# ---------------------------------------------------------------- re-checked at the send
@pytest.mark.usefixtures("released")
def test_the_clock_is_read_again_by_the_guard_immediately_before_the_send(stub, wire):
    times = iter([NOW, NOW + timedelta(days=2)])
    code, doc, _ = execute(
        stub, TICKET, now=None, clock=lambda: next(times, NOW + timedelta(days=2))
    )
    assert code == 2 and wire.requests == []
    assert "free_plan_attestation_stale" in codes(doc)
    assert doc["session"]["requests_started"] == 0
    assert doc["provider_request_state"] == "not_sent"


@pytest.mark.usefixtures("released")
def test_a_client_changed_after_it_is_built_is_refused_before_the_send(stub, wire):
    def changed_later() -> httpx.Client:
        http = groq_adapter.build_groq_http_client()
        original = http.build_request

        def build(*args: Any, **kwargs: Any) -> httpx.Request:
            http.follow_redirects = True  # after the construction checks
            return original(*args, **kwargs)

        http.build_request = build  # type: ignore[method-assign]
        return http

    code, doc, _ = execute(stub, TICKET, http_factory=changed_later)
    assert code == 2 and wire.requests == []
    assert doc["provider_request_state"] == "not_sent"


# ---------------------------------------------------------------- the credential is bound
@pytest.mark.usefixtures("released")
def test_the_validated_credential_is_the_one_that_is_sent(stub, wire):
    """The run reads one copy of the environment: a later change cannot swap the key or add a
    route, and what the policy validated is exactly what the request carries."""
    env = runner_env(stub.endpoint)
    wire.steps = [ok(content=GOOD[TICKET])]

    def mutate_then_build() -> httpx.Client:
        env["GROQ_API_KEY"] = "synthetic-other-key-not-validated-0000"
        env["HTTPS_PROXY"] = "http://127.0.0.1:9"
        return groq_adapter.build_groq_http_client()

    code, doc, _ = execute(stub, TICKET, env=env, http_factory=mutate_then_build)
    assert code == 0 and len(wire.requests) == 1
    assert wire.requests[0].headers.get_list("authorization") == [f"Bearer {FAKE_GROQ_KEY}"]


@pytest.mark.usefixtures("released")
def test_the_key_is_read_as_the_policy_reads_it(stub, wire):
    wire.steps = [ok(content=GOOD[TICKET])]
    env = runner_env(stub.endpoint, GROQ_API_KEY=f"  {FAKE_GROQ_KEY}\t")
    code, _, _ = execute(stub, TICKET, env=env)
    assert code == 0
    assert wire.requests[0].headers.get_list("authorization") == [f"Bearer {FAKE_GROQ_KEY}"]


# ---------------------------------------------------------------- interruption
@pytest.mark.usefixtures("released")
def test_an_interruption_during_the_send_is_possibly_sent_and_never_resent(stub, wire):
    wire.steps = [Step(raise_=KeyboardInterrupt), ok(content=GOOD[TICKET])]
    code, doc, _ = execute(stub, TICKET)
    assert code == 1 and len(wire.requests) == 1
    assert doc["provider_request_state"] == "possibly_sent"
    assert doc["provider_request_sent"] is None
    assert doc["provider"]["outcome"] == "interrupted"
    assert doc["session"]["physical_attempts"] == 1


@pytest.mark.usefixtures("released")
def test_an_interruption_before_the_send_is_a_refusal(stub, wire):
    def interrupted() -> httpx.Client:
        raise KeyboardInterrupt

    code, doc, _ = execute(stub, TICKET, http_factory=interrupted)
    assert code == 2 and wire.requests == []
    assert codes(doc) == {"interrupted_before_send"}
    assert doc["provider_request_state"] == "not_sent"


@pytest.mark.usefixtures("released")
def test_a_failing_client_factory_is_a_refusal(stub, wire):
    def broken() -> httpx.Client:
        raise OSError("SYNTHETIC unreadable CA bundle")

    code, doc, _ = execute(stub, TICKET, http_factory=broken)
    assert code == 2 and wire.requests == [] and codes(doc) == {"client_construction_failed"}
    assert "SYNTHETIC" not in json.dumps(doc)


@pytest.mark.usefixtures("released")
def test_a_client_that_is_not_the_audited_shape_is_refused(stub, wire):
    code, doc, _ = execute(
        stub, TICKET, http_factory=lambda: httpx.Client(trust_env=False, follow_redirects=False)
    )
    assert code == 2 and wire.requests == [] and codes(doc) == {"client_not_allowed"}


# ---------------------------------------------------------------- providers
@pytest.mark.parametrize("mode", [[], ["--execute"]])
def test_a_provider_without_an_executable_path_is_refused_before_anything_is_read(
    stub, mode, monkeypatch
):
    """Since ADR-49 every registered provider is executable; the refusal path stays for any
    entry without one."""
    blocked = runner.ProviderEntry(
        "gemini", "gcp.gemini", runner.Refusal("provider_blocked", "SYNTHETIC block")
    )
    monkeypatch.setattr(runner, "PROVIDERS", {**runner.PROVIDERS, "gemini": blocked})
    env = Recording(runner_env(stub.endpoint))
    never = NeverBuilt()
    rc, doc, _ = run(["--provider", "gemini", "--scenario", TICKET, *mode], env, http_factory=never)
    assert rc == 2 and env.reads == 0 and never.calls == 0 and stub.attempts == 0
    assert codes(doc) == {"provider_blocked"}
    assert doc["provider_selection"]["available"] is False
    assert doc["scenario"] is None and doc["plan"] is None


def test_the_provider_registry_is_closed():
    assert set(runner.PROVIDERS) == {"groq", "gemini", "openrouter"}
    executable = [name for name, e in runner.PROVIDERS.items() if e.refusal is None]
    assert executable == ["groq", "gemini", "openrouter"]
    assert runner.PROVIDERS["groq"].executable is runner.GROQ
    assert runner.PROVIDERS["gemini"].executable is runner.GEMINI
    assert runner.PROVIDERS["openrouter"].executable is runner.OPENROUTER
    assert runner.GEMINI.run is runner.run_gemini_request  # the shared Gemini path, no gate copy
    with pytest.raises(TypeError):
        runner.PROVIDERS["other"] = runner.PROVIDERS["groq"]  # type: ignore[index]


def test_an_unknown_provider_or_scenario_is_never_echoed(stub):
    for argv in (
        ["--provider", PROMPT_SENTINEL, "--scenario", TICKET],
        ["--provider", "groq", "--scenario", PROMPT_SENTINEL],
    ):
        code, doc, text = run(argv, runner_env(stub.endpoint))
        assert code == 2 and PROMPT_SENTINEL not in text
        assert codes(doc) <= {"provider_unknown", "scenario_unknown"}


# ---------------------------------------------------------------- command line
@pytest.mark.parametrize(
    "argv",
    [
        ["--provider", "groq", "--scenario", TICKET, "--exec"],
        ["--provider", "groq", "--scenario", TICKET, "--execut"],
        ["--provider", "groq", "--provider", "groq", "--scenario", TICKET],
        ["--provider", "groq", "--scenario", TICKET, "--scenario", USAGE],
        ["--provider", "groq"],
        ["--scenario", TICKET],
        ["--provider", "groq", "--scenario", TICKET, "--model", MODEL],
        ["--provider", "groq", "--scenario", TICKET, "--retries", "1"],
    ],
)
def test_an_invalid_command_line_is_refused_and_nothing_is_read(stub, argv):
    env = Recording(runner_env(stub.endpoint))
    code, doc, _ = run(argv, env)
    assert code == 2 and env.reads == 0 and codes(doc) == {"argument_invalid"}
    assert doc["mode"] == "invalid_arguments"


# ---------------------------------------------------------------- readiness and limits
@pytest.mark.usefixtures("released")
@pytest.mark.parametrize(
    ("changes", "argv_extra", "code"),
    [
        ({"LLMI_DASHBOARD_PROJECT_ID": None}, [], "dashboard_project_invalid"),
        ({"LLMI_DASHBOARD_PROJECT_ID": "0"}, [], "dashboard_project_invalid"),
        ({}, ["--dashboard-url", "http://example.invalid:3001"], "dashboard_url_invalid"),
        ({"LLMI_LIVE_MAX_OUTPUT_TOKENS": "512"}, [], "output_limit_exceeded"),
        ({"LLMI_LIVE_MAX_INPUT_TOKENS": "600"}, [], "input_too_large"),
        ({"HTTPS_PROXY": "http://127.0.0.1:9"}, [], "client_env_override"),
        ({"LLMI_LIVE_MODELS": f"{MODEL},other/model"}, [], "model_list_not_single"),
        ({"LLMI_GROQ_PLAN": "developer"}, [], "plan_not_free"),
        ({"LLMI_LIVE_MAX_RETRIES": "1"}, [], "setting_invalid"),
    ],
)
def test_each_refusal_blocks_execution_and_sends_nothing(stub, wire, changes, argv_extra, code):
    never = NeverBuilt()
    env = runner_env(stub.endpoint, **changes)
    argv = ["--provider", "groq", "--scenario", USAGE, *argv_extra]
    dry, dry_doc, _ = run(argv, env, http_factory=never)
    rc, doc, _ = run([*argv, "--execute"], env, http_factory=never)
    assert dry == 0 and dry_doc["execution_allowed"] is False and code in codes(dry_doc)
    assert rc == 2 and code in codes(doc) and doc["execution_allowed"] is False
    assert never.calls == 0 and wire.requests == []


@pytest.mark.usefixtures("released")
def test_an_unreachable_backend_blocks_execution(wire):
    never = NeverBuilt()
    env = runner_env(f"http://127.0.0.1:{free_port()}/v1/traces")
    code, doc, _ = run(
        ["--provider", "groq", "--scenario", TICKET, "--execute"], env, http_factory=never
    )
    assert code == 2 and "backend_not_ready" in codes(doc) and never.calls == 0


def test_the_installed_sdk_matches_this_checkout():
    assert runner.sdk_checkout_match() is True


@pytest.mark.usefixtures("released")
def test_a_stale_installed_sdk_blocks_execution(stub, wire, monkeypatch):
    never = NeverBuilt()
    monkeypatch.setattr(runner, "sdk_checkout_match", lambda: False)
    code, doc, _ = execute(stub, TICKET, http_factory=never)
    assert code == 2 and "sdk_checkout_mismatch" in codes(doc) and never.calls == 0


@pytest.mark.usefixtures("released")
def test_an_unaudited_library_version_blocks_execution(stub, wire, monkeypatch):
    never = NeverBuilt()
    monkeypatch.setattr(groq_adapter, "groq_library_problems", lambda: ["httpx is not 0.28.1"])
    code, doc, _ = execute(stub, TICKET, http_factory=never)
    assert code == 2 and "client_library_not_audited" in codes(doc) and never.calls == 0


@pytest.mark.usefixtures("released")
@pytest.mark.parametrize(
    "step",
    [ok(content=GOOD[TICKET]), fail(429), fail(503), Step(raise_=httpx.ReadTimeout)],
    ids=["completed", "429", "503", "timeout"],
)
def test_the_runner_asks_the_guard_exactly_once(stub, wire, monkeypatch, step):
    """Even where the guard itself would refuse a second request, the runner never asks."""
    calls: list[int] = []
    original = groq_live.FreePlanSession.authorize_send

    def counted(self: groq_live.FreePlanSession, *args: Any) -> Any:
        calls.append(1)
        return original(self, *args)

    monkeypatch.setattr(groq_live.FreePlanSession, "authorize_send", counted)
    wire.steps = [step]
    execute(stub, TICKET)
    assert calls == [1] and len(wire.requests) == 1


def test_the_fixed_constraints_are_reported_as_shipped(stub):
    _, doc, _ = run(["--provider", "groq", "--scenario", TICKET], runner_env(stub.endpoint))
    constraints = doc["constraints"]
    assert constraints["endpoint"] == f"POST {ENDPOINT}"
    assert constraints["requests_per_execution"] == 1 and constraints["physical_attempts"] == 1
    assert (constraints["retries"], constraints["concurrency"]) == (0, 1)
    assert constraints["max_output_tokens"] == 1024 and constraints["max_input_token_bound"] == 1024
    for flag in (
        "model_fallback",
        "provider_fallback",
        "paid_plan",
        "redirects_followed",
        "proxy",
        "base_url_override",
        "streaming",
        "include_reasoning",
    ):
        assert constraints[flag] is False, flag
    assert constraints["tools"] == []


# ---------------------------------------------------------------- privacy
@pytest.mark.usefixtures("released")
def test_no_secret_or_content_reaches_any_log(stub, wire, caplog):
    caplog.set_level(logging.DEBUG)
    wire.steps = [fail(400, "invalid_request_error")]
    code, _, _ = execute(stub, SUMMARY)
    assert code == 1
    for secret in SECRETS:
        assert secret not in caplog.text
    assert cs.compose_input(cs.SCENARIOS[SUMMARY]) not in caplog.text
    assert_exported_without_content(stub)


def test_the_runner_imports_no_other_provider_library():
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import demo_app.live_compare; "
        # google.protobuf is the OTLP encoder; google.genai and openai are provider libraries.
        "print([m for m in sys.modules if m.startswith(('google.genai', 'openai'))])"
    )
    env = {
        name: os.environ[name]
        for name in ("SYSTEMROOT", "PATH", "TEMP", "TMP")
        if name in os.environ
    }
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    result = subprocess.run(  # noqa: S603 - this interpreter, a fixed script
        [sys.executable, "-c", code, str(REPO)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=True,
    )
    assert result.stdout.strip() == "[]"


def test_the_gemini_runners_are_imported_only_for_a_gemini_run():
    """ADR-49: the Gemini modules are loaded through `_gemini_modules` only, never at import."""
    source = Path(runner.__file__).read_text(encoding="utf-8")
    import_lines = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
    assert not [line for line in import_lines if "gemini" in line or "google" in line]
    assert source.count('importlib.import_module("demo_app.live_gemini")') == 1
    assert "follow_redirects=True" not in source
    gemini_runner = (REPO / "demo_app" / "live_gemini.py").read_text(encoding="utf-8")
    assert "live_compare" not in gemini_runner


def test_a_reviewed_scenario_with_a_bad_pin_is_refused_before_the_policy(stub, monkeypatch):
    bad = dataclasses.replace(cs.SCENARIOS[TICKET], input_sha256="0" * 64)
    monkeypatch.setattr(cs, "SCENARIOS", {TICKET: bad})
    seen: list[datetime] = []

    def clock() -> datetime:
        seen.append(NOW)
        return NOW

    code, doc, _ = run(
        ["--provider", "groq", "--scenario", TICKET],
        runner_env(stub.endpoint),
        now=None,
        clock=clock,
    )
    assert code == 2 and codes(doc) == {"scenario_hash_mismatch"} and seen == []
