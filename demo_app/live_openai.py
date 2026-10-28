"""Phase 6A live demo runner: at most one OpenAI Responses request, observed by LLM Insight.

    sdk/.venv/Scripts/python -m demo_app.live_openai             # DRY RUN (default)
    sdk/.venv/Scripts/python -m demo_app.live_openai --execute   # one request, if allowed

**The default is a dry run.** It evaluates the live policy (`llm_insight_sdk.live`), checks
that the LLM Insight backend accepts the project key, and prints the plan with its
worst-case cost. It never creates an OpenAI client and never contacts a provider.

`--execute` sends one request only when every gate passes (ADR-29, ADR-30): the explicit
live configuration (provider mode `real`, the consent phrase, allowlists, token, retry,
time and money limits, a fresh price catalogue, an `OPENAI_API_KEY` in the environment),
a valid SDK telemetry configuration, a ready backend, and a session reservation. Any
failed gate is a refusal with exit code 2 and nothing is sent. The real client must pass
`openai_adapter.live_client_problems` (ADR-32): only `https://api.openai.com/v1`, no
redirect, no proxy or CA setting from the environment, no extra credential or header.

The request is fixed and SYNTHETIC (`INPUT_TEXT`), sent with `store=False` and
`service_tier="default"`. The output text is never printed, logged or exported. One JSON
line is printed: the trace ID, status, models, token counts, duration, the estimated cost
(an estimate, not a billed amount) and where to find the trace. Never the API key, the
project key, the input or the output.

Exit codes: 0 dry run printed, or the request completed (status `completed`/`incomplete`);
1 the provider call failed; 2 refused (nothing was sent). An `incomplete` answer is never
shown as `completed`: its status stays `incomplete` with the provider's `incomplete_reason`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, TextIO

import openai

import llm_insight_sdk as lli
from llm_insight_sdk import live
from llm_insight_sdk.config import PROJECT_KEY_HEADER, ConfigError
from llm_insight_sdk.openai_adapter import (
    ObservedOpenAIClient,
    OpenAIResponsesTextAdapter,
    Provenance,
    live_client_problems,
    live_library_problems,
)
from llm_insight_sdk.providers import AttemptRecord, TextOutcome, TextRequest

PROVIDER = "openai"
OPENAI_BASE_URL = "https://api.openai.com/v1"
# SYNTHETIC content: sent to the provider, never recorded or printed.
INPUT_TEXT = "SYNTHETIC LLM Insight live smoke test. Reply with the single word: OK"
PROMPT_NAME = "live-smoke"
PROMPT_VERSION = "v1"
ROOT_SPAN = "demo.live-openai"
DEFAULT_DASHBOARD_URL = "http://127.0.0.1:3001"
READINESS_TIMEOUT_SECONDS = 3.0
COST_BASIS = (
    "estimate from provider-reported usage and the verified price catalogue "
    f"{live.PRICE_CATALOGUE_VERSION}; not a billed amount"
)

ClientFactory = Callable[[live.LivePolicy], openai.OpenAI]


def check_backend(config: lli.LLMInsightConfig) -> dict[str, Any]:
    """POST an empty OTLP request: the backend answers 200 and writes nothing when it is up
    and accepts the project key (TELEMETRY_CONTRACT.md §1.1 step 12)."""
    request = urllib.request.Request(  # noqa: S310 - the endpoint is validated by LLMInsightConfig
        config.endpoint,
        data=b"",
        method="POST",
        headers={
            "Content-Type": "application/x-protobuf",
            PROJECT_KEY_HEADER: config.project_key,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=READINESS_TIMEOUT_SECONDS) as response:  # noqa: S310
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        status = exc.code
    except (urllib.error.URLError, OSError):
        return {"ready": False, "reason": "backend_unreachable"}
    if status == 200:
        return {"ready": True, "reason": "ready"}
    if status == 401:
        return {"ready": False, "reason": "project_key_rejected"}
    return {"ready": False, "reason": f"backend_http_{status}"}


def _plan(decision: live.PolicyDecision, model: str | None) -> dict[str, Any] | None:
    policy = decision.policy
    if policy is None or model is None:
        return None
    price = live.VERIFIED_PRICES.get((PROVIDER, model))
    bound_in = live.input_token_bound(INPUT_TEXT)
    worst = live.request_bound(policy, price, bound_in) if price is not None else None
    return {
        "provider": PROVIDER,
        "model": model,
        "api": "responses",
        "store": False,
        "service_tier": live.PRICED_SERVICE_TIER,
        "max_output_tokens": policy.max_output_tokens,
        "input_token_bound": bound_in,
        "max_attempts": policy.max_attempts,
        "timeout_seconds": policy.timeout_seconds,
        "worst_case_request_usd": None if worst is None else str(worst),
        "max_request_cost_usd": str(policy.max_request_cost_usd),
        "session_budget_usd": str(policy.session_budget_usd),
        "max_requests": policy.max_requests,
        "price_catalogue": live.PRICE_CATALOGUE_VERSION,
        "price_verified_at": live.PRICE_VERIFIED_AT.isoformat(),
    }


def _refusals(items: Sequence[live.Refusal]) -> list[dict[str, str]]:
    return [{"code": r.code, "message": r.message} for r in items]


def _tokens(outcome: TextOutcome) -> dict[str, int | None] | None:
    u = outcome.usage
    if u is None:
        return None  # unknown, never 0
    return {
        "input": u.input_tokens,
        "output": u.output_tokens,
        "cache_read_input": u.cache_read_input_tokens,
        "cache_write_input": u.cache_write_input_tokens,
        "reasoning_output": u.reasoning_output_tokens,
    }


def _dashboard(base: str, trace_id: str, env: Mapping[str, str]) -> dict[str, str]:
    project = (env.get("LLMI_DASHBOARD_PROJECT_ID") or "").strip()
    query = f"?project={project}" if project.isdigit() else ""
    return {
        "dashboard_url": f"{base.rstrip('/')}/traces/{trace_id}{query}",
        "api_path": f"/api/v1/traces/{trace_id}",
    }


def _default_client(env: Mapping[str, str]) -> ClientFactory:
    def factory(policy: live.LivePolicy) -> openai.OpenAI:
        return openai.OpenAI(
            api_key=env["OPENAI_API_KEY"],
            base_url=OPENAI_BASE_URL,  # never taken from OPENAI_BASE_URL
            timeout=policy.timeout_seconds,
            max_retries=0,
            # ADR-32: no redirect is followed and no proxy or CA setting is taken from the
            # environment or the Windows system proxy; the key goes only to OPENAI_BASE_URL.
            http_client=openai.DefaultHttpxClient(trust_env=False, follow_redirects=False),
        )

    return factory


def _emit(out: TextIO, doc: dict[str, Any]) -> None:
    out.write(json.dumps(doc, sort_keys=True) + "\n")


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
    parser = argparse.ArgumentParser(prog="python -m demo_app.live_openai")
    parser.add_argument("--execute", action="store_true", help="send one request if allowed")
    parser.add_argument("--model", help="one of LLMI_LIVE_MODELS (default: the first)")
    parser.add_argument("--dashboard-url", default=DEFAULT_DASHBOARD_URL)
    args = parser.parse_args(argv)
    environ: Mapping[str, str] = os.environ if env is None else env
    decision = live.evaluate_policy(environ, now=now or datetime.now(UTC))
    refusals = list(decision.refusals)
    policy = decision.policy
    model = args.model or (policy.models[0] if policy is not None else None)
    if policy is not None and model is not None and model not in policy.models:
        refusals.append(live.Refusal("model_not_allowed", "--model is not in LLMI_LIVE_MODELS"))
    for problem in live_library_problems():  # the endpoint checks hold for these only
        refusals.append(live.Refusal("client_library_not_audited", problem))

    try:
        config: lli.LLMInsightConfig | None = lli.LLMInsightConfig.from_env(environ)
        backend = check_backend(config)  # type: ignore[arg-type]
    except ConfigError:
        config, backend = None, {"ready": False, "reason": "telemetry_config_invalid"}

    doc: dict[str, Any] = {
        "mode": "execute" if args.execute else "dry_run",
        "provider_request_sent": False,
        "execution_allowed": not refusals,
        "refusals": _refusals(refusals),
        "plan": _plan(decision, model),
        "backend": backend,
    }
    if not args.execute:
        doc["note"] = "dry run: no provider request was sent; pass --execute to send one"
        _emit(out, doc)
        return 0
    if refusals or policy is None or model is None or config is None or not backend["ready"]:
        if not backend["ready"]:
            doc["refusals"].append(
                {"code": "backend_not_ready", "message": "the LLM Insight backend is not ready"}
            )
        doc["execution_allowed"] = False
        _emit(out, doc)
        return 2

    session = live.LiveSession(policy)
    try:
        reservation = session.reserve(
            provider=PROVIDER,
            model=model,
            input_text=INPUT_TEXT,
            max_output_tokens=policy.max_output_tokens,
        )
    except live.LiveRefusal as refusal:
        doc["execution_allowed"] = False
        doc["refusals"] = _refusals(refusal.refusals)
        _emit(out, doc)
        return 2

    factory = client_factory or _default_client(environ)
    try:
        client = factory(policy)
        problems = live_client_problems(client) if provenance == "real" else []
    except BaseException:
        session.settle(reservation, [])  # nothing was sent
        raise
    if problems:  # ADR-32: the credential may only go to the pinned endpoint
        session.settle(reservation, [])
        doc["execution_allowed"] = False
        doc["refusals"] = [{"code": "client_not_allowed", "message": p} for p in problems]
        _emit(out, doc)
        return 2

    outcome: TextOutcome | None = None
    telemetry = lli.init(config)
    try:
        observed = ObservedOpenAIClient(
            telemetry,
            client,
            provenance=provenance,
            backoff_seconds=1.0,
            require_live_endpoint=provenance == "real",
        )
        adapter = OpenAIResponsesTextAdapter(observed, max_attempts=policy.max_attempts)
        request = TextRequest(
            model=model,
            input_text=INPUT_TEXT,
            max_output_tokens=policy.max_output_tokens,
            prompt_name=PROMPT_NAME,
            prompt_version=PROMPT_VERSION,
        )
        with telemetry.tracer.start_as_current_span(ROOT_SPAN) as root:
            outcome = adapter.generate_text(request)
        trace_id = format(root.get_span_context().trace_id, "032x")
    finally:
        # An interrupted call may already have reached the provider: book every allowed
        # attempt at its worst-case bound.
        attempts = (
            outcome.attempts
            if outcome is not None
            else tuple(AttemptRecord(i) for i in range(1, policy.max_attempts + 1))
        )
        settlement = session.settle(reservation, attempts)
        flushed = telemetry.force_flush()
        telemetry.shutdown()

    if outcome is None:  # pragma: no cover - the try block either sets it or raises
        raise RuntimeError("no outcome")
    stats = telemetry.stats()
    doc.update(
        {
            "provider_request_sent": settlement.attempts > 0,
            "trace_id": trace_id,
            "status": outcome.status,
            "incomplete_reason": outcome.incomplete_reason,
            "error_category": outcome.error_category,
            "provenance": provenance,
            "request_model": outcome.request_model,
            "response_model": outcome.response_model,
            "service_tier": outcome.service_tier,
            "attempts": len(outcome.attempts),
            "tokens": _tokens(outcome),
            "duration_ms": outcome.duration_ms,
            "estimated_cost_usd": (
                None
                if settlement.estimated_cost_usd is None
                else str(settlement.estimated_cost_usd)
            ),
            "cost_basis": COST_BASIS,
            "booked_against_budget_usd": str(settlement.booked_usd),
            "session": session.snapshot(),
            "telemetry": {
                "flushed": flushed,
                "spans_exported": stats.spans_exported,
                "spans_export_failed": stats.spans_export_failed,
            },
            **_dashboard(args.dashboard_url, trace_id, environ),
        }
    )
    _emit(out, doc)
    return 0 if outcome.status in ("completed", "incomplete") else 1


if __name__ == "__main__":
    raise SystemExit(main())
