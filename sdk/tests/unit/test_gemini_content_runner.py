"""Gemini content demo runner `demo_app.live_gemini_content` (Phase 7B, ADR-39), offline: the
backend is the local OTLP stub, the provider is an injected SYNTHETIC fake transport
(`synthetic` provenance), and every non-loopback connection is blocked. No test sends a
provider request."""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from google import genai

from llm_insight_sdk import gemini_live
from llm_insight_sdk.telemetry import Telemetry, TelemetryStats
from tests.conftest import DUMMY_KEY, free_port
from tests.gemini_fixtures import (
    ENDPOINT,
    ERROR_SENTINEL,
    FAKE_GEMINI_KEY,
    MODEL,
    OUTPUT_SENTINEL,
    THOUGHT_SENTINEL,
    USAGE,
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

from demo_app import content_preview, gemini_scenarios, live_gemini  # noqa: E402
from demo_app import live_gemini_content as runner  # noqa: E402

SECRETS = (FAKE_GEMINI_KEY, DUMMY_KEY, OUTPUT_SENTINEL, THOUGHT_SENTINEL, ERROR_SENTINEL)
SUMMARY_ID = "short-text-summary"
TICKET_ID = "support-ticket-classification"
WEEKLY_ID = "json-data-interpretation"
GOOD_SUMMARY = "Search is faster and offline edits sync. A known issue remains: long exports fail."
GOOD_TICKET = json.dumps({"category": "billing", "priority": "high", "reason": "Charged twice."})
GOOD_WEEKLY = json.dumps(
    {
        "total_requests": 700,
        "highest_error_day": "Wed",
        "error_rate_percent": 3.0,
        "observation": "Errors peaked on Wednesday.",
    }
)

pytestmark = pytest.mark.usefixtures("gemini_released")  # ADR-50, test-only release
TRACE_ID = re.compile(r"[0-9a-f]{32}")


@pytest.fixture(autouse=True)
def offline() -> Iterator[None]:
    attempts: list[str] = []
    with block_network(attempts):
        yield
    assert attempts == [], f"a test tried to reach the network: {attempts}"


def runner_env(endpoint: str, **overrides: str | None) -> dict[str, str]:
    base = {
        "LLMI_LIVE_MAX_INPUT_TOKENS": "1024",
        "LLMI_LIVE_MAX_OUTPUT_TOKENS": "1024",
    }
    base.update(overrides)  # type: ignore[arg-type]
    env = gemini_env(**base)
    env.update(
        {
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": endpoint,
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": f"X-Project-Key={DUMMY_KEY}",
            "OTEL_SERVICE_NAME": "live-gemini-content-unit-test",
            "LLMI_SDK_SCHEDULE_DELAY_MILLIS": "50",
            "LLMI_DASHBOARD_PROJECT_ID": "7",
        }
    )
    for key, value in overrides.items():  # an override may also remove a variable set above
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return {k: v for k, v in env.items() if v is not None}


class NeverCalled:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, policy: gemini_live.FreeTierPolicy) -> genai.Client:
        self.calls += 1
        raise AssertionError("no provider client may be created")


class Terminal(io.StringIO):
    encoding = "utf-8"

    def isatty(self) -> bool:
        return True


def answer_part(text: str, finish: str = "STOP", thought: str | None = None) -> GStep:
    parts: list[dict[str, Any]] = []
    if thought is not None:
        parts.append({"text": thought, "thought": True})
    parts.append({"text": text})
    body = candidate(finish)
    body["content"] = {"parts": parts, "role": "model"}
    return g_ok(candidates=[body])


def run(
    argv: list[str],
    env: dict[str, str],
    *,
    err: io.StringIO | None = None,
    allowed_in_err: tuple[str, ...] = (),
    **kw: Any,
) -> tuple[int, dict[str, Any], str]:
    out = io.StringIO()
    stderr = err if err is not None else io.StringIO()
    kw.setdefault("now", NOW)
    kw.setdefault("provenance", "synthetic")
    code = runner.main(argv, env, out=out, err=stderr, **kw)
    text = out.getvalue()
    for secret in SECRETS:
        assert secret not in text
        if secret not in allowed_in_err:
            assert secret not in stderr.getvalue()
    for scenario in gemini_scenarios.SCENARIOS.values():
        assert scenario.fixture not in text and scenario.task not in text
    (line,) = text.splitlines()  # exactly one JSON line
    doc = json.loads(line)
    assert doc["schema"] == "llminsight.demo.gemini_content.v1"
    return code, doc, stderr.getvalue()


def execute(
    stub, fake: FakeGemini, scenario: str = SUMMARY_ID, **overrides: str | None
) -> tuple[int, dict[str, Any], str]:
    return run(
        ["--scenario", scenario, "--execute"],
        runner_env(stub.endpoint, **overrides),
        client_factory=lambda policy: fake.client(),
    )


def codes(doc: dict[str, Any]) -> set[str]:
    return {r["code"] for r in doc["refusals"]}


def fails_export_after_readiness(stub, fake: FakeGemini):
    def factory(policy: gemini_live.FreeTierPolicy) -> genai.Client:
        stub.behaviour.status = 401  # the probe already passed; the export is now rejected
        return fake.client()

    return factory


# ---------------------------------------------------------------- dry run
@pytest.mark.parametrize(
    ("scenario", "cap", "sha", "size"),
    [
        (TICKET_ID, 512, "18d3061e26e4b09759b469d674c0aba18a87a3610e83534ef679d758c2cbdf0d", 601),
        (WEEKLY_ID, 768, "1e9f1ba31ae538115071dc95e5fd81aeef150685ccbc27112d157116105d4951", 657),
        (SUMMARY_ID, 512, "740179ed99445b9564589b5c07518f4cea890fbb98dfa4ed112a680728be61b5", 611),
    ],
)
def test_dry_run_is_the_default_and_sends_nothing(stub, scenario, cap, sha, size):
    never = NeverCalled()
    code, doc, err = run(["--scenario", scenario], runner_env(stub.endpoint), client_factory=never)
    assert code == 0 and never.calls == 0 and err == ""
    assert doc["mode"] == "dry_run"
    assert doc["provider_request_sent"] is False and doc["provider_request_state"] == "not_sent"
    assert (doc["policy_eligible"], doc["execution_ready"], doc["execution_allowed"]) == (
        True,
        True,
        True,
    )
    assert doc["refusals"] == []
    assert doc["scenario"]["id"] == scenario and doc["scenario"]["input_sha256"] == sha
    assert doc["scenario"]["input_utf8_bytes"] == size
    assert doc["scenario"]["input_token_bound"] == size + 64
    assert doc["scenario"]["max_output_tokens"] == doc["plan"]["max_output_tokens"] == cap
    assert doc["plan"]["paid_fallback"] is False and doc["plan"]["tools"] == []
    assert doc["environment"]["backend"] == {"ready": True, "reason": "ready"}
    assert doc["environment"]["sdk_checkout_match"] is True
    assert doc["provider"] is None and doc["validation"] is None and doc["export"] is None
    assert doc["correlation"]["trace_id"] is None and doc["correlation"]["project_id"] == 7
    assert stub.spans() == []  # the readiness probe is an empty request


