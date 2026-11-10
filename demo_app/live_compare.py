"""Phase 8A-4 comparison runner: one fixed SYNTHETIC scenario, at most one provider request.

    sdk/.venv/Scripts/python -m demo_app.live_compare --provider groq --scenario <id>   # dry run
    sdk/.venv/Scripts/python -m demo_app.live_compare --provider groq --scenario <id> --execute

**The default is a dry run.** It checks the scenario, the provider's guard policy, the audited
libraries, the installed SDK against this checkout, the backend and the dashboard project, and
prints the plan. It never builds a provider client and never contacts a provider. `--execute` (the
complete flag; abbreviations are refused) sends one request only when every gate passes. Nothing
here retries, continues, falls back to another model or provider, or runs a second scenario.
**Never re-run automatically after a failure**: a timeout may have reached the provider.

**Providers** form a closed registry. Only `groq` can be executed, through the unchanged Free Plan
guard and endpoint boundary (ADR-43, ADR-44): `groq_live.FreePlanSession` is the client's send
gate and re-checks everything immediately before the send. In this version that guard refuses
every real request (`REAL_EXECUTION_ENABLED` is false and no Free Plan evidence is released).
`gemini` is refused until the R123 security fix is reviewed, and `openrouter` is refused because no
OpenRouter adapter exists. Both are refused before any setting is read.

**The credential** is read once from the same environment the policy validates and handed to the
client, which never sets it on the HTTP client. The send gate checks that the request carries
exactly that validated key. No key, input or output is printed, logged or exported.

**Comparison data** belongs in a dedicated LLM Insight project (decision D5): create its ingest key
with `python -m app.cli create-key --project llm-insight-compare` and use that key and project ID
here. The SDK cannot verify which project a key belongs to, so `project_id_source` says so. Each
scenario has its own `prompt_version`, so `/models/compare` keeps one row per provider, model and
scenario.

Stdout is one JSON line, schema `llminsight.demo.compare.v1`, with metadata only (`--help` is the
sole exception). Exit codes: 0 dry run printed, or the provider completed, local validation passed
and export is confirmed; 1 the provider failed or blocked the answer, or the call was interrupted
after the send may have begun; 2 refused before a provider send; 3 the provider answered but local
validation did not pass or export is not confirmed, or a Free Plan violation indicator halted the
session (`free_plan_violation`). Cost is always `unknown`, never 0.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, NoReturn, TextIO
from urllib.parse import urlsplit

import httpx

import llm_insight_sdk as lli
from demo_app import comparison_scenarios
from demo_app.comparison_scenarios import ComparisonScenario, ValidationResult
from demo_app.live_common import DEFAULT_DASHBOARD_URL, check_backend, emit
from llm_insight_sdk import groq_adapter, groq_live
from llm_insight_sdk.config import ConfigError
from llm_insight_sdk.groq_adapter import (
    GroqCompletion,
    GroqTextAdapter,
    ObservedGroqClient,
    UnsupportedRequestError,
)
from llm_insight_sdk.live import LiveRefusal, Refusal, input_token_bound
from llm_insight_sdk.providers import TextOutcome, TextRequest
from llm_insight_sdk.telemetry import Telemetry, TelemetryStats

SCHEMA = "llminsight.demo.compare.v1"
ROOT_SPAN = "demo.live-compare"
PROJECT_ID_SOURCE = "operator_env_unverified"
PROJECT_VARIABLE = "LLMI_DASHBOARD_PROJECT_ID"
COMPARISON_PROJECT_NAME = "llm-insight-compare"
COMPARE_GROUP_KEY = ("provider", "requested_model", "response_model", "prompt_version")
BIGINT_MAX = 9_223_372_036_854_775_807  # the read API's `project` bound
_PROJECT_ID = re.compile(r"[1-9][0-9]{0,18}")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_REPO_ROOT = Path(__file__).resolve().parents[1]
# The modules whose code decides whether, where and what is sent.
_AUDITED_SDK_MODULES = (
    "groq_live",
    "groq_adapter",
    "live",
    "providers",
    "instrumentation",
    "telemetry",
    "_guards",
)
PLAN_BASIS = (
    "operator attestation of the Groq account's plan; Groq exposes no plan API, so this is not "
    "verified with Groq's billing system"
)
COST_BASIS = (
    "Free Plan: no billed amount is reported and no stored price applies to groq (ADR-40); "
    "a published per-token price is never shown as a charge; unknown is never 0"
)

CheckoutMatch = Literal[True, False, "unknown"]
RequestState = Literal["not_sent", "sent", "possibly_sent"]
ExportOutcome = Literal["confirmed", "failed", "partial", "unknown"]
HttpFactory = Callable[[], httpx.Client]
Clock = Callable[[], datetime]


# ---------------------------------------------------------------- providers
@dataclass(frozen=True)
class ProviderEntry:
    """One provider of the closed registry. `refusal` set: refused before anything is read."""

    name: str  # the command-line value
    provider: str  # the telemetry `gen_ai.provider.name`
    refusal: Refusal | None = None


PROVIDERS: Mapping[str, ProviderEntry] = MappingProxyType(
    {
        "groq": ProviderEntry("groq", groq_adapter.PROVIDER),
        "gemini": ProviderEntry(
            "gemini",
            "gcp.gemini",
            Refusal(
                "provider_blocked",
                "Gemini comparison runs are blocked until the R123 security fix is reviewed",
            ),
        ),
        "openrouter": ProviderEntry(
            "openrouter",
            "openrouter",
            Refusal(
                "provider_not_implemented",
                "no OpenRouter adapter exists, so no request can be routed to OpenRouter",
            ),
        ),
    }
)


# ---------------------------------------------------------------- command line
class _ArgumentRefused(Exception):
    """The command line is invalid. argparse's message can quote a value, so it is dropped."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _ArgumentRefused


