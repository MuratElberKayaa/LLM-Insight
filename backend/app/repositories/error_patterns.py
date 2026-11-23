"""Deterministic error patterns (Phase 10D, ADR-57). Read-only, metadata only.

Every span with status ERROR whose trace starts in the window is grouped by a fingerprint of
stored identifiers:

    (source, provider, model, tool_name, error_type)

- `source`: `inference` (an inference operation, ADR-13), `tool` (`execute_tool`), `agent`
  (`invoke_agent` / `create_agent`) or `other`;
- `provider`: `gen_ai.provider.name` for inference and agent spans, else NULL;
- `model`: the response model, else the requested model, for inference spans only;
- `tool_name`: `gen_ai.tool.name` for tool spans only;
- `error_type`: the stored `spans.error_type` (NULL when none was recorded).

No content, span name or free text enters a fingerprint, and nothing is clustered by
similarity: two errors share a pattern only when every field is equal. Bounded: `limit + 1`
patterns, most spans first, then by the fields.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Row, and_, case, func, literal, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import Session

from app.models import Span, Trace
from app.services.ingestion.attrmap import INFERENCE_OPERATIONS


def _text(key: str) -> Any:
    return Span.attributes[key].astext


def list_patterns(
    session: Session, project_id: int, started_from: datetime, started_to: datetime, limit: int
) -> tuple[list[Row[Any]], int]:
    op = _text("gen_ai.operation.name")
    source = case(
        (op.in_(sorted(INFERENCE_OPERATIONS)), literal("inference")),
        (op == "execute_tool", literal("tool")),
        (op.in_(["invoke_agent", "create_agent"]), literal("agent")),
        else_=literal("other"),
    ).label("source")
    provider = case(
        (
            op.in_([*sorted(INFERENCE_OPERATIONS), "invoke_agent", "create_agent"]),
            _text("gen_ai.provider.name"),
        ),
        else_=None,
    ).label("provider")
    model = case(
        (
            op.in_(sorted(INFERENCE_OPERATIONS)),
            func.coalesce(_text("gen_ai.response.model"), _text("gen_ai.request.model")),
        ),
        else_=None,
    ).label("model")
    tool = case((op == "execute_tool", _text("gen_ai.tool.name")), else_=None).label("tool_name")
    errors = (
        select(
            source,
            provider,
            model,
            tool,
            Span.error_type.label("error_type"),
            Span.trace_id,
            Trace.started_at,
        )
        .join(Trace, and_(Trace.project_id == Span.project_id, Trace.trace_id == Span.trace_id))
        .where(
            Span.project_id == project_id,
            Span.status == "ERROR",
            Trace.started_at >= started_from,
            Trace.started_at < started_to,
        )
        .subquery()
    )
    keys = (
        errors.c.source,
        errors.c.provider,
        errors.c.model,
        errors.c.tool_name,
        errors.c.error_type,
    )
    stmt = (
        select(
            *keys,
            func.count().label("spans"),
            func.count(func.distinct(errors.c.trace_id)).label("traces"),
            func.min(errors.c.started_at).label("first_seen"),
            func.max(errors.c.started_at).label("last_seen"),
            # array_agg(trace_id ORDER BY started_at DESC, trace_id DESC): newest first
            func.array_agg(
                aggregate_order_by(
                    errors.c.trace_id, errors.c.started_at.desc(), errors.c.trace_id.desc()
                )
            ).label("trace_ids"),
        )
        .group_by(*keys)
        .order_by(func.count().desc(), *(k.asc().nulls_first() for k in keys))
        .limit(limit + 1)
    )
    total = select(func.count()).select_from(errors)
    return list(session.execute(stmt)), int(session.execute(total).scalar_one())