def test_dry_run_reports_backend_not_ready_as_not_allowed():
    endpoint = f"http://127.0.0.1:{free_port()}/v1/traces"
    never = NeverCalled()
    code, doc, _ = run(["--scenario", SUMMARY_ID], runner_env(endpoint), client_factory=never)
    assert code == 0 and never.calls == 0
    assert doc["policy_eligible"] is True
    assert doc["execution_ready"] is False and doc["execution_allowed"] is False
    assert doc["environment"]["backend"]["reason"] == "backend_unreachable"
    assert "backend_not_ready" in codes(doc)


def test_dry_run_without_configuration_lists_every_refusal():
    code, doc, _ = run(["--scenario", SUMMARY_ID], {}, client_factory=NeverCalled())
    assert code == 0 and doc["execution_allowed"] is False and doc["plan"] is None
    assert doc["policy_eligible"] is False and doc["execution_ready"] is False
    assert {
        "provider_mode_not_real",
        "gemini_mode_not_free_tier",
        "consent_missing",
        "api_key_missing",
        "free_tier_unverified",
        "backend_not_ready",
        "dashboard_project_invalid",
    } <= codes(doc)


def test_dry_run_reports_stale_evidence_without_bypassing_it(stub):
    later = gemini_live.FREE_TIER_VERIFIED_AT + gemini_live.FREE_TIER_MAX_AGE
    later = later.replace(minute=1)
    env = runner_env(stub.endpoint, LLMI_GEMINI_TIER_CHECKED_ON=later.date().isoformat())
    code, doc, _ = run(["--scenario", SUMMARY_ID], env, now=later, client_factory=NeverCalled())
    assert code == 0 and doc["execution_allowed"] is False
    assert "free_tier_evidence_stale" in codes(doc)


# ---------------------------------------------------------------- arguments
@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--scenario"],
        ["--scenario", SUMMARY_ID, "--exec"],
        ["--scenario", SUMMARY_ID, "--execu"],
        ["--scen", SUMMARY_ID],
        ["--scenario", SUMMARY_ID, "--prev"],
        ["--scenario", SUMMARY_ID, "--execute=yes"],
        ["--scenario", SUMMARY_ID, "--all"],
        ["--scenario", SUMMARY_ID, "--retry", "3"],
        ["--scenario", SUMMARY_ID, "--fallback-model", "x"],
        ["--scenario", SUMMARY_ID, "--input-file", "x"],
        ["--scenario", SUMMARY_ID, "--prompt", "x"],
        ["--scenario", SUMMARY_ID, "--max-output-tokens", "9"],
        ["--scenario", SUMMARY_ID, "--temperature", "1"],
        ["--scenario", SUMMARY_ID, TICKET_ID],
        ["--scenario", SUMMARY_ID, "--scenario", TICKET_ID],
        ["--scenario", SUMMARY_ID, "--model", "a", "--model", "b"],
    ],
)
def test_invalid_command_lines_are_refused_and_never_execute(stub, argv):
    never = NeverCalled()
    code, doc, err = run(argv, runner_env(stub.endpoint), client_factory=never)
    assert code == 2 and never.calls == 0 and err == ""
    assert doc["mode"] == "invalid_arguments" and codes(doc) == {"argument_invalid"}
    assert doc["provider_request_sent"] is False


def test_argument_errors_never_echo_the_value(stub):
    value = "--UNIQUE-ARGUMENT-VALUE-9f1"
    out, err = io.StringIO(), io.StringIO()
    code = runner.main([value], runner_env(stub.endpoint), out=out, err=err)
    assert code == 2 and value not in out.getvalue() and value not in err.getvalue()


def test_an_unknown_scenario_is_refused_and_not_echoed(stub):
    name = "UNIQUE-SCENARIO-NAME-1b2"
    never = NeverCalled()
    code, doc, _ = run(["--scenario", name], runner_env(stub.endpoint), client_factory=never)
    assert code == 2 and never.calls == 0
    assert codes(doc) == {"scenario_unknown"} and name not in json.dumps(doc)


# ---------------------------------------------------------------- registry refusals
def registry_with(monkeypatch, scenario: gemini_scenarios.Scenario) -> None:
    table = dict(gemini_scenarios.SCENARIOS)
    table[scenario.id] = scenario
    monkeypatch.setattr(gemini_scenarios, "SCENARIOS", table)


@pytest.mark.parametrize("execute_flag", [False, True])
def test_a_malformed_fixture_is_refused_before_any_client(stub, monkeypatch, execute_flag):
    bad = dataclasses.replace(
        gemini_scenarios.SCENARIOS[WEEKLY_ID],
        fixture='{"dataset":"x","days":[],"unit":"r","generationConfig":{"temperature":2}}',
    )
    registry_with(monkeypatch, bad)
    never = NeverCalled()
    argv = ["--scenario", WEEKLY_ID] + (["--execute"] if execute_flag else [])
    code, doc, _ = run(argv, runner_env(stub.endpoint), client_factory=never)
    assert code == 2 and never.calls == 0 and doc["scenario"] is None
    assert {"scenario_fixture_invalid", "scenario_hash_mismatch"} <= codes(doc)
    assert stub.attempts == 0  # refused before even the backend probe


def test_an_oversized_input_is_refused_before_any_client(stub, monkeypatch):
    scenario = gemini_scenarios.SCENARIOS[SUMMARY_ID]
    big = dataclasses.replace(
        scenario,
        fixture="word " * 400,
        max_input_bytes=100_000,
    )
    big = dataclasses.replace(
        big, input_sha256=gemini_scenarios.input_sha256(gemini_scenarios.compose_input(big))
    )
    registry_with(monkeypatch, big)
    never = NeverCalled()
    code, doc, _ = run(
        ["--scenario", SUMMARY_ID, "--execute"], runner_env(stub.endpoint), client_factory=never
    )
    assert code == 2 and never.calls == 0
    assert codes(doc) == {"scenario_input_out_of_bounds"}