class _Once(argparse.Action):
    """An option that may appear once: one invocation selects one provider and one scenario."""

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
    parser = _Parser(prog="python -m demo_app.live_compare", allow_abbrev=False)
    parser.add_argument("--provider", required=True, action=_Once, help="one registered provider")
    parser.add_argument("--scenario", required=True, action=_Once, help="one registered scenario")
    parser.add_argument("--execute", action="store_true", help="send one request if allowed")
    parser.add_argument("--dashboard-url", action=_Once, help="a loopback HTTP origin")
    return parser


# ---------------------------------------------------------------- local checks
def sdk_checkout_match(root: Path = _REPO_ROOT) -> CheckoutMatch:
    """Whether the imported SDK's audited modules are byte-identical to this checkout, so a stale
    non-editable install cannot run old guards beside new runner code. `unknown` (no source tree
    here) does not refuse; a mismatch or an unreadable file is False."""
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
    """The configured project ID, or None if missing or invalid: plain ASCII digits within the
    read API's bounds. An invalid value is refused rather than dropped from a link."""
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


def refusal_docs(items: Sequence[Refusal]) -> list[dict[str, str]]:
    return [{"code": r.code, "message": r.message} for r in items]


def _request_state(value: object) -> RequestState:
    """The guard's recorded state; anything unreadable is `possibly_sent`, never `not_sent`."""
    if value == "not_sent":
        return "not_sent"
    if value == "sent":
        return "sent"
    return "possibly_sent"


# ---------------------------------------------------------------- JSON sections
def constraints_doc() -> dict[str, Any]:
    """What every Groq comparison request is held to, whatever the environment says."""
    return {
        "provider": groq_adapter.PROVIDER,
        "endpoint": f"POST {groq_adapter.ENDPOINT_URL}",
        "messages": "exactly one user message; no system prompt",
        "request_fields": [
            "model",
            "messages",
            "max_completion_tokens",
            "stream",
            "include_reasoning",
        ],
        "streaming": False,
        "include_reasoning": False,
        "tools": [],
        "requests_per_execution": groq_live.MAX_REQUESTS_CAP,
        "physical_attempts": 1,
        "retries": 0,
        "concurrency": 1,
        "timeout_seconds": groq_adapter.TOTAL_DEADLINE_SECONDS,
        "max_input_token_bound": groq_live.MAX_INPUT_TOKENS_CAP,
        "max_output_tokens": comparison_scenarios.UNIFORM_OUTPUT_TOKENS,
        "model_fallback": False,
        "provider_fallback": False,
        "paid_plan": False,
        "redirects_followed": False,
        "proxy": False,
        "base_url_override": False,
        "real_execution_enabled": groq_adapter.REAL_EXECUTION_ENABLED,
        "free_plan_models_released": len(groq_live.FREE_PLAN_MODELS),
    }


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
        "provider_selection": None,
        "scenario": None,
        "constraints": None,
        "plan": None,
        "environment": None,
        "provider": None,
        "usage": None,
        "session": None,
        "free_plan_violation": None,
        "validation": None,
        "export": None,
        "comparison": _comparison_doc(None, None),
        "correlation": _correlation_doc(None, None, None),
    }


