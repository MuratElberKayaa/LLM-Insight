"""Evidence for the three-provider comparison (ADR-49): Groq, Gemini and OpenRouter on the same
pinned scenario, each through its own guard and the one comparison runner:

    runner → provider guard → audited client → SYNTHETIC wire → adapter → OTLP/HTTP protobuf
           → live `python -m app` → disposable PostgreSQL → Overview and Models / Compare

The provider side is OFFLINE: only `httpx.HTTPTransport.handle_request` is replaced, so each
runner client, endpoint check and guard runs, and every non-loopback connection is blocked. The
Groq and OpenRouter locks are lifted inside this test process only (`released`). Gemini has no
execution flag: its lock is the Free Tier evidence window, and this module's clock lies inside it.
As in the other comparison modules (R129), the runner has only the real path, so its spans carry
`llminsight.provenance = real` although every response is SYNTHETIC; nothing here is evidence
about a real provider response. Each execution stands for one process: it gets a fresh slot.
"""

from __future__ import annotations

import io
import json
import sys
import threading
import uuid
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import pytest

import llm_insight_sdk as lli
from llm_insight_sdk import (
    gemini_live,
    groq_adapter,
    groq_live,
    openrouter_adapter,
    openrouter_live,
)
from llm_insight_sdk.groq_live import FreePlanModel
from llm_insight_sdk.openrouter_live import FreeModel
from tests.conftest import DUMMY_KEY
from tests.gemini_fixtures import FAKE_GEMINI_KEY, THOUGHT_SENTINEL, gemini_body
from tests.gemini_fixtures import MODEL as GEMINI_MODEL
from tests.groq_fixtures import MODEL as GROQ_MODEL
from tests.groq_fixtures import FakeGroq, Step
from tests.groq_fixtures import ok as groq_ok
from tests.integration.conftest import LiveBackend
from tests.openai_fixtures import block_network
from tests.openrouter_fixtures import MODEL as OPENROUTER_MODEL
from tests.openrouter_fixtures import SENTINELS
from tests.openrouter_fixtures import ok as openrouter_ok
from tests.unit.test_gemini_live_guard import gemini_env
from tests.unit.test_groq_live_guard import ENV as GROQ_ENV
from tests.unit.test_openrouter_live_guard import ENV as OPENROUTER_ENV
from tests.unit.test_openrouter_live_guard import NOW

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))  # demo_app lives at the repository root, outside the SDK

from demo_app import comparison_scenarios as cs  # noqa: E402
from demo_app import live_compare as runner  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("gemini_released")]  # ADR-50

SCENARIO = "cmp-ticket-triage"
PROMPT_VERSION = "cmp-v1.ticket-triage"
TODAY = NOW.astimezone(UTC).date().isoformat()
ANSWER = json.dumps({"category": "login", "priority": "high", "reason": "Sign-in fails."})
MODELS = {"groq": GROQ_MODEL, "gcp.gemini": GEMINI_MODEL, "openrouter": OPENROUTER_MODEL}
PROMPT_TEXTS = tuple(
    text for s in cs.SCENARIOS.values() for text in (s.task, s.fixture, cs.compose_input(s))
)


@pytest.fixture(autouse=True)
def offline() -> Iterator[None]:
    attempts: list[str] = []
    lli.shutdown(timeout_millis=3000)
    with block_network(attempts):
        yield
    lli.shutdown(timeout_millis=3000)
    assert attempts == [], f"a test tried to reach the network: {attempts}"


@pytest.fixture
def released(monkeypatch) -> None:
    monkeypatch.setattr(groq_adapter, "REAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(groq_live, "FREE_PLAN_EVIDENCE_VERIFIED_AT", NOW - timedelta(days=1))
    monkeypatch.setattr(
        groq_live,
        "FREE_PLAN_MODELS",
        {GROQ_MODEL: FreePlanModel(GROQ_MODEL, ("https://example.invalid/synthetic",))},
    )
    monkeypatch.setattr(openrouter_adapter, "REAL_EXECUTION_ENABLED", True)
    for name in ("FREE_MODEL_EVIDENCE_VERIFIED_AT", "ZERO_PRICE_ROUTING_VERIFIED_AT"):
        monkeypatch.setattr(openrouter_live, name, NOW - timedelta(days=1))
    monkeypatch.setattr(
        openrouter_live,
        "FREE_MODELS",
        {OPENROUTER_MODEL: FreeModel(OPENROUTER_MODEL, ("https://example.invalid/synthetic",))},
    )


@pytest.fixture
def wire(monkeypatch) -> FakeGroq:
    fake = FakeGroq([])
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", lambda self, request: fake.handler(request)
    )
    return fake


def provider_env(name: str) -> dict[str, str]:
    """Each guard test's SYNTHETIC environment, attested on this module's clock."""
    if name == "groq":
        return {**GROQ_ENV, "LLMI_GROQ_PLAN_CHECKED_ON": TODAY}
    if name == "openrouter":
        return {**OPENROUTER_ENV, "LLMI_OPENROUTER_ACCOUNT_CHECKED_ON": TODAY}
    return gemini_env(LLMI_GEMINI_TIER_CHECKED_ON=TODAY, LLMI_LIVE_MAX_INPUT_TOKENS="1024")


