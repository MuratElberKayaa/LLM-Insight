"""Phase 7B live demo runner: one fixed SYNTHETIC scenario, at most one Gemini Free Tier call.

    sdk/.venv/Scripts/python -m demo_app.live_gemini_content --scenario <id>              # DRY RUN
    sdk/.venv/Scripts/python -m demo_app.live_gemini_content --scenario <id> --execute    # one call
    sdk/.venv/Scripts/python -m demo_app.live_gemini_content --scenario <id> --preview    # + local

**The default is a dry run**: it checks the scenario, the Free Tier policy, the libraries,
the installed SDK against this checkout, the backend and the dashboard project, prints the
plan and never creates a Gemini client. `--execute` (the complete flag; abbreviations are
refused) sends one request through `live_gemini.run_single_request`, the path the smoke
runner uses. Nothing here retries, continues, falls back to another model or runs a second
scenario. **Never re-run automatically after a failure**: a timeout may have reached the
provider.

Stdout is one JSON line, schema `llminsight.demo.gemini_content.v1`, with metadata only; the
sole exception is `--help`. `--preview` additionally shows the synthetic input and the
model's non-thought text on stderr: see `content_preview` (a terminal can retain it).

Exit codes: 0 dry run printed, or the provider completed, local validation passed and export
is confirmed; 1 the provider failed, blocked the answer, or the call was interrupted;
2 refused before a provider send; 3 the provider returned `completed` or `incomplete`, but
local validation did not pass or export is not confirmed (the JSON says which).

`dashboard_url` and `api_path` carry the operator-supplied `LLMI_DASHBOARD_PROJECT_ID`, which
the SDK cannot verify against the ingest key; the read API requires that parameter.
"""

from __future__ import annotations

import argparse
import importlib
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NoReturn, TextIO
from urllib.parse import urlsplit

import llm_insight_sdk as lli
from demo_app import content_preview, gemini_scenarios, live_gemini
from demo_app.gemini_scenarios import Scenario, ValidationResult
from demo_app.live_common import DEFAULT_DASHBOARD_URL, check_backend, emit
from demo_app.live_gemini import ClientFactory, SingleRequestResult, refusal_docs
from llm_insight_sdk import gemini_live
from llm_insight_sdk.config import ConfigError
from llm_insight_sdk.gemini_adapter import (
    PROVIDER,
    Provenance,
    gemini_library_problems,
    usage_from_gemini_response,
)
from llm_insight_sdk.live import Refusal, input_token_bound
from llm_insight_sdk.providers import TextOutcome
from llm_insight_sdk.telemetry import TelemetryStats

SCHEMA = "llminsight.demo.gemini_content.v1"
ROOT_SPAN = "demo.live-gemini-content"
PROJECT_ID_SOURCE = "operator_env_unverified"
PROJECT_VARIABLE = "LLMI_DASHBOARD_PROJECT_ID"
BIGINT_MAX = 9_223_372_036_854_775_807  # the read API's `project` bound
_PROJECT_ID = re.compile(r"[1-9][0-9]{0,18}")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_REPO_ROOT = Path(__file__).resolve().parents[1]
_AUDITED_SDK_MODULES = (
    "gemini_live",
    "gemini_adapter",
    "live",
    "providers",
    "instrumentation",
    "telemetry",
)

CheckoutMatch = Literal[True, False, "unknown"]
RequestState = Literal["not_sent", "sent", "possibly_sent"]
ExportOutcome = Literal["confirmed", "failed", "partial", "unknown"]


class _ArgumentRefused(Exception):
    """The command line is invalid. argparse's message can quote a value, so it is dropped."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _ArgumentRefused


class _Once(argparse.Action):
    """An option that may appear once: one invocation selects one scenario."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        if getattr(namespace, self.dest, None) is not None:
            parser.error("repeated option")
        setattr(namespace, self.dest, values)


def _build_parser() -> _Parser:
    parser = _Parser(prog="python -m demo_app.live_gemini_content", allow_abbrev=False)
    parser.add_argument(
        "--scenario", required=True, action=_Once, help="one registered scenario ID"
    )
    parser.add_argument("--execute", action="store_true", help="send one request if allowed")
    parser.add_argument(
        "--preview", action="store_true", help="show the synthetic text on this terminal's stderr"
    )
    parser.add_argument("--model", action=_Once, help="one of LLMI_LIVE_MODELS (default: first)")
    parser.add_argument("--dashboard-url", action=_Once, help="a loopback HTTP origin")
    return parser