def _scenario_doc(scenario: ComparisonScenario, text: str) -> dict[str, Any]:
    return {
        "id": scenario.id,
        "version": scenario.version,
        "fixture_id": scenario.fixture_id,
        "prompt_name": scenario.prompt_name,
        "prompt_version": scenario.prompt_version,
        "input_sha256": comparison_scenarios.input_sha256(text),
        "input_sha256_scope": "local composed text; not proof of provider receipt",
        "input_utf8_bytes": len(text.encode("utf-8")),
        "input_token_bound": input_token_bound(text),
        "input_bound_note": "UTF-8 bytes + 64: a safe upper bound, not a token count",
        "max_output_tokens": scenario.max_output_tokens,
        "expected_form": scenario.expected_form,
    }


def _comparison_doc(scenario: ComparisonScenario | None, project_id: int | None) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "project_id_source": PROJECT_ID_SOURCE if project_id is not None else None,
        "project_requirement": (
            f"a dedicated LLM Insight project (create-key --project {COMPARISON_PROJECT_NAME}); "
            "the SDK cannot verify which project the ingest key belongs to"
        ),
        "root_span": ROOT_SPAN,
        "prompt_name": scenario.prompt_name if scenario is not None else None,
        "prompt_version": scenario.prompt_version if scenario is not None else None,
        "compare_group_key": list(COMPARE_GROUP_KEY),
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


def build_plan(
    policy: groq_live.FreePlanPolicy | None, scenario: ComparisonScenario, text: str
) -> dict[str, Any] | None:
    if policy is None:
        return None
    return {
        "provider": groq_adapter.PROVIDER,
        "model": policy.model,
        "endpoint": groq_adapter.ENDPOINT_URL,
        "max_output_tokens": scenario.max_output_tokens,
        "input_token_bound": input_token_bound(text),
        "max_requests": policy.max_requests,
        "max_retries": policy.max_retries,
        "max_concurrency": policy.max_concurrency,
        "timeout_seconds": policy.timeout_seconds,
        "plan": groq_live.PLAN_FREE,
        "plan_checked_on": policy.plan_checked_on.isoformat(),
        "plan_basis": PLAN_BASIS,
        "free_plan_evidence_verified_at": (
            groq_live.FREE_PLAN_EVIDENCE_VERIFIED_AT.isoformat()
            if groq_live.FREE_PLAN_EVIDENCE_VERIFIED_AT is not None
            else None
        ),
        "paid_fallback": False,
    }


def _provider_doc(
    outcome: TextOutcome | None, *, outcome_label: str | None = None
) -> dict[str, Any]:
    return {
        "outcome": outcome.status if outcome is not None else outcome_label,
        "incomplete_reason": outcome.incomplete_reason if outcome is not None else None,
        "provider_reason": outcome.provider_reason if outcome is not None else None,
        "error_category": outcome.error_category if outcome is not None else None,
        "provider": groq_adapter.PROVIDER,
        "request_model": outcome.request_model if outcome is not None else None,
        "response_model": outcome.response_model if outcome is not None else None,
        "service_tier": outcome.service_tier if outcome is not None else None,
        "attempts": len(outcome.attempts) if outcome is not None else None,
        "duration_ms": outcome.duration_ms if outcome is not None else None,
        "provenance": "real",
    }


def _usage_doc(outcome: TextOutcome | None, policy: groq_live.FreePlanPolicy) -> dict[str, Any]:
    usage = outcome.usage if outcome is not None else None
    return {
        "tokens": (
            {
                "input": usage.input_tokens,
                "output": usage.output_tokens,
                "cache_read_input": usage.cache_read_input_tokens,
                "reasoning_output": usage.reasoning_output_tokens,
            }
            if usage is not None
            else None  # unknown, never 0
        ),
        "usage_source": "provider_reported" if usage is not None else "unknown",
        "cost": {
            "status": "unknown",
            "billed_amount_usd": None,
            "list_price_estimate_usd": None,
            "ledger_cost_status": "unknown (price_missing)",
            "plan": groq_live.PLAN_FREE,
            "plan_checked_on": policy.plan_checked_on.isoformat(),
            "plan_basis": PLAN_BASIS,
            "basis": COST_BASIS,
        },
    }