# ---------------------------------------------------------------- execute: refusals
@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"LLMI_PROVIDER_MODE": "mock"}, "provider_mode_not_real"),
        ({"LLMI_GEMINI_MODE": "paid"}, "gemini_mode_not_free_tier"),
        ({"LLMI_LIVE_CONSENT": None}, "consent_missing"),
        ({"GEMINI_API_KEY": None}, "api_key_missing"),
        ({"LLMI_GEMINI_BILLING_TIER": "tier1"}, "billing_tier_not_free"),
        ({"LLMI_GEMINI_TIER_CHECKED_ON": "2026-10-01"}, "free_tier_attestation_stale"),
        ({"LLMI_LIVE_MODELS": "gemini-3.5-flash-lite"}, "model_not_free_tier_verified"),
        ({"LLMI_LIVE_MAX_REQUESTS": "2"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_RETRIES": "1"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_CONCURRENCY": "2"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_OUTPUT_TOKENS": "2000"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_INPUT_TOKENS": "2000"}, "setting_invalid"),
        ({"GOOGLE_GENAI_USE_VERTEXAI": "true"}, "client_env_override"),
        ({"GOOGLE_API_KEY": "SYNTHETIC"}, "client_env_override"),
        ({"GOOGLE_GEMINI_BASE_URL": "https://evil.example/"}, "client_env_override"),
        ({"LLMI_LIVE_MAX_OUTPUT_TOKENS": "256"}, "output_limit_exceeded"),
        ({"LLMI_LIVE_MAX_INPUT_TOKENS": "256"}, "input_too_large"),
        ({"LLMI_DASHBOARD_PROJECT_ID": None}, "dashboard_project_invalid"),
        ({"LLMI_DASHBOARD_PROJECT_ID": "0"}, "dashboard_project_invalid"),
        ({"LLMI_DASHBOARD_PROJECT_ID": "-1"}, "dashboard_project_invalid"),
        ({"LLMI_DASHBOARD_PROJECT_ID": "abc"}, "dashboard_project_invalid"),
        ({"LLMI_DASHBOARD_PROJECT_ID": "٣"}, "dashboard_project_invalid"),
        ({"LLMI_DASHBOARD_PROJECT_ID": "9223372036854775808"}, "dashboard_project_invalid"),
    ],
)
def test_execute_is_refused_by_every_gate(stub, overrides, code):
    never = NeverCalled()
    rc, doc, _ = run(
        ["--scenario", SUMMARY_ID, "--execute"],
        runner_env(stub.endpoint, **overrides),
        client_factory=never,
    )
    assert rc == 2 and never.calls == 0
    assert doc["provider_request_sent"] is False and doc["provider_request_state"] == "not_sent"
    assert doc["execution_allowed"] is False and code in codes(doc)
    assert doc["provider"] is None and stub.spans() == []


def test_only_the_complete_flag_authorizes_sending(stub):
    never = NeverCalled()
    code, doc, _ = run(
        ["--scenario", SUMMARY_ID, "--exec"], runner_env(stub.endpoint), client_factory=never
    )
    assert code == 2 and never.calls == 0 and doc["provider_request_sent"] is False


def test_a_model_outside_the_allowlist_is_refused(stub):
    never = NeverCalled()
    rc, doc, _ = run(
        ["--scenario", SUMMARY_ID, "--execute", "--model", "gemini-2.5-pro"],
        runner_env(stub.endpoint),
        client_factory=never,
    )
    assert rc == 2 and never.calls == 0 and "model_not_allowed" in codes(doc)
    assert "gemini-2.5-pro" not in json.dumps(doc) and doc["plan"] is None


def test_backend_not_ready_refuses_execution():
    endpoint = f"http://127.0.0.1:{free_port()}/v1/traces"
    never = NeverCalled()
    rc, doc, _ = run(
        ["--scenario", SUMMARY_ID, "--execute"], runner_env(endpoint), client_factory=never
    )
    assert rc == 2 and never.calls == 0 and "backend_not_ready" in codes(doc)
    assert doc["policy_eligible"] is True and doc["execution_ready"] is False


def test_a_stale_sdk_install_refuses_execution(stub, monkeypatch):
    monkeypatch.setattr(runner, "sdk_checkout_match", lambda: False)
    never = NeverCalled()
    rc, doc, _ = run(
        ["--scenario", SUMMARY_ID, "--execute"], runner_env(stub.endpoint), client_factory=never
    )
    assert rc == 2 and never.calls == 0 and "sdk_checkout_mismatch" in codes(doc)
    assert doc["environment"]["sdk_checkout_match"] is False


def test_an_unknown_checkout_state_does_not_refuse(stub, monkeypatch):
    monkeypatch.setattr(runner, "sdk_checkout_match", lambda: "unknown")
    _, doc, _ = run(["--scenario", SUMMARY_ID], runner_env(stub.endpoint))
    assert doc["execution_allowed"] is True
    assert doc["environment"]["sdk_checkout_match"] == "unknown"


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:3001",
        "http://evil.example",
        "http://127.0.0.1.evil.example",
        "http://user:pw@127.0.0.1:3001",
        "http://127.0.0.1:3001/path",
        "http://127.0.0.1:3001?x=1",
        "http://127.0.0.1:99999",
        "not a url",
    ],
)
def test_a_non_loopback_dashboard_url_is_refused(stub, url):
    never = NeverCalled()
    rc, doc, _ = run(
        ["--scenario", SUMMARY_ID, "--execute", "--dashboard-url", url],
        runner_env(stub.endpoint),
        client_factory=never,
    )
    assert rc == 2 and never.calls == 0 and "dashboard_url_invalid" in codes(doc)
    assert url not in json.dumps(doc)


def test_a_wrong_endpoint_client_is_refused_in_real_mode(stub):
    fake = FakeGemini([g_ok()])
    rc, doc, _ = run(
        ["--scenario", SUMMARY_ID, "--execute"],
        runner_env(stub.endpoint),
        client_factory=lambda policy: fake.client(),
        provenance="real",
    )
    assert rc == 2 and fake.requests == [] and stub.spans() == []
    assert codes(doc) == {"client_not_allowed"} and doc["provider_request_state"] == "not_sent"


def test_a_failing_client_construction_sends_nothing(stub):
    def broken(policy):
        raise FileNotFoundError(FAKE_GEMINI_KEY)  # the message is never printed

    rc, doc, _ = run(
        ["--scenario", SUMMARY_ID, "--execute"], runner_env(stub.endpoint), client_factory=broken
    )
    assert rc == 2 and doc["provider_request_sent"] is False
    assert doc["refusals"] == [
        {"code": "client_construction_failed", "message": "FileNotFoundError"}
    ]
    assert stub.spans() == []


def test_a_recheck_refusal_immediately_before_send_sends_nothing(stub, monkeypatch):
    from llm_insight_sdk import gemini_adapter
    from llm_insight_sdk.config import ConfigError

    fake = FakeGemini([g_ok()])

    class Refusing(gemini_adapter.GeminiTextAdapter):
        def generate_text(self, request):
            raise ConfigError("live Gemini client: changed")

    monkeypatch.setattr(live_gemini, "GeminiTextAdapter", Refusing)
    rc, doc, _ = execute(stub, fake)
    assert rc == 2 and fake.requests == []
    assert codes(doc) == {"client_not_allowed"} and doc["provider_request_state"] == "not_sent"


def test_an_invalid_session_state_is_refused(stub, monkeypatch):
    from llm_insight_sdk.live import LiveRefusal, Refusal

    class Exhausted(gemini_live.FreeTierSession):
        def reserve(self, **kw):
            raise LiveRefusal([Refusal("request_limit_reached", "LLMI_LIVE_MAX_REQUESTS reached")])

    monkeypatch.setattr(live_gemini.gemini_live, "FreeTierSession", Exhausted)
    never = NeverCalled()
    rc, doc, _ = run(
        ["--scenario", SUMMARY_ID, "--execute"], runner_env(stub.endpoint), client_factory=never
    )
    assert rc == 2 and never.calls == 0 and codes(doc) == {"request_limit_reached"}
    assert doc["provider_request_state"] == "not_sent"


