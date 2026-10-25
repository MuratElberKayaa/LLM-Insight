"""`GET /api/v1/traces` and `GET /api/v1/traces/{trace_id}` (ARCHITECTURE.md §5, Phase 1 stub).

Local-only admin read API: `require_admin` (bearer token, server-side only, CR-06) runs
before any parameter is looked at. Every request is scoped by the mandatory `project` id; a
trace of another project is a 404 identical to a missing one (T28).

Output rules:
- timestamps are ISO-8601 UTC with `Z`, rendered from the stored nanoseconds (all 9 digits),
  and the nanoseconds themselves are strings (`*_unix_nano`, like OTLP/JSON) because JSON
  numbers lose precision above 2^53 (T26)
- unknown stays `null`, never 0; money is a decimal string (ADR-11)
- `provenance_verified` is always false: provenance is asserted by the instrumentation
- no `status_message` (R21), no internal ids, no keys or tokens
"""

import base64
import binascii
import logging
import re
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.admin import (
    NO_STORE,
    internal_error,
    invalid,
    not_found,
    require_admin,
    storage_unavailable,
)
from app.core.db import RETRYABLE_DB_ERRORS
from app.repositories import traces as repo

logger = logging.getLogger("app.api")

router = APIRouter(prefix="/api/v1", tags=["traces"], dependencies=[Depends(require_admin)])

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MAX_WINDOW = timedelta(days=31)
MAX_LIMIT = 200
DEFAULT_LIMIT = 50
BIGINT_MAX = 2**63 - 1
TRACE_ID_PATTERN = r"^[0-9a-f]{32}$"
_CURSOR_RE = re.compile(r"v1:([0-9]{1,19}):([0-9a-f]{32})")

ProjectParam = Annotated[int, Query(ge=1, le=BIGINT_MAX, description="Project id (required)")]


