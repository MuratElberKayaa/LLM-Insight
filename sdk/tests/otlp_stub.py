"""A local OTLP/HTTP receiver for unit tests (stdlib HTTP server + opentelemetry-proto).

It decodes the real wire format the SDK sends (gzip + binary ExportTraceServiceRequest), so
unit tests check the protobuf actually exported. Its behaviour can be switched to simulate
a rejecting (401), unavailable (503), slow or blocked backend. It never logs headers.
"""

from __future__ import annotations

import gzip
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue

KINDS = {0: "UNSPECIFIED", 1: "INTERNAL", 2: "SERVER", 3: "CLIENT", 4: "PRODUCER", 5: "CONSUMER"}
STATUS = {0: "UNSET", 1: "OK", 2: "ERROR"}


def any_value(v: AnyValue) -> Any:
    which = v.WhichOneof("value")
    if which == "array_value":
        return [any_value(x) for x in v.array_value.values]
    return getattr(v, which) if which else None


@dataclass
class ReceivedRequest:
    headers: dict[str, str]
    raw_body: bytes  # as received (possibly gzip)
    body: bytes  # decompressed protobuf
    request: ExportTraceServiceRequest

    def spans(self) -> list[dict[str, Any]]:
        out = []
        for rs in self.request.resource_spans:
            resource = {a.key: any_value(a.value) for a in rs.resource.attributes}
            for ss in rs.scope_spans:
                for s in ss.spans:
                    out.append(
                        {
                            "name": s.name,
                            "kind": KINDS[s.kind],
                            "trace_id": s.trace_id.hex(),
                            "span_id": s.span_id.hex(),
                            "parent_span_id": s.parent_span_id.hex() or None,
                            "attributes": {a.key: any_value(a.value) for a in s.attributes},
                            "status": STATUS[s.status.code],
                            "status_message": s.status.message,
                            "events": len(s.events),
                            "start_ns": s.start_time_unix_nano,
                            "end_ns": s.end_time_unix_nano,
                            "resource": resource,
                            "scope": (ss.scope.name, ss.scope.version),
                        }
                    )
        return out


@dataclass
class StubBehaviour:
    status: int = 200
    delay_s: float = 0.0
    gate: threading.Event | None = None  # requests wait until it is set


@dataclass
class OTLPStub:
    server: ThreadingHTTPServer
    thread: threading.Thread
    behaviour: StubBehaviour = field(default_factory=StubBehaviour)
    received: list[ReceivedRequest] = field(default_factory=list)
    attempts: int = 0  # every POST, including rejected ones
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def endpoint(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/v1/traces"

    def spans(self) -> list[dict[str, Any]]:
        with self.lock:
            return [s for r in self.received for s in r.spans()]

    def stop(self) -> None:
        if self.behaviour.gate is not None:
            self.behaviour.gate.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def start_stub() -> OTLPStub:
    holder: dict[str, OTLPStub] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass  # never log request lines or headers

        def do_POST(self) -> None:  # noqa: N802
            stub = holder["stub"]
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            with stub.lock:
                stub.attempts += 1
            b = stub.behaviour
            if b.gate is not None:
                b.gate.wait(timeout=30)
            if b.delay_s:
                time.sleep(b.delay_s)
            if b.status != 200:
                self.send_response(b.status)
                self.send_header("Content-Type", "application/x-protobuf")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = gzip.decompress(raw) if self.headers.get("Content-Encoding") == "gzip" else raw
            request = ExportTraceServiceRequest.FromString(body)
            with stub.lock:
                stub.received.append(
                    ReceivedRequest(
                        {k.lower(): v for k, v in self.headers.items()}, raw, body, request
                    )
                )
            payload = ExportTraceServiceResponse().SerializeToString()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    stub = OTLPStub(server, thread)
    holder["stub"] = stub
    return stub
