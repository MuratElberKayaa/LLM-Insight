"""Phase 6C-A live demo runner: at most one Gemini Free Tier request, observed by LLM Insight.

    sdk/.venv/Scripts/python -m demo_app.live_gemini             # DRY RUN (default)
    sdk/.venv/Scripts/python -m demo_app.live_gemini --execute   # one request, if allowed

**The default is a dry run.** It evaluates the Free Tier policy (`gemini_live`), audits the
installed client libraries, checks that the LLM Insight backend accepts the project key and
prints the plan. It never creates a Gemini client and never contacts a provider.

`--execute` sends one request only when every gate passes (ADR-35, ADR-36): provider mode
`real`, Gemini mode `free-tier`, the Gemini consent phrase, the operator's Free Tier
attestation (AI Studio Billing Tier `free`, checked at most one day ago), the verified model
allowlist, one request, concurrency one, zero retries, token and time limits, no client
override in the environment, a `GEMINI_API_KEY`, a valid telemetry configuration, a ready
backend and the session reservation. The real client must pass
`gemini_adapter.live_gemini_client_problems` (ADR-34) before telemetry starts and again
immediately before the send: only `https://generativelanguage.googleapis.com/v1beta`, the
Gemini Developer API (never Vertex AI), no redirect, no proxy or CA setting from the
environment, no extra credential or header, one library attempt. Any failed gate is a
refusal with exit code 2 and nothing is sent.

The request is fixed and SYNTHETIC (`INPUT_TEXT`): one `generateContent` call without tools,
search, code execution, files, history, streaming, automatic continuation or automatic
function calling. The output text is never printed, logged or exported. One JSON line is
printed: trace ID, model, outcome, token counts (the provider's own and the normalized
ones), duration, the cost status and where to find the trace. Never the API key, the
project key, the input or the output. **Cost:** a Free Tier call has no billed amount the
API reports; the cost stays `unknown` (never 0), no paid-tier list price is computed or
applied, and the tier is the operator's attestation, not a fact from Google's billing.

Exit codes: 0 dry run printed, or the request completed (status `completed`/`incomplete`);
1 the provider call failed or the answer was blocked; 2 refused (nothing was sent).
**Never re-run automatically after a failure**: see the operator guide in
docs/reports/phase-6c-a.md.
"""

from __future__ import annotations

import argparse
import os
import ssl
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TextIO

import certifi
import httpx
from google import genai
from google.genai import client as genai_client
from google.genai import types as genai_types

import llm_insight_sdk as lli
from demo_app.live_common import DEFAULT_DASHBOARD_URL, check_backend, dashboard_links, emit
from llm_insight_sdk import gemini_live
from llm_insight_sdk.config import ConfigError
from llm_insight_sdk.gemini_adapter import (
    LIVE_API_VERSION,
    LIVE_BASE_URL,
    PROVIDER,
    GeminiTextAdapter,
    ObservedGeminiClient,
    Provenance,
    gemini_library_problems,
    live_gemini_client_problems,
    usage_from_gemini_response,
)
from llm_insight_sdk.live import LiveRefusal, Refusal, input_token_bound
from llm_insight_sdk.providers import AttemptRecord, TextOutcome, TextRequest
from llm_insight_sdk.telemetry import Telemetry, TelemetryStats

# SYNTHETIC content: sent to the provider, never recorded or printed. On the Free Tier,
# Google may use prompts and responses to improve its products (pricing page), so nothing
# but this fixed sentence is ever sent.
INPUT_TEXT = "SYNTHETIC LLM Insight live smoke test. Reply with the single word: OK"
PROMPT_NAME = "live-smoke"
PROMPT_VERSION = "v1"
ROOT_SPAN = "demo.live-gemini"
API = "generateContent (Gemini Developer API)"
ENDPOINT = f"{LIVE_BASE_URL}{LIVE_API_VERSION}/models/{{model}}:generateContent"
TIER_BASIS = (
    "operator attestation of the AI Studio Billing Tier; not verified with Google's billing backend"
)
COST_BASIS = (
    "Free Tier: the API reports no billed amount; no Gemini price is applied (a paid-tier "
    "list price is never shown as a Free Tier charge); unknown is never 0"
)

