"""Read-only trace queries for `/api/v1/traces` (ARCHITECTURE.md §5, §4.4–4.6).

Every query is filtered by `project_id` first and built from SQLAlchemy expressions with bound
parameters (SECURITY.md §3: no string-built SQL). Index use:
- list: `ix_traces_project_id_started_at` (project, started_at DESC) for the window, the
  keyset cursor and the order; ties on `started_at` are broken by `trace_id`, which is
  unique per project (and, unlike the surrogate `id`, says nothing about other projects)
- per-trace usage summary and the `model` filter: `uq_usage_records_project_id_trace_id_span_id`
- detail spans: `ix_spans_project_id_trace_id_start_ns`; parent lookup: the spans unique key

`spans.status_message` is never selected (R21).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, Select, exists, func, or_, select, text, true
from sqlalchemy.orm import Session, aliased

from app.models import Project, Span, Trace, UsageRecord

DETAIL_MAX_SPANS = 2000  # one trace can grow across batches; the detail response is bounded


@dataclass(frozen=True)
class TraceFilter:
    project_id: int
    started_from: datetime  # inclusive
    started_to: datetime  # exclusive
    status: str | None = None
    provenance: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class Cursor:
    """Position after the last returned item: (started_at, trace_id) of that item."""

    started_at: datetime
    trace_id: str


def begin_read_only(session: Session) -> None:
    """First statement of the transaction: a read-only snapshot. The read API cannot write,
    and the detail queries see one consistent state even while ingest commits."""
    session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))


def project_exists(session: Session, project_id: int) -> bool:
    return session.execute(select(exists().where(Project.id == project_id))).scalar_one()


def _summary_select() -> Select[Any]:
    """Trace columns plus a per-trace usage summary. Unknown stays unknown: token sums skip
    NULL (and are NULL when nothing is known), unknown-token and unknown-cost calls are
    counted, and the known cost is NULL when no row has a computed cost (ADR-11)."""
    u = UsageRecord
    usage = (
        select(
            func.count().label("inference_calls"),
            func.sum(u.input_tokens).label("input_tokens"),
            func.sum(u.output_tokens).label("output_tokens"),
            func.count()
            .filter(or_(u.input_tokens.is_(None), u.output_tokens.is_(None)))
            .label("calls_with_unknown_tokens"),
            func.sum(u.cost_usd).filter(u.cost_status == "computed").label("known_cost_usd"),
            func.count().filter(u.cost_status == "unknown").label("unknown_cost_calls"),
        )
        .where(u.project_id == Trace.project_id, u.trace_id == Trace.trace_id)
        .lateral("usage")
    )
    return (
        select(
            Trace.trace_id,
            Trace.service_name,
            Trace.start_ns,
            Trace.end_ns,
            Trace.started_at,
            Trace.status,
            Trace.prompt_version,
            Trace.provenance,
            Trace.span_count,
            usage.c.inference_calls,
            usage.c.input_tokens,
            usage.c.output_tokens,
            usage.c.calls_with_unknown_tokens,
            usage.c.known_cost_usd,
            usage.c.unknown_cost_calls,
        )
        .select_from(Trace)
        .join(usage, true(), isouter=True)
    )


def list_traces_stmt(f: TraceFilter, cursor: Cursor | None, limit: int) -> Select[Any]:
    """Newest first; fetches `limit + 1` rows so the caller knows whether a next page exists."""
    stmt = _summary_select().where(
        Trace.project_id == f.project_id,
        Trace.started_at >= f.started_from,
        Trace.started_at < f.started_to,
    )
    if f.status is not None:
        stmt = stmt.where(Trace.status == f.status)
    if f.provenance is not None:
        stmt = stmt.where(Trace.provenance == f.provenance)
    if f.model is not None:
        u = aliased(UsageRecord)
        stmt = stmt.where(
            exists().where(
                u.project_id == Trace.project_id,
                u.trace_id == Trace.trace_id,
                or_(u.response_model == f.model, u.requested_model == f.model),
            )
        )
    if cursor is not None:
        # Row comparison (started_at, trace_id) < cursor, written so the started_at bound
        # is an index condition.
        stmt = stmt.where(
            Trace.started_at <= cursor.started_at,
            or_(Trace.started_at < cursor.started_at, Trace.trace_id < cursor.trace_id),
        )
    return stmt.order_by(Trace.started_at.desc(), Trace.trace_id.desc()).limit(limit + 1)


def list_traces(
    session: Session, f: TraceFilter, cursor: Cursor | None, limit: int
) -> list[Row[Any]]:
    return list(session.execute(list_traces_stmt(f, cursor, limit)))


def get_trace(session: Session, project_id: int, trace_id: str) -> Row[Any] | None:
    stmt = _summary_select().where(Trace.project_id == project_id, Trace.trace_id == trace_id)
    return session.execute(stmt).one_or_none()


def get_spans(session: Session, project_id: int, trace_id: str) -> list[Row[Any]]:
    """Up to DETAIL_MAX_SPANS + 1 spans by start time. `parent_stored` is computed over all
    stored spans, so a truncated response still labels orphans correctly."""
    parent = aliased(Span)
    parent_stored = exists().where(
        parent.project_id == Span.project_id,
        parent.trace_id == Span.trace_id,
        parent.span_id == Span.parent_span_id,
    )
    stmt = (
        select(
            Span.span_id,
            Span.parent_span_id,
            Span.name,
            Span.kind,
            Span.start_ns,
            Span.end_ns,
            Span.duration_ns,
            Span.status,
            Span.error_type,
            Span.scope_name,
            Span.scope_version,
            Span.resource_attributes,
            Span.attributes,
            Span.attributes_schema_version,
            Span.provenance,
            Span.server_dropped_attributes_count,
            parent_stored.label("parent_stored"),
        )
        .where(Span.project_id == project_id, Span.trace_id == trace_id)
        .order_by(Span.start_ns, Span.span_id)
        .limit(DETAIL_MAX_SPANS + 1)
    )
    return list(session.execute(stmt))


def get_usage(session: Session, project_id: int, trace_id: str) -> list[Row[Any]]:
    u = UsageRecord
    stmt = (
        select(
            u.span_id,
            u.canonical_call_key,
            u.call_key_source,
            u.call_id,
            u.attempt_id,
            u.provider_response_id,
            u.operation_name,
            u.provider,
            u.requested_model,
            u.response_model,
            u.input_tokens,
            u.output_tokens,
            u.cached_input_tokens,
            u.cache_write_input_tokens,
            u.reasoning_output_tokens,
            u.usage_source,
            u.cost_usd,
            u.cost_status,
            u.cost_unknown_reason,
            u.price_version,
            u.provenance,
            u.started_at,
        )
        .where(u.project_id == project_id, u.trace_id == trace_id)
        .order_by(u.started_at, u.span_id)
        .limit(DETAIL_MAX_SPANS + 1)
    )
    return list(session.execute(stmt))
