"""Operator-approved, sequential batch wrapper around live_compare.

No provider client here. Each send uses a fresh child process and the unchanged one-call
guard. A permanent claim burns approval BEFORE any send. Journal reservations are fsynced
before starting a child; an incomplete reservation means possibly_sent, never retryable.
The global lock survives abnormal exits. Recovery requires an inspected, fully verified
completed prefix, a fresh dry-run, and a new one-use approval; uncertain sends are not resumable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from llm_insight_sdk.config import ConfigError, LLMInsightConfig
from llm_insight_sdk.live import input_token_bound
from llm_insight_sdk.providers import ERROR_CATEGORIES

from demo_app import comparison_scenarios as scenarios

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "docs" / "reports" / "batches"
ENDPOINT = "http://127.0.0.1:8010/v1/traces"
DASHBOARD = "http://127.0.0.1:3001"
COUNT = 20
MIN_BATCH_REQUESTS = 1
GEMINI_INTERVAL_SECONDS = 4.2  # 14.29 RPM, below the verified 15 RPM ceiling.
MODELS = {
    "openai/gpt-oss-20b": ("groq", "groq", "groq-free-plan-single-call"),
    # Selection support only. The SDK Free Plan guard remains authoritative and the
    # account-specific quota ceiling is intentionally absent until verified.
    "openai/gpt-oss-120b": ("groq", "groq", "groq-free-plan-single-call"),
    "gemini-3.1-flash-lite": ("gemini", "gcp.gemini", "gemini-free-tier-single-call"),
    "qwen/qwen3.8-27b": ("groq", "groq", "groq-free-plan-single-call"),
    "deepseek/deepseek-v4-flash-0731:free": (
        "openrouter",
        "openrouter",
        "openrouter-free-model-single-call",
    ),
}
LIMITS = ("rpm", "rpd", "tpm", "tpd", "itpm", "otpm")
# Account evidence, not public paid-plan defaults. A snapshot may lower these, never raise.
ACCOUNT_CEILINGS = {
    "openai/gpt-oss-20b": {"rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000},
    "qwen/qwen3.8-27b": {"rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000},
    "gemini-3.1-flash-lite": {"rpm": 15, "rpd": 500, "tpm": 250000},
}


class Refused(Exception):
    """Static error code only; never embed exception messages or untrusted values."""


def require(ok: object, code: str) -> None:
    if not ok:
        raise Refused(code)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def read(path: Path) -> dict[str, Any]:
    require(path.stat().st_size <= 1_000_000, "file_too_large")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise Refused("invalid_document")
    return value


def stamp(value: Any) -> float:
    require(isinstance(value, str), "invalid_timestamp")
    d = datetime.fromisoformat(value)
    require(d.tzinfo is not None, "timezone_required")
    return d.timestamp()


def integer(value: Any, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def code_fingerprint() -> str:
    files = sorted((ROOT / "sdk" / "src" / "llm_insight_sdk").rglob("*.py"))
    files += sorted((ROOT / "demo_app").glob("*.py"))
    return digest(
        {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    )


def save_new(path: Path, value: Any) -> None:
    # x mode never overwrites historical results, approval claims or plans.
    with path.open("x", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(value, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def replace_json(path: Path, value: Any) -> None:
    """Atomically update an existing lock without an unlocked interval."""
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    save_new(temporary, value)
    os.replace(temporary, path)


def append(path: Path, value: Any) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(value, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def quota_check(
    q: dict[str, Any],
    model: str,
    now: float,
    *,
    fresh: bool = False,
    planned_requests: int = COUNT,
) -> None:
    require(
        type(planned_requests) is int
        and MIN_BATCH_REQUESTS <= planned_requests <= COUNT,
        "invalid_request_count",
    )
    required = {
        "model",
        "checked_at",
        "valid_until",
        "daily_reset_at",
        "exclusive_account",
        "all_scopes_checked",
        "free_account",
        "model_access",
        "project_1_ingest",
        "limits",
        "daily_used",
        "usage_basis",
    }
    require(set(q) == required, "quota_schema")
    require(q["model"] == model, "quota_model_mismatch")
    for key in (
        "exclusive_account",
        "all_scopes_checked",
        "free_account",
        "model_access",
        "project_1_ingest",
    ):
        require(q[key] is True, "quota_attestation_missing")
    checked, until = stamp(q["checked_at"]), stamp(q["valid_until"])
    reset = stamp(q["daily_reset_at"]) if q["daily_reset_at"] is not None else None
    require(
        checked <= now < until <= checked + 3600 and (reset is None or until <= reset),
        "quota_stale",
    )
    if fresh:
        require(now - checked <= 300, "quota_snapshot_not_fresh")
    limits = q["limits"]
    require(isinstance(limits, dict) and set(limits) == set(LIMITS), "quota_limits_missing")
    if MODELS[model][0] == "groq":
        require(model in ACCOUNT_CEILINGS, "quota_model_unverified")
    for key in LIMITS:
        # null explicitly means operator verified that this dimension does not apply.
        required_dimension = key in {"rpm", "rpd", "tpm"} or (
            key == "tpd" and MODELS[model][0] == "groq"
        )
        require(
            (not required_dimension and limits[key] is None) or integer(limits[key], 1),
            "quota_limit_unknown",
        )
    used = q["daily_used"]
    for key, ceiling in ACCOUNT_CEILINGS.get(model, {}).items():
        require(limits[key] <= ceiling, "quota_exceeds_verified_account_limit")
    if q["usage_basis"] == "operator_attested_sufficient_headroom":
        require(used is None and reset is None, "quota_attestation_must_not_invent_usage_or_reset")
    else:
        require(q["usage_basis"] == "account_usage_snapshot", "quota_usage_basis_unknown")
        require(isinstance(used, dict) and set(used) == {"requests", "tokens"}, "quota_usage_missing")
        require(integer(used["requests"]) and integer(used["tokens"]), "quota_usage_unknown")
        require(
            used["requests"] + planned_requests <= limits["rpd"],
            "daily_requests_insufficient",
        )
        if limits["tpd"] is not None:
            require(
                used["tokens"] + planned_requests * 2048 <= limits["tpd"],
                "daily_tokens_insufficient",
            )
    require(limits["tpm"] >= 2048, "minute_tokens_insufficient")
    for key in ("itpm", "otpm"):
        require(limits[key] is None or limits[key] >= 1024, "split_token_limit_insufficient")
    if MODELS[model][0] == "openrouter":
        require(limits["rpd"] <= 50 and limits["rpm"] <= 20, "openrouter_free_cap")


def refresh_gemini_quota_snapshot(
    source_path: Path,
    output_path: Path,
    source: dict[str, str],
    *,
    confirm: Any = input,
    planned_requests: int = COUNT,
) -> dict[str, Any]:
    """Re-attest the existing Gemini quota schema after a fresh operator check.

    This carries forward only previously verified limits and the explicitly
    unknown usage/reset fields. New validity timestamps are written only after
    the operator confirms the current AI Studio account state.
    """
    required_env = (
        "GEMINI_API_KEY",
        "LLMI_BATCH_READ_TOKEN",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_SERVICE_NAME",
        "LLMI_DASHBOARD_PROJECT_ID",
    )
    missing = [name for name in required_env if not source.get(name)]
    require(not missing, "missing_batch_environment")
    require(source.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") == ENDPOINT, "wrong_otlp_endpoint")
    require(source.get("LLMI_DASHBOARD_PROJECT_ID") == "1", "wrong_project")
    try:
        telemetry = LLMInsightConfig.from_env(source)
    except ConfigError:
        raise Refused("telemetry_config_invalid") from None
    require(bool(telemetry.project_key), "project1_ingest_key_missing")

    prior = read(source_path)
    require(prior.get("model") == "gemini-3.1-flash-lite", "quota_model_mismatch")
    require(
        set(prior)
        == {
            "model", "checked_at", "valid_until", "daily_reset_at", "exclusive_account",
            "all_scopes_checked", "free_account", "model_access", "project_1_ingest",
            "limits", "daily_used", "usage_basis",
        },
        "quota_schema",
    )
    require(
        prior.get("limits") == {"rpm": 15, "rpd": 500, "tpm": 250000,
                                "tpd": None, "itpm": None, "otpm": None}
        and prior.get("usage_basis") == "operator_attested_sufficient_headroom"
        and prior.get("daily_used") is None
        and prior.get("daily_reset_at") is None
        and all(prior.get(k) is True for k in (
            "exclusive_account", "all_scopes_checked", "free_account",
            "model_access", "project_1_ingest",
        )),
        "prior_quota_evidence_incomplete",
    )
    require(not output_path.exists(), "quota_output_exists")
    require(
        type(planned_requests) is int and MIN_BATCH_REQUESTS <= planned_requests <= COUNT,
        "invalid_request_count",
    )
    phrase = f"ATTEST GEMINI FREE 15 500 250000 HEADROOM-{planned_requests} PROJECT-1"
    prompt = (
        "Check the current AI Studio account: Free Tier; gemini-3.1-flash-lite enabled; "
        "limits RPM 15 / RPD 500 / TPM 250000; all scopes reviewed; sufficient remaining "
        f"headroom for {planned_requests} requests; this key is exclusive to this batch; Project 1 ingest "
        f"is still configured. Type exactly: {phrase}"
    )
    require(confirm(prompt) == phrase, "quota_attestation_not_confirmed")

    checked = datetime.now(UTC)
    snapshot = {
        **prior,
        "checked_at": checked.isoformat(),
        "valid_until": (checked + timedelta(minutes=45)).isoformat(),
        "daily_reset_at": None,
        "daily_used": None,
        "usage_basis": "operator_attested_sufficient_headroom",
    }
    quota_check(
        snapshot,
        snapshot["model"],
        checked.timestamp(),
        fresh=True,
        planned_requests=planned_requests,
    )
    save_new(output_path, snapshot)
    return {
        "status": "quota_snapshot_refreshed",
        "model": snapshot["model"],
        "checked_at": snapshot["checked_at"],
        "valid_until": snapshot["valid_until"],
        "daily_used": None,
        "provider_request_sent": False,
    }


def child_env(model: str, source: dict[str, str]) -> dict[str, str]:
    e = dict(source)
    require(e.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") == ENDPOINT, "wrong_otlp_endpoint")
    require(e.get("LLMI_DASHBOARD_PROJECT_ID") == "1", "wrong_project")
    require(bool(e.get("LLMI_BATCH_READ_TOKEN")), "backend_read_token_missing")
    _, provider, consent = MODELS[model]
    e.update(
        LLMI_LIVE_MODELS=model,
        LLMI_LIVE_PROVIDERS=provider,
        LLMI_LIVE_MAX_REQUESTS="1",
        LLMI_LIVE_MAX_RETRIES="0",
        LLMI_LIVE_MAX_CONCURRENCY="1",
        LLMI_LIVE_MAX_INPUT_TOKENS="1024",
        LLMI_LIVE_MAX_OUTPUT_TOKENS="1024",
        LLMI_LIVE_CONSENT=consent,
    )
    if model == "gemini-3.1-flash-lite":
        e["LLMI_LIVE_TIMEOUT_SECONDS"] = "30"
    # Billing evidence, provider mode and credentials are never fabricated or persisted.
    return e


def run_child(
    spec: dict[str, Any], env: dict[str, str], execute: bool
) -> tuple[int, dict[str, Any]]:
    args = [
        sys.executable,
        "-m",
        "demo_app.live_compare",
        "--provider",
        spec["cli_provider"],
        "--scenario",
        spec["scenario"],
        "--dashboard-url",
        DASHBOARD,
    ]
    if execute:
        args.append("--execute")
    # stderr and stdout stay private in memory; never forward exception/output text.
    # Popen guarantees a killed/reaped child on Ctrl-C, timeout or parent exceptions.
    with subprocess.Popen(  # noqa: S603 - fixed executable/module/closed arguments, no shell
        args,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    ) as process:
        try:
            stdout, _ = process.communicate(timeout=90)
        except BaseException:
            process.kill()
            process.wait()
            raise
    value = json.loads(stdout)
    require(isinstance(value, dict), "invalid_runner_output")
    return process.returncode, value


def check_dry(spec: dict[str, Any], doc: dict[str, Any], rc: int) -> None:
    require(rc == 0 and doc.get("mode") == "dry_run", "dry_run_failed")
    require(doc.get("execution_allowed") is True and doc.get("refusals") == [], "policy_refused")
    e, plan, sc = doc.get("environment") or {}, doc.get("plan") or {}, doc.get("scenario") or {}
    require(e.get("backend", {}).get("ready") is True, "backend_not_ready")
    require(e.get("sdk_checkout_match") is True, "sdk_checkout_mismatch")
    require(
        plan.get("provider") == spec["provider"] and plan.get("model") == spec["model"],
        "provider_model_mismatch",
    )
    require(
        plan.get("max_requests") == 1
        and plan.get("max_retries") == 0
        and plan.get("max_concurrency") == 1
        and plan.get("paid_fallback") is False,
        "unsafe_plan",
    )
    require(
        plan.get("max_output_tokens") == 1024
        and integer(plan.get("input_token_bound"), 1)
        and plan["input_token_bound"] <= 1024,
        "unsafe_token_limits",
    )
    if spec["model"] == "gemini-3.1-flash-lite":
        require(
            spec.get("timeout_seconds") == 30.0 and plan.get("timeout_seconds") == 30.0,
            "unsafe_timeout",
        )
    require(
        sc.get("id") == spec["scenario"]
        and sc.get("input_sha256") == spec["input_sha256"]
        and sc.get("prompt_version") == spec["prompt_version"],
        "scenario_changed",
    )
    require(doc.get("comparison", {}).get("project_id") == 1, "wrong_project")


def metadata(spec: dict[str, Any], doc: dict[str, Any], trial: int) -> dict[str, Any]:
    p, u, x = doc.get("provider") or {}, doc.get("usage") or {}, doc.get("export") or {}
    tok, corr = u.get("tokens") or {}, doc.get("correlation") or {}
    trace = corr.get("trace_id")
    trace = trace if isinstance(trace, str) and re.fullmatch(r"[a-f0-9]{32}", trace) else None
    state = doc.get("provider_request_state")
    state = state if state in ("sent", "not_sent", "possibly_sent") else "possibly_sent"
    outcome = p.get("outcome")
    validation = (doc.get("validation") or {}).get("status")
    latency = p.get("duration_ms")
    return {
        "batch_id": spec["batch_id"],
        "trial_id": trial,
        "provider": spec["provider"],
        "model": spec["model"],
        "scenario": spec["scenario"],
        "prompt_version": spec["prompt_version"],
        "trace_id": trace,
        "request_state": state,
        "export_state": x.get("outcome")
        if x.get("outcome") in ("confirmed", "failed", "partial")
        else "unknown",
        "latency_ms": latency
        if isinstance(latency, (int, float))
        and not isinstance(latency, bool)
        and math.isfinite(latency)
        and latency >= 0
        else None,
        "input_tokens": tok.get("input") if integer(tok.get("input")) else None,
        "output_tokens": tok.get("output") if integer(tok.get("output")) else None,
        "reasoning_tokens": tok.get("reasoning_output")
        if integer(tok.get("reasoning_output"))
        else None,
        "technical_status": outcome
        if outcome in ("completed", "error", "failed", "incomplete")
        else "unknown",
        "validation": validation
        if validation in ("passed", "failed", "not_evaluated")
        else "not_evaluated",
        "error_code": p.get("error_category")
        if p.get("error_category") in ERROR_CATEGORIES
        else ("provider_or_guard_error" if outcome != "completed" else None),
        "provider_error_code": p.get("provider_reason")
        if isinstance(p.get("provider_reason"), str)
        and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", p["provider_reason"])
        else None,
        "rate_limit_signals": p.get("rate_limit_signals")
        if isinstance(p.get("rate_limit_signals"), dict)
        else None,
        "cost_status": "unknown",
        "cost_usd": None,
    }


def check_result(spec: dict[str, Any], doc: dict[str, Any], rc: int) -> None:
    p, x = doc.get("provider") or {}, doc.get("export") or {}
    require(doc.get("mode") == "execute", "runner_failed")
    if rc != 0:
        category = p.get("error_category")
        if category in ERROR_CATEGORIES:
            raise Refused(category)
        raise Refused("runner_failed")
    require(
        doc.get("provider_request_state") == "sent" and p.get("attempts") == 1,
        "uncertain_or_multiple_send",
    )
    require(
        p.get("provider") == spec["provider"]
        and p.get("request_model") == spec["model"]
        and p.get("response_model") == spec["model"],
        "result_model_mismatch",
    )
    require(
        p.get("outcome") == "completed" and not doc.get("free_plan_violation"), "provider_failed"
    )
    require(p.get("provenance") == "real", "non_real_result")
    require((doc.get("validation") or {}).get("status") == "passed", "validation_failed")
    require(
        x.get("outcome") == "confirmed" and x.get("spans_export_failed") == 0, "telemetry_failed"
    )
    require(
        (doc.get("scenario") or {}).get("input_sha256") == spec["input_sha256"],
        "result_scenario_mismatch",
    )
    require((doc.get("correlation") or {}).get("project_id") == 1, "result_project_mismatch")


def read_trace(trace_id: str, env: dict[str, str]) -> dict[str, Any]:
    require(bool(re.fullmatch(r"[a-f0-9]{32}", trace_id)), "invalid_trace_id")
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=5) as client:
        response = client.get(
            f"http://127.0.0.1:8010/api/v1/traces/{trace_id}?project=1",
            headers={"Authorization": "Bearer " + env["LLMI_BATCH_READ_TOKEN"]},
        )
        require(response.status_code == 200, "trace_read_failed")
        value = response.json()
        if not isinstance(value, dict):
            raise Refused("trace_read_invalid")
        return value


def verify_trace(spec: dict[str, Any], record: dict[str, Any], env: dict[str, str]) -> float:
    require(record["trace_id"] is not None, "trace_missing")
    d = read_trace(record["trace_id"], env)
    t, spans, usage = d["trace"], d["spans"], d["usage"]
    require(
        t["trace_id"] == record["trace_id"]
        and t["provenance"] == "real"
        and t["prompt_version"] == spec["prompt_version"],
        "trace_identity_mismatch",
    )
    require(
        not d["spans_truncated"]
        and not d["usage_truncated"]
        and len(spans) == 2
        and len(usage) == 1,
        "trace_structure_invalid",
    )
    roots = [s for s in spans if s["parent_span_id"] is None]
    clients = [s for s in spans if s["kind"] == "CLIENT"]
    require(len(roots) == len(clients) == 1, "span_relationship_invalid")
    require(
        clients[0]["parent_span_id"] == roots[0]["span_id"]
        and usage[0]["span_id"] == clients[0]["span_id"],
        "usage_relationship_invalid",
    )
    u = usage[0]
    require(
        u["provider"] == spec["provider"]
        and u["requested_model"] == spec["model"]
        and u["attempt_id"] == 1
        and u["provenance"] == "real",
        "usage_identity_invalid",
    )
    require(u["cost_status"] == "unknown" and u["cost_usd"] is None, "cost_not_unknown")
    for key, field in (
        ("input_tokens", "input_tokens"),
        ("output_tokens", "output_tokens"),
        ("reasoning_tokens", "reasoning_output_tokens"),
    ):
        require(u[field] == record[key], "stored_tokens_mismatch")
    if record["technical_status"] == "completed":
        require(
            t["status"] != "error"
            and clients[0]["status"] != "ERROR"
            and u["response_model"] == spec["model"],
            "stored_error",
        )
    require(all(s["server_dropped_attributes_count"] == 0 for s in spans), "dropped_metadata")
    return stamp(t["started_at"])


def spec_for(
    model: str,
    scenario: str,
    batch_id: str,
    planned_requests: int = COUNT,
    smoke_first: bool = False,
) -> dict[str, Any]:
    require(model in MODELS and scenario in scenarios.SCENARIOS, "unknown_selection")
    require(type(smoke_first) is bool, "invalid_smoke_mode")
    require(
        type(planned_requests) is int
        and MIN_BATCH_REQUESTS <= planned_requests <= COUNT,
        "invalid_request_count",
    )
    sc = scenarios.SCENARIOS[scenario]
    require(not scenarios.check_scenario(scenario, sc), "scenario_invalid")
    return {
        "batch_id": batch_id,
        "model": model,
        "cli_provider": MODELS[model][0],
        "provider": MODELS[model][1],
        "scenario": scenario,
        "prompt_version": sc.prompt_version,
        "input_sha256": sc.input_sha256,
        "planned_requests": planned_requests,
        "smoke_first": smoke_first,
        "project_id": 1,
        "otlp_endpoint": ENDPOINT,
        "max_input_tokens": input_token_bound(scenarios.compose_input(sc)),
        "max_output_tokens": sc.max_output_tokens,
        "interval_seconds": (
            GEMINI_INTERVAL_SECONDS if model == "gemini-3.1-flash-lite" else 61
        ),
        **({"timeout_seconds": 30.0} if model == "gemini-3.1-flash-lite" else {}),
        "code_sha256": code_fingerprint(),
    }


def check_smoke(spec: dict[str, Any], path: Path, env: dict[str, str], now: float) -> None:
    doc = read(path)
    sc = (doc.get("scenario") or {}).get("id")
    if not isinstance(sc, str):
        raise Refused("smoke_scenario_missing")
    smoke = spec_for(spec["model"], sc, spec["batch_id"])
    check_result(smoke, doc, 0)
    when = verify_trace(smoke, metadata(smoke, doc, 0), env)
    require(0 <= now - when <= 86400, "smoke_stale")


def latest_reservation() -> float:
    # All local accounts/providers conservatively share one ledger and one execution lock.
    latest = 0.0
    for path in STORE.glob("*/journal.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)  # corrupt/torn journal blocks new work
            latest = max(latest, float(event["at"]))
    return latest


def prepare(
    model: str,
    scenario: str,
    quota_path: Path,
    smoke_path: Path | None,
    source: dict[str, str],
    planned_requests: int = COUNT,
    smoke_first: bool = False,
) -> dict[str, Any]:
    require(smoke_path is not None or smoke_first, "smoke_result_required")
    now = time.time()
    spec = spec_for(model, scenario, uuid.uuid4().hex, planned_requests, smoke_first)
    q = read(quota_path)
    quota_check(q, model, now, fresh=True, planned_requests=planned_requests)
    require(stamp(q["checked_at"]) >= latest_reservation(), "quota_predates_local_usage")
    env = child_env(model, source)
    rc, doc = run_child(spec, env, False)
    check_dry(spec, doc, rc)
    if not smoke_first:
        check_smoke(spec, smoke_path, env, now)
    spec.update(
        quota_sha256=digest(q),
        smoke_sha256=None if smoke_first else digest(read(smoke_path)),
    )
    folder = STORE / spec["batch_id"]
    folder.mkdir(parents=True, exist_ok=False)
    save_new(folder / "plan.json", {"spec": spec, "spec_sha256": digest(spec)})
    return {"status": "awaiting_batch_approval", "spec": spec, "approve_sha256": digest(spec)}


def execute(
    batch_id: str,
    approval: str,
    quota_path: Path,
    smoke_path: Path | None,
    source: dict[str, str],
) -> dict[str, Any]:
    require(bool(re.fullmatch(r"[a-f0-9]{32}", batch_id)), "invalid_batch_id")
    folder = STORE / batch_id
    saved = read(folder / "plan.json")
    spec = saved["spec"]
    require(approval == digest(spec) == saved["spec_sha256"], "approval_mismatch")
    expected = spec_for(
        spec["model"], spec["scenario"], batch_id,
        spec.get("planned_requests", 0), spec.get("smoke_first", False),
    )
    require({k: spec.get(k) for k in expected} == expected, "plan_or_code_changed")
    require(set(spec) == set(expected) | {"quota_sha256", "smoke_sha256"}, "plan_schema")
    require(smoke_path is not None or spec["smoke_first"], "smoke_result_required")
    env = child_env(spec["model"], source)
    q = read(quota_path)
    require(digest(q) == spec["quota_sha256"], "quota_changed")
    if spec["smoke_first"]:
        require(spec["smoke_sha256"] is None, "plan_schema")
    else:
        require(digest(read(smoke_path)) == spec["smoke_sha256"], "smoke_changed")
    quota_check(
        q,
        spec["model"],
        time.time(),
        # Planning requires a newly checked snapshot. Execution can follow the
        # one-use approval later, as long as that same snapshot remains valid.
        # The digest and the per-send checks below still reject edits or expiry.
        fresh=False,
        planned_requests=spec["planned_requests"],
    )
    require(stamp(q["checked_at"]) >= latest_reservation(), "quota_predates_local_usage")
    if not spec["smoke_first"]:
        check_smoke(spec, smoke_path, env, time.time())
    lock = STORE / "execution.lock"
    save_new(lock, {"batch_id": batch_id})  # O_EXCL prevents concurrent managers
    journal = folder / "journal.jsonl"
    reserved = 0
    try:
        save_new(
            folder / "approval-consumed.json",
            {"spec_sha256": approval, "at": time.time(), "authorization_reusable": False},
        )
        # Wall time validates dated evidence; monotonic time enforces request spacing.
        interval = spec["interval_seconds"]
        next_send = time.monotonic() + max(
            0, interval - (time.time() - stamp(q["checked_at"]))
        )
        seen_traces = (
            set()
            if spec["smoke_first"]
            else {(read(smoke_path).get("correlation") or {}).get("trace_id")}
        )
        for trial in range(1, spec["planned_requests"] + 1):
            while time.monotonic() < next_send:
                time.sleep(max(0, min(1, next_send - time.monotonic())))
            require(code_fingerprint() == spec["code_sha256"], "code_changed")
            current = read(quota_path)  # deleting/editing the attestation revokes it
            require(digest(current) == spec["quota_sha256"], "quota_changed")
            quota_check(
                current,
                spec["model"],
                time.time(),
                planned_requests=spec["planned_requests"],
            )
            rc, dry = run_child(spec, env, False)
            check_dry(spec, dry, rc)
            # Dry-run itself takes time: recheck validity immediately before the child.
            require(digest(read(quota_path)) == spec["quota_sha256"], "quota_changed")
            quota_check(
                current,
                spec["model"],
                time.time(),
                planned_requests=spec["planned_requests"],
            )
            require(time.time() + 90 < stamp(current["valid_until"]), "quota_expires_during_send")
            append(
                journal,
                {
                    **metadata(spec, {}, trial),
                    "event": "reserved",
                    "trial_id": trial,
                    "at": time.time(),
                    "request_state": "possibly_sent",
                    "error_code": None,
                    "tokens_reserved": 2048,
                },
            )
            reserved += 1
            rc, doc = run_child(spec, env, True)
            record = metadata(spec, doc, trial)
            append(journal, {"event": "result", "at": time.time(), **record})
            require(record["trace_id"] not in seen_traces, "duplicate_trace")
            seen_traces.add(record["trace_id"])
            # Verify even a provider-error trace before stopping; never retry a missing export.
            verify_trace(spec, record, env)
            append(journal, {"event": "backend_verified", "trial_id": trial, "at": time.time()})
            check_result(spec, doc, rc)
            if spec["smoke_first"] and trial == 1:
                append(journal, {"event": "smoke_verified", "trial_id": 1, "at": time.time()})
            signals = (doc.get("provider") or {}).get("rate_limit_signals") or {}
            require(
                # Groq's requests headers are RPD (not RPM); tokens headers are TPM.
                signals.get("requests_limit", current["limits"]["rpd"])
                <= current["limits"]["rpd"]
                and signals.get("tokens_limit", current["limits"]["tpm"])
                <= current["limits"]["tpm"]
                and signals.get("requests_remaining", 1) > 0
                and signals.get("tokens_remaining", 2048) >= 2048,
                "provider_rate_limit_headroom_exhausted",
            )
            next_send = time.monotonic() + interval
        append(journal, {"event": "finished", "at": time.time(), "authorization_active": False})
        lock.unlink()
        return {
            "status": "completed",
            "batch_id": batch_id,
            "verified_trials": spec["planned_requests"],
            "authorization_active": False,
        }
    except BaseException as exc:
        # Any error consumes the approval. A hard kill still leaves the exclusive claim/lock.
        code = str(exc) if isinstance(exc, Refused) else "interrupted_or_internal_error"
        append(
            journal,
            {
                "event": "stopped",
                "at": time.time(),
                "error_code": code,
                "reserved_trials": reserved,
                "authorization_active": False,
            },
        )
        if reserved == 0:
            lock.unlink()
        raise Refused(code) from None


def inspect(batch_id: str) -> dict[str, Any]:
    require(bool(re.fullmatch(r"[a-f0-9]{32}", batch_id)), "invalid_batch_id")
    folder = STORE / batch_id
    path = folder / "journal.jsonl"
    events = []
    corrupt = False
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                corrupt = True
    reserved = {e.get("trial_id") for e in events if e.get("event") == "reserved"}
    results = {e.get("trial_id") for e in events if e.get("event") == "result"}
    return {
        "batch_id": batch_id,
        "reserved_trials": len(reserved),
        "possibly_sent_without_result": sorted(reserved - results),
        "corrupt_journal": corrupt,
        "approval_consumed": (folder / "approval-consumed.json").exists(),
        "authorization_active": False,
        "resume_allowed": False,
        "global_lock_present": (STORE / "execution.lock").exists(),
    }


def assert_no_active_batch_process() -> None:
    """Fail closed if another SDK-venv Python process may be running a batch or child runner."""
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    require(powershell is not None, "process_check_unavailable")
    target = json.dumps(str((ROOT / "sdk" / ".venv" / "Scripts" / "python.exe").resolve()))
    script = (
        f"$target={target}; $self={os.getpid()}; "
        "$items=@(Get-Process -Name python,pythonw -ErrorAction SilentlyContinue | "
        "Where-Object { if ($_.Id -eq $self) { return $false }; "
        "try { $_.Path -eq $target } catch { $true } }); "
        "if ($items.Count -gt 0) { exit 2 } else { exit 0 }"
    )
    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            cwd=ROOT,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise Refused("process_check_unavailable") from None
    require(result.returncode == 0, "active_or_unverifiable_batch_process")


def stopped_batch_state(
    batch_id: str,
) -> tuple[Path, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], int]:
    """Validate a contiguous, completed prefix; reject every possibly-sent or failed trial."""
    require(bool(re.fullmatch(r"[a-f0-9]{32}", batch_id)), "invalid_batch_id")
    folder = STORE / batch_id
    plan_doc = read(folder / "plan.json")
    spec = plan_doc.get("spec")
    require(isinstance(spec, dict) and digest(spec) == plan_doc.get("spec_sha256"), "parent_plan_corrupt")
    require(
        spec.get("batch_id") == batch_id
        and spec.get("planned_requests") == COUNT
        and spec.get("model") == "openai/gpt-oss-20b"
        and spec.get("scenario") == "cmp-ticket-triage"
        and spec.get("provider") == "groq",
        "parent_plan_not_recoverable",
    )
    consumed = read(folder / "approval-consumed.json")
    require(
        consumed.get("spec_sha256") == plan_doc["spec_sha256"]
        and consumed.get("authorization_reusable") is False,
        "parent_approval_invalid",
    )
    lock_path = STORE / "execution.lock"
    require(read(lock_path) == {"batch_id": batch_id}, "global_lock_mismatch")
    journal = folder / "journal.jsonl"
    raw = journal.read_bytes()
    require(bool(raw) and raw.endswith(b"\n") and len(raw) <= 5_000_000, "journal_incomplete")
    try:
        events = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
    except (UnicodeError, ValueError):
        raise Refused("journal_corrupt") from None
    require(all(isinstance(e, dict) for e in events), "journal_corrupt")
    require([e.get("event") for e in events[:4]] == ["reserved", "result", "backend_verified", "stopped"], "journal_unexpected_state")
    reserved, result, verified, stopped = events[:4]
    require(
        reserved.get("trial_id") == result.get("trial_id") == verified.get("trial_id") == 1
        and reserved.get("request_state") == "possibly_sent"
        and result.get("request_state") == "sent"
        and result.get("technical_status") == "completed"
        and verified.get("event") == "backend_verified"
        and stopped.get("error_code") == "provider_rate_limit_headroom_exhausted"
        and stopped.get("reserved_trials") == 1,
        "journal_state_not_recoverable",
    )
    trace = result.get("trace_id")
    require(isinstance(trace, str) and re.fullmatch(r"[a-f0-9]{32}", trace), "journal_trace_invalid")
    require(result.get("model") == spec["model"] and result.get("scenario") == spec["scenario"], "journal_identity_mismatch")
    signals = result.get("rate_limit_signals")
    require(isinstance(signals, dict), "journal_rate_signals_missing")
    results = [result]
    last_trial = 1
    index = 4
    recovery_sequence = 0
    while index < len(events):
        require(events[index].get("event") == "recovery_started", "journal_unexpected_state")
        recovery_sequence += 1
        started = events[index]
        if started.get("recovery_sequence") is not None:
            require(started["recovery_sequence"] == recovery_sequence, "journal_recovery_sequence")
        plan_name = "recovery-plan.json" if recovery_sequence == 1 else f"recovery-plan-{recovery_sequence}.json"
        approval_name = (
            "recovery-approval-consumed.json"
            if recovery_sequence == 1
            else f"recovery-approval-consumed-{recovery_sequence}.json"
        )
        recovery_doc = read(folder / plan_name)
        recovery_spec = recovery_doc.get("spec")
        require(
            isinstance(recovery_spec, dict)
            and digest(recovery_spec) == recovery_doc.get("spec_sha256")
            and recovery_spec.get("batch_id") == batch_id
            and recovery_spec.get("recovery_sequence", 1) == recovery_sequence,
            "recovery_plan_corrupt",
        )
        prefix = b"\n".join(raw.splitlines()[:index]) + b"\n"
        parent_hash = hashlib.sha256(prefix).hexdigest()
        require(recovery_spec.get("parent_journal_sha256") == parent_hash, "recovery_parent_journal_mismatch")
        require(
            started.get("recovery_plan_sha256") in {parent_hash, recovery_doc["spec_sha256"]},
            "journal_recovery_plan_mismatch",
        )
        approval = read(folder / approval_name)
        require(
            approval.get("spec_sha256") == recovery_doc["spec_sha256"]
            and approval.get("authorization_reusable") is False,
            "recovery_approval_invalid",
        )
        index += 1
        reserved_this_recovery = 0
        while index < len(events) and events[index].get("event") == "reserved":
            trial_reservation = events[index]
            require(trial_reservation.get("trial_id") == last_trial + 1, "journal_trial_sequence")
            require(trial_reservation.get("request_state") == "possibly_sent", "journal_reservation_state")
            require(index + 2 < len(events), "journal_incomplete")
            trial_result, trial_verified = events[index + 1], events[index + 2]
            require(
                trial_result.get("event") == "result"
                and trial_result.get("trial_id") == trial_reservation["trial_id"]
                and trial_result.get("request_state") == "sent"
                and trial_result.get("technical_status") == "completed"
                and trial_result.get("validation") == "passed"
                and trial_result.get("export_state") == "confirmed"
                and trial_result.get("provider") == "groq"
                and trial_result.get("model") == spec["model"]
                and trial_result.get("scenario") == spec["scenario"]
                and trial_verified.get("event") == "backend_verified"
                and trial_verified.get("trial_id") == trial_reservation["trial_id"],
                "journal_recovery_trial_not_verified",
            )
            trial_trace = trial_result.get("trace_id")
            require(
                isinstance(trial_trace, str)
                and re.fullmatch(r"[a-f0-9]{32}", trial_trace)
                and trial_trace not in {x.get("trace_id") for x in results},
                "journal_trace_invalid_or_duplicate",
            )
            signals = trial_result.get("rate_limit_signals")
            require(isinstance(signals, dict), "journal_rate_signals_missing")
            results.append(trial_result)
            last_trial += 1
            reserved_this_recovery += 1
            index += 3
        require(index < len(events) and events[index].get("event") == "recovery_stopped", "journal_unexpected_state")
        require(
            events[index].get("reserved_recovery_trials") == reserved_this_recovery,
            "journal_recovery_reservation_count",
        )
        if events[index].get("recovery_sequence") is not None:
            require(events[index]["recovery_sequence"] == recovery_sequence, "journal_recovery_sequence")
        index += 1
    if recovery_sequence == 0:
        require(len(events) == 4 and last_trial == 1, "journal_not_stopped_for_recovery")
        require(not (STORE / "recovery-active.lock").exists(), "unexpected_recovery_lock")
    else:
        require(events[-1].get("event") == "recovery_stopped", "journal_not_stopped_for_recovery")
    require(last_trial < COUNT, "journal_has_no_pending_trials")
    if recovery_sequence > 0:
        active_lock = STORE / "recovery-active.lock"
        require(
            read(active_lock)
            == {
                "batch_id": batch_id,
                "recovery_plan_sha256": read(folder / ("recovery-plan.json" if recovery_sequence == 1 else f"recovery-plan-{recovery_sequence}.json"))["spec_sha256"],
            },
            "recovery_lock_mismatch",
        )
    return folder, spec, results, events, recovery_sequence


def check_groq_headroom(
    signals: dict[str, Any], limits: dict[str, Any], *, pending_requests: int, token_bound: int
) -> None:
    """Groq request headers mean RPD; 61-second spacing enforces RPM locally."""
    for key in ("requests_limit", "requests_remaining", "tokens_limit", "tokens_remaining"):
        require(integer(signals.get(key)), "rate_limit_signal_missing")
    require(
        signals["requests_limit"] <= limits["rpd"]
        and signals["requests_remaining"] >= pending_requests
        and signals["tokens_limit"] <= limits["tpm"]
        and signals["tokens_limit"] >= token_bound,
        "rate_limit_headroom_insufficient",
    )
    reset = signals.get("tokens_reset_seconds")
    require(
        isinstance(reset, (int, float))
        and not isinstance(reset, bool)
        and math.isfinite(reset)
        and 0 <= reset <= 86400,
        "token_reset_signal_missing",
    )


def prepare_recovery(
    batch_id: str, quota_path: Path, smoke_path: Path, source: dict[str, str]
) -> dict[str, Any]:
    assert_no_active_batch_process()
    folder, parent_spec, completed_results, events, previous_sequence = stopped_batch_state(batch_id)
    sequence = previous_sequence + 1
    plan_name = "recovery-plan.json" if sequence == 1 else f"recovery-plan-{sequence}.json"
    approval_name = "recovery-approval-consumed.json" if sequence == 1 else f"recovery-approval-consumed-{sequence}.json"
    require(not (folder / plan_name).exists(), "recovery_plan_already_exists")
    require(not (folder / approval_name).exists(), "recovery_approval_consumed")
    if sequence == 1:
        require(not (STORE / "recovery-active.lock").exists(), "recovery_lock_present")
    else:
        require((STORE / "recovery-active.lock").exists(), "recovery_lock_missing")
    q = read(quota_path)
    now = time.time()
    quota_check(q, parent_spec["model"], now, fresh=True)
    require(stamp(q["checked_at"]) >= float(events[-1]["at"]), "quota_predates_stopped_batch")
    completed_trials = len(completed_results)
    pending_trials = list(range(completed_trials + 1, COUNT + 1))
    pending = len(pending_trials)
    last_result = completed_results[-1]
    token_bound = parent_spec["max_input_tokens"] + parent_spec["max_output_tokens"]
    check_groq_headroom(
        last_result["rate_limit_signals"], q["limits"],
        pending_requests=pending, token_bound=token_bound,
    )
    env = child_env(parent_spec["model"], source)
    rc, dry = run_child(parent_spec, env, False)
    check_dry(parent_spec, dry, rc)
    check_smoke(parent_spec, smoke_path, env, now)
    spec = {
        "batch_id": batch_id,
        "model": parent_spec["model"],
        "scenario": parent_spec["scenario"],
        "recovery_sequence": sequence,
        "prompt_version": parent_spec["prompt_version"],
        "input_sha256": parent_spec["input_sha256"],
        "parent_plan_sha256": digest(parent_spec),
        "parent_journal_sha256": hashlib.sha256((folder / "journal.jsonl").read_bytes()).hexdigest(),
        "completed_trials": list(range(1, completed_trials + 1)),
        "last_trace_id": last_result["trace_id"],
        "pending_trials": pending_trials,
        "pending_requests": pending,
        "token_bound": token_bound,
        "quota_sha256": digest(q),
        "smoke_sha256": digest(read(smoke_path)),
        "code_sha256": code_fingerprint(),
    }
    save_new(folder / plan_name, {"spec": spec, "spec_sha256": digest(spec)})
    return {
        "status": "awaiting_new_recovery_approval",
        "mode": "recovery_dry_run",
        "provider_request_sent": False,
        "journal_integrity": "passed",
        "parent_approval_reused": False,
        "completed_trials": spec["completed_trials"],
        "pending_trials": spec["pending_trials"],
        "dry_run": {
            "execution_allowed": dry["execution_allowed"],
            "backend_ready": dry["environment"]["backend"]["ready"],
            "sdk_checkout_match": dry["environment"]["sdk_checkout_match"],
            "refusals": dry["refusals"],
            "model": dry["plan"]["model"],
            "scenario": dry["scenario"]["id"],
            "project_id": dry["comparison"]["project_id"],
            "provider_request_state": dry["provider_request_state"],
        },
        "rate_limit_headroom": {
            "rpd_limit": last_result["rate_limit_signals"]["requests_limit"],
            "rpd_remaining": last_result["rate_limit_signals"]["requests_remaining"],
            "tpm_limit": last_result["rate_limit_signals"]["tokens_limit"],
            "tpm_remaining_at_last_response": last_result["rate_limit_signals"]["tokens_remaining"],
            "rpm_enforced_by_spacing_seconds": 61,
            "pending_requests": pending,
        },
        "spec": spec,
        "approve_sha256": digest(spec),
    }


def execute_recovery(
    batch_id: str, approval: str, quota_path: Path, smoke_path: Path, source: dict[str, str]
) -> dict[str, Any]:
    assert_no_active_batch_process()
    folder, parent_spec, completed_results, events, previous_sequence = stopped_batch_state(batch_id)
    sequence = previous_sequence + 1
    plan_name = "recovery-plan.json" if sequence == 1 else f"recovery-plan-{sequence}.json"
    approval_name = "recovery-approval-consumed.json" if sequence == 1 else f"recovery-approval-consumed-{sequence}.json"
    saved = read(folder / plan_name)
    spec = saved.get("spec")
    require(isinstance(spec, dict) and digest(spec) == saved.get("spec_sha256"), "recovery_plan_corrupt")
    require(approval == digest(spec) == saved["spec_sha256"], "recovery_approval_mismatch")
    require(
        spec.get("recovery_sequence") == sequence
        and spec.get("parent_plan_sha256") == digest(parent_spec)
        and spec.get("parent_journal_sha256")
        == hashlib.sha256((folder / "journal.jsonl").read_bytes()).hexdigest()
        and spec.get("completed_trials") == list(range(1, len(completed_results) + 1))
        and spec.get("last_trace_id") == completed_results[-1]["trace_id"]
        and spec.get("pending_trials") == list(range(len(completed_results) + 1, COUNT + 1)),
        "recovery_parent_changed",
    )
    require(spec.get("code_sha256") == code_fingerprint(), "recovery_code_changed")
    quota_doc = read(quota_path)
    require(digest(quota_doc) == spec.get("quota_sha256"), "recovery_quota_changed")
    quota_check(quota_doc, parent_spec["model"], time.time(), fresh=True)
    require(stamp(quota_doc["checked_at"]) >= float(events[-1]["at"]), "recovery_quota_stale")
    smoke_doc = read(smoke_path)
    require(digest(smoke_doc) == spec.get("smoke_sha256"), "recovery_smoke_changed")
    env = child_env(parent_spec["model"], source)
    check_smoke(parent_spec, smoke_path, env, time.time())
    for prior_record in completed_results:
        verify_trace(parent_spec, prior_record, env)
    token_bound = parent_spec["max_input_tokens"] + parent_spec["max_output_tokens"]
    last_result = completed_results[-1]
    check_groq_headroom(
        last_result["rate_limit_signals"], quota_doc["limits"],
        pending_requests=len(spec["pending_trials"]), token_bound=token_bound,
    )
    rc, dry = run_child(parent_spec, env, False)
    check_dry(parent_spec, dry, rc)

    global_lock = STORE / "execution.lock"
    active_lock = STORE / "recovery-active.lock"
    require(read(global_lock) == {"batch_id": batch_id}, "global_lock_mismatch")
    if sequence == 1:
        save_new(active_lock, {"batch_id": batch_id, "recovery_plan_sha256": digest(spec)})
    else:
        require(
            read(active_lock)["batch_id"] == batch_id,
            "recovery_lock_mismatch",
        )
    approval_path = folder / approval_name
    reserved = 0
    journal = folder / "journal.jsonl"
    try:
        save_new(
            approval_path,
            {"spec_sha256": approval, "at": time.time(), "authorization_reusable": False},
        )
        append(
            journal,
            {
                "event": "recovery_started",
                "at": time.time(),
                "recovery_sequence": sequence,
                "recovery_plan_sha256": digest(spec),
                "completed_trials": spec["completed_trials"],
                "pending_trials": spec["pending_trials"],
            },
        )
        if sequence > 1:
            replace_json(active_lock, {"batch_id": batch_id, "recovery_plan_sha256": digest(spec)})
        previous_result_at = float(last_result["at"])
        initial_reset = float(last_result["rate_limit_signals"]["tokens_reset_seconds"])
        next_send = time.monotonic() + max(
            0.0,
            61.0 - (time.time() - previous_result_at),
            initial_reset - (time.time() - previous_result_at),
        )
        seen_traces = {result["trace_id"] for result in completed_results}
        last_signals = last_result["rate_limit_signals"]
        for trial in spec["pending_trials"]:
            while time.monotonic() < next_send:
                time.sleep(max(0.0, min(1.0, next_send - time.monotonic())))
            assert_no_active_batch_process()
            require(code_fingerprint() == spec["code_sha256"], "recovery_code_changed")
            current = read(quota_path)
            require(digest(current) == spec["quota_sha256"], "recovery_quota_changed")
            quota_check(current, parent_spec["model"], time.time())
            check_groq_headroom(
                last_signals,
                current["limits"],
                pending_requests=COUNT - trial + 1,
                token_bound=token_bound,
            )
            rc, dry = run_child(parent_spec, env, False)
            check_dry(parent_spec, dry, rc)
            require(digest(read(quota_path)) == spec["quota_sha256"], "recovery_quota_changed")
            quota_check(current, parent_spec["model"], time.time())
            require(time.time() + 90 < stamp(current["valid_until"]), "recovery_quota_expires")
            append(
                journal,
                {
                    **metadata(parent_spec, {}, trial),
                    "event": "reserved",
                    "trial_id": trial,
                    "at": time.time(),
                    "request_state": "possibly_sent",
                    "error_code": None,
                    "tokens_reserved": 2048,
                },
            )
            reserved += 1
            rc, doc = run_child(parent_spec, env, True)
            record = metadata(parent_spec, doc, trial)
            append(journal, {"event": "result", "at": time.time(), **record})
            require(record["trace_id"] not in seen_traces, "duplicate_trace")
            seen_traces.add(record["trace_id"])
            verify_trace(parent_spec, record, env)
            append(journal, {"event": "backend_verified", "trial_id": trial, "at": time.time()})
            check_result(parent_spec, doc, rc)
            last_signals = (doc.get("provider") or {}).get("rate_limit_signals") or {}
            check_groq_headroom(
                last_signals,
                current["limits"],
                pending_requests=COUNT - trial,
                token_bound=token_bound,
            )
            previous_result_at = time.time()
            next_send = time.monotonic() + max(61.0, float(last_signals["tokens_reset_seconds"]))

        all_events = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
        reserved_ids = [e["trial_id"] for e in all_events if e.get("event") == "reserved"]
        result_ids = [e["trial_id"] for e in all_events if e.get("event") == "result"]
        verified_ids = [e["trial_id"] for e in all_events if e.get("event") == "backend_verified"]
        require(
            reserved_ids == result_ids == verified_ids == list(range(1, COUNT + 1)),
            "recovery_final_journal_incomplete",
        )
        append(journal, {"event": "recovery_finished", "at": time.time(), "verified_trials": COUNT})
        append(journal, {"event": "finished", "at": time.time(), "authorization_active": False})
        assert_no_active_batch_process()
        active_lock.unlink()
        global_lock.unlink()
        return {
            "status": "completed",
            "batch_id": batch_id,
            "completed_before_recovery": len(completed_results),
            "verified_recovery_trials": len(spec["pending_trials"]),
            "verified_total_trials": COUNT,
            "authorization_active": False,
        }
    except BaseException as exc:
        code = str(exc) if isinstance(exc, Refused) else "recovery_interrupted_or_internal_error"
        append(
            journal,
            {
                "event": "recovery_stopped",
                "at": time.time(),
                "recovery_sequence": sequence,
                "error_code": code,
                "reserved_recovery_trials": reserved,
                "authorization_active": False,
            },
        )
        raise Refused(code) from None


def console_confirm(prompt: str) -> str:
    print(prompt, file=sys.stderr, flush=True)
    return input()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", allow_abbrev=False)
    plan.add_argument("--model", choices=MODELS, required=True)
    plan.add_argument("--scenario", choices=scenarios.SCENARIOS, required=True)
    plan.add_argument("--requests", type=int, default=COUNT)
    plan.add_argument(
        "--smoke-first", action="store_true",
        help="use trial 1 as the smoke check; require its local validation and Project 1 trace before trial 2",
    )
    run = sub.add_parser("execute", allow_abbrev=False)
    run.add_argument("--batch", required=True)
    run.add_argument(
        "--approve", required=True, help="exact spec SHA256; authorizes this batch only"
    )
    for p in (plan, run):
        p.add_argument("--quota", type=Path, required=True)
        p.add_argument("--smoke-result", type=Path)
    status = sub.add_parser("inspect", allow_abbrev=False)
    status.add_argument("--batch", required=True)
    quota_refresh = sub.add_parser("quota-refresh", allow_abbrev=False)
    quota_refresh.add_argument("--source", type=Path, required=True)
    quota_refresh.add_argument("--output", type=Path, required=True)
    quota_refresh.add_argument("--requests", type=int, default=COUNT)
    recovery_plan = sub.add_parser("recover-plan", allow_abbrev=False)
    recovery_plan.add_argument("--batch", required=True)
    recovery_execute = sub.add_parser("recover-execute", allow_abbrev=False)
    recovery_execute.add_argument("--batch", required=True)
    recovery_execute.add_argument("--approve", required=True, help="new recovery spec SHA256")
    for p in (recovery_plan, recovery_execute):
        p.add_argument("--quota", type=Path, required=True)
        p.add_argument("--smoke-result", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "quota-refresh":
            result = refresh_gemini_quota_snapshot(
                args.source,
                args.output,
                dict(os.environ),
                confirm=console_confirm,
                planned_requests=args.requests,
            )
        elif args.command == "plan":
            result = prepare(
                args.model,
                args.scenario,
                args.quota,
                args.smoke_result,
                dict(os.environ),
                args.requests,
                args.smoke_first,
            )
        elif args.command == "execute":
            result = execute(
                args.batch, args.approve, args.quota, args.smoke_result, dict(os.environ)
            )
        elif args.command == "recover-plan":
            result = prepare_recovery(
                args.batch, args.quota, args.smoke_result, dict(os.environ)
            )
        elif args.command == "recover-execute":
            result = execute_recovery(
                args.batch, args.approve, args.quota, args.smoke_result, dict(os.environ)
            )
        else:
            result = inspect(args.batch)
    except BaseException as exc:
        code = str(exc) if isinstance(exc, Refused) else "invalid_input_or_interrupted"
        print(
            json.dumps(
                {"status": "refused_or_stopped", "error_code": code, "authorization_active": False}
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