ClientFactory = Callable[[gemini_live.FreeTierPolicy], genai.Client]


def build_plan(
    decision: gemini_live.FreeTierDecision,
    model: str | None,
    *,
    input_text: str = INPUT_TEXT,
    max_output_tokens: int | None = None,
) -> dict[str, Any] | None:
    """The dry-run plan. `input_text` and `max_output_tokens` default to the smoke request."""
    policy = decision.policy
    if policy is None or model is None:
        return None
    return {
        "provider": PROVIDER,
        "model": model,
        "api": API,
        "endpoint": ENDPOINT.format(model=model),
        "tools": [],
        "streaming": False,
        "automatic_continuation": False,
        "automatic_function_calling": False,
        "max_output_tokens": (
            policy.max_output_tokens if max_output_tokens is None else max_output_tokens
        ),
        "max_output_tokens_note": "thinking tokens count against this ceiling",
        "input_token_bound": input_token_bound(input_text),
        "max_attempts": policy.max_attempts,
        "max_requests": policy.max_requests,
        "max_concurrency": policy.max_concurrency,
        "timeout_seconds": policy.timeout_seconds,
        "billing_tier": gemini_live.BILLING_TIER_FREE,
        "billing_tier_checked_on": policy.tier_checked_on.isoformat(),
        "billing_tier_basis": TIER_BASIS,
        "paid_fallback": False,
        "free_tier_evidence_verified_at": gemini_live.FREE_TIER_VERIFIED_AT.isoformat(),
    }


def refusal_docs(items: Sequence[Refusal]) -> list[dict[str, str]]:
    return [{"code": r.code, "message": r.message} for r in items]


def tokens_doc(outcome: TextOutcome) -> dict[str, int | None] | None:
    u = outcome.usage
    if u is None:
        return None  # unknown, never 0
    return {
        "input": u.input_tokens,
        "output": u.output_tokens,
        "cache_read_input": u.cache_read_input_tokens,
        "reasoning_output": u.reasoning_output_tokens,
    }


def cost_doc(outcome: TextOutcome | None, policy: gemini_live.FreeTierPolicy) -> dict[str, Any]:
    reported = outcome is not None and outcome.usage is not None
    return {
        "status": "unknown",
        "usage_source": "provider_reported" if reported else "unknown",
        "billing_tier": gemini_live.BILLING_TIER_FREE,
        "billing_tier_checked_on": policy.tier_checked_on.isoformat(),
        "billing_tier_basis": TIER_BASIS,
        "billed_amount_usd": None,
        "paid_tier_list_price_estimate_usd": None,
        "ledger_cost_status": "unknown (price_missing)",
        "basis": COST_BASIS,
    }


def _default_client(env: Mapping[str, str]) -> ClientFactory:
    def factory(policy: gemini_live.FreeTierPolicy) -> genai.Client:
        # ADR-34: no redirect is followed (a redirect would carry x-goog-api-key, which httpx
        # does not strip), no proxy or CA setting is taken from the environment, the CA
        # bundle is the pinned certifi one, and the library sends exactly one attempt.
        http = httpx.Client(
            verify=ssl.create_default_context(cafile=certifi.where()),
            trust_env=False,
            follow_redirects=False,
            timeout=policy.timeout_seconds,
        )
        return genai.Client(
            vertexai=False,  # never read from GOOGLE_GENAI_USE_VERTEXAI
            api_key=env[gemini_live.API_KEY_VAR],
            debug_config=genai_client.DebugConfig(
                client_mode=None, replays_directory=None, replay_id=None
            ),
            http_options=genai_types.HttpOptions(
                base_url=LIVE_BASE_URL,  # never read from GOOGLE_GEMINI_BASE_URL
                api_version=LIVE_API_VERSION,
                timeout=int(policy.timeout_seconds * 1000),  # milliseconds
                httpx_client=http,
            ),
        )

    return factory


