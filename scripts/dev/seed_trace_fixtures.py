"""Seed SYNTHETIC edge-case traces for the trace explorer (Phase 3B) through the real
`POST /v1/traces` (OTLP/HTTP protobuf), so they pass ingest validation like any client data.

Run with the SDK venv (it has `opentelemetry-proto` and `requests`), backend running:

    LLMI_SEED_PROJECT_KEY=<ingest key> sdk/.venv/Scripts/python scripts/dev/seed_trace_fixtures.py [--seed S] [--set edge|isolation]

- The key is read from the environment only (never argv, never printed). The endpoint must be
  loopback (`LLMI_SEED_ENDPOINT`, default http://127.0.0.1:8000/v1/traces).
- No provider is called. Every inference span claims `synthetic`, except the spans that
  deliberately carry no claim (provenance `unknown`, and the `mixed` trace). Nothing claims `real`,
  except one span of `--set phase6c` (an asserted-real SYNTHETIC fixture, see below).
- IDs derive from `--seed` (random by default); timestamps are anchored a few minutes before
  now with deliberate sub-microsecond digits (T26).
- Prints one JSON line: `{service.name: trace_id}`. No key, no content.

Edge cases (`--set edge`, service names `phase3b.*`): nested spans with a known cost and a
>2^53 attribute, equal start times, an orphan, a parent cycle, mixed and unknown provenance,
an application-span error, hostile labels (XSS probes), and a large trace (700+ spans).
`--set isolation`: one trace, to be sent with a key of a second project (T28 via the UI).
`--set phase3c` (Phase 3C, service names `phase3c.*`): a retried call (attempt 1 fails with
`429`, attempt 2 succeeds, one `llminsight.call_id`) and a trace that calls several providers
and models (priced, unpriced, and a failed attempt without a response model), so the E2E
suite needs no demo-app traffic (R41).
`--set phase6c` (Phase 6C-C, service names `phase6c.*`): one trace per provider/provenance case
for the dashboard filters: `openai` (ok and failed), `gcp.gemini` synthetic, `gcp.gemini` with an
asserted `real` claim and a response id, a `real` claim without a response id (stored as
`unknown` by the server's downgrade rule), and a span with no provider, model or claim.
`--set phase6c-new`: one more SYNTHETIC trace, sent during a browser test.
`--set phase8a6` (Phase 8A-6, service names `cmp8a6.*`): one SYNTHETIC multi-provider
comparison set: OpenAI (priced), Gemini, the three Groq comparison scenarios under a
`demo.live-compare` root, a SYNTHETIC OpenRouter compatibility row, a root-only
`demo.live-compare` trace (R131) and one adversarial trace (see `phase8a6_set`).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import time
from urllib.parse import urlsplit

import requests
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import (
    AnyValue,
    ArrayValue,
    InstrumentationScope,
    KeyValue,
)
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import (
    ResourceSpans,
    ScopeSpans,
    Span,
    Status,
)

KIND = {
    "INTERNAL": Span.SPAN_KIND_INTERNAL,
    "SERVER": Span.SPAN_KIND_SERVER,
    "CLIENT": Span.SPAN_KIND_CLIENT,
}
XSS = "window.__llmiXss=1"


def any_value(v: object) -> AnyValue:
    if isinstance(v, bool):
        return AnyValue(bool_value=v)
    if isinstance(v, int):
        return AnyValue(int_value=v)
    if isinstance(v, float):
        return AnyValue(double_value=v)
    if isinstance(v, list):
        return AnyValue(array_value=ArrayValue(values=[any_value(x) for x in v]))
    return AnyValue(string_value=str(v))


def kv(attrs: dict[str, object]) -> list[KeyValue]:
    return [KeyValue(key=k, value=any_value(v)) for k, v in attrs.items()]


class Builder:
    """Deterministic ids from (seed, trace, span) names; times relative to an anchor."""

    def __init__(self, seed: str, anchor_ns: int) -> None:
        self.seed = seed
        self.anchor = anchor_ns
        self.traces: list[tuple[TraceBuilder, Resource, InstrumentationScope]] = []
        self.trace_ids: dict[str, str] = {}

    def _id(self, *parts: str, n: int) -> bytes:
        return hashlib.sha256("|".join((self.seed, *parts)).encode()).digest()[:n]

    def trace(
        self,
        service: str,
        resource_extra: dict[str, object] | None = None,
        scope: str = "phase3b.seed",
        scope_version: str = "1.0",
    ) -> TraceBuilder:
        tb = TraceBuilder(self, service)
        resource = Resource(
            attributes=kv({"service.name": service, **(resource_extra or {})})
        )
        self.traces.append(
            (tb, resource, InstrumentationScope(name=scope, version=scope_version))
        )
        self.trace_ids[service] = tb.trace_id.hex()
        return tb

    def request(self) -> ExportTraceServiceRequest:
        # Built at the end: protobuf copies repeated fields at construction time.
        return ExportTraceServiceRequest(
            resource_spans=[
                ResourceSpans(
                    resource=r, scope_spans=[ScopeSpans(scope=sc, spans=tb.spans)]
                )
                for tb, r, sc in self.traces
            ]
        )


class TraceBuilder:
    def __init__(self, root: Builder, service: str) -> None:
        self.root = root
        self.service = service
        self.trace_id = root._id("trace", service, n=16)
        self.spans: list[Span] = []

    def span_id(self, name: str) -> bytes:
        return self.root._id("span", self.service, name, n=8)

    def add(
        self,
        key: str,
        name: str,
        kind: str,
        start: int,
        end: int,
        parent: str | bytes | None = None,
        attrs: dict[str, object] | None = None,
        error: str | None = None,
    ) -> None:
        parent_id = (
            b""
            if parent is None
            else (parent if isinstance(parent, bytes) else self.span_id(parent))
        )
        status = (
            Status(code=Status.STATUS_CODE_ERROR)
            if error
            else Status(code=Status.STATUS_CODE_UNSET)
        )
        a = dict(attrs or {})
        if error:
            a["error.type"] = error
        self.spans.append(
            Span(
                trace_id=self.trace_id,
                span_id=self.span_id(key),
                parent_span_id=parent_id,
                name=name,
                kind=KIND[kind],
                start_time_unix_nano=self.root.anchor + start,
                end_time_unix_nano=self.root.anchor + end,
                attributes=kv(a),
                status=status,
            )
        )


def inference(
    model: str,
    provenance: str | None = "synthetic",
    call_id: str | None = None,
    attempt: int = 1,
    usage: tuple[int, int] | None = (120, 48),
    **extra: object,
) -> dict[str, object]:
    a: dict[str, object] = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "llminsight.mock",
        "gen_ai.request.model": model,
        "llminsight.attempt": attempt,
    }
    if call_id:
        a["llminsight.call_id"] = call_id
    if provenance:
        a["llminsight.provenance"] = provenance
    if usage:
        a["gen_ai.usage.input_tokens"], a["gen_ai.usage.output_tokens"] = usage
        a["llminsight.usage_source"] = "provider_reported"
    a.update(extra)
    return a


def uuid_from(b: Builder, name: str) -> str:
    h = b._id("call", name, n=16).hex()
    return f"{h[:8]}-{h[8:12]}-4{h[13:16]}-a{h[17:20]}-{h[20:32]}"


MS = 1_000_000


def edge_set(b: Builder) -> None:
    # Nested: SERVER → INTERNAL → INTERNAL → CLIENT, plus a priced sibling with cached and
    # reasoning subsets and a >2^53 seed attribute. Odd ns digits test exact offsets (T26).
    t = b.trace("phase3b.nested")
    t.add(
        "root",
        "POST /v1/agent",
        "SERVER",
        123_456_789,
        123_456_789 + 90 * MS + 1,
        attrs={
            "http.request.method": "POST",
            "http.route": "/v1/agent",
            "http.response.status_code": 200,
        },
    )
    t.add(
        "plan",
        "agent.plan",
        "INTERNAL",
        123_456_789 + 1_000_001,
        123_456_789 + 80 * MS,
        parent="root",
    )
    t.add(
        "tool",
        "agent.tool.lookup",
        "INTERNAL",
        123_456_789 + 2_000_003,
        123_456_789 + 30 * MS,
        parent="plan",
    )
    t.add(
        "llm1",
        "chat mock-chat-small",
        "CLIENT",
        123_456_789 + 3_000_007,
        123_456_789 + 25 * MS + 17,
        parent="tool",
        attrs=inference("mock-chat-small", call_id=uuid_from(b, "n1")),
    )
    t.add(
        "llm2",
        "chat gpt-5.4-mini",
        "CLIENT",
        123_456_789 + 31 * MS + 11,
        123_456_789 + 79 * MS + 99,
        parent="plan",
        attrs=inference(
            "gpt-5.4-mini",
            call_id=uuid_from(b, "n2"),
            usage=(1200, 300),
            **{
                "gen_ai.provider.name": "openai",
                "gen_ai.response.model": "gpt-5.4-mini-2026-03-17",
                "gen_ai.response.id": "chatcmpl-phase3b-" + b._id("resp", n=8).hex(),
                "gen_ai.usage.cache_read.input_tokens": 200,
                "gen_ai.usage.reasoning.output_tokens": 64,
                "gen_ai.request.seed": 2**62 + 1,
                "openai.response.service_tier": "default",
                "server.address": "api.openai.com",
            },
        ),
    )

    # Equal start times: two siblings start at the same nanosecond (order = span id).
    t = b.trace("phase3b.equal-start")
    t.add("root", "batch.handle", "INTERNAL", 0, 40 * MS)
    for key in ("b-second", "a-first"):
        t.add(
            key,
            f"chat mock-chat-small ({key})",
            "CLIENT",
            5 * MS + 555,
            20 * MS,
            parent="root",
            attrs=inference("mock-chat-small", call_id=uuid_from(b, key)),
        )

    # Orphan: a span whose parent was never stored, with its own child.
    t = b.trace("phase3b.orphan")
    t.add(
        "root",
        "GET /v1/status",
        "SERVER",
        0,
        10 * MS,
        attrs={"http.request.method": "GET", "http.route": "/v1/status"},
    )
    missing_parent = b._id("never-sent-parent", n=8)
    t.add(
        "orph",
        "chat mock-chat-small (orphan)",
        "CLIENT",
        12 * MS,
        30 * MS,
        parent=missing_parent,
        attrs=inference("mock-chat-small", call_id=uuid_from(b, "o1")),
    )
    t.add("orph-child", "post.process", "INTERNAL", 25 * MS, 29 * MS, parent="orph")

    # Parent cycle (client-supplied): A's parent is B and B's parent is A, beside a root.
    t = b.trace("phase3b.cycle")
    t.add("root", "job.run", "INTERNAL", 0, 50 * MS)
    t.add("cyc-a", "cycle.a", "INTERNAL", 10 * MS, 20 * MS, parent="cyc-b")
    t.add("cyc-b", "cycle.b", "INTERNAL", 12 * MS, 22 * MS, parent="cyc-a")

    # Mixed: one synthetic inference span and one without any provenance claim.
    t = b.trace("phase3b.mixed")
    t.add(
        "root",
        "POST /v1/compare",
        "SERVER",
        0,
        60 * MS,
        attrs={"http.request.method": "POST", "http.route": "/v1/compare"},
    )
    t.add(
        "syn",
        "chat mock-chat-small",
        "CLIENT",
        2 * MS,
        20 * MS,
        parent="root",
        attrs=inference("mock-chat-small", call_id=uuid_from(b, "m1")),
    )
    t.add(
        "unl",
        "chat mock-chat-large",
        "CLIENT",
        22 * MS,
        50 * MS,
        parent="root",
        attrs=inference("mock-chat-large", provenance=None, call_id=uuid_from(b, "m2")),
    )

    # Unknown: the only inference span carries no claim and no usage.
    t = b.trace("phase3b.unknown")
    t.add(
        "root",
        "POST /v1/unlabeled",
        "SERVER",
        0,
        30 * MS,
        attrs={"http.request.method": "POST", "http.route": "/v1/unlabeled"},
    )
    t.add(
        "unl",
        "chat mock-chat-small",
        "CLIENT",
        3 * MS,
        25 * MS,
        parent="root",
        attrs=inference(
            "mock-chat-small", provenance=None, usage=None, call_id=uuid_from(b, "u1")
        ),
    )

    # Application-span error: the root fails although its inference attempt succeeded.
    t = b.trace("phase3b.app-error")
    t.add(
        "root",
        "POST /v1/summarize",
        "SERVER",
        0,
        45 * MS,
        attrs={
            "http.request.method": "POST",
            "http.route": "/v1/summarize",
            "http.response.status_code": 500,
        },
        error="500",
    )
    t.add(
        "llm",
        "chat mock-chat-small",
        "CLIENT",
        2 * MS,
        30 * MS,
        parent="root",
        attrs=inference("mock-chat-small", call_id=uuid_from(b, "e1")),
    )

    # Hostile labels (T41): only free-text labels can carry markup through ingest; identifier
    # fields such as the model and provider are dropped by the server's format rules.
    t = b.trace(
        f'<img src=x onerror="{XSS}">phase3b.xss',
        resource_extra={
            "deployment.environment.name": "<b>bold</b><svg onload=" + XSS + ">",
            "service.namespace": "</code><script>" + XSS + "</script>",
        },
        scope='"><svg onload=' + XSS + ">",
        scope_version="<i>1</i>",
    )
    t.add(
        "root",
        "<script>" + XSS + "</script>",
        "SERVER",
        0,
        20 * MS,
        attrs={
            "http.request.method": "GET",
            "http.route": "/<script>" + XSS + "</script>",
        },
    )
    t.add(
        "llm",
        "chat <img src=x onerror=" + XSS + ">",
        "CLIENT",
        1 * MS,
        15 * MS,
        parent="root",
        attrs=inference(
            "<script>alert(1)</script>",
            call_id=uuid_from(b, "x1"),
            **{"gen_ai.provider.name": "<img src=x onerror=alert(1)>"},
        ),
    )

    # Large trace: 1 root, 700 steps, every 10th step with one child (771 spans).
    t = b.trace("phase3b.large")
    t.add("root", "bulk.import", "SERVER", 0, 800 * MS)
    for i in range(700):
        start = (i + 1) * MS
        t.add(
            f"s{i}", f"step {i:04d}", "INTERNAL", start, start + MS // 2, parent="root"
        )
        if i % 10 == 0:
            t.add(
                f"s{i}c",
                f"step {i:04d}.child",
                "INTERNAL",
                start + 1000,
                start + 2000,
                parent=f"s{i}",
            )


def letters_from(b: Builder, name: str, n: int = 16) -> str:
    """Deterministic lowercase letters: a digit run in an id could be dropped by the R08 rule."""
    return "".join(chr(97 + x % 26) for x in b._id("letters", name, n=n))


def phase3c_set(b: Builder) -> None:
    # A retried logical call: attempt 1 fails (429, no usage), attempt 2 succeeds.
    t = b.trace("phase3c.retry")
    t.add(
        "root", "POST /v1/chat", "SERVER", 0, 60 * MS, attrs={"http.route": "/v1/chat"}
    )
    call = uuid_from(b, "retry")
    t.add(
        "a1",
        "chat mock-chat-small",
        "CLIENT",
        1 * MS,
        11 * MS,
        parent="root",
        attrs=inference("mock-chat-small", call_id=call, attempt=1, usage=None),
        error="429",
    )
    t.add(
        "a2",
        "chat mock-chat-small",
        "CLIENT",
        15 * MS,
        45 * MS,
        parent="root",
        attrs=inference(
            "mock-chat-small",
            call_id=call,
            attempt=2,
            usage=(100, 20),
            **{"gen_ai.response.id": "resp-" + letters_from(b, "a2")},
        ),
    )

    # Several providers and models in one trace (R40): two priced openai calls, one unpriced
    # mock call and a failed call of a third provider that reported no response model.
    t = b.trace("phase3c.multi-model")
    t.add(
        "root",
        "POST /v1/compare",
        "SERVER",
        0,
        120 * MS,
        attrs={"http.route": "/v1/compare"},
    )
    for key, start in (("o1", 2), ("o2", 30)):
        t.add(
            key,
            "chat gpt-5.4-mini",
            "CLIENT",
            start * MS,
            (start + 20) * MS,
            parent="root",
            attrs=inference(
                "gpt-5.4-mini",
                call_id=uuid_from(b, key),
                usage=(400, 100),
                **{
                    "gen_ai.provider.name": "openai",
                    "gen_ai.response.model": "gpt-5.4-mini-2026-03-17",
                    "gen_ai.response.id": "chatcmpl-" + letters_from(b, key),
                },
            ),
        )
    t.add(
        "m1",
        "chat mock-chat-small",
        "CLIENT",
        60 * MS,
        70 * MS,
        parent="root",
        attrs=inference(
            "mock-chat-small",
            call_id=uuid_from(b, "m1"),
            **{"gen_ai.response.id": "resp-" + letters_from(b, "m1")},
        ),
    )
    t.add(
        "x1",
        "chat alt-model-x",
        "CLIENT",
        80 * MS,
        95 * MS,
        parent="root",
        attrs=inference(
            "alt-model-x",
            call_id=uuid_from(b, "x1"),
            usage=None,
            **{"gen_ai.provider.name": "synthetic.alt"},
        ),
        error="500",
    )


def phase6c_set(b: Builder) -> None:
    """Phase 6C-C: provider, model and provenance variety for the dashboard filters. Every
    value is a SYNTHETIC fixture and no provider is called; the one `real` claim exercises the
    asserted-real label (the server stores it as asserted, never verified)."""

    def call(
        key: str, provider: str | None, model: str | None, **kw: object
    ) -> dict[str, object]:
        a = inference(model or "unused", call_id=uuid_from(b, key), **kw)
        if provider is None:
            del a["gen_ai.provider.name"]  # stored as provider `unknown`
        else:
            a["gen_ai.provider.name"] = provider
        if model is None:
            del a["gen_ai.request.model"]  # no model reported
        else:
            a["gen_ai.response.model"] = model
        return a

    rows = (
        # service, provider, model, provenance, response id, error
        ("phase6c.openai", "openai", "gpt-5.4-mini", "synthetic", True, None),
        ("phase6c.openai-error", "openai", "gpt-5.4-mini", "synthetic", False, "500"),
        (
            "phase6c.gemini",
            "gcp.gemini",
            "gemini-3.1-flash-lite",
            "synthetic",
            True,
            None,
        ),
        (
            "phase6c.gemini-asserted-real",
            "gcp.gemini",
            "gemini-3.1-flash-lite",
            "real",
            True,
            None,
        ),
        # Claims real without a response id: the server downgrades it to unknown (never real).
        (
            "phase6c.claim-downgraded",
            "gcp.gemini",
            "gemini-3.1-flash-lite",
            "real",
            False,
            None,
        ),
        ("phase6c.unlabelled", None, None, None, False, None),
    )
    for i, (service, provider, model, provenance, with_id, error) in enumerate(rows):
        t = b.trace(service, scope="phase6c.seed")
        t.add(
            "root",
            "POST /v1/run",
            "SERVER",
            i * MS,
            (i + 40) * MS,
            attrs={"http.route": "/v1/run"},
        )
        extra: dict[str, object] = {}
        if with_id:
            extra["gen_ai.response.id"] = "resp-" + letters_from(b, service)
        t.add(
            "llm",
            f"chat {model or 'unknown'}",
            "CLIENT",
            (i + 2) * MS,
            (i + 30) * MS,
            parent="root",
            attrs=call(
                service,
                provider,
                model,
                provenance=provenance,
                usage=None if error else (18, 1),
                **extra,
            ),
            error=error,
        )


def phase6c_new_set(b: Builder) -> None:
    """Phase 6C-C: one new SYNTHETIC trace, sent while a browser test waits for a refresh."""
    t = b.trace("phase6c.arrived-later", scope="phase6c.seed")
    t.add("root", "POST /v1/run", "SERVER", 0, 25 * MS, attrs={"http.route": "/v1/run"})
    t.add(
        "llm",
        "chat gpt-5.4-mini",
        "CLIENT",
        2 * MS,
        20 * MS,
        parent="root",
        attrs=inference(
            "gpt-5.4-mini",
            call_id=uuid_from(b, "later"),
            usage=(10, 2),
            **{
                "gen_ai.provider.name": "openai",
                "gen_ai.response.id": "resp-" + letters_from(b, "later"),
            },
        ),
    )


# Phase 8A-6: the three comparison scenarios of Phase 8A-4 (`demo_app/comparison_scenarios.py`,
# ADR-45), pinned here so the dashboard fixture does not import the demo app. Distinct token
# counts per scenario make a merged row visible.
COMPARISON_SCENARIOS = (
    ("ticket-triage", "cmp.ticket-triage", "cmp-v1.ticket-triage", (20, 32)),
    ("json-aggregate", "cmp.json-aggregate", "cmp-v1.json-aggregate", (24, 40)),
    ("release-summary", "cmp.release-summary", "cmp-v1.release-summary", (30, 64)),
)
GROQ_MODEL = "openai/gpt-oss-20b"
# SYNTHETIC: no OpenRouter adapter exists.
OPENROUTER_MODEL = "synthetic/compat-only:free"
CONTENT_SENTINEL = "PHASE8A6-CONTENT-SENTINEL-never-stored"
REASONING_SENTINEL = "PHASE8A6-REASONING-SENTINEL-never-stored"


def phase8a6_set(b: Builder) -> None:
    """Phase 8A-6: one SYNTHETIC multi-provider comparison set for the dashboard. No provider
    is called and nothing claims `real`. Every inference span is `synthetic`, with
    provider-reported usage shaped like each adapter's spans (the Groq ones carry the exact
    attribute set of `docs/fixtures/groq_client_span.v1.json`). The OpenRouter trace is a
    SYNTHETIC compatibility representation only, with a model name that says so. Also: a
    root-only `demo.live-compare` trace (R131: what a send-time refusal leaves), and one
    adversarial trace whose hostile provider and model, content, reasoning and key-shaped
    response id the server must drop."""

    def root(t: TraceBuilder, i: int, name: str, kind: str = "INTERNAL") -> None:
        t.add("root", name, kind, i * 1000 * MS, i * 1000 * MS + 50 * MS)

    def client(
        t: TraceBuilder,
        i: int,
        model: str,
        attrs: dict[str, object],
        error: str | None = None,
    ) -> None:
        t.add(
            "llm",
            f"chat {model}",
            "CLIENT",
            i * 1000 * MS + MS,
            i * 1000 * MS + 40 * MS,
            parent="root",
            attrs=attrs,
            error=error,
        )

    def call(
        key: str,
        provider: str,
        model: str,
        prompt: tuple[str, str],
        usage: tuple[int, int],
        **extra: object,
    ) -> dict[str, object]:
        return inference(
            model,
            call_id=uuid_from(b, key),
            usage=usage,
            **{
                "gen_ai.provider.name": provider,
                "gen_ai.response.model": model,
                "gen_ai.response.id": f"chatcmpl-SYNTHETIC-8a6-{letters_from(b, key)}",
                "gen_ai.response.finish_reasons": ["stop"],
                "gen_ai.prompt.name": prompt[0],
                "gen_ai.prompt.version": prompt[1],
                **extra,
            },
        )

    # A: OpenAI, priced by the verified catalogue (standard tier, global host), as in
    # backend/tests/integration/test_multi_provider_read.py: 0.0015750000 USD.
    t = b.trace("cmp8a6.openai", scope="phase8a6.seed")
    root(t, 0, "POST /v1/answer", "SERVER")
    a = call("openai", "openai", "gpt-5.4-mini", ("cmp8a6.baseline", "v1"), (1200, 300))
    a["gen_ai.response.model"] = "gpt-5.4-mini-2026-03-17"
    a["gen_ai.usage.cache_read.input_tokens"] = 1000
    a["gen_ai.usage.reasoning.output_tokens"] = 200
    a["openai.response.service_tier"] = "default"
    a["server.address"] = "api.openai.com"
    client(t, 0, "gpt-5.4-mini", a)

    # B: Gemini, never priced (ADR-35).
    t = b.trace("cmp8a6.gemini", scope="phase8a6.seed")
    root(t, 1, "POST /v1/answer", "SERVER")
    m = "gemini-3.1-flash-lite"
    client(t, 1, m, call("gemini", "gcp.gemini", m, ("cmp8a6.baseline", "v1"), (18, 6)))

    # C: Groq, one trace per comparison scenario, as the runner exports them: a
    # `demo.live-compare` root and the adapter's CLIENT span. Never priced (ADR-40).
    for n, (scenario, name, version, usage) in enumerate(COMPARISON_SCENARIOS):
        i = 2 + n
        t = b.trace(f"cmp8a6.groq.{scenario}", scope="phase8a6.seed")
        root(t, i, "demo.live-compare")
        attrs = call(
            f"groq-{scenario}",
            "groq",
            GROQ_MODEL,
            (name, version),
            usage,
            **{
                "gen_ai.request.max_tokens": 1024,
                "server.address": "api.groq.com",
                "server.port": 443,
            },
        )
        client(t, i, GROQ_MODEL, attrs)

    # D: OpenRouter, a SYNTHETIC compatibility representation only (no adapter, no runner
    # support), so its root is not the runner's. Never priced (ADR-40).
    t = b.trace("cmp8a6.openrouter-synthetic", scope="phase8a6.seed")
    root(t, 5, "synthetic.openrouter-compat")
    attrs = call(
        "openrouter",
        "openrouter",
        OPENROUTER_MODEL,
        ("cmp.ticket-triage", "cmp-v1.ticket-triage"),
        (20, 32),
        **{"server.address": "openrouter.ai", "server.port": 443},
    )
    client(t, 5, OPENROUTER_MODEL, attrs)

    # R131: the runner's root span alone, as a send-time refusal leaves it. No inference.
    t = b.trace("cmp8a6.refused-root-only", scope="phase8a6.seed")
    root(t, 6, "demo.live-compare")

    # Adversarial: hostile labels (kept as text), and six values the server must drop: a
    # provider and a model with markup, two content attributes, a reasoning attribute and a
    # key-shaped response id (assembled here, never a literal). No usage reported.
    t = b.trace(f'<img src=x onerror="{XSS}">cmp8a6.adversarial', scope="phase8a6.seed")
    t.add("root", f"<script>{XSS}</script>", "INTERNAL", 7000 * MS, 7050 * MS)
    hostile: dict[str, object] = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": f"<img src=x onerror={XSS}>",
        "gen_ai.request.model": f"<script>{XSS}</script>",
        "gen_ai.response.id": "gsk" + "_" + "Q7xE2EDASH" * 4,
        "gen_ai.input.messages": CONTENT_SENTINEL,
        "gen_ai.output.messages": CONTENT_SENTINEL,
        "llm.reasoning": REASONING_SENTINEL,
        "llminsight.call_id": uuid_from(b, "adversarial"),
        "llminsight.attempt": 1,
        "llminsight.provenance": "synthetic",
    }
    t.add(
        "llm",
        f"chat <img src=x onerror={XSS}>",
        "CLIENT",
        7001 * MS,
        7040 * MS,
        parent="root",
        attrs=hostile,
    )


def isolation_set(b: Builder) -> None:
    t = b.trace("phase3b.isolation")
    t.add("root", "POST /v1/other-project", "SERVER", 0, 20 * MS)
    t.add(
        "llm",
        "chat mock-chat-small",
        "CLIENT",
        1 * MS,
        15 * MS,
        parent="root",
        attrs=inference("mock-chat-small", call_id=uuid_from(b, "i1")),
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--seed", default=None, help="derive ids from this string (default: random)"
    )
    parser.add_argument(
        "--set",
        choices=("edge", "isolation", "phase3c", "phase6c", "phase6c-new", "phase8a6"),
        default="edge",
    )
    args = parser.parse_args(argv)

    key = os.environ.get("LLMI_SEED_PROJECT_KEY", "")
    if not key:
        print(
            "error: set LLMI_SEED_PROJECT_KEY (an ingest key) in the environment",
            file=sys.stderr,
        )
        return 2
    endpoint = os.environ.get("LLMI_SEED_ENDPOINT", "http://127.0.0.1:8000/v1/traces")
    if urlsplit(endpoint).hostname not in ("127.0.0.1", "localhost", "::1"):
        print("error: the endpoint must be a loopback address", file=sys.stderr)
        return 2

    seed = args.seed or secrets.token_hex(8)
    anchor = (
        (time.time_ns() - 10 * 60 * 1_000_000_000) // 1_000_000_000 * 1_000_000_000
    )  # whole second, 10 min ago
    b = Builder(seed, anchor)
    {
        "edge": edge_set,
        "isolation": isolation_set,
        "phase3c": phase3c_set,
        "phase6c": phase6c_set,
        "phase6c-new": phase6c_new_set,
        "phase8a6": phase8a6_set,
    }[args.set](b)

    request = b.request()
    spans = sum(len(ss.spans) for rs in request.resource_spans for ss in rs.scope_spans)
    if spans == 0:
        print("error: built an empty request", file=sys.stderr)
        return 1
    body = request.SerializeToString()
    resp = requests.post(
        endpoint,
        data=body,
        headers={"Content-Type": "application/x-protobuf", "X-Project-Key": key},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"error: ingest answered HTTP {resp.status_code}", file=sys.stderr)
        return 1
    reply = ExportTraceServiceResponse.FromString(resp.content)
    rejected = (
        reply.partial_success.rejected_spans if reply.HasField("partial_success") else 0
    )
    print(
        json.dumps(
            {
                "set": args.set,
                "seed": seed,
                "spans_sent": spans,
                "rejected_spans": rejected,
                "traces": b.trace_ids,
            }
        )
    )
    return 0 if rejected == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