def test_the_endpoint_and_session_controls_are_those_of_the_shared_path():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert "live_gemini.run_single_request(" in source
    for forbidden in (
        "genai.Client(",
        "httpx.Client(",
        "FreeTierSession(",
        "ObservedGeminiClient(",
        "GeminiTextAdapter(",
    ):
        assert forbidden not in source


# ---------------------------------------------------------------- execute: one request
def test_one_successful_request_is_reported_with_metadata_only(stub):
    fake = FakeGemini([answer_part(GOOD_SUMMARY, thought=THOUGHT_SENTINEL), g_ok()])
    code, doc, err = execute(stub, fake)
    assert code == 0 and len(fake.requests) == 1 and err == ""
    assert doc["mode"] == "execute"
    assert doc["provider_request_sent"] is True and doc["provider_request_state"] == "sent"
    assert GOOD_SUMMARY not in json.dumps(doc)

    provider = doc["provider"]
    assert (provider["outcome"], provider["provider"]) == ("completed", "gcp.gemini")
    assert (provider["request_model"], provider["response_model"]) == (MODEL, MODEL)
    assert provider["attempts"] == 1 and provider["provenance"] == "synthetic"
    assert provider["duration_ms"] >= 0

    usage = doc["usage"]
    assert usage["tokens"] == {
        "input": 20,
        "output": 32,
        "cache_read_input": 4,
        "reasoning_output": 30,
    }
    assert usage["provider_usage"] == {
        "prompt": 20,
        "candidates": 2,
        "thoughts": 30,
        "cached_content": 4,
        "tool_use_prompt": None,
        "total": 52,
    }
    assert usage["usage_check"] == "consistent"
    assert usage["cost"]["status"] == "unknown" and usage["cost"]["billed_amount_usd"] is None
    assert usage["cost"]["paid_tier_list_price_estimate_usd"] is None

    assert doc["validation"]["status"] == "passed"
    assert doc["session"] == {
        "requests_started": 1,
        "max_requests": 1,
        "physical_attempts": 1,
        "halted": None,
    }
    assert doc["export"] == {
        "outcome": "confirmed",
        "flushed": True,
        "shutdown_ok": True,
        "spans_ended": 2,
        "spans_exported": 2,
        "spans_export_failed": 0,
    }
    corr = doc["correlation"]
    tid = corr["trace_id"]
    assert TRACE_ID.fullmatch(tid)
    assert corr["project_id"] == 7 and corr["project_id_source"] == "operator_env_unverified"
    assert corr["dashboard_url"] == f"http://127.0.0.1:3001/traces/{tid}?project=7"
    assert corr["api_path"] == f"/api/v1/traces/{tid}?project=7"

    spans = stub.spans()
    assert len(spans) == 2 and {s["trace_id"] for s in spans} == {tid}
    (root,) = [s for s in spans if s["name"] == runner.ROOT_SPAN]
    (child,) = [s for s in spans if s["kind"] == "CLIENT"]
    assert child["parent_span_id"] == root["span_id"] and root["kind"] == "INTERNAL"
    assert not any(key.startswith("gen_ai.") for key in root["attributes"])
    attrs = child["attributes"]
    assert attrs["gen_ai.provider.name"] == "gcp.gemini"
    assert attrs["llminsight.provenance"] == "synthetic"
    assert attrs["gen_ai.prompt.name"] == "demo.short-summary"
    assert attrs["gen_ai.prompt.version"] == "v1"


@pytest.mark.parametrize(
    ("scenario", "cap"), [(TICKET_ID, 512), (WEEKLY_ID, 768), (SUMMARY_ID, 512)]
)
def test_the_composed_text_is_the_only_content_and_the_scenario_cap_is_the_only_setting(
    stub, scenario, cap
):
    fake = FakeGemini([g_ok()])
    execute(stub, fake, scenario)
    text = gemini_scenarios.compose_input(gemini_scenarios.SCENARIOS[scenario])
    body = fake.request_json(0)
    assert body["contents"] == [{"parts": [{"text": text}], "role": "user"}]
    assert body["generationConfig"] == {"maxOutputTokens": cap}
    assert set(body) == {"contents", "generationConfig"}
    assert str(fake.requests[0].url) == ENDPOINT


def test_the_environment_can_refuse_but_never_change_the_request(stub):
    fake = FakeGemini([g_ok()])
    execute(stub, fake, WEEKLY_ID, LLMI_LIVE_MAX_OUTPUT_TOKENS="900")
    assert fake.request_json(0)["generationConfig"] == {"maxOutputTokens": 768}


def test_the_reported_hash_is_of_the_exact_bytes_sent(stub):
    fake = FakeGemini([g_ok()])
    _, doc, _ = execute(stub, fake, TICKET_ID)
    sent = fake.request_json(0)["contents"][0]["parts"][0]["text"]
    assert doc["scenario"]["input_sha256"] == hashlib.sha256(sent.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    ("scenario", "answer"), [(TICKET_ID, GOOD_TICKET), (WEEKLY_ID, GOOD_WEEKLY)]
)
def test_valid_json_answers_pass(stub, scenario, answer):
    code, doc, _ = execute(stub, FakeGemini([answer_part(answer)]), scenario)
    assert code == 0 and doc["validation"]["status"] == "passed"
    assert doc["validation"]["fence_stripped"] is False


def test_a_fenced_json_answer_passes_and_says_so(stub):
    step = answer_part(f"```json\n{GOOD_TICKET}\n```")
    code, doc, _ = execute(stub, FakeGemini([step]), TICKET_ID)
    assert code == 0 and doc["validation"]["fence_stripped"] is True


