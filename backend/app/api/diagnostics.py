"""`GET /api/v1/traces/{trace_id}/diagnostics` (ARCHITECTURE.md §5.6, Phase 4A).

Read-only and derived on request: nothing is persisted, no table or migration is added.
Access is exactly that of the trace detail (§5.1): `require_admin` runs before any parameter
is looked at, the `project` id scopes every query, and an unknown project, an unknown trace
and another project's trace are one indistinguishable 404 (T28). The rows are the detail's
own bounded loads (at most 2000 spans and 2000 usage rows, read in one `REPEATABLE READ,
READ ONLY` snapshot with a fixed number of statements), so a request costs at most what the
detail costs plus the in-memory rule pass.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.admin import NO_STORE, not_found, require_admin
from app.api.traces import TRACE_ID_PATTERN, ProjectParam, read_snapshot
from app.repositories import traces as repo
from app.services import diagnostics

router = APIRouter(prefix="/api/v1", tags=["diagnostics"], dependencies=[Depends(require_admin)])


def _inputs(
    trace: Any, span_rows: list[Any], usage_rows: list[Any]
) -> tuple[
    diagnostics.TraceInput,
    list[diagnostics.SpanInput],
    list[diagnostics.UsageInput],
    bool,
    bool,
]:
    spans_truncated = len(span_rows) > repo.DETAIL_MAX_SPANS
    usage_truncated = len(usage_rows) > repo.DETAIL_MAX_SPANS
    return (
        diagnostics.TraceInput(trace.trace_id, trace.status, trace.provenance, trace.span_count),
        [
            diagnostics.SpanInput(
                span_id=s.span_id,
                parent_span_id=s.parent_span_id,
                parent_stored=bool(s.parent_stored),
                kind=s.kind,
                start_ns=s.start_ns,
                end_ns=s.end_ns,
                status=s.status,
                error_type=s.error_type,
                attributes=s.attributes,
                provenance=s.provenance,
                server_dropped_attributes_count=s.server_dropped_attributes_count,
            )
            for s in span_rows[: repo.DETAIL_MAX_SPANS]
        ],
        [
            diagnostics.UsageInput(
                span_id=u.span_id,
                usage_source=u.usage_source,
                input_tokens=u.input_tokens,
                output_tokens=u.output_tokens,
                cached_input_tokens=u.cached_input_tokens,
                cache_write_input_tokens=u.cache_write_input_tokens,
                reasoning_output_tokens=u.reasoning_output_tokens,
                cost_status=u.cost_status,
                cost_unknown_reason=u.cost_unknown_reason,
            )
            for u in usage_rows[: repo.DETAIL_MAX_SPANS]
        ],
        spans_truncated,
        usage_truncated,
    )


def _diagnose(trace: Any, span_rows: list[Any], usage_rows: list[Any]) -> dict[str, Any]:
    t, spans, usage, spans_truncated, usage_truncated = _inputs(trace, span_rows, usage_rows)
    return diagnostics.diagnose(
        t, spans, usage, spans_truncated=spans_truncated, usage_truncated=usage_truncated
    )


@router.get("/traces/{trace_id}/diagnostics")
async def get_trace_diagnostics(
    request: Request,
    trace_id: Annotated[str, Path(pattern=TRACE_ID_PATTERN, max_length=32)],
    project: ProjectParam,
) -> JSONResponse:
    """Deterministic, metadata-only findings for one trace: observed facts, evidence spans,
    an unverified hypothesis, limitations and investigation steps per finding."""

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
    result = await run_in_threadpool(_diagnose, trace, span_rows, usage_rows)
    return JSONResponse(result, headers=NO_STORE)