def env_for(name: str, live: LiveBackend, key: str, project_id: int) -> dict[str, str]:
    env = provider_env(name)
    env.update(
        {
            "LLMI_LIVE_MAX_OUTPUT_TOKENS": "1024",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": live.ingest_endpoint,
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": f"X-Project-Key={key}",
            "OTEL_SERVICE_NAME": "live-compare-three-it-SYNTHETIC",
            "LLMI_SDK_SCHEDULE_DELAY_MILLIS": "100",
            "LLMI_DASHBOARD_PROJECT_ID": str(project_id),
        }
    )
    return env


def invoke(name: str, env: Mapping[str, str]) -> tuple[int, dict[str, Any]]:
    out = io.StringIO()
    argv = ["--provider", name, "--scenario", SCENARIO, "--execute"]
    code = runner.main(argv, env, now=NOW, out=out)
    (line,) = out.getvalue().splitlines()
    for forbidden in (*SENTINELS, *PROMPT_TEXTS, THOUGHT_SENTINEL, FAKE_GEMINI_KEY, ANSWER):
        assert forbidden not in line
    return code, json.loads(line)


def gemini_step() -> Step:
    parts = [{"text": THOUGHT_SENTINEL, "thought": True}, {"text": ANSWER}]
    candidates = [
        {"content": {"parts": parts, "role": "model"}, "index": 0, "finishReason": "STOP"}
    ]
    return Step(200, gemini_body(candidates=candidates, response_id=f"SYNTH{uuid.uuid4().hex}"))


def test_the_gemini_clock_lies_inside_the_shipped_evidence_window():
    """Database-free: the module's clock must be one the shipped Gemini evidence still covers."""
    verified = gemini_live.FREE_TIER_VERIFIED_AT
    assert verified <= NOW <= verified + gemini_live.FREE_TIER_MAX_AGE
    decision = gemini_live.evaluate_free_tier_policy(provider_env("gemini"), now=NOW)
    assert decision.allowed, sorted(r.code for r in decision.refusals)


@pytest.mark.usefixtures("released")
def test_three_providers_on_one_scenario_give_three_compare_rows(groq_price, wire, monkeypatch):
    live = groq_price
    key, prefix = live.create_key(f"sdk-compare-three-it-{uuid.uuid4().hex[:16]}")
    (row,) = live.query("SELECT project_id FROM project_api_keys WHERE prefix = %s", (prefix,))
    project_id = int(row[0])
    started = datetime.now(UTC)
    steps = {
        "groq": groq_ok(content=ANSWER, response_id=f"chatcmpl-SYNTHETIC-{uuid.uuid4().hex}"),
        "gemini": gemini_step(),
        "openrouter": openrouter_ok(
            content=ANSWER, response_id=f"gen-SYNTHETIC-{uuid.uuid4().hex}"
        ),
    }
    docs = {}
    for name, step in steps.items():
        monkeypatch.setattr(groq_live, "_PROCESS_SLOT", threading.Lock())  # one process each
        wire.steps = [step]
        code, doc = invoke(name, env_for(name, live, key, project_id))
        assert code == 0, (name, doc["refusals"], doc["provider"])
        docs[name] = doc
    assert len(wire.requests) == 3

    # The same pinned text and cap reached every provider.
    shas = {d["scenario"]["input_sha256"] for d in docs.values()}
    assert shas == {cs.SCENARIOS[SCENARIO].input_sha256}
    assert {d["plan"]["max_output_tokens"] for d in docs.values()} == {1024}

    scope = {"project": project_id, **_window(started)}
    groups = live.api(f"/api/v1/models/compare?{urlencode(scope)}")["groups"]
    by_provider = {g["provider"]: g for g in groups}
    assert set(by_provider) == set(MODELS) and len(groups) == 3
    for provider, group in by_provider.items():
        assert group["requested_model"] == MODELS[provider]
        assert group["prompt_version"] == PROMPT_VERSION and group["attempts"] == 1
        assert group["cost"]["known_cost_usd"] is None and group["cost"]["cost_complete"] is False
        assert group["latency"]["low_sample"] and all(group["latency"]["low_sample"].values())
    overview = live.api(f"/api/v1/overview?{urlencode(scope)}")
    cost = overview["inference"]["cost"]["total"]
    assert cost["known_cost_usd"] is None and cost["unknown_cost_calls"] == 3

    stored = live.query(
        "SELECT row_to_json(s) FROM spans s WHERE project_id = %s"
        " UNION ALL SELECT row_to_json(u) FROM usage_records u WHERE project_id = %s",
        (project_id, project_id),
    )
    text = json.dumps(stored, default=str)
    for forbidden in (*SENTINELS, *PROMPT_TEXTS, THOUGHT_SENTINEL, FAKE_GEMINI_KEY, key, DUMMY_KEY):
        assert forbidden not in text


def _window(started: datetime) -> dict[str, str]:
    return {
        "from": (started - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": (datetime.now(UTC) + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