# ---------------------------------------------------------------- outcomes and exit codes
@pytest.mark.parametrize(
    ("step", "exit_code", "status", "category", "reason", "validation"),
    [
        (g_ok(finish="MAX_TOKENS"), 3, "incomplete", None, "MAX_TOKENS", "provider_incomplete"),
        (g_ok(candidates=[candidate("CONTINUATION", continuationToken="U1lO")]), 3,
         "incomplete", None, "CONTINUATION", "provider_incomplete"),
        (g_ok(finish="SAFETY"), 1, "blocked", "content_blocked", "SAFETY",
         "provider_not_completed"),
        (GStep(200, {"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"},
                     "usageMetadata": {"promptTokenCount": 9, "totalTokenCount": 9}}),
         1, "blocked", "content_blocked", "PROHIBITED_CONTENT", "provider_not_completed"),
        (GStep(200, {}), 1, "failed", "empty_response", None, "provider_not_completed"),
        (g_fail(429, "RESOURCE_EXHAUSTED"), 1, "error", "rate_limited", "RESOURCE_EXHAUSTED",
         "provider_not_completed"),
        (g_fail(402, "PAYMENT_REQUIRED"), 1, "error", "payment_required", "PAYMENT_REQUIRED",
         "provider_not_completed"),
        (g_fail(500, "INTERNAL"), 1, "error", "provider_error", "INTERNAL",
         "provider_not_completed"),
        (GStep(raise_=httpx.ReadTimeout), 1, "error", "timeout", None,
         "provider_not_completed"),
        (GStep(raise_=httpx.ConnectError), 1, "error", "connection_error", None,
         "provider_not_completed"),
        (GStep(200, raw=b"<html>SYNTHETIC</html>", content_type="text/html"), 1, "error",
         "malformed_response", None, "provider_not_completed"),
    ],
)  # fmt: skip
def test_outcomes_are_reported_as_they_are_and_never_resubmitted(
    stub, step, exit_code, status, category, reason, validation
):
    fake = FakeGemini([step, g_ok(), g_ok()])
    code, doc, _ = execute(stub, fake)
    assert len(fake.requests) == 1  # the spare scripted answers are never requested
    assert code == exit_code
    provider = doc["provider"]
    assert (provider["outcome"], provider["error_category"], provider["provider_reason"]) == (
        status,
        category,
        reason,
    )
    assert provider["attempts"] == 1
    assert doc["provider_request_state"] == "sent" and doc["provider_request_sent"] is True
    assert doc["validation"] == {
        "status": "not_evaluated",
        "reason": validation,
        "rules": [],
        "fence_stripped": False,
    }
    assert doc["usage"]["cost"]["status"] == "unknown"
    assert doc["usage"]["cost"]["billed_amount_usd"] is None
    assert doc["session"]["physical_attempts"] == 1


def test_an_incomplete_response_is_distinct_from_a_completed_one(stub):
    code, doc, _ = execute(stub, FakeGemini([answer_part(GOOD_SUMMARY, finish="MAX_TOKENS")]))
    assert code == 3 and doc["provider"]["outcome"] == "incomplete"
    assert doc["provider"]["incomplete_reason"] == "MAX_TOKENS"
    assert doc["validation"]["status"] == "not_evaluated"


@pytest.mark.parametrize(
    ("usage", "check"),
    [
        (None, "missing"),
        ({"promptTokenCount": 3}, "missing"),
        ({"promptTokenCount": 3, "totalTokenCount": 77}, "inconsistent"),
    ],
)
def test_missing_or_inconsistent_usage_stays_unknown_never_zero(stub, usage, check):
    body = answer_part(GOOD_SUMMARY)
    assert body.body is not None
    if usage is None:
        body.body.pop("usageMetadata")
    else:
        body.body["usageMetadata"] = usage
    code, doc, _ = execute(stub, FakeGemini([body]))
    assert code == 0 and doc["provider"]["outcome"] == "completed"
    assert doc["usage"]["tokens"] is None and doc["usage"]["usage_check"] == check
    assert doc["usage"]["cost"]["usage_source"] == "unknown"
    assert doc["usage"]["cost"]["billed_amount_usd"] is None
    (child,) = [s for s in stub.spans() if s["kind"] == "CLIENT"]
    assert "gen_ai.usage.input_tokens" not in child["attributes"]


def test_reported_usage_is_echoed_not_estimated(stub):
    assert USAGE["totalTokenCount"] == 52  # the fixture the assertion below depends on
    _, doc, _ = execute(stub, FakeGemini([answer_part(GOOD_SUMMARY)]))
    assert doc["usage"]["provider_usage"]["total"] == 52


# ---------------------------------------------------------------- local validation
@pytest.mark.parametrize(
    ("scenario", "answer"),
    [
        (TICKET_ID, "not json"),
        (TICKET_ID, json.dumps({"category": "login", "priority": "low", "reason": "r"})),
        (WEEKLY_ID, GOOD_WEEKLY.replace("700", "701")),
        (SUMMARY_ID, "No mention of that problem here."),
    ],
)
def test_a_local_failure_leaves_the_provider_completed_and_sends_nothing_more(
    stub, scenario, answer
):
    fake = FakeGemini([answer_part(answer), g_ok()])
    code, doc, _ = execute(stub, fake, scenario)
    assert code == 3 and len(fake.requests) == 1
    assert doc["provider"]["outcome"] == "completed"
    assert doc["validation"]["status"] == "failed"
    assert doc["export"]["outcome"] == "confirmed"
    assert answer not in json.dumps(doc)


def test_a_non_text_answer_is_not_evaluated(stub):
    body = candidate("STOP")
    body["content"] = {
        "parts": [{"functionCall": {"name": "lookup", "args": {}}}],
        "role": "model",
    }
    code, doc, _ = execute(stub, FakeGemini([g_ok(candidates=[body])]))
    assert code == 3 and doc["provider"]["outcome"] == "completed"
    assert doc["validation"]["status"] == "not_evaluated"
    assert doc["validation"]["reason"] == "no_text_answer"


# ---------------------------------------------------------------- export
def test_export_failure_is_not_provider_failure(stub):
    fake = FakeGemini([answer_part(GOOD_SUMMARY), g_ok()])
    code, doc, _ = run(
        ["--scenario", SUMMARY_ID, "--execute"],
        runner_env(stub.endpoint),
        client_factory=fails_export_after_readiness(stub, fake),
    )
    assert code == 3 and len(fake.requests) == 1
    assert doc["provider"]["outcome"] == "completed"
    assert doc["validation"]["status"] == "passed"
    assert doc["export"]["outcome"] == "failed" and doc["export"]["spans_export_failed"] == 2
    assert doc["correlation"]["trace_id"] is not None


def test_a_prior_export_failure_is_not_hidden_by_a_successful_flush(stub, monkeypatch):
    real_flush = Telemetry.force_flush

    def flush(self: Telemetry, timeout_millis: int | None = None) -> bool:
        self._counters.add("export_batches_failed")  # an earlier batch failed on the worker
        self._counters.add("spans_export_failed", 1)
        real_flush(self, timeout_millis)
        return True  # as force_flush reports when nothing failed during the flush itself

    monkeypatch.setattr(Telemetry, "force_flush", flush)
    code, doc, _ = execute(stub, FakeGemini([answer_part(GOOD_SUMMARY)]))
    assert doc["export"]["flushed"] is True and doc["export"]["spans_export_failed"] == 1
    assert doc["export"]["outcome"] == "partial" and code == 3


def test_shutdown_uncertainty_is_reported(stub, monkeypatch):
    real_shutdown = Telemetry.shutdown

    def shutdown(self: Telemetry, timeout_millis: int | None = None) -> bool:
        real_shutdown(self, timeout_millis)
        return False

    monkeypatch.setattr(Telemetry, "shutdown", shutdown)
    code, doc, _ = execute(stub, FakeGemini([answer_part(GOOD_SUMMARY)]))
    assert doc["export"]["shutdown_ok"] is False and doc["export"]["outcome"] == "unknown"
    assert code == 3 and doc["provider"]["outcome"] == "completed"


