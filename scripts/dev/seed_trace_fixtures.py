"""Seed SYNTHETIC edge-case traces for the trace explorer (Phase 3B) through the real
`POST /v1/traces` (OTLP/HTTP protobuf), so they pass ingest validation like any client data.

Run with the SDK venv (it has `opentelemetry-proto` and `requests`), backend running:

    LLMI_SEED_PROJECT_KEY=<ingest key> sdk/.venv/Scripts/python scripts/dev/seed_trace_fixtures.py [--seed S] [--set edge|isolation]

- The key is read from the environment only (never argv, never printed). The endpoint must be
  loopback (`LLMI_SEED_ENDPOINT`, default http://127.0.0.1:8000/v1/traces).
- No provider is called. Every inference span claims `synthetic`, except the spans that
  deliberately carry no claim (provenance `unknown`, and the `mixed` trace). Nothing claims `real`.
- IDs derive from `--seed` (random by default); timestamps are anchored a few minutes before
  now with deliberate sub-microsecond digits (T26).
- Prints one JSON line: `{service.name: trace_id}`. No key, no content.

Edge cases (`--set edge`, service names `phase3b.*`): nested spans with a known cost and a
>2^53 attribute, equal start times, an orphan, a parent cycle, mixed and unknown provenance,
an application-span error, hostile labels (XSS probes), and a large trace (700+ spans).
`--set isolation`: one trace, to be sent with a key of a second project (T28 via the UI).
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
    parser.add_argument("--set", choices=("edge", "isolation"), default="edge")
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
    (edge_set if args.set == "edge" else isolation_set)(b)

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
