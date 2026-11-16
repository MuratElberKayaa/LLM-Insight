"""Live A/B contract and orchestration tests; provider/network access is forbidden."""

from __future__ import annotations

import dataclasses
import io
import json
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from demo_app import live_compare  # noqa: E402
from demo_app import live_prompt_ab as ab  # noqa: E402
from demo_app import prompt_ab_scenarios as suite  # noqa: E402
from tests.unit.test_live_batch import output, quota  # noqa: E402


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A/B unit test attempted a network call")

    monkeypatch.setattr(socket.socket, "connect", forbidden)


def test_suite_actual_prompt_texts_pairs_bounds_and_immutable_pin():
    assert suite.PROMPTS["v1"] != suite.PROMPTS["v2"]
    assert suite.suite_digest() == suite.SUITE_SHA256
    assert len(suite.SCENARIOS) == len(suite.ordered_scenarios()) == 12
    for ticket in suite.CASES:
        a, b = [suite.SCENARIOS[f"cmp-ab-{ticket.id}-{v}"] for v in ("v1", "v2")]
        assert a.fixture == b.fixture == ticket.text
        assert a.task != b.task and a.input_sha256 != b.input_sha256
        for sc in (a, b):
            assert not suite.check_scenario(sc.id, sc)
            assert suite.check_scenario(sc.id, dataclasses.replace(sc, task="changed"))
            # Reference labels are an evaluation-only structure, not serialized into input.
            assert '"expected' not in suite.compose_input(sc)


@pytest.mark.parametrize("ticket", suite.CASES)
def test_same_rubric_checks_both_versions(ticket):
    answer = json.dumps({"category": ticket.category, "priority": ticket.priority})
    for variant in ("v1", "v2"):
        result = suite.SCENARIOS[f"cmp-ab-{ticket.id}-{variant}"].validate(answer)
        assert result.status == "passed" and len(result.rules) == 8
        wrong = suite.validate(ticket, '{"category":"not-a-category","priority":"high"}')
        assert wrong.status == "failed"


@pytest.mark.parametrize(
    "bad",
    [
        '```json\n{"category":"login","priority":"high"}\n```',
        '{"category":"login","category":"login","priority":"high"}',
        '{"category":"login","priority":"high","extra":0}',
        '{"category":"login","priority":"low"}',
        '{"category":null,"priority":NaN}',
        "[]",
        "not JSON",
    ],
)
def test_strict_validator_rejects_format_and_policy_errors(bad):
    assert suite.validate(suite.CASES[0], bad).status == "failed"


def result_for(job, passed=True):
    doc = output(job, execute=True)
    doc["suite"] = suite.SUITE
    doc["correlation"]["trace_id"] = f"{job['trial_id']:032x}"
    doc["validation"] = {
        "status": "passed" if passed else "failed",
        "rules": [
            {"rule": name, "passed": passed or name != "expected_priority"}
            for name in suite.RULE_NAMES
        ],
    }
    return doc


def test_failed_quality_is_a_recorded_result_not_a_transport_failure():
    job = ab.spec_for("a" * 32)["jobs"][0]
    rules = ab.validate_result(job, result_for(job, False), 3)
    assert not rules["expected_priority"]


@pytest.mark.parametrize(
    "path,value",
    [
        (("provider", "outcome"), "error"),
        (("provider", "attempts"), 2),
        (("provider", "response_model"), "other"),
        (("provider_request_state",), "possibly_sent"),
        (("export", "outcome"), "failed"),
        (("free_plan_violation",), "halted"),
        (("scenario", "input_sha256"), "other"),
        (("validation", "status"), "not_evaluated"),
    ],
)
def test_non_quality_failure_never_treated_as_evaluable(path, value):
    job = ab.spec_for("a" * 32)["jobs"][0]
    doc = result_for(job)
    obj = doc
    for k in path[:-1]:
        obj = obj[k]
    obj[path[-1]] = value
    with pytest.raises(ab.Refused):
        ab.validate_result(job, doc, 0)


def test_registry_selection_refuses_unknown_suite_before_client():
    out = io.StringIO()
    rc = live_compare.main(
        ["--provider", "groq", "--suite", "unregistered", "--scenario", "cmp-ticket-triage"],
        env={},
        out=out,
    )
    assert rc == 2 and json.loads(out.getvalue())["refusals"][0]["code"] == "suite_unknown"