def stats(ended: int, exported: int, failed: int) -> TelemetryStats:
    return TelemetryStats(ended, exported, failed, 0, 0, 0)


@pytest.mark.parametrize(
    ("flushed", "shutdown_ok", "values", "expected"),
    [
        (True, True, (2, 2, 0), "confirmed"),
        (True, True, (2, 0, 2), "failed"),
        (False, True, (2, 0, 2), "failed"),
        (True, True, (2, 1, 1), "partial"),
        (True, True, (2, 1, 0), "partial"),
        (False, False, (2, 0, 0), "unknown"),
        (True, False, (2, 2, 0), "unknown"),
        (False, True, (2, 2, 0), "unknown"),
        (None, None, (2, 2, 0), "unknown"),
        (True, True, (0, 0, 0), "unknown"),
    ],
)
def test_export_outcome_table(flushed, shutdown_ok, values, expected):
    assert runner.export_outcome(flushed, shutdown_ok, stats(*values)) == expected


def test_export_outcome_without_telemetry_is_unknown():
    assert runner.export_outcome(None, None, None) == "unknown"


@pytest.mark.parametrize(
    ("status", "validation", "export", "code"),
    [
        ("completed", "passed", "confirmed", 0),
        ("completed", "failed", "confirmed", 3),
        ("completed", "not_evaluated", "confirmed", 3),
        ("completed", "passed", "failed", 3),
        ("completed", "passed", "partial", 3),
        ("completed", "passed", "unknown", 3),
        ("incomplete", "not_evaluated", "confirmed", 3),
        ("blocked", "not_evaluated", "confirmed", 1),
        ("failed", "not_evaluated", "failed", 1),
        ("error", "not_evaluated", "unknown", 1),
        ("malformed", "not_evaluated", "confirmed", 1),
    ],
)
def test_exit_code_table(status, validation, export, code):
    assert runner._exit_code(status, validation, export) == code


# ---------------------------------------------------------------- interruption
class InterruptingGemini(FakeGemini):
    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        raise KeyboardInterrupt


def test_an_interrupted_call_reports_possibly_sent_and_never_resends(stub):
    fake = InterruptingGemini([g_ok(), g_ok()])
    code, doc, _ = execute(stub, fake)
    assert code == 1 and len(fake.requests) == 1
    assert doc["provider_request_sent"] is None
    assert doc["provider_request_state"] == "possibly_sent"
    assert doc["provider"]["outcome"] == "interrupted"
    assert doc["validation"]["status"] == "not_evaluated"
    assert doc["usage"]["tokens"] is None and doc["usage"]["cost"]["status"] == "unknown"
    assert doc["correlation"]["trace_id"] is not None
    assert doc["session"]["physical_attempts"] == 1


def test_an_interrupted_call_with_preview_withholds_the_output(stub):
    fake = InterruptingGemini([g_ok()])
    err = Terminal()
    _, doc, text = run(
        ["--scenario", SUMMARY_ID, "--execute", "--preview"],
        runner_env(stub.endpoint),
        err=err,
        client_factory=lambda policy: fake.client(),
    )
    assert text == "" and doc["preview"]["shown"] is False
    assert doc["preview"]["reason"] == "output_withheld_interrupted"


def test_an_interrupt_before_the_send_reports_not_sent(stub):
    def interrupted(policy):
        raise KeyboardInterrupt

    code, doc, _ = run(
        ["--scenario", SUMMARY_ID, "--execute"],
        runner_env(stub.endpoint),
        client_factory=interrupted,
    )
    assert code == 2 and doc["provider_request_state"] == "not_sent"
    assert doc["provider_request_sent"] is False
    assert codes(doc) == {"interrupted_before_send"}


def test_an_interrupt_during_preflight_produces_one_line(stub, monkeypatch):
    def interrupted(config):
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "check_backend", interrupted)
    code, doc, _ = run(
        ["--scenario", SUMMARY_ID, "--execute"],
        runner_env(stub.endpoint),
        client_factory=NeverCalled(),
    )
    assert code == 2 and doc["provider_request_state"] == "not_sent"
    assert codes(doc) == {"interrupted_before_send"} and doc["mode"] == "execute"


def test_an_interrupt_during_the_flush_keeps_the_known_provider_outcome(stub, monkeypatch):
    def flush(self: Telemetry, timeout_millis: int | None = None) -> bool:
        raise KeyboardInterrupt

    monkeypatch.setattr(Telemetry, "force_flush", flush)
    fake = FakeGemini([answer_part(GOOD_SUMMARY), g_ok()])
    code, doc, _ = execute(stub, fake)
    assert code == 3 and len(fake.requests) == 1
    assert doc["provider"]["outcome"] == "completed"
    assert doc["provider_request_state"] == "sent"
    assert doc["export"]["outcome"] == "unknown" and doc["export"]["interrupted"] is True


def test_an_interrupt_outside_the_guarded_path_is_never_reported_as_not_sent(stub, monkeypatch):
    def interrupted(**kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(live_gemini, "run_single_request", interrupted)
    code, doc, _ = run(
        ["--scenario", SUMMARY_ID, "--execute"],
        runner_env(stub.endpoint),
        client_factory=NeverCalled(),
    )
    assert code == 1 and doc["provider_request_state"] == "possibly_sent"
    assert doc["provider_request_sent"] is None


def test_an_unexpected_error_is_sanitized(stub, monkeypatch):
    def boom(**kw):
        raise RuntimeError(FAKE_GEMINI_KEY)

    monkeypatch.setattr(live_gemini, "run_single_request", boom)
    code, doc, _ = run(
        ["--scenario", SUMMARY_ID, "--execute"],
        runner_env(stub.endpoint),
        client_factory=NeverCalled(),
    )
    assert code == 1 and doc["provider"]["outcome"] == "internal_error"


def test_a_second_line_is_never_written_after_the_result(stub, monkeypatch):
    calls: list[int] = []
    real_emit = runner.emit

    def emit_then_interrupt(out, doc):
        calls.append(1)
        real_emit(out, doc)
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "emit", emit_then_interrupt)
    code, doc, _ = run(["--scenario", SUMMARY_ID], runner_env(stub.endpoint))
    assert code == 0 and calls == [1] and doc["mode"] == "dry_run"


# ---------------------------------------------------------------- preview
def preview_run(stub, step: GStep, scenario: str = SUMMARY_ID, **kw: Any):
    fake = FakeGemini([step, g_ok()])
    err = Terminal()
    code, doc, text = run(
        ["--scenario", scenario, "--execute", "--preview"],
        runner_env(stub.endpoint),
        err=err,
        allowed_in_err=kw.pop("allowed_in_err", ()),
        client_factory=lambda policy: fake.client(),
    )
    assert len(fake.requests) == 1
    return code, doc, text