def _evaluate(scenario: ComparisonScenario, outcome: TextOutcome | None) -> ValidationResult:
    """Local validation of a completed answer only; it never affects the provider status. The
    answer is read here and nowhere else, and only rule codes leave this function."""
    if outcome is None or outcome.status not in ("completed", "incomplete"):
        return comparison_scenarios.not_evaluated("provider_not_completed")
    if outcome.status == "incomplete":
        return comparison_scenarios.not_evaluated("provider_incomplete")
    completion = outcome.provider_response
    text = completion.text if isinstance(completion, GroqCompletion) else None
    if text is None:
        return comparison_scenarios.not_evaluated("no_text_answer")
    return comparison_scenarios.validate_answer(scenario, text)


# ---------------------------------------------------------------- the one request
def _default_http() -> httpx.Client:
    return groq_adapter.build_groq_http_client()


@dataclass
class GroqRunResult:
    """What `run_groq_request` did. `refusals` non-empty and `request_state` `not_sent`: nothing
    was sent. `session` is the guard's own record, so `request_state` is never guessed here.
    `aborted` is an exception that ended the attempt, handed back after telemetry is closed."""

    refusals: list[dict[str, str]] = field(default_factory=list)
    outcome: TextOutcome | None = None
    trace_id: str | None = None
    session: dict[str, object] | None = None
    flushed: bool | None = None
    shutdown_ok: bool | None = None
    stats: TelemetryStats | None = None
    aborted: BaseException | None = None

    @property
    def request_state(self) -> RequestState:
        return _request_state((self.session or {}).get("provider_request_state"))

    @property
    def halted(self) -> str | None:
        halted = (self.session or {}).get("halted")
        return halted if isinstance(halted, str) else None


def run_groq_request(
    *,
    policy: groq_live.FreePlanPolicy,
    config: lli.LLMInsightConfig,
    scenario: ComparisonScenario,
    input_text: str,
    environ: Mapping[str, str],
    clock: Clock,
    http_factory: HttpFactory = _default_http,
    session_holder: list[groq_live.FreePlanSession] | None = None,
) -> GroqRunResult:
    """The one security path: the guard's session, the audited client, the ADR-43 checks, one
    adapter call under a root span, flush and shutdown. At most one request, never resent. The
    session re-evaluates the policy, the boundary, the credential and the body before the send."""
    result = GroqRunResult()
    session = groq_live.FreePlanSession(environ, clock=clock)
    if session_holder is not None:
        session_holder.append(session)
    http: httpx.Client | None = None
    telemetry: Telemetry | None = None
    try:
        try:
            http = http_factory()
            problems = groq_adapter.live_groq_client_problems(http)
        except Exception as exc:  # e.g. an unreadable CA bundle: nothing was sent
            result.refusals = [
                {"code": "client_construction_failed", "message": type(exc).__name__}
            ]
            return result
        if problems:
            result.refusals = [{"code": "client_not_allowed", "message": p} for p in problems]
            return result
        telemetry = lli.init(config)
        try:
            # The same reading the policy validated; the send gate checks the request carries it.
            observed = ObservedGroqClient(
                telemetry,
                groq_live.api_key_value(environ),
                http_client=http,
                provenance="real",
                send_gate=session,
            )
        except ConfigError:
            result.refusals = [
                {"code": "client_not_allowed", "message": "the real client could not be built"}
            ]
            return result
        request = TextRequest(
            model=policy.model,
            input_text=input_text,
            max_output_tokens=scenario.max_output_tokens,
            prompt_name=scenario.prompt_name,
            prompt_version=scenario.prompt_version,
        )
        with telemetry.tracer.start_as_current_span(ROOT_SPAN) as root:
            result.trace_id = format(root.get_span_context().trace_id, "032x")
            try:
                result.outcome = GroqTextAdapter(observed).generate_text(request)
            except LiveRefusal as refusal:  # the gate refused immediately before the send
                result.refusals = refusal_docs(refusal.refusals)
            except (ConfigError, UnsupportedRequestError):
                result.refusals = [
                    {
                        "code": "client_not_allowed",
                        "message": "the client or request failed its check before the send",
                    }
                ]
    except BaseException as exc:  # handed back after the snapshot and the flush below
        result.aborted = exc
    finally:
        result.session = session.snapshot()
        if telemetry is not None:
            try:
                result.flushed = telemetry.force_flush()
                result.shutdown_ok = telemetry.shutdown()
            except BaseException as exc:
                result.aborted = result.aborted or exc
            result.stats = telemetry.stats()
        if http is not None:
            with contextlib.suppress(Exception):  # closing never changes what was sent
                http.close()
    return result