def test_new_suite_reaches_policy_but_cannot_send_without_credentials():
    out = io.StringIO()
    rc = live_compare.main(
        ["--provider", "groq", "--suite", suite.SUITE, "--scenario", suite.ordered_scenarios()[0]],
        env={},
        out=out,
    )
    doc = json.loads(out.getvalue())
    assert rc == 0 and doc["execution_allowed"] is False
    assert doc["suite"] == suite.SUITE and doc["provider_request_state"] == "not_sent"
    assert doc["scenario"]["prompt_version"] == "live-ticket-v1"


@pytest.fixture
def harness(tmp_path, monkeypatch):
    now = datetime(2026, 11, 12, 18, tzinfo=UTC).timestamp()
    clock = [now]
    monkeypatch.setattr(ab, "STORE", tmp_path / "ab")
    monkeypatch.setattr(ab.batch, "STORE", tmp_path / "batches")
    monkeypatch.setattr(ab.time, "time", lambda: clock[0])
    monkeypatch.setattr(ab.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(ab.time, "sleep", lambda n: clock.__setitem__(0, clock[0] + n))
    monkeypatch.setattr(ab.batch, "code_fingerprint", lambda: "frozen-code")
    monkeypatch.setattr(ab, "check_reader", lambda source: None)
    env = {
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": ab.batch.ENDPOINT,
        "LLMI_DASHBOARD_PROJECT_ID": "1",
        "LLMI_BATCH_READ_TOKEN": "DUMMY-READ",
    }
    paths = {}
    for i, model in enumerate(ab.MODELS):
        p = tmp_path / f"quota-{i}.json"
        p.write_text(json.dumps(quota(model)))
        paths[model] = p
    sends = []

    def child(job, source, execute):
        if not execute:
            return 0, output(job)
        sends.append(job["trial_id"])
        passed = job["variant"] == "v2"
        return 0 if passed else 3, result_for(job, passed)

    monkeypatch.setattr(ab, "run_child", child)
    monkeypatch.setattr(ab, "verify_record", lambda *args: 123.5)
    return paths, env, sends, child


def test_complete_run_24_calls_ab_ba_no_quality_stop_no_reuse(harness):
    paths, env, sends, _ = harness
    plan = ab.prepare(paths, env)
    assert not sends and plan["status"] == "ready"
    result = ab.execute(plan["run_id"], plan["approve_sha256"], paths, env)
    assert result["verified_calls"] == 24 and sends == list(range(1, 25))
    report = ab.write_report(plan["run_id"])
    assert report["complete"]
    assert [g["passed"] for g in report["groups"]] == [0, 6, 0, 6]
    assert all(c["delta_passed_v2_minus_v1"] == 6 for c in report["comparisons"])
    assert report["winner"] is None
    with pytest.raises(ab.Refused, match="approval_consumed"):
        ab.execute(plan["run_id"], plan["approve_sha256"], paths, env)
    assert len(sends) == 24


def test_provider_failure_stops_without_retry_keeps_lock_and_partial_report(harness, monkeypatch):
    paths, env, sends, original = harness
    plan = ab.prepare(paths, env)

    def fail(job, source, execute):
        rc, doc = original(job, source, execute)
        if execute and job["trial_id"] == 2:
            doc["provider"]["outcome"] = "error"
            return 1, doc
        return rc, doc

    monkeypatch.setattr(ab, "run_child", fail)
    with pytest.raises(ab.Refused):
        ab.execute(plan["run_id"], plan["approve_sha256"], paths, env)
    assert sends == [1, 2]
    assert (ab.batch.STORE / "execution.lock").exists()
    report = ab.write_report(plan["run_id"])
    assert not report["complete"] and report["groups"][0]["verified"] == 1
    assert report["comparisons"][0]["delta_passed_v2_minus_v1"] is None


def test_changed_quota_and_code_prevent_all_sends(harness, monkeypatch):
    paths, env, sends, _ = harness
    plan = ab.prepare(paths, env)
    monkeypatch.setattr(ab.batch, "code_fingerprint", lambda: "changed-code")
    with pytest.raises(ab.Refused, match="plan_or_code_changed"):
        ab.execute(plan["run_id"], plan["approve_sha256"], paths, env)
    assert not sends


def test_incomplete_evidence_does_not_turn_into_zero_cost_or_winner():
    spec = ab.spec_for("b" * 32)
    report = ab.summarize(spec, [])
    assert all(
        g["pass_rate"] is None and g["cost_status"] == "unknown" and g["not_evaluated"] == 6
        for g in report["groups"]
    )
    assert report["winner"] is None and not report["complete"]


def test_same_label_with_different_instruction_is_not_silently_accepted():
    sc = suite.SCENARIOS[suite.ordered_scenarios()[0]]
    assert suite.check_scenario(sc.id, dataclasses.replace(sc, fixture="not the same case"))