def test_preview_off_writes_nothing_to_stderr(stub):
    fake = FakeGemini([g_ok()])
    err = Terminal()
    code, doc, text = run(
        ["--scenario", SUMMARY_ID, "--execute"],
        runner_env(stub.endpoint),
        err=err,
        client_factory=lambda policy: fake.client(),
    )
    assert text == "" and doc["preview"]["requested"] is False
    assert doc["preview"]["shown"] is False
    assert code == 0 and doc["provider"]["outcome"] == "completed"  # the output was not shown


def test_preview_shows_only_the_synthetic_input_and_answer_on_stderr(stub):
    step = answer_part(OUTPUT_SENTINEL, thought=THOUGHT_SENTINEL)
    code, doc, text = preview_run(stub, step, allowed_in_err=(OUTPUT_SENTINEL,))
    assert OUTPUT_SENTINEL in text and THOUGHT_SENTINEL not in text
    assert FAKE_GEMINI_KEY not in text and DUMMY_KEY not in text
    assert "SYNTHETIC LLM Insight demo data" in text
    assert all(line.startswith("preview| ") for line in text.splitlines())
    preview = doc["preview"]
    assert preview["requested"] is True and preview["shown"] is True
    assert preview["output_shown"] is True and preview["thought_parts_excluded"] == 1
    assert preview["non_text_parts"] == 0 and preview["output_chars"] == len(OUTPUT_SENTINEL)
    assert OUTPUT_SENTINEL not in json.dumps(doc)


def test_preview_escapes_terminal_control_bytes(stub):
    hostile = "safe\x1b[2J\x1b]0;owned\x07\r‮gnirts\x9b"
    _, doc, text = preview_run(stub, answer_part(hostile))
    for raw in ("\x1b", "\x07", "\r", "‮", "\x9b"):
        assert raw not in text
    assert "\\x1b[2J" in text and "\\u202e" in text
    assert doc["preview"]["shown"] is True


def test_preview_is_bounded(stub):
    _, doc, text = preview_run(stub, answer_part("z" * 9000))
    body = [line for line in text.splitlines() if "zzz" in line]
    assert sum(len(line) - len("preview| ") for line in body) == 2000
    assert doc["preview"]["truncated"] is True and doc["preview"]["output_chars"] == 9000
    assert content_preview.TRUNCATION_MARKER in text


def test_incomplete_answers_are_labelled_in_the_preview(stub):
    code, doc, text = preview_run(
        stub,
        answer_part(OUTPUT_SENTINEL, finish="MAX_TOKENS"),
        allowed_in_err=(OUTPUT_SENTINEL,),
    )
    assert code == 3 and "TRUNCATED BY THE PROVIDER" in text and OUTPUT_SENTINEL in text


@pytest.mark.parametrize(
    ("step", "reason"),
    [
        (g_ok(finish="SAFETY"), "output_withheld_blocked"),
        (GStep(200, {}), "output_withheld_failed"),
        (g_fail(429, "RESOURCE_EXHAUSTED"), "output_withheld_error"),
        (GStep(raise_=httpx.ReadTimeout), "output_withheld_error"),
    ],
)
def test_nothing_is_previewed_for_blocked_or_failed_outcomes(stub, step, reason):
    code, doc, text = preview_run(stub, step)
    assert code == 1 and text == ""
    assert doc["preview"]["shown"] is False and doc["preview"]["reason"] == reason


def test_a_non_text_answer_withholds_only_the_output(stub):
    body = candidate("STOP")
    body["content"] = {
        "parts": [{"text": OUTPUT_SENTINEL}, {"functionCall": {"name": "lookup", "args": {}}}],
        "role": "model",
    }
    _, doc, text = preview_run(stub, g_ok(candidates=[body]))
    assert OUTPUT_SENTINEL not in text and "SYNTHETIC LLM Insight demo data" in text
    assert doc["preview"]["output_shown"] is False
    assert doc["preview"]["reason"] == "output_withheld_non_text"
    assert doc["preview"]["non_text_parts"] == 1


@pytest.mark.parametrize(
    ("overrides", "stream", "reason"),
    [
        ({}, io.StringIO, "stderr_not_a_terminal"),
        ({"CI": "true"}, Terminal, "ci_environment"),
        ({"GITHUB_ACTIONS": "true"}, Terminal, "ci_environment"),
    ],
)
def test_an_unsafe_preview_refuses_before_any_send(stub, overrides, stream, reason):
    never = NeverCalled()
    err = stream()
    code, doc, text = run(
        ["--scenario", SUMMARY_ID, "--execute", "--preview"],
        runner_env(stub.endpoint, **overrides),
        err=err,
        client_factory=never,
    )
    assert code == 2 and never.calls == 0 and text == ""
    assert "preview_not_safe" in codes(doc) and doc["execution_ready"] is False
    assert doc["preview"]["reason"] == reason and doc["preview"]["shown"] is False


def test_a_dry_run_preview_shows_the_input_only_and_sends_nothing(stub):
    never = NeverCalled()
    err = Terminal()
    code, doc, text = run(
        ["--scenario", SUMMARY_ID, "--preview"],
        runner_env(stub.endpoint),
        err=err,
        client_factory=never,
    )
    assert code == 0 and never.calls == 0
    assert "SYNTHETIC LLM Insight demo data" in text and "MODEL ANSWER" not in text
    assert doc["preview"]["shown"] is True and doc["preview"]["output_chars"] is None


def test_an_unsafe_dry_run_preview_is_refused_with_exit_zero(stub):
    code, doc, text = run(
        ["--scenario", SUMMARY_ID, "--preview"],
        runner_env(stub.endpoint),
        client_factory=NeverCalled(),
    )
    assert code == 0 and text == "" and doc["execution_allowed"] is False
    assert "preview_not_safe" in codes(doc)


def test_a_failed_preview_write_changes_nothing_else(stub, monkeypatch):
    monkeypatch.setattr(content_preview, "write_preview", lambda err, text: False)
    code, doc, _ = preview_run(stub, answer_part(GOOD_SUMMARY))
    assert code == 0 and doc["preview"]["shown"] is False
    assert doc["preview"]["reason"] == "stderr_write_failed"


# ---------------------------------------------------------------- links and project
@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("http://127.0.0.1:3001", "http://127.0.0.1:3001"),
        ("http://127.0.0.1:3001/", "http://127.0.0.1:3001"),
        ("http://localhost:3001", "http://localhost:3001"),
        ("http://[::1]:3001", "http://[::1]:3001"),
        ("http://127.0.0.1", "http://127.0.0.1"),
    ],
)
def test_loopback_origins_are_accepted(base, expected):
    assert runner.loopback_origin(base) == expected


def test_a_newline_cannot_survive_into_the_origin():
    assert runner.loopback_origin("http://127.0.0.1:3001\n") == "http://127.0.0.1:3001"


def test_links_carry_the_project():
    tid = "a" * 32
    assert runner.project_links("http://127.0.0.1:3001", tid, 42) == {
        "dashboard_url": f"http://127.0.0.1:3001/traces/{tid}?project=42",
        "api_path": f"/api/v1/traces/{tid}?project=42",
    }


