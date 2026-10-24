"""Phase 2A demo: deterministic mock LLM calls, instrumented by `llm_insight_sdk`.

Run from the repository root with the SDK venv (the backend must be running):

    sdk/.venv/Scripts/python -m demo_app

Configuration comes from the environment (sdk/README.md): `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`,
`OTEL_EXPORTER_OTLP_TRACES_HEADERS=X-Project-Key=<key>` and `OTEL_SERVICE_NAME`.
Only `LLMI_PROVIDER_MODE=mock` exists in Phase 2A: no real provider is ever called.

Scenarios (every inference span is SYNTHETIC):
  A  one successful inference under a root span
  B  several child inference spans (sync and async) under one root span
  C  a failed inference (provider 500) that the application handles
  D  a rate-limited attempt retried as a separate physical attempt
  E  telemetry export failure (unreachable backend, then a rejected key): the application
     still answers every request; the spans are lost (best effort, no persistent queue)

Prints one JSON summary line: trace IDs, application outcomes and SDK counters. Never the
project key, prompts or completions.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import secrets
import socket
import string
import sys
import time
from typing import Any

import llm_insight_sdk as lli
from llm_insight_sdk.mock import MockProviderError, ObservedMockClient, Scenario

MODEL = "mock-chat-small"
PROMPT_VERSION = "v1"
# Synthetic content: passed to the mock client, never exported.
MESSAGES = [
    {"role": "system", "content": "SYNTHETIC: summarize the text in one sentence."},
    {"role": "user", "content": "SYNTHETIC: LLM Insight demo input."},
]


def _trace_id(span: Any) -> str:
    return format(span.get_span_context().trace_id, "032x")


def _handle(client: ObservedMockClient, scenario: Scenario, **kwargs: Any) -> str:
    """What a request handler would do: answer, or fall back on a provider error."""
    try:
        client.chat(
            model=MODEL,
            messages=MESSAGES,
            scenario=scenario,
            prompt_name="summarize",
            prompt_version=PROMPT_VERSION,
            **kwargs,
        )
        return "answered"
    except MockProviderError:
        return "fallback"  # the provider failed; the application still responds


def run_main_scenarios(telemetry: lli.Telemetry) -> dict[str, Any]:
    tracer = telemetry.tracer
    client = ObservedMockClient(telemetry, max_attempts=3)
    out: dict[str, Any] = {}

    with tracer.start_as_current_span("demo.success") as root:
        out["A_success"] = {"trace_id": _trace_id(root), "app": _handle(client, Scenario.SUCCESS)}

    async def fan_out() -> list[str]:
        calls = [
            client.achat(model=MODEL, messages=MESSAGES, prompt_version=PROMPT_VERSION),
            client.achat(
                model=MODEL, messages=MESSAGES, scenario=Scenario.DELAYED, prompt_version="v1"
            ),
        ]
        return [r.response_model for r in await asyncio.gather(*calls)]

    with tracer.start_as_current_span("demo.multi-call") as root:
        sync = [_handle(client, Scenario.SUCCESS) for _ in range(2)]
        asyncio.run(fan_out())
        out["B_multi"] = {"trace_id": _trace_id(root), "app": sync + ["answered"] * 2}

    with tracer.start_as_current_span("demo.failure") as root:
        out["C_failure"] = {
            "trace_id": _trace_id(root),
            "app": _handle(client, Scenario.SERVER_ERROR, max_attempts=1),
        }

    with tracer.start_as_current_span("demo.retry") as root:
        out["D_retry"] = {
            "trace_id": _trace_id(root),
            "app": _handle(client, Scenario.RETRY_THEN_SUCCESS),
        }
    return out


def _unused_loopback_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _unknown_key() -> str:
    """A well-formed key that the backend has never issued (synthetic, random)."""
    alphabet = string.ascii_letters + string.digits
    prefix = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"lli_{prefix}_{secrets.token_urlsafe(32)}"


def run_export_failure(base: lli.LLMInsightConfig) -> dict[str, Any]:
    results: dict[str, Any] = {}
    quick = dataclasses.replace(base, export_timeout_seconds=1.0)
    variants = {
        "unreachable_backend": dataclasses.replace(
            quick, endpoint=f"http://127.0.0.1:{_unused_loopback_port()}/v1/traces"
        ),
        "rejected_key": dataclasses.replace(quick, project_key=_unknown_key()),
    }
    for name, config in variants.items():
        telemetry = lli.init(config)
        client = ObservedMockClient(telemetry)
        started = time.monotonic()
        with telemetry.tracer.start_as_current_span("demo.export-failure") as root:
            app = _handle(client, Scenario.SUCCESS)
        app_ms = round((time.monotonic() - started) * 1000, 1)
        delivered = telemetry.force_flush(3000)
        telemetry.shutdown(3000)
        results[name] = {
            "trace_id": _trace_id(root),
            "app": app,
            "app_latency_ms": app_ms,
            "delivered": delivered,
            "stats": dataclasses.asdict(telemetry.stats()),
        }
    return results


def main() -> int:
    mode = os.environ.get("LLMI_PROVIDER_MODE", "mock")
    if mode != "mock":
        print("error: only LLMI_PROVIDER_MODE=mock exists in Phase 2A", file=sys.stderr)
        return 2
    try:
        config = lli.LLMInsightConfig.from_env()
    except lli.ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)  # names the setting, never its value
        return 2

    telemetry = lli.init(config)
    scenarios = run_main_scenarios(telemetry)
    delivered = telemetry.force_flush()
    telemetry.shutdown()
    summary = {
        "provenance": "synthetic",
        "scenarios": scenarios,
        "export": {"delivered": delivered, "stats": dataclasses.asdict(telemetry.stats())},
        "E_export_failure": run_export_failure(config),
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
