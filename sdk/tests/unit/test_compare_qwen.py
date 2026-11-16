"""Offline adapter/guard/runner integration. Provider HTTP is a scripted fake."""

from __future__ import annotations

import json

import httpx
import pytest

from llm_insight_sdk import groq_adapter, groq_live
from tests.groq_fixtures import ok
from tests.unit.test_compare_runner import (
    GOOD,
    NOW,
    TICKET,
    offline,  # noqa: F401 - imported pytest fixture blocks external connections
    own_slot,  # noqa: F401
    released,  # noqa: F401
    run,
    runner_env,
)
from tests.unit.test_compare_runner import (
    wire as fake_wire,  # noqa: F401
)

QWEN = "qwen/qwen3.8-27b"


@pytest.mark.usefixtures("released", "own_slot", "offline")
def test_qwen_through_existing_runner_exports_one_trace(stub, fake_wire, monkeypatch):  # noqa: F811
    wire = fake_wire
    # Account evidence is synthetic and released ONLY inside this offline test.
    monkeypatch.setattr(
        groq_live, "FREE_PLAN_MODELS", {QWEN: groq_live.FreePlanModel(QWEN, ("test:synthetic",))}
    )
    wire.steps = [ok(model=QWEN, content=GOOD[TICKET])]
    env = runner_env(stub.endpoint, LLMI_LIVE_MODELS=QWEN)
    code, doc, _ = run(["--provider", "groq", "--scenario", TICKET, "--execute"], env, now=NOW)
    assert code == 0
    assert len(wire.requests) == 1
    body = json.loads(wire.requests[0].content)
    assert body["model"] == QWEN and body["reasoning_format"] == "hidden"
    assert "include_reasoning" not in body and "service_tier" not in body
    assert doc["constraints"]["reasoning_format"] == "hidden"
    assert doc["export"]["outcome"] == "confirmed"
    assert doc["validation"]["status"] == "passed"
    assert doc["usage"]["cost"]["status"] == "unknown"
    # A new session in the same process still cannot obtain another send slot.
    code, doc, _ = run(["--provider", "groq", "--scenario", TICKET, "--execute"], env, now=NOW)
    assert code != 0 and len(wire.requests) == 1


def test_qwen_evidence_release_preserves_single_request_cap():
    assert QWEN in groq_live.FREE_PLAN_MODELS
    assert groq_live.MAX_REQUESTS_CAP == 1


@pytest.mark.parametrize("model", [QWEN, "openai/gpt-oss-20b"])
def test_guard_accepts_only_model_specific_hidden_reasoning(model):
    body = json.loads(groq_adapter._request_body(model, "synthetic", 128))

    def read(value):
        return groq_live._read_body(httpx.Request("POST", groq_adapter.ENDPOINT_URL, json=value))

    assert read(body) is not None
    if model == QWEN:
        body["reasoning_format"] = "raw"
    else:
        body["include_reasoning"] = True
    assert read(body) is None


def test_qwen_refused_without_account_evidence(stub, fake_wire, monkeypatch):  # noqa: F811
    wire = fake_wire
    monkeypatch.setattr(groq_live, "FREE_PLAN_MODELS", {})
    env = runner_env(stub.endpoint, LLMI_LIVE_MODELS=QWEN)
    code, doc, _ = run(["--provider", "groq", "--scenario", TICKET, "--execute"], env, now=NOW)
    assert code == 2 and not wire.requests
    assert "model_not_free_plan_verified" in {r["code"] for r in doc["refusals"]}