# ---------------------------------------------------------------- local checks
def sdk_checkout_match(root: Path = _REPO_ROOT) -> CheckoutMatch:
    """Whether the imported SDK's audited modules are byte-identical to this checkout. A
    stale non-editable install would otherwise run old guards beside new demo code. `unknown`
    (no source tree here) does not refuse; a mismatch or an unreadable file is False."""
    source = root / "sdk" / "src" / "llm_insight_sdk"
    if not source.is_dir():
        return "unknown"
    try:
        for name in _AUDITED_SDK_MODULES:
            module = importlib.import_module(f"llm_insight_sdk.{name}")
            installed = Path(str(module.__file__)).read_bytes()
            if installed != (source / f"{name}.py").read_bytes():
                return False
    except OSError:
        return False
    return True


def project_id_from(env: Mapping[str, str]) -> int | None:
    """The configured project ID, or None if missing or invalid. Plain ASCII digits within
    the read API's bounds: an invalid value is refused rather than dropped from a link."""
    raw = (env.get(PROJECT_VARIABLE) or "").strip()
    if not _PROJECT_ID.fullmatch(raw):
        return None
    value = int(raw)
    return value if value <= BIGINT_MAX else None


def loopback_origin(url: str) -> str | None:
    """The origin of `url` if it is a plain loopback HTTP origin, else None. Rebuilt from the
    parsed parts, because `urlsplit` silently removes tabs and newlines from its input."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "http" or parts.hostname not in _LOOPBACK_HOSTS or port == 0:
        return None
    if parts.username or parts.password or parts.query or parts.fragment:
        return None
    if parts.path not in ("", "/"):
        return None
    return f"http://{parts.netloc}"


def project_links(
    base: str | None, trace_id: str | None, project_id: int | None
) -> dict[str, str | None]:
    """Both links name the project: the read API answers 422 without it."""
    if base is None or trace_id is None or project_id is None:
        return {"dashboard_url": None, "api_path": None}
    query = f"?project={project_id}"
    return {
        "dashboard_url": f"{base}/traces/{trace_id}{query}",
        "api_path": f"/api/v1/traces/{trace_id}{query}",
    }


def export_outcome(
    flushed: bool | None, shutdown_ok: bool | None, stats: TelemetryStats | None
) -> ExportOutcome:
    """`confirmed` means the local backend acknowledged every ended span; it is not proof of
    storage. The counters are read too, so a successful flush cannot hide an earlier failed
    export. Telemetry remains best effort."""
    if stats is None:
        return "unknown"
    ended, exported, failed = stats.spans_ended, stats.spans_exported, stats.spans_export_failed
    if failed > 0 and exported == 0:
        return "failed"
    if exported > 0 and (failed > 0 or exported < ended):
        return "partial"
    if flushed is True and shutdown_ok is True and failed == 0 and exported == ended > 0:
        return "confirmed"
    return "unknown"


def _exit_code(status: str, validation: str, export: ExportOutcome) -> int:
    if status not in ("completed", "incomplete"):
        return 1
    return 0 if status == "completed" and validation == "passed" and export == "confirmed" else 3


# ---------------------------------------------------------------- JSON sections
@dataclass
class _RunState:
    mode: str = "invalid_arguments"
    may_have_sent: bool = False
    emitted: bool = False
    exit_code: int = 2


def _base_doc(mode: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "mode": mode,
        "provider_request_sent": False,
        "provider_request_state": "not_sent",
        "policy_eligible": False,
        "execution_ready": False,
        "execution_allowed": False,
        "refusals": [],
        "scenario": None,
        "plan": None,
        "environment": None,
        "provider": None,
        "usage": None,
        "session": None,
        "validation": None,
        "export": None,
        "preview": _preview_doc(False, None),
        "correlation": _correlation_doc(None, None, None),
    }


def _scenario_doc(scenario: Scenario, text: str) -> dict[str, Any]:
    return {
        "id": scenario.id,
        "version": scenario.version,
        "fixture_id": scenario.fixture_id,
        "prompt_name": scenario.prompt_name,
        "prompt_version": scenario.prompt_version,
        "input_sha256": gemini_scenarios.input_sha256(text),
        "input_sha256_scope": "local composed text; not proof of provider receipt",
        "input_utf8_bytes": len(text.encode("utf-8")),
        "input_token_bound": input_token_bound(text),
        "input_bound_note": "UTF-8 bytes + 64: a safe upper bound, not a token count",
        "max_output_tokens": scenario.max_output_tokens,
    }


def _preview_doc(requested: bool, reason: str | None) -> dict[str, Any]:
    return {
        "requested": requested,
        "shown": False,
        "output_shown": False,
        "reason": reason,
        "input_chars": None,
        "output_chars": None,
        "truncated": False,
        "thought_parts_excluded": None,
        "non_text_parts": None,
    }


def _correlation_doc(
    trace_id: str | None, project_id: int | None, base: str | None
) -> dict[str, Any]:
    return {
        "trace_id": trace_id,
        "project_id": project_id,
        "project_id_source": PROJECT_ID_SOURCE if project_id is not None else None,
        **project_links(base, trace_id, project_id),
    }


def _provider_doc(
    outcome: TextOutcome | None, provenance: Provenance, *, outcome_label: str | None = None
) -> dict[str, Any]:
    return {
        "outcome": outcome.status if outcome is not None else outcome_label,
        "incomplete_reason": outcome.incomplete_reason if outcome is not None else None,
        "provider_reason": outcome.provider_reason if outcome is not None else None,
        "error_category": outcome.error_category if outcome is not None else None,
        "provider": PROVIDER,
        "request_model": outcome.request_model if outcome is not None else None,
        "response_model": outcome.response_model if outcome is not None else None,
        "attempts": len(outcome.attempts) if outcome is not None else None,
        "duration_ms": outcome.duration_ms if outcome is not None else None,
        "provenance": provenance,
    }


def _usage_doc(outcome: TextOutcome | None, policy: gemini_live.FreeTierPolicy) -> dict[str, Any]:
    readable = outcome is not None and outcome.status != "error"
    reading = usage_from_gemini_response(outcome.provider_response) if outcome else None
    return {
        "tokens": live_gemini.tokens_doc(outcome) if outcome is not None else None,
        "provider_usage": reading.provider_counts if readable and reading else None,
        "usage_check": reading.check if readable and reading else "missing",
        "cost": live_gemini.cost_doc(outcome, policy),
    }


def _export_doc(result: SingleRequestResult) -> dict[str, Any]:
    stats = result.stats
    return {
        "outcome": export_outcome(result.flushed, result.shutdown_ok, stats),
        "flushed": result.flushed,
        "shutdown_ok": result.shutdown_ok,
        "spans_ended": stats.spans_ended if stats is not None else None,
        "spans_exported": stats.spans_exported if stats is not None else None,
        "spans_export_failed": stats.spans_export_failed if stats is not None else None,
    }


def _evaluate(
    scenario: Scenario, outcome: TextOutcome | None
) -> tuple[ValidationResult, content_preview.AnswerText | None]:
    """Local validation, only for a completed answer; it never affects the provider status."""
    if outcome is None or outcome.status not in ("completed", "incomplete"):
        return gemini_scenarios.not_evaluated("provider_not_completed"), None
    answer = content_preview.extract_answer(outcome.provider_response)
    if outcome.status == "incomplete":
        return gemini_scenarios.not_evaluated("provider_incomplete"), answer
    if answer.text is None:
        return gemini_scenarios.not_evaluated("no_text_answer"), answer
    return gemini_scenarios.validate_answer(scenario, answer.text), answer


def _show_preview(
    doc: dict[str, Any],
    err: TextIO,
    text: str,
    answer: content_preview.AnswerText | None,
    *,
    incomplete: bool,
) -> None:
    preview = doc["preview"]
    rendered = content_preview.render_preview(text, answer, incomplete=incomplete)
    shown = content_preview.write_preview(err, rendered.text)
    preview.update(
        {
            "shown": shown,
            "output_shown": shown and rendered.output_shown,
            "reason": None if shown else "stderr_write_failed",
            "input_chars": rendered.input_chars,
            "output_chars": rendered.output_chars,
            "truncated": rendered.truncated,
        }
    )
    if answer is not None:
        preview["thought_parts_excluded"] = answer.thought_parts_excluded
        preview["non_text_parts"] = answer.non_text_parts
        if answer.text is None:
            preview["reason"] = "output_withheld_non_text"


# ---------------------------------------------------------------- the run
def _finish(out: TextIO, doc: dict[str, Any], code: int, state: _RunState) -> int:
    state.emitted = True  # never a second line, whatever interrupts the write
    state.exit_code = code
    emit(out, doc)
    return code


def _refuse(out: TextIO, doc: dict[str, Any], items: list[dict[str, str]], state: _RunState) -> int:
    doc["execution_allowed"] = False
    doc["refusals"] = items
    return _finish(out, doc, 2, state)


def _scenario_limit_refusals(
    policy: gemini_live.FreeTierPolicy, scenario: Scenario, text: str
) -> list[Refusal]:
    """The session's own limits, evaluated early so a dry run cannot report ready for a
    request the reservation would refuse. The environment can refuse, never alter."""
    refusals = []
    if input_token_bound(text) > policy.max_input_tokens:
        refusals.append(
            Refusal("input_too_large", "the input bound exceeds LLMI_LIVE_MAX_INPUT_TOKENS")
        )
    if not gemini_live.MIN_OUTPUT_TOKENS <= scenario.max_output_tokens <= policy.max_output_tokens:
        refusals.append(
            Refusal(
                "output_limit_exceeded",
                "the scenario's output cap exceeds LLMI_LIVE_MAX_OUTPUT_TOKENS",
            )
        )
    return refusals


def _report_attempt(
    doc: dict[str, Any],
    result: SingleRequestResult,
    scenario: Scenario,
    text: str,
    policy: gemini_live.FreeTierPolicy,
    provenance: Provenance,
    err: TextIO,
    *,
    preview_wanted: bool,
) -> int:
    """Fill the sent/possibly-sent sections. Returns the exit code."""
    outcome = result.outcome
    doc["session"] = result.session
    doc["export"] = _export_doc(result)
    doc["usage"] = _usage_doc(outcome, policy)
    validation, answer = _evaluate(scenario, outcome)
    doc["validation"] = validation.to_doc()
    if outcome is None:
        label = "interrupted" if isinstance(result.aborted, KeyboardInterrupt) else "internal_error"
        doc["provider_request_sent"] = None  # unknown: the send may have begun
        doc["provider_request_state"] = "possibly_sent"
        doc["provider"] = _provider_doc(None, provenance, outcome_label=label)
        if preview_wanted:
            doc["preview"]["reason"] = f"output_withheld_{label}"
        return 1
    doc["provider_request_sent"] = True
    doc["provider_request_state"] = "sent"
    doc["provider"] = _provider_doc(outcome, provenance)
    if preview_wanted:
        if outcome.status in ("completed", "incomplete"):
            _show_preview(doc, err, text, answer, incomplete=outcome.status == "incomplete")
        else:
            doc["preview"]["reason"] = f"output_withheld_{outcome.status}"
    return _exit_code(outcome.status, validation.status, doc["export"]["outcome"])


def _run(
    argv: Sequence[str] | None,
    environ: Mapping[str, str],
    now: datetime | None,
    client_factory: ClientFactory | None,
    provenance: Provenance,
    out: TextIO,
    err: TextIO,
    state: _RunState,
) -> int:
    try:
        args = _build_parser().parse_args(argv)
    except _ArgumentRefused:
        doc = _base_doc("invalid_arguments")
        message = "the command line is invalid; see --help (values are not echoed)"
        return _refuse(out, doc, [{"code": "argument_invalid", "message": message}], state)
    state.mode = "execute" if args.execute else "dry_run"
    doc = _base_doc(state.mode)
    doc["preview"]["requested"] = bool(args.preview)

    scenario = gemini_scenarios.SCENARIOS.get(args.scenario)
    if scenario is None:
        message = "--scenario is not a registered scenario (the value is not echoed)"
        return _refuse(out, doc, [{"code": "scenario_unknown", "message": message}], state)
    registry_problems = gemini_scenarios.check_scenario(args.scenario, scenario)
    if registry_problems:  # before any policy read, client or provider contact
        return _refuse(out, doc, refusal_docs(registry_problems), state)
    text = gemini_scenarios.compose_input(scenario)
    doc["scenario"] = _scenario_doc(scenario, text)

    decision = gemini_live.evaluate_free_tier_policy(environ, now=now or datetime.now(UTC))
    policy_refusals = list(decision.refusals)
    policy = decision.policy
    model = args.model or (policy.models[0] if policy is not None else None)
    if policy is not None and model is not None and model not in policy.models:
        policy_refusals.append(Refusal("model_not_allowed", "--model is not in LLMI_LIVE_MODELS"))
        model = None  # a refused value is never echoed through the plan
    for problem in gemini_library_problems():  # the endpoint checks hold for these only
        policy_refusals.append(Refusal("client_library_not_audited", problem))
    if policy is not None:
        policy_refusals += _scenario_limit_refusals(policy, scenario, text)

    readiness_refusals: list[Refusal] = []
    config: lli.LLMInsightConfig | None
    try:
        config = lli.LLMInsightConfig.from_env(environ)
    except ConfigError:
        config, backend = None, {"ready": False, "reason": "telemetry_config_invalid"}
    else:
        backend = check_backend(config)
    if not backend["ready"]:
        readiness_refusals.append(
            Refusal("backend_not_ready", "the LLM Insight backend is not ready")
        )
    checkout = sdk_checkout_match()
    if checkout is False:
        readiness_refusals.append(
            Refusal(
                "sdk_checkout_mismatch",
                "the installed SDK differs from this checkout; reinstall it",
            )
        )
    project_id = project_id_from(environ)
    if project_id is None:
        readiness_refusals.append(
            Refusal(
                "dashboard_project_invalid",
                f"{PROJECT_VARIABLE} must be a positive integer project ID",
            )
        )
    base = loopback_origin(args.dashboard_url or DEFAULT_DASHBOARD_URL)
    if base is None:
        readiness_refusals.append(
            Refusal("dashboard_url_invalid", "--dashboard-url must be a loopback HTTP origin")
        )
    unsafe_preview = content_preview.preview_problem(err, environ) if args.preview else None
    if unsafe_preview is not None:
        readiness_refusals.append(
            Refusal("preview_not_safe", "--preview needs a real terminal outside CI")
        )
        doc["preview"]["reason"] = unsafe_preview

    eligible = not policy_refusals and policy is not None and model is not None
    ready = not readiness_refusals and config is not None
    doc.update(
        {
            "policy_eligible": eligible,
            "execution_ready": ready,
            "execution_allowed": eligible and ready,
            "refusals": refusal_docs([*policy_refusals, *readiness_refusals]),
            "plan": live_gemini.build_plan(
                decision,
                model,
                input_text=text,
                max_output_tokens=scenario.max_output_tokens,
            ),
            "environment": {"backend": backend, "sdk_checkout_match": checkout},
            "correlation": _correlation_doc(None, project_id, base),
        }
    )

    if not args.execute:
        doc["note"] = "dry run: no provider request was sent; pass --execute to send one"
        if args.preview and unsafe_preview is None:
            _show_preview(doc, err, text, None, incomplete=False)
        return _finish(out, doc, 0, state)
    if policy is None or model is None or config is None or not (eligible and ready):
        return _finish(out, doc, 2, state)

    state.may_have_sent = True
    result = live_gemini.run_single_request(
        policy=policy,
        config=config,
        model=model,
        input_text=text,
        max_output_tokens=scenario.max_output_tokens,
        prompt_name=scenario.prompt_name,
        prompt_version=scenario.prompt_version,
        root_span=ROOT_SPAN,
        environ=environ,
        client_factory=client_factory,
        provenance=provenance,
    )
    if result.aborted is not None and not isinstance(result.aborted, KeyboardInterrupt | Exception):
        raise result.aborted  # SystemExit and the like are not this runner's to report
    state.may_have_sent = result.send_started
    doc["correlation"] = _correlation_doc(result.trace_id, project_id, base)
    if result.outcome is None and not result.send_started:
        doc["session"] = result.session
        if result.refusals:
            return _refuse(out, doc, result.refusals, state)
        interrupted = isinstance(result.aborted, KeyboardInterrupt)
        code = "interrupted_before_send" if interrupted else "internal_error"
        name = type(result.aborted).__name__
        return _refuse(out, doc, [{"code": code, "message": name}], state)
    code_ = _report_attempt(
        doc,
        result,
        scenario,
        text,
        policy,
        provenance,
        err,
        preview_wanted=bool(args.preview),
    )
    if result.aborted is not None and result.outcome is not None:
        doc["export"]["interrupted"] = True
    return _finish(out, doc, code_, state)


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    *,
    now: datetime | None = None,
    client_factory: ClientFactory | None = None,
    provenance: Provenance = "real",
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """`client_factory` and `provenance="synthetic"` exist for offline tests only; the
    command line always uses the real client and `real` provenance."""
    stdout = sys.stdout if out is None else out
    stderr = sys.stderr if err is None else err
    environ: Mapping[str, str] = os.environ if env is None else env
    state = _RunState()
    try:
        return _run(argv, environ, now, client_factory, provenance, stdout, stderr, state)
    except (KeyboardInterrupt, Exception) as exc:
        if state.emitted:
            return state.exit_code
        # Exactly one sanitized line and no further call, whatever was interrupted.
        interrupted = isinstance(exc, KeyboardInterrupt)
        doc = _base_doc(state.mode)
        if state.may_have_sent:
            doc["provider_request_sent"] = None
            doc["provider_request_state"] = "possibly_sent"
            label = "interrupted" if interrupted else "internal_error"
            doc["provider"] = _provider_doc(None, provenance, outcome_label=label)
            doc["validation"] = gemini_scenarios.not_evaluated("provider_not_completed").to_doc()
            return _finish(stdout, doc, 1, state)
        code = "interrupted_before_send" if interrupted else "internal_error"
        return _refuse(stdout, doc, [{"code": code, "message": type(exc).__name__}], state)


if __name__ == "__main__":
    raise SystemExit(main())
