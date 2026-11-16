"""Bounded live prompt A/B: 2 approved models x 6 cases x 2 versions = 24 calls.

Reuses the existing single-call provider gates and batch metadata/trace checks. No retry,
no fallback, no content retention. A quality failure is a result, not a reason to stop;
a transport, quota, export or integrity failure stops the run without replaying any call.
Experiments in the backend remain mock-only. This runner produces a separate live report.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from demo_app import live_batch as batch
from demo_app import prompt_ab_scenarios as suite

ROOT = batch.ROOT
STORE = ROOT / "docs" / "reports" / "prompt-ab"
MODELS = ("openai/gpt-oss-20b", "gemini-3.1-flash-lite")
CALLS_PER_MODEL = 12
TOTAL_CALLS = 24
Refused = batch.Refused
require = batch.require


def load_environment(path: Path | None) -> dict[str, str]:
    """Literal dotenv subset, no shell/eval/interpolation; values are never logged."""
    values: dict[str, str] = {}
    if path:
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            require(
                bool(sep) and bool(re.fullmatch(r"[A-Z][A-Z0-9_]*", key.strip())),
                "env_file_invalid",
            )
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key.strip()] = value
    values.update(os.environ)
    if not values.get("LLMI_BATCH_READ_TOKEN"):
        values["LLMI_BATCH_READ_TOKEN"] = values.get("LLMI_API_TOKEN", "")
    return values


def child_env(model: str, source: dict[str, str], quota: dict[str, Any]) -> dict[str, str]:
    env = batch.child_env(model, source)
    checked = datetime.fromtimestamp(batch.stamp(quota["checked_at"]), UTC).date().isoformat()
    # These derive from the operator's supplied, validated free-account attestation.
    env["LLMI_PROVIDER_MODE"] = "real"
    env["OTEL_SERVICE_NAME"] = "llm-insight-live-prompt-ab"
    if model == MODELS[0]:
        env.update(
            LLMI_GROQ_MODE="free-plan", LLMI_GROQ_PLAN="free", LLMI_GROQ_PLAN_CHECKED_ON=checked
        )
    else:
        env.update(
            LLMI_GEMINI_MODE="free-tier",
            LLMI_GEMINI_BILLING_TIER="free",
            LLMI_GEMINI_TIER_CHECKED_ON=checked,
        )
    return env


def spec_for(run_id: str) -> dict[str, Any]:
    require(re.fullmatch(r"[a-f0-9]{32}", run_id), "invalid_run_id")
    jobs = []
    for model in MODELS:
        for key in suite.ordered_scenarios():
            sc = suite.SCENARIOS[key]
            require(not suite.check_scenario(key, sc), "suite_changed")
            jobs.append(
                {
                    "batch_id": run_id,
                    "trial_id": len(jobs) + 1,
                    "model": model,
                    "cli_provider": batch.MODELS[model][0],
                    "provider": batch.MODELS[model][1],
                    "scenario": key,
                    "case_id": key.split("-")[-2],
                    "variant": key.split("-")[-1],
                    "prompt_version": sc.prompt_version,
                    "input_sha256": sc.input_sha256,
                    "max_input_tokens": batch.input_token_bound(suite.compose_input(sc)),
                    "max_output_tokens": 1024,
                    "project_id": 1,
                    **({"timeout_seconds": 30.0} if model == MODELS[1] else {}),
                }
            )
    return {
        "run_id": run_id,
        "suite": suite.SUITE,
        "suite_sha256": suite.SUITE_SHA256,
        "rule_version": suite.RULE_VERSION,
        "dataset": suite.DATASET,
        "planned_requests": TOTAL_CALLS,
        "requests_per_model": CALLS_PER_MODEL,
        "code_sha256": batch.code_fingerprint(),
        "jobs": jobs,
        "max_retries": 0,
        "paid_fallback": False,
        "max_concurrency": 1,
        "project_id": 1,
        "cost_status": "unknown",
        # 31 s allows at most 2 sends/minute: <=4096 reserved tokens, below 8000 TPM.
        "intervals": {MODELS[0]: 31.0, MODELS[1]: 4.2},
        "initial_cooldown_seconds": 61.0,
    }


def run_child(job: dict[str, Any], env: dict[str, str], execute: bool):
    args = [
        sys.executable,
        "-m",
        "demo_app.live_compare",
        "--suite",
        suite.SUITE,
        "--provider",
        job["cli_provider"],
        "--scenario",
        job["scenario"],
        "--dashboard-url",
        batch.DASHBOARD,
    ]
    if execute:
        args.append("--execute")
    with subprocess.Popen(  # noqa: S603 - fixed module/registered arguments; no shell
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
    require(isinstance(value, dict), "runner_output_invalid")
    return process.returncode, value


def validate_result(job: dict[str, Any], doc: dict[str, Any], rc: int) -> dict[str, bool]:
    """Permit genuine quality failures (exit 3), never transport/guard/export failures."""
    p, x, v = doc.get("provider") or {}, doc.get("export") or {}, doc.get("validation") or {}
    require(doc.get("mode") == "execute" and doc.get("suite") == suite.SUITE, "runner_failed")
    require(v.get("status") in ("passed", "failed"), "validation_not_evaluated")
    require(rc == (0 if v["status"] == "passed" else 3), "runner_failed")
    require(
        doc.get("provider_request_state") == "sent" and p.get("attempts") == 1,
        "uncertain_or_multiple_send",
    )
    require(
        p.get("provider") == job["provider"]
        and p.get("request_model") == job["model"]
        and p.get("response_model") == job["model"],
        "result_model_mismatch",
    )
    require(
        p.get("outcome") == "completed"
        and not doc.get("free_plan_violation")
        and p.get("provenance") == "real",
        "provider_failed",
    )
    require(
        x.get("outcome") == "confirmed" and x.get("spans_export_failed") == 0, "telemetry_failed"
    )
    sc = doc.get("scenario") or {}
    require(
        sc.get("id") == job["scenario"]
        and sc.get("input_sha256") == job["input_sha256"]
        and sc.get("prompt_version") == job["prompt_version"],
        "result_scenario_mismatch",
    )
    require((doc.get("correlation") or {}).get("project_id") == 1, "wrong_project")
    items = v.get("rules")
    require(isinstance(items, list) and len(items) == len(suite.RULE_NAMES), "invalid_rules")
    require(
        all(
            isinstance(i, dict)
            and set(i) == {"rule", "passed"}
            and i["rule"] in suite.RULE_NAMES
            and type(i["passed"]) is bool
            for i in items
        ),
        "invalid_rules",
    )
    rules = {i["rule"]: i["passed"] for i in items}
    require(set(rules) == set(suite.RULE_NAMES), "invalid_rules")
    require(all(rules.values()) == (v["status"] == "passed"), "inconsistent_validation")
    return rules


def verify_record(job: dict[str, Any], record: dict[str, Any], env: dict[str, str]) -> float:
    batch.verify_trace(job, record, env)
    stored = batch.read_trace(record["trace_id"], env)
    client = next(s for s in stored["spans"] if s["kind"] == "CLIENT")
    ns = int(client["duration_ns"])
    require(ns >= 0, "invalid_duration")
    # Repeat-export and provider response mismatch are covered by the existing verifier.
    return ns / 1_000_000


def quotas_for(paths: dict[str, Path], *, fresh: bool) -> dict[str, dict[str, Any]]:
    result = {}
    for model in MODELS:
        q = batch.read(paths[model])
        batch.quota_check(q, model, time.time(), fresh=fresh, planned_requests=CALLS_PER_MODEL)
        result[model] = q
    return result


def check_reader(source: dict[str, str]) -> None:
    """Verify read-back access before spending any of the approved requests."""
    token = source.get("LLMI_BATCH_READ_TOKEN")
    require(bool(token), "backend_read_token_missing")
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=5) as client:
        response = client.get(
            "http://127.0.0.1:8010/api/v1/projects",
            headers={"Authorization": "Bearer " + token},
        )
        require(response.status_code == 200, "backend_read_access_failed")
        data = response.json()
        require(isinstance(data, dict) and isinstance(data.get("items"), list),
                "backend_project_list_invalid")
        require(any(p.get("id") == 1 for p in data["items"]), "project_not_visible")


def prepare(paths: dict[str, Path], source: dict[str, str]) -> dict[str, Any]:
    spec = spec_for(uuid.uuid4().hex)
    # Evidence must remain within its originally attested <=1h lifetime. No timestamp refresh.
    quotas = quotas_for(paths, fresh=False)
    check_reader(source)
    spec["quota_sha256"] = {m: batch.digest(q) for m, q in quotas.items()}
    refusals = []
    for job in spec["jobs"]:
        env = child_env(job["model"], source, quotas[job["model"]])
        rc, doc = run_child(job, env, False)
        try:
            batch.check_dry(job, doc, rc)
        except Refused as exc:
            # Only allowlisted policy codes, not credentials or provider content.
            codes = [
                x.get("code")
                for x in doc.get("refusals", [])
                if isinstance(x, dict)
                and isinstance(x.get("code"), str)
                and re.fullmatch(r"[a-z_]{1,80}", x["code"])
            ]
            refusals.append({"trial_id": job["trial_id"], "code": str(exc), "policy": codes})
    folder = STORE / spec["run_id"]
    folder.mkdir(parents=True, exist_ok=False)
    batch.save_new(folder / "plan.json", {"spec": spec, "spec_sha256": batch.digest(spec)})
    result = {
        "run_id": spec["run_id"],
        "status": "ready" if not refusals else "blocked",
        "planned_requests": TOTAL_CALLS,
        "provider_requests_sent": 0,
        "approve_sha256": batch.digest(spec),
        "refusals": refusals,
    }
    batch.save_new(folder / "preflight.json", result)
    return result


def execute(run_id: str, approval: str, paths: dict[str, Path], source: dict[str, str]):
    expected = spec_for(run_id)
    folder = STORE / run_id
    saved = batch.read(folder / "plan.json")
    spec = saved["spec"]
    require(batch.digest(spec) == approval == saved["spec_sha256"], "approval_mismatch")
    require(
        set(spec) == set(expected) | {"quota_sha256"}
        and {k: spec.get(k) for k in expected} == expected,
        "plan_or_code_changed",
    )
    require(batch.read(folder / "preflight.json")["status"] == "ready", "preflight_not_ready")
    quotas = quotas_for(paths, fresh=False)
    require(
        {m: batch.digest(q) for m, q in quotas.items()} == spec["quota_sha256"], "quota_changed"
    )
    require(not (folder / "approval-consumed.json").exists(), "approval_consumed")
    envs = {m: child_env(m, source, quotas[m]) for m in MODELS}
    check_reader(source)
    batch.STORE.mkdir(exist_ok=True, parents=True)
    lock = batch.STORE / "execution.lock"  # shared with every old batch, not a parallel lane
    batch.save_new(lock, {"batch_id": run_id, "kind": "prompt_ab"})
    journal = folder / "journal.jsonl"
    reserved = 0
    try:
        batch.save_new(
            folder / "approval-consumed.json",
            {
                "spec_sha256": approval,
                "at": time.time(),
                "authorization_reusable": False,
            },
        )
        next_send = time.monotonic() + spec["initial_cooldown_seconds"]
        seen: set[str] = set()
        for job in spec["jobs"]:
            model, trial = job["model"], job["trial_id"]
            while time.monotonic() < next_send:
                time.sleep(max(0, min(1, next_send - time.monotonic())))
            require(batch.code_fingerprint() == spec["code_sha256"], "code_changed")
            q = batch.read(paths[model])
            require(batch.digest(q) == spec["quota_sha256"][model], "quota_changed")
            batch.quota_check(q, model, time.time(), planned_requests=CALLS_PER_MODEL)
            rc, dry = run_child(job, envs[model], False)
            batch.check_dry(job, dry, rc)
            require(
                batch.digest(batch.read(paths[model])) == spec["quota_sha256"][model],
                "quota_changed",
            )
            require(time.time() + 90 < batch.stamp(q["valid_until"]), "quota_expires_during_send")
            batch.append(
                journal,
                {
                    "event": "reserved",
                    "at": time.time(),
                    "trial_id": trial,
                    "request_state": "possibly_sent",
                    "tokens_reserved": 2048,
                },
            )
            reserved += 1
            rc, doc = run_child(job, envs[model], True)
            record = {
                **batch.metadata(job, doc, trial),
                "case_id": job["case_id"],
                "variant": job["variant"],
                "input_sha256": job["input_sha256"],
            }
            batch.append(journal, {"event": "result", "at": time.time(), **record})
            require(
                record["trace_id"] is not None and record["trace_id"] not in seen,
                "missing_or_duplicate_trace",
            )
            seen.add(record["trace_id"])
            duration = verify_record(job, record, envs[model])
            rules = validate_result(job, doc, rc)
            batch.append(
                journal,
                {
                    "event": "verified",
                    "at": time.time(),
                    "trial_id": trial,
                    "client_latency_ms": duration,
                    "rules": rules,
                },
            )
            print(
                json.dumps(
                    {
                        "event": "verified",
                        "trial": trial,
                        "of": TOTAL_CALLS,
                        "model": model,
                        "case": job["case_id"],
                        "variant": job["variant"],
                        "validation": record["validation"],
                    }
                ),
                flush=True,
            )
            signals = (doc.get("provider") or {}).get("rate_limit_signals") or {}
            require(
                signals.get("requests_limit", q["limits"]["rpd"]) <= q["limits"]["rpd"]
                and signals.get("tokens_limit", q["limits"]["tpm"]) <= q["limits"]["tpm"]
                and signals.get("requests_remaining", 1) > 0
                and signals.get("tokens_remaining", 2048) >= 2048,
                "quota_signal_stop",
            )
            next_send = time.monotonic() + spec["intervals"][model]
        batch.append(journal, {"event": "finished", "at": time.time()})
        lock.unlink()
    except BaseException as exc:
        code = str(exc) if isinstance(exc, Refused) else "interrupted_or_internal_error"
        batch.append(journal, {"event": "stopped", "at": time.time(), "code": code})
        if reserved == 0:
            lock.unlink()
        raise Refused(code) from None
    finally:
        write_report(run_id)
    return {"status": "completed", "run_id": run_id, "verified_calls": TOTAL_CALLS}


def summarize(spec: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    results = {e["trial_id"]: e for e in events if e["event"] == "result"}
    verified = {e["trial_id"]: e for e in events if e["event"] == "verified"}
    rows = []
    for j in spec["jobs"]:
        result, evidence = results.get(j["trial_id"], {}), verified.get(j["trial_id"], {})
        rows.append(
            {
                **j,
                "validation": result.get("validation", "not_evaluated"),
                "trace_id": result.get("trace_id"),
                "verified": bool(evidence),
                "technical_status": result.get("technical_status", "not_run"),
                "input_tokens": result.get("input_tokens"),
                "output_tokens": result.get("output_tokens"),
                "client_latency_ms": evidence.get("client_latency_ms"),
                "rules": evidence.get("rules", {}),
            }
        )
    groups = []
    for model in MODELS:
        for variant in ("v1", "v2"):
            selected = [r for r in rows if r["model"] == model and r["variant"] == variant]
            ok = [r for r in selected if r["verified"]]
            passed = sum(r["validation"] == "passed" for r in ok)
            durations = [r["client_latency_ms"] for r in ok]
            groups.append(
                {
                    "model": model,
                    "variant": variant,
                    "planned": 6,
                    "verified": len(ok),
                    "passed": passed,
                    "failed": len(ok) - passed,
                    "not_evaluated": 6 - len(ok),
                    "pass_rate": passed / len(ok) if ok else None,
                    "client_p50_ms": statistics.median(durations) if durations else None,
                    "reported_input_tokens": sum(r["input_tokens"] or 0 for r in ok),
                    "reported_output_tokens": sum(r["output_tokens"] or 0 for r in ok),
                    "missing_input_usage": sum(r["input_tokens"] is None for r in ok),
                    "missing_output_usage": sum(r["output_tokens"] is None for r in ok),
                    "cost_status": "unknown",
                }
            )
    comparisons = []
    for model in MODELS:
        a, b = [g for g in groups if g["model"] == model]
        complete = a["verified"] == b["verified"] == 6
        comparisons.append(
            {
                "model": model,
                "complete_paired_evidence": complete,
                "delta_passed_v2_minus_v1": b["passed"] - a["passed"] if complete else None,
                "conclusion": "descriptive_only_n6" if complete else "incomplete_no_comparison",
            }
        )
    return {
        "run_id": spec["run_id"],
        "suite": suite.SUITE,
        "complete": all(r["verified"] for r in rows),
        "groups": groups,
        "comparisons": comparisons,
        "cases": rows,
        "provider_requests_reserved": sum(e["event"] == "reserved" for e in events),
        "source_provenance": "real_asserted",
        "inputs": "synthetic",
        "winner": None,
        "statistical_significance": "not_computed",
    }


def write_report(run_id: str) -> dict[str, Any]:
    require(re.fullmatch(r"[a-f0-9]{32}", run_id), "invalid_run_id")
    folder = STORE / run_id
    spec = batch.read(folder / "plan.json")["spec"]
    journal = folder / "journal.jsonl"
    events = (
        [json.loads(line) for line in journal.read_text().splitlines()] if journal.exists() else []
    )
    report = summarize(spec, events)
    batch.replace_json(folder / "report.json", report)
    lines = [
        "# Canlı prompt A/B — gerçek sağlayıcı yanıtları",
        "",
        "6 sentetik vaka × 2 gerçek prompt × 2 model. Sonuçlar önceden yazılmış değildir.",
        "V2, V1'e kategori/öncelik iş kurallarını ekler. "
        "Aynı girdiler ve aynı değerlendirici kullanılır.",
        "Küçük örneklem; tek tekrar; sağlayıcı varsayılan sıcaklığı. "
        "Genel model sıralaması yapılmaz.",
        "Maliyet bilinmiyor; real etiketi SDK beyanıdır. Ham cevaplar saklanmaz.",
        "",
        "| Model | Prompt | Geçti / değerlendirilen | Değerlendirilmeyen | CLIENT p50 |",
        "|---|---|---:|---:|---:|",
    ]
    for g in report["groups"]:
        latency = "—" if g["client_p50_ms"] is None else f"{g['client_p50_ms']:.1f} ms"
        lines.append(
            f"| {g['model']} | {g['variant']} | {g['passed']}/{g['verified']} | "
            f"{g['not_evaluated']}/6 | {latency} |"
        )
    lines += [
        "",
        "## Vaka bazında izlenebilirlik",
        "",
        "| Model | Vaka | Prompt | Sonuç | Başarısız kurallar | Trace |",
        "|---|---|---|---|---|---|",
    ]
    for r in report["cases"]:
        trace = r["trace_id"]
        link = f"[{trace[:8]}]({batch.DASHBOARD}/traces/{trace}?project=1)" if trace else "—"
        failed = ", ".join(k for k, v in r["rules"].items() if not v) or "—"
        status = r["validation"] if r["verified"] else "doğrulanmadı / değerlendirilmedi"
        lines.append(
            f"| {r['model']} | {r['case_id']} | {r['variant']} | {status} | {failed} | {link} |"
        )
    lines += [
        "",
        "Tam eşleşmiş kanıt: " + ("24/24" if report["complete"] else "Eksik; kazanan çıkarılmaz."),
        "",
        "Promptlar ve referans etiketler: `demo_app/prompt_ab_scenarios.py`.",
        "Eski golden mock sonuçları bu rapora dahil değildir.",
    ]
    (folder / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("action", choices=("plan", "execute", "report"))
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--groq-quota", type=Path)
    parser.add_argument("--gemini-quota", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--approve")
    args = parser.parse_args()
    try:
        if args.action == "report":
            report = write_report(args.run_id)
            value = {"status": "reported", "complete": report["complete"]}
        else:
            require(args.groq_quota is not None and args.gemini_quota is not None, "quota_required")
            paths = {MODELS[0]: args.groq_quota, MODELS[1]: args.gemini_quota}
            env = load_environment(args.env_file)
            value = (
                prepare(paths, env)
                if args.action == "plan"
                else execute(args.run_id, args.approve, paths, env)
            )
        print(json.dumps(value), flush=True)
        return 0 if value.get("status") != "blocked" else 2
    except (Refused, Exception) as exc:
        code = str(exc) if isinstance(exc, Refused) else "local_error"
        print(json.dumps({"status": "refused", "code": code, "retry_allowed": False}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