# ---------------------------------------------------------------- formatting
def iso_from_ns(ns: int) -> str:
    seconds, nanos = divmod(ns, 1_000_000_000)
    return (EPOCH + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S") + f".{nanos:09d}Z"


def iso_from_datetime(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _int(v: Decimal | int | None) -> int | None:
    return None if v is None else int(v)


def _money(v: Decimal | None) -> str | None:
    return None if v is None else format(v, "f")


# ---------------------------------------------------------------- parameters
def parse_utc(field: str, raw: str) -> datetime:
    """ISO-8601 with an explicit UTC offset (`Z` or `+00:00`); naive or other offsets are 422."""
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        raise invalid(field, f"{field} must be an ISO-8601 UTC timestamp") from None
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        raise invalid(field, f"{field} must be an ISO-8601 timestamp in UTC (Z or +00:00)")
    return dt.astimezone(UTC)


def resolve_window(
    raw_from: str | None, raw_to: str | None, now: datetime
) -> tuple[datetime, datetime]:
    """`[from, to)` on the trace start time, at most 31 days. Defaults: `to` = now,
    `from` = `to` - 31 days, so the window is bounded even when neither is given."""
    end = parse_utc("to", raw_to) if raw_to is not None else now
    start = parse_utc("from", raw_from) if raw_from is not None else end - MAX_WINDOW
    if start >= end:
        raise invalid("from", "from must be earlier than to")
    if end - start > MAX_WINDOW:
        raise invalid("from", "the time window must not exceed 31 days")
    return start, end


def encode_cursor(started_at: datetime, trace_id: str) -> str:
    micros = (started_at - EPOCH) // timedelta(microseconds=1)
    return base64.urlsafe_b64encode(f"v1:{micros}:{trace_id}".encode()).decode().rstrip("=")


def decode_cursor(raw: str) -> repo.Cursor:
    """Opaque to clients. Anything that does not decode to a v1 position is 422."""
    try:
        decoded = base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True)
        m = _CURSOR_RE.fullmatch(decoded.decode("ascii"))
        if m is None:
            raise ValueError
        started_at = EPOCH + timedelta(microseconds=int(m.group(1)))
    except (binascii.Error, ValueError, UnicodeDecodeError, OverflowError):
        raise invalid("cursor", "cursor is not valid") from None
    return repo.Cursor(started_at=started_at, trace_id=m.group(2))


# ---------------------------------------------------------------- DB access
def read_snapshot[T](request: Request, fn: Callable[[Session], T]) -> T:
    """Run `fn` in one read-only snapshot transaction (always rolled back)."""
    factory = request.app.state.session_factory
    try:
        with factory() as session:
            repo.begin_read_only(session)
            return fn(session)
    except RETRYABLE_DB_ERRORS as exc:
        logger.error("api read failed: %s", type(exc).__name__)
        raise storage_unavailable() from None
    except SQLAlchemyError as exc:
        logger.error("api read failed: %s", type(exc).__name__)
        raise internal_error() from None


# ---------------------------------------------------------------- serialization
def trace_summary(r: Any) -> dict[str, Any]:
    return {
        "trace_id": r.trace_id,
        "service_name": r.service_name,
        "started_at": iso_from_ns(r.start_ns),
        "ended_at": iso_from_ns(r.end_ns),
        "start_time_unix_nano": str(r.start_ns),
        "end_time_unix_nano": str(r.end_ns),
        "duration_ns": str(r.end_ns - r.start_ns),
        "status": r.status,
        "prompt_version": r.prompt_version,
        "provenance": r.provenance,
        "provenance_verified": False,
        "span_count": r.span_count,
        "usage": {
            "inference_calls": r.inference_calls,
            "input_tokens": _int(r.input_tokens),
            "output_tokens": _int(r.output_tokens),
            "calls_with_unknown_tokens": r.calls_with_unknown_tokens,
            "known_cost_usd": _money(r.known_cost_usd),
            "unknown_cost_calls": r.unknown_cost_calls,
        },
    }


def _span(r: Any, children: list[str]) -> dict[str, Any]:
    # T27: a span whose parent is not stored is an orphan; no parent is invented.
    role = "root" if r.parent_span_id is None else "child" if r.parent_stored else "orphan"
    return {
        "span_id": r.span_id,
        "parent_span_id": r.parent_span_id,
        "role": role,
        "children": children,
        "name": r.name,
        "kind": r.kind,
        "started_at": iso_from_ns(r.start_ns),
        "ended_at": iso_from_ns(r.end_ns),
        "start_time_unix_nano": str(r.start_ns),
        "end_time_unix_nano": str(r.end_ns),
        "duration_ns": str(r.duration_ns),
        "status": r.status,
        "error_type": r.error_type,
        "scope_name": r.scope_name,
        "scope_version": r.scope_version,
        "resource_attributes": r.resource_attributes,
        "attributes": r.attributes,
        "attributes_schema_version": r.attributes_schema_version,
        "provenance": r.provenance,
        "provenance_verified": False,
        "server_dropped_attributes_count": r.server_dropped_attributes_count,
    }


def _usage(r: Any) -> dict[str, Any]:
    return {
        "span_id": r.span_id,
        "canonical_call_key": r.canonical_call_key,
        "call_key_source": r.call_key_source,
        "call_id": r.call_id,
        "attempt_id": r.attempt_id,
        "provider_response_id": r.provider_response_id,
        "operation_name": r.operation_name,
        "provider": r.provider,
        "requested_model": r.requested_model,
        "response_model": r.response_model,
        "input_tokens": r.input_tokens,
        "output_tokens": r.output_tokens,
        "cached_input_tokens": r.cached_input_tokens,
        "cache_write_input_tokens": r.cache_write_input_tokens,
        "reasoning_output_tokens": r.reasoning_output_tokens,
        "usage_source": r.usage_source,
        "cost_usd": _money(r.cost_usd),
        "cost_status": r.cost_status,
        "cost_unknown_reason": r.cost_unknown_reason,
        "price_version": r.price_version,
        "provenance": r.provenance,
        "provenance_verified": False,
        "started_at": iso_from_datetime(r.started_at),
    }


# ---------------------------------------------------------------- endpoints
@router.get("/traces")
async def list_traces(
    request: Request,
    project: ProjectParam,
    from_: Annotated[str | None, Query(alias="from", min_length=1, max_length=64)] = None,
    to: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    model: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
    status: Literal["ok", "error"] | None = None,
    provenance: Literal["real", "synthetic", "mixed", "unknown"] | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> JSONResponse:
    """Traces of one project whose start is in `[from, to)`, newest first, keyset-paginated."""
    start, end = resolve_window(from_, to, datetime.now(UTC))
    position = decode_cursor(cursor) if cursor is not None else None
    f = repo.TraceFilter(project, start, end, status, provenance, model)

    def read(session: Session) -> list[Any]:
        if not repo.project_exists(session, project):
            raise not_found()
        return repo.list_traces(session, f, position, limit)

    rows = await run_in_threadpool(read_snapshot, request, read)
    page = rows[:limit]
    next_cursor = (
        encode_cursor(page[-1].started_at, page[-1].trace_id) if len(rows) > limit else None
    )
    return JSONResponse(
        {
            "items": [trace_summary(r) for r in page],
            "next_cursor": next_cursor,
            "window": {"from": iso_from_datetime(start), "to": iso_from_datetime(end)},
        },
        headers=NO_STORE,
    )


@router.get("/traces/{trace_id}")
async def get_trace(
    request: Request,
    trace_id: Annotated[str, Path(pattern=TRACE_ID_PATTERN, max_length=32)],
    project: ProjectParam,
) -> JSONResponse:
    """Span tree (flat, with roles and children), usage rows and linked reviews of one trace.
    Failure reviews arrive in Phase 4; until then `reviews` is always empty."""

    def read(session: Session) -> tuple[Any, list[Any], list[Any]]:
        trace = repo.get_trace(session, project, trace_id)
        if trace is None:  # also an unknown project: one indistinguishable 404
            raise not_found()
        return (
            trace,
            repo.get_spans(session, project, trace_id),
            repo.get_usage(session, project, trace_id),
        )

    trace, span_rows, usage_rows = await run_in_threadpool(read_snapshot, request, read)
    spans_truncated = len(span_rows) > repo.DETAIL_MAX_SPANS
    span_rows = span_rows[: repo.DETAIL_MAX_SPANS]
    children: dict[str, list[str]] = defaultdict(list)
    for s in span_rows:
        if s.parent_span_id is not None and s.parent_stored:
            children[s.parent_span_id].append(s.span_id)
    usage_truncated = len(usage_rows) > repo.DETAIL_MAX_SPANS
    return JSONResponse(
        {
            "trace": trace_summary(trace),
            "spans": [_span(s, children.get(s.span_id, [])) for s in span_rows],
            "spans_truncated": spans_truncated,
            "usage": [_usage(u) for u in usage_rows[: repo.DETAIL_MAX_SPANS]],
            "usage_truncated": usage_truncated,
            "reviews": [],
        },
        headers=NO_STORE,
    )
