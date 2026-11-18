"""The actual Groq/Gemini adapters carry the two A/B texts; transports are fake."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from tests.groq_fixtures import ok
from tests.unit.test_compare_runner import NOW as GROQ_NOW
from tests.unit.test_compare_runner import offline, own_slot, runner_env  # noqa: F401
from tests.unit.test_compare_runner import released as groq_released  # noqa: F401
from tests.unit.test_compare_runner import wire as groq_wire  # noqa: F401
from tests.unit.test_compare_runner_gemini import NOW as GEMINI_NOW
from tests.unit.test_compare_runner_gemini import answer, compare_env
from tests.unit.test_compare_runner_gemini import wire as gemini_wire  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from demo_app import live_compare as runner  # noqa: E402
from demo_app import prompt_ab_scenarios as suite  # noqa: E402


@pytest.mark.parametrize("provider", ["groq", "gemini"])
@pytest.mark.parametrize("variant", ["v1", "v2"])
@pytest.mark.parametrize("valid", [True, False])
def test_actual_text_and_same_scoring_in_both_provider_paths(
    stub,
    request,
    provider,
    variant,
    valid,
):
    sc = suite.SCENARIOS[f"cmp-ab-t01-{variant}"]
    response = json.dumps({"category": "login", "priority": "high" if valid else "low"})
    if provider == "groq":
        request.getfixturevalue("groq_released")
        wire = request.getfixturevalue("groq_wire")
        wire.steps = [ok(content=response)]
        env, now = runner_env(stub.endpoint), GROQ_NOW
    else:
        request.getfixturevalue("gemini_released")
        wire = request.getfixturevalue("gemini_wire")
        wire.replies = [answer(response)]
        env, now = compare_env(stub.endpoint), GEMINI_NOW
    out = io.StringIO()
    rc = runner.main(
        ["--provider", provider, "--suite", suite.SUITE, "--scenario", sc.id, "--execute"],
        env=env,
        now=now,
        out=out,
    )
    doc = json.loads(out.getvalue())
    assert rc == (0 if valid else 3)
    assert doc["provider"]["outcome"] == "completed"
    assert doc["validation"]["status"] == ("passed" if valid else "failed")
    assert doc["scenario"]["input_sha256"] == sc.input_sha256
    requests = wire.requests if provider == "groq" else wire
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    text = (
        payload["messages"][0]["content"]
        if provider == "groq"
        else payload["contents"][0]["parts"][0]["text"]
    )
    assert text == suite.compose_input(sc)
    assert sc.task not in out.getvalue() and sc.fixture not in out.getvalue()
    assert response not in out.getvalue()
    for record in stub.received:
        assert sc.task.encode() not in record.body
        assert sc.fixture.encode() not in record.body
        assert response.encode() not in record.body