# ---------------------------------------------------------------- the run
@dataclass
class _RunState:
    mode: str = "invalid_arguments"
    sessions: list[groq_live.FreePlanSession] = field(default_factory=list)
    emitted: bool = False
    exit_code: int = 2

    def request_state(self) -> RequestState:
        if not self.sessions:
            return "not_sent"
        return _request_state(self.sessions[0].snapshot()["provider_request_state"])


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
    policy: groq_live.FreePlanPolicy, scenario: ComparisonScenario, text: str
) -> list[Refusal]:
    """The session's own limits, evaluated early so a dry run cannot report ready for a request
    the gate would refuse. The environment can refuse, never alter."""
    refusals = []
    if input_token_bound(text) > policy.max_input_tokens:
        refusals.append(
            Refusal("input_too_large", "the input bound exceeds LLMI_LIVE_MAX_INPUT_TOKENS")
        )
    if not groq_live.MIN_OUTPUT_TOKENS <= scenario.max_output_tokens <= policy.max_output_tokens:
        refusals.append(
            Refusal(
                "output_limit_exceeded",
                "the scenario's output cap exceeds LLMI_LIVE_MAX_OUTPUT_TOKENS",
            )
        )
    return refusals


def _exit_code(outcome: TextOutcome, validation: str, export: ExportOutcome, halted: bool) -> int:
    if halted:
        return 3
    if outcome.status not in ("completed", "incomplete"):
        return 1
    if outcome.status == "completed" and validation == "passed" and export == "confirmed":
        return 0
    return 3


def _report_attempt(
    doc: dict[str, Any],
    result: GroqRunResult,
    scenario: ComparisonScenario,
    policy: groq_live.FreePlanPolicy,
) -> int:
    """Fill the sent / possibly-sent sections. Returns the exit code."""
    outcome = result.outcome
    stats = result.stats
    doc["session"] = result.session
    doc["free_plan_violation"] = result.halted
    doc["export"] = {
        "outcome": export_outcome(result.flushed, result.shutdown_ok, stats),
        "flushed": result.flushed,
        "shutdown_ok": result.shutdown_ok,
        "spans_ended": stats.spans_ended if stats is not None else None,
        "spans_exported": stats.spans_exported if stats is not None else None,
        "spans_export_failed": stats.spans_export_failed if stats is not None else None,
    }
    doc["usage"] = _usage_doc(outcome, policy)
    validation = _evaluate(scenario, outcome)
    doc["validation"] = validation.to_doc()
    state = result.request_state
    doc["provider_request_state"] = state
    doc["provider_request_sent"] = {"sent": True, "not_sent": False}.get(state)
    if outcome is None:
        label = "interrupted" if isinstance(result.aborted, KeyboardInterrupt) else "internal_error"
        doc["provider"] = _provider_doc(None, outcome_label=label)
        return 1  # a halt needs a response or a provider error, so it always has an outcome
    doc["provider"] = _provider_doc(outcome)
    return _exit_code(
        outcome, validation.status, doc["export"]["outcome"], result.halted is not None
    )