@pytest.mark.parametrize(
    ("base", "trace", "project"),
    [(None, "a" * 32, 1), ("http://127.0.0.1", None, 1), ("http://127.0.0.1", "a" * 32, None)],
)
def test_no_partial_links_are_built(base, trace, project):
    assert runner.project_links(base, trace, project) == {"dashboard_url": None, "api_path": None}


@pytest.mark.parametrize(
    ("raw", "value"),
    [
        ("7", 7),
        (" 7 ", 7),
        ("9223372036854775807", 9223372036854775807),
        ("", None),
        ("0", None),
        ("07", None),
        ("1.5", None),
        ("1e3", None),
        ("١٢", None),
        ("9223372036854775808", None),
    ],
)
def test_project_id_parsing(raw, value):
    assert runner.project_id_from({"LLMI_DASHBOARD_PROJECT_ID": raw}) == value


# ---------------------------------------------------------------- checkout comparison
def checkout_tree(root: Path, *, change: str | None = None) -> None:
    source = root / "sdk" / "src" / "llm_insight_sdk"
    source.mkdir(parents=True)
    for name in runner._AUDITED_SDK_MODULES:
        real = REPO / "sdk" / "src" / "llm_insight_sdk" / f"{name}.py"
        data = real.read_bytes()
        (source / f"{name}.py").write_bytes(data + b"\n# changed\n" if name == change else data)


def test_tests_run_against_this_checkout():
    assert runner.sdk_checkout_match() is True


def test_an_identical_tree_matches(tmp_path):
    checkout_tree(tmp_path)
    assert runner.sdk_checkout_match(tmp_path) is True


@pytest.mark.parametrize("name", ["gemini_live", "gemini_adapter", "telemetry"])
def test_any_changed_audited_module_is_a_mismatch(tmp_path, name):
    checkout_tree(tmp_path, change=name)
    assert runner.sdk_checkout_match(tmp_path) is False


def test_a_missing_source_file_is_a_mismatch(tmp_path):
    checkout_tree(tmp_path)
    (tmp_path / "sdk" / "src" / "llm_insight_sdk" / "live.py").unlink()
    assert runner.sdk_checkout_match(tmp_path) is False


def test_no_source_tree_is_unknown(tmp_path):
    assert runner.sdk_checkout_match(tmp_path) == "unknown"


# ---------------------------------------------------------------- source properties
def test_the_runner_has_no_retry_fallback_or_second_send_path():
    for module in (runner, gemini_scenarios, content_preview):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for word in ("while ", "for attempt", "retry_options=", "fallback_model", "import openai"):
            assert word not in source, f"{module.__name__}: {word}"
    runner_source = Path(runner.__file__).read_text(encoding="utf-8")
    assert runner_source.count("run_single_request(") == 1


def test_the_command_line_can_only_reach_the_real_client():
    runner_source = Path(runner.__file__).read_text(encoding="utf-8")
    assert "client_factory" not in runner_source.split("def _build_parser", 1)[1].split("return")[0]
    assert 'provenance: Provenance = "real"' in runner_source


# ---------------------------------------------------------------- smoke runner regressions
SMOKE_MAX = {"LLMI_LIVE_MAX_INPUT_TOKENS": "256", "LLMI_LIVE_MAX_OUTPUT_TOKENS": "256"}


def smoke(argv: list[str], env: dict[str, str], **kw: Any) -> tuple[int, dict[str, Any]]:
    out = io.StringIO()
    kw.setdefault("now", NOW)
    kw.setdefault("provenance", "synthetic")
    code = live_gemini.main(argv, env, out=out, **kw)
    (line,) = out.getvalue().splitlines()
    return code, json.loads(line)


def test_smoke_links_are_unchanged(stub):
    fake = FakeGemini([g_ok()])
    _, doc = smoke(
        ["--execute"],
        runner_env(stub.endpoint, **SMOKE_MAX),
        client_factory=lambda policy: fake.client(),
    )
    tid = doc["trace_id"]
    assert doc["dashboard_url"] == f"http://127.0.0.1:3001/traces/{tid}?project=7"
    assert doc["api_path"] == f"/api/v1/traces/{tid}"


def test_smoke_output_schema_is_unchanged(stub):
    fake = FakeGemini([g_ok()])
    code, doc = smoke(
        ["--execute"],
        runner_env(stub.endpoint, **SMOKE_MAX),
        client_factory=lambda policy: fake.client(),
    )
    assert code == 0
    assert set(doc) == {
        "mode",
        "provider_request_sent",
        "execution_allowed",
        "refusals",
        "plan",
        "backend",
        "trace_id",
        "status",
        "incomplete_reason",
        "provider_reason",
        "error_category",
        "provenance",
        "provider",
        "request_model",
        "response_model",
        "attempts",
        "tokens",
        "provider_usage",
        "usage_check",
        "duration_ms",
        "cost",
        "session",
        "telemetry",
        "dashboard_url",
        "api_path",
    }
    assert set(doc["telemetry"]) == {"flushed", "spans_exported", "spans_export_failed"}


def test_smoke_keeps_its_fixed_input_and_root_span(stub):
    fake = FakeGemini([g_ok()])
    smoke(
        ["--execute"],
        runner_env(stub.endpoint, **SMOKE_MAX),
        client_factory=lambda policy: fake.client(),
    )
    assert fake.request_json(0)["contents"][0]["parts"] == [{"text": live_gemini.INPUT_TEXT}]
    assert live_gemini.ROOT_SPAN in {s["name"] for s in stub.spans()}
    assert runner.ROOT_SPAN not in {s["name"] for s in stub.spans()}


def test_smoke_exit_code_for_incomplete_stays_zero(stub):
    fake = FakeGemini([g_ok(finish="MAX_TOKENS")])
    code, doc = smoke(
        ["--execute"],
        runner_env(stub.endpoint, **SMOKE_MAX),
        client_factory=lambda policy: fake.client(),
    )
    assert code == 0 and doc["status"] == "incomplete"


def test_smoke_still_propagates_an_interrupt_after_settling(stub):
    fake = InterruptingGemini([g_ok()])
    out = io.StringIO()
    with pytest.raises(KeyboardInterrupt):
        live_gemini.main(
            ["--execute"],
            runner_env(stub.endpoint, **SMOKE_MAX),
            now=NOW,
            client_factory=lambda policy: fake.client(),
            provenance="synthetic",
            out=out,
        )
    assert out.getvalue() == "" and len(fake.requests) == 1


def test_smoke_still_accepts_its_abbreviated_flag(stub):
    """The smoke runner's public behaviour is unchanged by this phase; only the new runner
    refuses abbreviations (recorded limit, ADR-39)."""
    never = NeverCalled()
    code, doc = smoke(["--exec"], runner_env(stub.endpoint, LLMI_PROVIDER_MODE="mock"),
                      client_factory=never)  # fmt: skip
    assert code == 2 and doc["mode"] == "execute" and never.calls == 0
