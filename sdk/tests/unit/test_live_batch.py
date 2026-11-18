"""Offline batch state-machine tests: no sockets, database or provider credentials."""

from __future__ import annotations

import copy
import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from demo_app import live_batch as b  # noqa: E402

MODEL = "gemini-3.1-flash-lite"
SCENARIO = "cmp-ticket-triage"
NOW = datetime(2026, 11, 12, 18, tzinfo=UTC).timestamp()


def iso(seconds):
    return datetime.fromtimestamp(seconds, UTC).isoformat()


def quota(model=MODEL):
    groq_model = model in {"openai/gpt-oss-20b", "qwen/qwen3.8-27b"}
    return {
        "model": model,
        "checked_at": iso(NOW),
        "valid_until": iso(NOW + 3500),
        "daily_reset_at": iso(NOW + 3600),
        "exclusive_account": True,
        "all_scopes_checked": True,
        "free_account": True,
        "model_access": True,
        "project_1_ingest": True,
        "limits": (
            {"rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000, "itpm": None, "otpm": None}
            if groq_model
            else {"rpm": 15, "rpd": 500, "tpm": 250000, "tpd": None, "itpm": None, "otpm": None}
        ),
        "daily_used": {"requests": 3, "tokens": 800},
        "usage_basis": "account_usage_snapshot",
    }


def output(spec, execute=False):
    return {
        "mode": "execute" if execute else "dry_run",
        "execution_allowed": True,
        "refusals": [],
        "environment": {"backend": {"ready": True}, "sdk_checkout_match": True},
        "plan": {
            "provider": spec["provider"],
            "model": spec["model"],
            "max_requests": 1,
            "max_retries": 0,
            "max_concurrency": 1,
            "paid_fallback": False,
            "max_output_tokens": 1024,
            "input_token_bound": spec["max_input_tokens"],
            **({"timeout_seconds": spec["timeout_seconds"]} if "timeout_seconds" in spec else {}),
        },
        "comparison": {"project_id": 1},
        "scenario": {
            "id": spec["scenario"],
            "input_sha256": spec["input_sha256"],
            "prompt_version": spec["prompt_version"],
        },
        "provider_request_state": "sent" if execute else "not_sent",
        "provider": {
            "provider": spec["provider"],
            "request_model": spec["model"],
            "response_model": spec["model"],
            "attempts": 1,
            "outcome": "completed",
            "provenance": "real",
            "duration_ms": 42,
        },
        "correlation": {"trace_id": "a" * 32, "project_id": 1},
        "usage": {"tokens": {"input": 150, "output": 40, "reasoning_output": None}},
        "export": {"outcome": "confirmed", "spans_export_failed": 0},
        "validation": {"status": "passed"},
        "free_plan_violation": None,
    }


@pytest.fixture
def harness(tmp_path, monkeypatch, request):
    def no_network(*args, **kwargs):
        pytest.fail("offline test attempted a socket connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(b, "STORE", tmp_path / "batches")
    clock = [NOW]
    monkeypatch.setattr(b.time, "time", lambda: clock[0])
    monkeypatch.setattr(b.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(b.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(b, "code_fingerprint", lambda: "reviewed-code")
    model = getattr(request, "param", MODEL)
    spec = b.spec_for(model, SCENARIO, "b" * 32)
    quota_path, smoke = tmp_path / "quota.json", tmp_path / "smoke.json"
    quota_path.write_text(json.dumps(quota(model)), encoding="utf-8")
    smoke.write_text(json.dumps(output(spec, True)), encoding="utf-8")
    env = {
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": b.ENDPOINT,
        "LLMI_DASHBOARD_PROJECT_ID": "1",
        "LLMI_BATCH_READ_TOKEN": "DUMMY-READ-TOKEN",
    }
    calls, verified, send_times = [], [], []

    def child(spec, env, execute):
        calls.append(execute)
        if execute:
            send_times.append(clock[0])
        d = output(spec, execute)
        if execute:
            d["correlation"]["trace_id"] = f"{calls.count(True):032x}"
        return 0, d

    def verify(spec, record, env):
        verified.append(record["trial_id"])
        return NOW - 60

    monkeypatch.setattr(b, "run_child", child)
    monkeypatch.setattr(b, "verify_trace", verify)

    def plan(requests=20, smoke_first=False):
        return b.prepare(
            model,
            SCENARIO,
            quota_path,
            None if smoke_first else smoke,
            env,
            requests,
            smoke_first,
        )

    def execute(p):
        return b.execute(p["spec"]["batch_id"], p["approve_sha256"], quota_path, smoke, env)

    return SimpleNamespace(
        q=quota_path,
        smoke=smoke,
        env=env,
        calls=calls,
        verified=verified,
        send_times=send_times,
        clock=clock,
        plan=plan,
        execute=execute,
        child=child,
        spec=spec,
        model=model,
    )


def test_twenty_sequential_sends_one_approval_no_reuse(harness):
    h = harness
    p = h.plan()
    assert True not in h.calls
    assert p["spec"]["planned_requests"] == 20
    result = h.execute(p)
    assert result["verified_trials"] == 20 and not result["authorization_active"]
    assert h.calls == [False] + [False, True] * 20
    assert h.verified == [0, 0] + list(range(1, 21))
    assert h.clock[0] >= NOW + 20 * p["spec"]["interval_seconds"] - 0.001
    with pytest.raises(b.Refused):
        h.execute(p)
    assert h.calls.count(True) == 20
    journal = (b.STORE / p["spec"]["batch_id"] / "journal.jsonl").read_text()
    assert "DUMMY-READ-TOKEN" not in journal
    assert journal.count('"event": "reserved"') == 20


def test_fifteen_request_plan_executes_only_fifteen_sequential_sends(harness):
    h = harness
    p = h.plan(15)
    assert p["spec"]["planned_requests"] == 15
    result = h.execute(p)
    assert result["verified_trials"] == 15 and not result["authorization_active"]
    assert h.calls == [False] + [False, True] * 15
    assert h.verified == [0, 0] + list(range(1, 16))
    journal = (b.STORE / p["spec"]["batch_id"] / "journal.jsonl").read_text()
    assert journal.count('"event": "reserved"') == 15


def test_approved_plan_reuses_same_snapshot_within_its_validity_window(harness):
    h = harness
    p = h.plan()
    quota_hash = b.digest(b.read(h.q))
    h.clock[0] += 301  # beyond plan's five-minute freshness threshold
    assert h.execute(p)["verified_trials"] == 20
    assert b.digest(b.read(h.q)) == quota_hash


def test_gemini_batch_paces_below_fifteen_rpm(harness):
    h = harness
    p = h.plan()
    assert p["spec"]["interval_seconds"] == 4.2
    assert p["spec"]["timeout_seconds"] == 30.0
    assert b.child_env(MODEL, h.env)["LLMI_LIVE_TIMEOUT_SECONDS"] == "30"
    assert h.execute(p)["verified_trials"] == 20
    assert all(
        current - previous >= 4.199
        for previous, current in zip(h.send_times, h.send_times[1:], strict=False)
    )
    # At 4.2 seconds between sends, 15 calls span 58.8 seconds.
    assert len(h.send_times) == 20


def test_smoke_first_uses_trial_one_then_one_approval_for_remaining_nineteen(harness):
    h = harness
    p = h.plan(smoke_first=True)
    assert p["spec"]["planned_requests"] == 20
    assert p["spec"]["smoke_first"] is True
    assert p["spec"]["smoke_sha256"] is None
    result = h.execute(p)
    assert result["verified_trials"] == 20
    events = [
        json.loads(line)
        for line in (b.STORE / p["spec"]["batch_id"] / "journal.jsonl").read_text().splitlines()
    ]
    smoke_index = next(i for i, event in enumerate(events) if event["event"] == "smoke_verified")
    second_reservation = next(
        i
        for i, event in enumerate(events)
        if event["event"] == "reserved" and event["trial_id"] == 2
    )
    assert smoke_index < second_reservation
    assert len([event for event in events if event["event"] == "reserved"]) == 20
    assert h.calls.count(True) == 20


def test_smoke_first_failure_stops_before_trial_two_and_keeps_lock(harness, monkeypatch):
    h = harness
    p = h.plan(smoke_first=True)

    def child(spec, env, execute):
        rc, doc = h.child(spec, env, execute)
        if execute:
            doc["validation"]["status"] = "failed"
        return rc, doc

    monkeypatch.setattr(b, "run_child", child)
    with pytest.raises(b.Refused, match="validation_failed"):
        h.execute(p)
    assert h.calls.count(True) == 1
    assert (b.STORE / "execution.lock").exists()
    events = [
        json.loads(line)
        for line in (b.STORE / p["spec"]["batch_id"] / "journal.jsonl").read_text().splitlines()
    ]
    assert not any(event["event"] == "smoke_verified" for event in events)
    assert not any(event["event"] == "reserved" and event["trial_id"] == 2 for event in events)
    with pytest.raises((b.Refused, FileExistsError)):
        h.execute(p)
    assert h.calls.count(True) == 1


def test_smoke_first_duplicate_trace_stops_without_second_retry(harness, monkeypatch):
    h = harness
    p = h.plan(smoke_first=True)

    def child(spec, env, execute):
        rc, doc = h.child(spec, env, execute)
        if execute and h.calls.count(True) > 1:
            doc["correlation"]["trace_id"] = f"{1:032x}"
        return rc, doc

    monkeypatch.setattr(b, "run_child", child)
    with pytest.raises(b.Refused, match="duplicate_trace"):
        h.execute(p)
    assert h.calls.count(True) == 2
    events = [
        json.loads(line)
        for line in (b.STORE / p["spec"]["batch_id"] / "journal.jsonl").read_text().splitlines()
    ]
    assert [e["trial_id"] for e in events if e["event"] == "reserved"] == [1, 2]
    assert (b.STORE / "execution.lock").exists()


def test_smoke_first_uncertain_send_is_reserved_and_never_retried(harness, monkeypatch):
    h = harness
    p = h.plan(smoke_first=True)

    def child(spec, env, execute):
        h.calls.append(execute)
        if execute:
            raise KeyboardInterrupt
        return 0, output(spec, False)

    monkeypatch.setattr(b, "run_child", child)
    with pytest.raises(b.Refused, match="interrupted_or_internal_error"):
        h.execute(p)
    state = b.inspect(p["spec"]["batch_id"])
    assert state["possibly_sent_without_result"] == [1]
    assert state["approval_consumed"] and not state["resume_allowed"]
    assert (b.STORE / "execution.lock").exists()
    assert h.calls.count(True) == 1


def test_groq_120b_needs_its_own_verified_account_quota(harness):
    q = quota("openai/gpt-oss-120b")
    with pytest.raises(b.Refused, match="quota_model_unverified"):
        b.quota_check(q, "openai/gpt-oss-120b", NOW)


def test_gemini_quota_refresh_reuses_schema_and_only_advances_time_after_attestation(tmp_path):
    source_path, output_path = tmp_path / "old-quota.json", tmp_path / "fresh-quota.json"
    prior = quota(MODEL)
    prior.update(
        daily_reset_at=None,
        daily_used=None,
        usage_basis="operator_attested_sufficient_headroom",
    )
    source_path.write_text(json.dumps(prior), encoding="utf-8")
    original = source_path.read_bytes()
    env = {
        "GEMINI_API_KEY": "secret-not-returned",
        "LLMI_BATCH_READ_TOKEN": "read-secret-not-returned",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": b.ENDPOINT,
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS": f"x-project-key=lli_Abcdef12_{'a' * 43}",
        "OTEL_SERVICE_NAME": "llm-insight-demo",
        "LLMI_DASHBOARD_PROJECT_ID": "1",
    }
    phrase = "ATTEST GEMINI FREE 15 500 250000 HEADROOM-12 PROJECT-1"
    prompts = []

    def confirm(prompt):
        prompts.append(prompt)
        return phrase

    result = b.refresh_gemini_quota_snapshot(
        source_path, output_path, env, confirm=confirm, planned_requests=12
    )
    saved = b.read(output_path)
    assert result["status"] == "quota_snapshot_refreshed"
    assert saved["checked_at"] != prior["checked_at"]
    assert saved["valid_until"] != prior["valid_until"]
    assert saved["daily_used"] is None and saved["daily_reset_at"] is None
    assert saved["usage_basis"] == "operator_attested_sufficient_headroom"
    assert source_path.read_bytes() == original
    assert "secret-not-returned" not in json.dumps(result)
    assert "ingest-secret" not in output_path.read_text(encoding="utf-8")
    assert len(prompts) == 1 and "sufficient remaining headroom for 12 requests" in prompts[0]


def test_gemini_quota_refresh_requires_environment_and_exact_operator_confirmation(tmp_path):
    source_path, output_path = tmp_path / "old.json", tmp_path / "new.json"
    prior = quota(MODEL)
    prior.update(
        daily_reset_at=None, daily_used=None, usage_basis="operator_attested_sufficient_headroom"
    )
    source_path.write_text(json.dumps(prior), encoding="utf-8")
    with pytest.raises(b.Refused, match="missing_batch_environment"):
        b.refresh_gemini_quota_snapshot(source_path, output_path, {})
    env = {
        "GEMINI_API_KEY": "secret",
        "LLMI_BATCH_READ_TOKEN": "secret",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": b.ENDPOINT,
        "OTEL_EXPORTER_OTLP_HEADERS": f"x-project-key=lli_Abcdef12_{'a' * 43}",
        "OTEL_SERVICE_NAME": "llm-insight-demo",
        "LLMI_DASHBOARD_PROJECT_ID": "1",
    }
    with pytest.raises(b.Refused, match="quota_attestation_not_confirmed"):
        b.refresh_gemini_quota_snapshot(source_path, output_path, env, confirm=lambda _: "no")
    assert not output_path.exists()


def test_quota_cli_confirmation_prompt_does_not_contaminate_json_stdout(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda: "accepted")
    assert b.console_confirm("current quota confirmation") == "accepted"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "current quota confirmation" in captured.err


def test_approval_cannot_authorize_another_batch(harness):
    p, other = harness.plan(), harness.plan()
    with pytest.raises(b.Refused, match="approval_mismatch"):
        b.execute(
            other["spec"]["batch_id"], p["approve_sha256"], harness.q, harness.smoke, harness.env
        )
    assert True not in harness.calls


@pytest.mark.parametrize(
    "field,value",
    [
        ("model", "qwen/qwen3.8-27b"),
        ("planned_requests", 21),
        ("scenario", "cmp-release-summary"),
        ("max_output_tokens", 9999),
    ],
)
def test_mutated_plan_not_approved(harness, field, value):
    p = harness.plan()
    path = b.STORE / p["spec"]["batch_id"] / "plan.json"
    d = b.read(path)
    d["spec"][field] = value
    path.write_text(json.dumps(d))
    with pytest.raises(b.Refused, match="approval_mismatch"):
        harness.execute(p)
    assert True not in harness.calls


@pytest.mark.parametrize(
    "key,value",
    [
        ("exclusive_account", False),
        ("model_access", False),
        ("free_account", False),
        ("all_scopes_checked", False),
        ("project_1_ingest", False),
        ("checked_at", iso(NOW + 1)),
        ("valid_until", iso(NOW)),
        ("daily_reset_at", iso(NOW + 20)),
        ("model", "openai/gpt-oss-20b"),
    ],
)
def test_quota_fails_closed(harness, key, value):
    q = quota()
    q[key] = value
    with pytest.raises(b.Refused):
        b.quota_check(q, MODEL, NOW)


@pytest.mark.parametrize(
    "key,value",
    [("rpm", None), ("rpd", True), ("tpd", -1), ("tpm", 2047), ("itpm", 100), ("otpm", 100)],
)
def test_unknown_or_insufficient_limits(harness, key, value):
    q = quota()
    q["limits"][key] = value
    with pytest.raises(b.Refused):
        b.quota_check(q, MODEL, NOW)


def test_full_daily_budget_reserved_including_failures(harness):
    q = quota()
    q["daily_used"]["requests"] = 481
    with pytest.raises(b.Refused, match="daily_requests"):
        b.quota_check(q, MODEL, NOW)
    q = quota("openai/gpt-oss-20b")
    q["limits"].update(rpm=30, rpd=1000, tpm=8000, tpd=200000)
    q["daily_used"]["tokens"] = 200000 - 20 * 2048 + 1
    with pytest.raises(b.Refused, match="daily_tokens"):
        b.quota_check(q, q["model"], NOW)


def test_fifteen_request_quota_reserves_only_fifteen_daily_slots(harness):
    q = quota()
    q["daily_used"]["requests"] = 481
    b.quota_check(q, MODEL, NOW, planned_requests=15)
    with pytest.raises(b.Refused, match="daily_requests_insufficient"):
        b.quota_check(q, MODEL, NOW)


def test_operator_headroom_attestation_records_no_invented_usage(harness):
    q = quota()
    q["usage_basis"] = "operator_attested_sufficient_headroom"
    q["daily_used"] = None
    q["daily_reset_at"] = None
    b.quota_check(q, MODEL, NOW, fresh=True)
    q["daily_used"] = {"requests": 0, "tokens": 0}
    with pytest.raises(b.Refused, match="quota_attestation_must_not_invent"):
        b.quota_check(q, MODEL, NOW)


@pytest.mark.parametrize(
    "mutation",
    [
        "rate_limit",
        "permission",
        "quota",
        "transport",
        "possibly_sent",
        "export",
        "validation",
        "model",
    ],
)
def test_failure_never_retries_and_retains_lock(harness, monkeypatch, mutation):
    h = harness
    p = h.plan()

    def child(spec, env, execute):
        rc, d = h.child(spec, env, execute)
        if execute:
            if mutation in ("rate_limit", "permission", "quota", "transport"):
                d["provider"].update(outcome="error", error_category=mutation)
                rc = 1
            elif mutation == "possibly_sent":
                d["provider_request_state"] = "possibly_sent"
            elif mutation == "export":
                d["export"]["outcome"] = "failed"
            elif mutation == "validation":
                d["validation"]["status"] = "failed"
            else:
                d["provider"]["response_model"] = "unexpected-model"
        return rc, d

    monkeypatch.setattr(b, "run_child", child)
    with pytest.raises(b.Refused):
        h.execute(p)
    assert h.calls.count(True) == 1
    assert (b.STORE / "execution.lock").exists()
    assert b.inspect(p["spec"]["batch_id"])["approval_consumed"]
    with pytest.raises((b.Refused, FileExistsError)):
        h.execute(p)
    assert h.calls.count(True) == 1


def test_response_rate_limit_headroom_exhaustion_stops_after_one_send(harness, monkeypatch):
    h = harness
    p = h.plan()

    def child(spec, env, execute):
        rc, d = h.child(spec, env, execute)
        if execute:
            d["provider"]["rate_limit_signals"] = {
                "requests_remaining": 19,
                "tokens_remaining": 1024,
            }
        return rc, d

    monkeypatch.setattr(b, "run_child", child)
    with pytest.raises(b.Refused, match="provider_rate_limit_headroom_exhausted"):
        h.execute(p)
    assert h.calls.count(True) == 1
    events = [
        json.loads(line)
        for line in (b.STORE / p["spec"]["batch_id"] / "journal.jsonl").read_text().splitlines()
    ]
    result = next(e for e in events if e.get("event") == "result")
    assert result["rate_limit_signals"]["tokens_remaining"] == 1024
    assert (b.STORE / "execution.lock").exists()


@pytest.mark.parametrize("harness", ["openai/gpt-oss-20b"], indirect=True)
def test_groq_rpd_header_is_not_misread_as_rpm(harness, monkeypatch):
    h = harness
    p = h.plan()

    def child(spec, env, execute):
        rc, d = h.child(spec, env, execute)
        if execute:
            d["provider"]["rate_limit_signals"] = {
                "requests_limit": 1000,
                "requests_remaining": 999,
                "tokens_limit": 8000,
                "tokens_remaining": 7611,
            }
        return rc, d

    monkeypatch.setattr(b, "run_child", child)
    assert h.execute(p)["verified_trials"] == 20


def make_stopped_parent(h):
    spec = b.spec_for(h.model, SCENARIO, "b" * 32)
    spec.update(quota_sha256=b.digest(b.read(h.q)), smoke_sha256=b.digest(b.read(h.smoke)))
    folder = b.STORE / spec["batch_id"]
    folder.mkdir(parents=True)
    spec_hash = b.digest(spec)
    b.save_new(folder / "plan.json", {"spec": spec, "spec_sha256": spec_hash})
    b.save_new(
        folder / "approval-consumed.json",
        {"spec_sha256": spec_hash, "authorization_reusable": False, "at": NOW - 3},
    )
    signals = {
        "requests_limit": 1000,
        "requests_remaining": 999,
        "requests_reset_seconds": 900.0,
        "tokens_limit": 8000,
        "tokens_remaining": 7611,
        "tokens_reset_seconds": 2.917,
    }
    doc = output(spec, True)
    doc["correlation"]["trace_id"] = "c" * 32
    doc["provider"]["rate_limit_signals"] = signals
    record = b.metadata(spec, doc, 1)
    journal = folder / "journal.jsonl"
    for event in (
        {"event": "reserved", "trial_id": 1, "at": NOW - 5, "request_state": "possibly_sent"},
        {"event": "result", "at": NOW - 4, **record},
        {"event": "backend_verified", "trial_id": 1, "at": NOW - 3.5},
        {
            "event": "stopped",
            "at": NOW - 3,
            "error_code": "provider_rate_limit_headroom_exhausted",
            "reserved_trials": 1,
            "authorization_active": False,
        },
    ):
        b.append(journal, event)
    b.save_new(b.STORE / "execution.lock", {"batch_id": spec["batch_id"]})
    return spec["batch_id"], spec_hash


def add_verified_recovery_prefix(h, batch_id, recovery, trials):
    folder = b.STORE / batch_id
    spec = b.read(folder / "plan.json")["spec"]
    b.save_new(
        folder / "recovery-approval-consumed.json",
        {"spec_sha256": recovery["approve_sha256"], "at": NOW - 4, "authorization_reusable": False},
    )
    b.append(
        folder / "journal.jsonl",
        {
            "event": "recovery_started",
            "at": NOW - 3,
            "recovery_sequence": 1,
            "recovery_plan_sha256": recovery["approve_sha256"],
            "completed_trials": [1],
            "pending_trials": list(range(2, 21)),
        },
    )
    for trial in trials:
        doc = output(spec, True)
        doc["correlation"]["trace_id"] = f"{trial + 2:032x}"
        doc["provider"]["rate_limit_signals"] = {
            "requests_limit": 1000,
            "requests_remaining": 999 - trial,
            "requests_reset_seconds": 900.0,
            "tokens_limit": 8000,
            "tokens_remaining": 7600,
            "tokens_reset_seconds": 2.9,
        }
        record = b.metadata(spec, doc, trial)
        b.append(
            folder / "journal.jsonl",
            {
                "event": "reserved",
                "trial_id": trial,
                "at": NOW - 2.8,
                "request_state": "possibly_sent",
            },
        )
        b.append(folder / "journal.jsonl", {"event": "result", "at": NOW - 2.5, **record})
        b.append(
            folder / "journal.jsonl",
            {"event": "backend_verified", "trial_id": trial, "at": NOW - 2.2},
        )
    b.append(
        folder / "journal.jsonl",
        {
            "event": "recovery_stopped",
            "at": NOW - 1,
            "recovery_sequence": 1,
            "error_code": "synthetic_operator_stop",
            "reserved_recovery_trials": len(trials),
            "authorization_active": False,
        },
    )
    b.save_new(
        b.STORE / "recovery-active.lock",
        {"batch_id": batch_id, "recovery_plan_sha256": recovery["approve_sha256"]},
    )


@pytest.mark.parametrize("harness", ["openai/gpt-oss-20b"], indirect=True)
def test_recovery_dry_run_keeps_lock_and_requires_new_approval(harness, monkeypatch):
    h = harness
    batch_id, old_approval = make_stopped_parent(h)
    monkeypatch.setattr(b, "assert_no_active_batch_process", lambda: None)
    result = b.prepare_recovery(batch_id, h.q, h.smoke, h.env)
    assert result["mode"] == "recovery_dry_run"
    assert result["provider_request_sent"] is False
    assert result["completed_trials"] == [1]
    assert result["pending_trials"] == list(range(2, 21))
    assert result["rate_limit_headroom"]["rpd_remaining"] == 999
    assert result["approve_sha256"] != old_approval
    assert (b.STORE / "execution.lock").exists()
    assert True not in h.calls


@pytest.mark.parametrize("harness", ["openai/gpt-oss-20b"], indirect=True)
def test_recovery_refuses_corrupt_journal_without_touching_lock(harness, monkeypatch):
    h = harness
    batch_id, _ = make_stopped_parent(h)
    monkeypatch.setattr(b, "assert_no_active_batch_process", lambda: None)
    journal = b.STORE / batch_id / "journal.jsonl"
    journal.write_bytes(journal.read_bytes() + b"torn")
    with pytest.raises(b.Refused, match="journal_incomplete"):
        b.prepare_recovery(batch_id, h.q, h.smoke, h.env)
    assert (b.STORE / "execution.lock").exists()
    assert not (b.STORE / batch_id / "recovery-plan.json").exists()
    assert True not in h.calls


@pytest.mark.parametrize("harness", ["openai/gpt-oss-20b"], indirect=True)
def test_recovery_refuses_when_process_state_is_not_clear(harness, monkeypatch):
    h = harness
    batch_id, _ = make_stopped_parent(h)

    def active_process():
        raise b.Refused("active_or_unverifiable_batch_process")

    monkeypatch.setattr(b, "assert_no_active_batch_process", active_process)
    with pytest.raises(b.Refused, match="active_or_unverifiable_batch_process"):
        b.prepare_recovery(batch_id, h.q, h.smoke, h.env)
    assert (b.STORE / "execution.lock").exists()
    assert not (b.STORE / batch_id / "recovery-plan.json").exists()
    assert True not in h.calls


@pytest.mark.parametrize("harness", ["openai/gpt-oss-20b"], indirect=True)
def test_recovery_executes_only_trials_two_to_twenty_then_releases_lock(harness, monkeypatch):
    h = harness
    batch_id, _ = make_stopped_parent(h)
    monkeypatch.setattr(b, "assert_no_active_batch_process", lambda: None)
    recovery = b.prepare_recovery(batch_id, h.q, h.smoke, h.env)

    def child(spec, env, execute):
        rc, doc = h.child(spec, env, execute)
        if execute:
            doc["correlation"]["trace_id"] = f"{100 + h.calls.count(True):032x}"
            doc["provider"]["rate_limit_signals"] = {
                "requests_limit": 1000,
                "requests_remaining": 998 - (h.calls.count(True) - 1),
                "requests_reset_seconds": 900.0,
                "tokens_limit": 8000,
                "tokens_remaining": 7611,
                "tokens_reset_seconds": 2.917,
            }
        return rc, doc

    monkeypatch.setattr(b, "run_child", child)
    result = b.execute_recovery(batch_id, recovery["approve_sha256"], h.q, h.smoke, h.env)
    assert result["completed_before_recovery"] == 1
    assert result["verified_recovery_trials"] == 19
    assert result["verified_total_trials"] == 20
    journal = [
        json.loads(line) for line in (b.STORE / batch_id / "journal.jsonl").read_text().splitlines()
    ]
    assert [e["trial_id"] for e in journal if e.get("event") == "reserved"] == list(range(1, 21))
    assert (b.STORE / batch_id / "recovery-approval-consumed.json").exists()
    assert not (b.STORE / "execution.lock").exists()


@pytest.mark.parametrize("harness", ["openai/gpt-oss-20b"], indirect=True)
def test_recovery_after_verified_interruption_plans_and_executes_only_suffix(harness, monkeypatch):
    h = harness
    batch_id, _ = make_stopped_parent(h)
    monkeypatch.setattr(b, "assert_no_active_batch_process", lambda: None)
    first = b.prepare_recovery(batch_id, h.q, h.smoke, h.env)
    add_verified_recovery_prefix(h, batch_id, first, [2, 3, 4])
    second = b.prepare_recovery(batch_id, h.q, h.smoke, h.env)
    assert second["spec"]["recovery_sequence"] == 2
    assert second["completed_trials"] == [1, 2, 3, 4]
    assert second["pending_trials"] == list(range(5, 21))
    assert second["approve_sha256"] != first["approve_sha256"]

    def child(spec, env, execute):
        rc, doc = h.child(spec, env, execute)
        if execute:
            doc["correlation"]["trace_id"] = f"{100 + h.calls.count(True):032x}"
            doc["provider"]["rate_limit_signals"] = {
                "requests_limit": 1000,
                "requests_remaining": 990,
                "requests_reset_seconds": 900.0,
                "tokens_limit": 8000,
                "tokens_remaining": 7600,
                "tokens_reset_seconds": 2.9,
            }
        return rc, doc

    monkeypatch.setattr(b, "run_child", child)
    result = b.execute_recovery(batch_id, second["approve_sha256"], h.q, h.smoke, h.env)
    assert result["completed_before_recovery"] == 4
    assert result["verified_recovery_trials"] == 16
    assert result["verified_total_trials"] == 20


def test_interruption_reserved_before_spawn_and_no_recovery_send(harness, monkeypatch):
    h = harness
    p = h.plan()

    def child(spec, env, execute):
        if execute:
            state = b.inspect(spec["batch_id"])
            assert state["possibly_sent_without_result"] == [1]
            raise KeyboardInterrupt
        return h.child(spec, env, execute)

    monkeypatch.setattr(b, "run_child", child)
    with pytest.raises(b.Refused, match="interrupted"):
        h.execute(p)
    state = b.inspect(p["spec"]["batch_id"])
    assert state["possibly_sent_without_result"] == [1]
    assert not state["resume_allowed"] and not state["authorization_active"]


def test_no_concurrent_manager(harness):
    p = harness.plan()
    b.save_new(b.STORE / "execution.lock", {"batch_id": "other"})
    with pytest.raises(FileExistsError):
        harness.execute(p)
    assert True not in harness.calls


def test_fresh_policy_before_each_send(harness, monkeypatch):
    h = harness
    p = h.plan()

    def child(spec, env, execute):
        rc, d = h.child(spec, env, execute)
        if not execute and h.calls.count(True) == 1:
            d["execution_allowed"] = False
        return rc, d

    monkeypatch.setattr(b, "run_child", child)
    with pytest.raises(b.Refused, match="policy_refused"):
        h.execute(p)
    assert h.calls.count(True) == 1


def test_revoked_quota_prevents_next_send(harness, monkeypatch):
    h = harness
    p = h.plan()

    def child(spec, env, execute):
        rc, d = h.child(spec, env, execute)
        if execute:
            q = quota()
            q["exclusive_account"] = False
            h.q.write_text(json.dumps(q))
        return rc, d

    monkeypatch.setattr(b, "run_child", child)
    with pytest.raises(b.Refused, match="quota_changed"):
        h.execute(p)
    assert h.calls.count(True) == 1


def test_backend_verification_failure_stops(harness, monkeypatch):
    h = harness
    p = h.plan()

    def verify(spec, record, env):
        if record["trial_id"]:
            raise b.Refused("trace_read_failed")
        return NOW - 1

    monkeypatch.setattr(b, "verify_trace", verify)
    with pytest.raises(b.Refused, match="trace_read_failed"):
        h.execute(p)
    assert h.calls.count(True) == 1


def test_torn_journal_blocks_new_plan(harness):
    p = harness.plan()
    path = b.STORE / p["spec"]["batch_id"] / "journal.jsonl"
    path.write_text('{"event": "reserved"')
    assert b.inspect(p["spec"]["batch_id"])["corrupt_journal"]
    with pytest.raises(ValueError):
        harness.plan()
    assert True not in harness.calls


def test_metadata_allowlist(harness):
    secret = "PRIVATE-CONTENT-AND-KEY"
    d = output(harness.spec, True)
    d.update(prompt=secret, completion=secret, api_key=secret)
    d["provider"].update(error_category=secret, provider_reason=secret, duration_ms=float("nan"))
    d["correlation"]["trace_id"] = secret
    d["usage"]["tokens"]["output"] = secret
    result = b.metadata(harness.spec, d, 1)
    assert secret not in json.dumps(result)
    assert result["cost_status"] == "unknown" and result["cost_usd"] is None
    assert result["trace_id"] is None and result["latency_ms"] is None
    d["provider"].update(error_category="provider_error", provider_reason="DEADLINE_EXCEEDED")
    result = b.metadata(harness.spec, d, 1)
    assert result["provider_error_code"] == "DEADLINE_EXCEEDED"


def test_runner_failure_keeps_safe_provider_category(harness):
    d = output(harness.spec, True)
    d["provider"].update(outcome="error", error_category="provider_error")
    with pytest.raises(b.Refused, match="provider_error"):
        b.check_result(harness.spec, d, 2)


def test_trace_relationship_and_usage_verification(harness, monkeypatch):
    h = harness
    # Restore real verifier, with an in-memory read API response.
    record = b.metadata(h.spec, output(h.spec, True), 1)
    d = {
        "trace": {
            "trace_id": "a" * 32,
            "provenance": "real",
            "prompt_version": h.spec["prompt_version"],
            "status": "ok",
            "started_at": iso(NOW),
        },
        "spans_truncated": False,
        "usage_truncated": False,
        "spans": [
            {
                "span_id": "root",
                "parent_span_id": None,
                "kind": "INTERNAL",
                "server_dropped_attributes_count": 0,
            },
            {
                "span_id": "child",
                "parent_span_id": "root",
                "kind": "CLIENT",
                "status": "UNSET",
                "server_dropped_attributes_count": 0,
            },
        ],
        "usage": [
            {
                "span_id": "child",
                "provider": h.spec["provider"],
                "requested_model": MODEL,
                "response_model": MODEL,
                "attempt_id": 1,
                "provenance": "real",
                "input_tokens": 150,
                "output_tokens": 40,
                "reasoning_output_tokens": None,
                "cost_status": "unknown",
                "cost_usd": None,
            }
        ],
    }
    monkeypatch.setattr(b, "read_trace", lambda *args: copy.deepcopy(d))
    assert REAL_VERIFY(h.spec, record, h.env) == NOW
    d["spans"][1]["parent_span_id"] = "broken"
    with pytest.raises(b.Refused, match="usage_relationship"):
        REAL_VERIFY(h.spec, record, h.env)


REAL_VERIFY = b.verify_trace


def test_actual_single_runner_child_dry_run_refuses_without_credentials(tmp_path):
    # Real child/JSON protocol, no credentials/config -> refused before backend/provider I/O.
    spec = b.spec_for(MODEL, SCENARIO, "c" * 32)
    env = {k: os.environ[k] for k in ("SystemRoot", "WINDIR") if k in os.environ}
    rc, d = b.run_child(spec, env, False)
    assert rc == 0 and d["mode"] == "dry_run"
    assert d["provider_request_state"] == "not_sent"
    assert d["execution_allowed"] is False and d["refusals"]


@pytest.mark.parametrize(
    "error", [subprocess.TimeoutExpired("private-value", 90), KeyboardInterrupt()]
)
def test_child_timeout_or_interrupt_is_killed_reaped_and_not_retried(harness, monkeypatch, error):
    seen = []

    class Child:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def communicate(self, **kwargs):
            seen.append("communicate")
            raise error

        def kill(self):
            seen.append("kill")

        def wait(self):
            seen.append("wait")

    monkeypatch.setattr(b.subprocess, "Popen", lambda *args, **kwargs: Child())
    with pytest.raises(type(error)):
        REAL_CHILD(harness.spec, harness.env, True)
    assert seen == ["communicate", "kill", "wait"]


def test_duplicate_trace_is_not_counted_twice(harness, monkeypatch):
    h = harness
    p = h.plan()

    def child(spec, env, execute):
        rc, doc = h.child(spec, env, execute)
        if execute:
            doc["correlation"]["trace_id"] = "a" * 32  # existing smoke
        return rc, doc

    monkeypatch.setattr(b, "run_child", child)
    with pytest.raises(b.Refused, match="duplicate_trace"):
        h.execute(p)
    assert h.calls.count(True) == 1


def test_unknown_cost_and_project_are_enforced(harness, monkeypatch):
    h = harness
    p = h.plan()
    h.env["LLMI_DASHBOARD_PROJECT_ID"] = "2"
    with pytest.raises(b.Refused, match="wrong_project"):
        h.execute(p)
    assert True not in h.calls


REAL_CHILD = b.run_child


def test_snapshot_cannot_raise_verified_account_limits(harness):
    q = quota()
    q["limits"]["rpd"] = 501
    with pytest.raises(b.Refused, match="quota_exceeds_verified"):
        b.quota_check(q, MODEL, NOW)