def _run(
    argv: Sequence[str] | None,
    env: Mapping[str, str],
    clock: Clock,
    http_factory: HttpFactory,
    out: TextIO,
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

    entry = PROVIDERS.get(args.provider)
    if entry is None:
        message = "--provider is not a registered provider (the value is not echoed)"
        return _refuse(out, doc, [{"code": "provider_unknown", "message": message}], state)
    doc["provider_selection"] = {
        "name": entry.name,
        "provider": entry.provider,
        "available": entry.refusal is None,
    }
    if entry.refusal is not None:  # before any setting, key or client
        return _refuse(out, doc, refusal_docs([entry.refusal]), state)

    scenario = comparison_scenarios.SCENARIOS.get(args.scenario)
    if scenario is None:
        message = "--scenario is not a registered scenario (the value is not echoed)"
        return _refuse(out, doc, [{"code": "scenario_unknown", "message": message}], state)
    registry_problems = comparison_scenarios.check_scenario(args.scenario, scenario)
    if registry_problems:  # before any policy read, client or provider contact
        return _refuse(out, doc, refusal_docs(registry_problems), state)
    text = comparison_scenarios.compose_input(scenario)
    doc["scenario"] = _scenario_doc(scenario, text)
    doc["constraints"] = constraints_doc()

    # One read-only copy for the whole run: the policy, the client's credential and the send
    # gate all see the same values.
    environ: Mapping[str, str] = MappingProxyType(dict(env))
    decision = groq_live.evaluate_free_plan_policy(environ, now=clock())
    policy_refusals = list(decision.refusals)
    policy = decision.policy
    for problem in groq_adapter.groq_library_problems():  # the boundary holds for these only
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
                f"{PROJECT_VARIABLE} must be the comparison project's positive integer ID",
            )
        )
    base = loopback_origin(args.dashboard_url or DEFAULT_DASHBOARD_URL)
    if base is None:
        readiness_refusals.append(
            Refusal("dashboard_url_invalid", "--dashboard-url must be a loopback HTTP origin")
        )

    eligible = not policy_refusals and policy is not None
    ready = not readiness_refusals and config is not None
    doc.update(
        {
            "policy_eligible": eligible,
            "execution_ready": ready,
            "execution_allowed": eligible and ready,
            "refusals": refusal_docs([*policy_refusals, *readiness_refusals]),
            "plan": build_plan(policy, scenario, text),
            "environment": {"backend": backend, "sdk_checkout_match": checkout},
            "comparison": _comparison_doc(scenario, project_id),
            "correlation": _correlation_doc(None, project_id, base),
        }
    )
    if not args.execute:
        doc["note"] = "dry run: no provider request was sent; pass --execute to send one"
        return _finish(out, doc, 0, state)
    if policy is None or config is None or not (eligible and ready):
        return _finish(out, doc, 2, state)

    result = run_groq_request(
        policy=policy,
        config=config,
        scenario=scenario,
        input_text=text,
        environ=environ,
        clock=clock,
        http_factory=http_factory,
        session_holder=state.sessions,
    )
    if result.aborted is not None and not isinstance(result.aborted, KeyboardInterrupt | Exception):
        raise result.aborted  # SystemExit and the like are not this runner's to report
    doc["correlation"] = _correlation_doc(result.trace_id, project_id, base)
    if result.outcome is None and result.request_state == "not_sent":
        doc["session"] = result.session
        if result.refusals:
            return _refuse(out, doc, result.refusals, state)
        interrupted = isinstance(result.aborted, KeyboardInterrupt)
        code = "interrupted_before_send" if interrupted else "internal_error"
        name = type(result.aborted).__name__
        return _refuse(out, doc, [{"code": code, "message": name}], state)
    code_ = _report_attempt(doc, result, scenario, policy)
    if result.aborted is not None and result.outcome is not None:
        doc["export"]["interrupted"] = True
    return _finish(out, doc, code_, state)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _fixed_clock(now: datetime) -> Clock:
    def clock() -> datetime:
        return now

    return clock


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    *,
    now: datetime | None = None,
    clock: Clock | None = None,
    http_factory: HttpFactory | None = None,
    out: TextIO | None = None,
) -> int:
    """`now`, `clock` and `http_factory` exist for offline tests only. The command line always
    uses the real UTC clock and the audited client of `groq_adapter.build_groq_http_client`."""
    stdout = sys.stdout if out is None else out
    environ: Mapping[str, str] = os.environ if env is None else env
    if clock is None:
        clock = _utc_now if now is None else _fixed_clock(now)
    state = _RunState()
    try:
        return _run(argv, environ, clock, http_factory or _default_http, stdout, state)
    except (KeyboardInterrupt, Exception) as exc:
        if state.emitted:
            return state.exit_code
        # Exactly one sanitized line and no further call, whatever was interrupted.
        interrupted = isinstance(exc, KeyboardInterrupt)
        doc = _base_doc(state.mode)
        request_state = state.request_state()
        if request_state != "not_sent":
            doc["provider_request_sent"] = True if request_state == "sent" else None
            doc["provider_request_state"] = request_state
            label = "interrupted" if interrupted else "internal_error"
            doc["provider"] = _provider_doc(None, outcome_label=label)
            doc["validation"] = comparison_scenarios.not_evaluated(
                "provider_not_completed"
            ).to_doc()
            return _finish(stdout, doc, 1, state)
        code = "interrupted_before_send" if interrupted else "internal_error"
        return _refuse(stdout, doc, [{"code": code, "message": type(exc).__name__}], state)


if __name__ == "__main__":
    raise SystemExit(main())