@dataclass
class SingleRequestResult:
    """What `run_single_request` did. `refusals` non-empty: nothing was sent. `send_started`
    turns true immediately before the adapter is called, so an interruption after it may
    have reached the provider. `aborted` is an exception that ended the attempt; the session
    is settled and telemetry closed before it is handed back."""

    refusals: list[dict[str, str]] = field(default_factory=list)
    outcome: TextOutcome | None = None
    trace_id: str | None = None
    settlement: gemini_live.FreeTierSettlement | None = None
    flushed: bool | None = None
    shutdown_ok: bool | None = None
    stats: TelemetryStats | None = None
    session: dict[str, object] | None = None
    send_started: bool = False
    aborted: BaseException | None = None


def run_single_request(
    *,
    policy: gemini_live.FreeTierPolicy,
    config: lli.LLMInsightConfig,
    model: str,
    input_text: str,
    max_output_tokens: int,
    prompt_name: str,
    prompt_version: str,
    root_span: str,
    environ: Mapping[str, str],
    client_factory: ClientFactory | None = None,
    provenance: Provenance = "real",
) -> SingleRequestResult:
    """The one security path of both Gemini runners (ADR-36, ADR-39): session reservation,
    client construction, the ADR-34 endpoint checks, one adapter call under a root span,
    settlement, flush and shutdown. At most one request; nothing here resends."""
    result = SingleRequestResult()
    session = gemini_live.FreeTierSession(policy)
    try:
        reservation = session.reserve(
            provider=PROVIDER,
            model=model,
            input_text=input_text,
            max_output_tokens=max_output_tokens,
        )
    except LiveRefusal as refusal:
        result.refusals = refusal_docs(refusal.refusals)
        result.session = session.snapshot()
        return result

    factory = client_factory or _default_client(environ)
    problems: list[str] = []
    client: genai.Client | None = None
    try:
        client = factory(policy)
        problems = live_gemini_client_problems(client) if provenance == "real" else []
    except Exception as exc:  # e.g. an unreadable CA file: nothing was sent
        result.refusals = [{"code": "client_construction_failed", "message": type(exc).__name__}]
    except BaseException as exc:  # interrupted before anything could be sent
        result.aborted = exc
    if problems:  # ADR-34: the key may only go to the pinned endpoint
        result.refusals = [{"code": "client_not_allowed", "message": p} for p in problems]
    if client is None or result.refusals or result.aborted is not None:
        result.settlement = session.settle(reservation, [])
        result.session = session.snapshot()
        return result

    telemetry: Telemetry | None = None
    refused: str | None = None
    try:
        telemetry = lli.init(config)
        try:
            observed = ObservedGeminiClient(
                telemetry,
                client,
                provenance=provenance,
                require_live_endpoint=provenance == "real",
            )
        except ConfigError:
            refused = "the client changed after its check"
        else:
            adapter = GeminiTextAdapter(observed)
            request = TextRequest(
                model=model,
                input_text=input_text,
                max_output_tokens=max_output_tokens,
                prompt_name=prompt_name,
                prompt_version=prompt_version,
            )
            with telemetry.tracer.start_as_current_span(root_span) as root:
                result.trace_id = format(root.get_span_context().trace_id, "032x")
                result.send_started = True
                try:
                    result.outcome = adapter.generate_text(request)
                except ConfigError:  # the re-check before the send refused: nothing sent
                    result.send_started = False
                    refused = "the client failed its check immediately before the send"
    except BaseException as exc:  # handed back after the settlement and the flush below
        result.aborted = exc

    # An interrupted call may already have reached the provider: count the attempt.
    attempts: tuple[AttemptRecord, ...] = ()
    if result.outcome is not None:
        attempts = result.outcome.attempts
    elif result.send_started:
        attempts = (AttemptRecord(1),)
    result.settlement = session.settle(reservation, attempts)
    result.session = session.snapshot()
    if telemetry is not None:
        try:
            result.flushed = telemetry.force_flush()
            result.shutdown_ok = telemetry.shutdown()
        except BaseException as exc:
            result.aborted = result.aborted or exc
        result.stats = telemetry.stats()
    if refused is not None:
        result.refusals = [{"code": "client_not_allowed", "message": refused}]
    return result


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    *,
    now: datetime | None = None,
    client_factory: ClientFactory | None = None,
    provenance: Provenance = "real",
    out: TextIO = sys.stdout,
) -> int:
    """`client_factory` and `provenance="synthetic"` exist for offline tests only; the
    command line always uses the real client and `real` provenance."""
    parser = argparse.ArgumentParser(prog="python -m demo_app.live_gemini")
    parser.add_argument("--execute", action="store_true", help="send one request if allowed")
    parser.add_argument("--model", help="one of LLMI_LIVE_MODELS (default: the first)")
    parser.add_argument("--dashboard-url", default=DEFAULT_DASHBOARD_URL)
    args = parser.parse_args(argv)
    environ: Mapping[str, str] = os.environ if env is None else env
    decision = gemini_live.evaluate_free_tier_policy(environ, now=now or datetime.now(UTC))
    refusals = list(decision.refusals)
    policy = decision.policy
    model = args.model or (policy.models[0] if policy is not None else None)
    if policy is not None and model is not None and model not in policy.models:
        refusals.append(Refusal("model_not_allowed", "--model is not in LLMI_LIVE_MODELS"))
    for problem in gemini_library_problems():  # the endpoint checks hold for these only
        refusals.append(Refusal("client_library_not_audited", problem))

    try:
        config: lli.LLMInsightConfig | None = lli.LLMInsightConfig.from_env(environ)
        backend = check_backend(config)  # type: ignore[arg-type]
    except ConfigError:
        config, backend = None, {"ready": False, "reason": "telemetry_config_invalid"}

    doc: dict[str, Any] = {
        "mode": "execute" if args.execute else "dry_run",
        "provider_request_sent": False,
        "execution_allowed": not refusals,
        "refusals": refusal_docs(refusals),
        "plan": build_plan(decision, model),
        "backend": backend,
    }
    if not args.execute:
        doc["note"] = "dry run: no provider request was sent; pass --execute to send one"
        emit(out, doc)
        return 0
    if refusals or policy is None or model is None or config is None or not backend["ready"]:
        if not backend["ready"]:
            doc["refusals"].append(
                {"code": "backend_not_ready", "message": "the LLM Insight backend is not ready"}
            )
        doc["execution_allowed"] = False
        emit(out, doc)
        return 2

    result = run_single_request(
        policy=policy,
        config=config,
        model=model,
        input_text=INPUT_TEXT,
        max_output_tokens=policy.max_output_tokens,
        prompt_name=PROMPT_NAME,
        prompt_version=PROMPT_VERSION,
        root_span=ROOT_SPAN,
        environ=environ,
        client_factory=client_factory,
        provenance=provenance,
    )
    if result.aborted is not None:  # e.g. Ctrl-C: settled and flushed, no JSON line
        raise result.aborted
    outcome, settlement, stats, trace_id = (
        result.outcome,
        result.settlement,
        result.stats,
        result.trace_id,
    )
    if (
        result.refusals
        or outcome is None
        or settlement is None
        or stats is None
        or trace_id is None
    ):
        doc["execution_allowed"] = False
        doc["refusals"] = result.refusals or [{"code": "client_not_allowed", "message": "refused"}]
        emit(out, doc)
        return 2
    reading = usage_from_gemini_response(outcome.provider_response)
    doc.update(
        {
            "provider_request_sent": settlement.attempts > 0,
            "trace_id": trace_id,
            "status": outcome.status,
            "incomplete_reason": outcome.incomplete_reason,
            "provider_reason": outcome.provider_reason,
            "error_category": outcome.error_category,
            "provenance": provenance,
            "provider": PROVIDER,
            "request_model": outcome.request_model,
            "response_model": outcome.response_model,
            "attempts": len(outcome.attempts),
            "tokens": tokens_doc(outcome),
            "provider_usage": reading.provider_counts if outcome.status != "error" else None,
            "usage_check": reading.check if outcome.status != "error" else "missing",
            "duration_ms": outcome.duration_ms,
            "cost": cost_doc(outcome, policy),
            "session": result.session,
            "telemetry": {
                "flushed": result.flushed,
                "spans_exported": stats.spans_exported,
                "spans_export_failed": stats.spans_export_failed,
            },
            **dashboard_links(args.dashboard_url, trace_id, environ),
        }
    )
    emit(out, doc)
    return 0 if outcome.status in ("completed", "incomplete") else 1


if __name__ == "__main__":
    raise SystemExit(main())
