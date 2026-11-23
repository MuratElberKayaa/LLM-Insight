"""Read-only session aggregates (Phase 10C, ADR-56).

A session is the pseudonymous `llminsight.session.hash` stored on spans (never a raw ID). A trace
belongs to every session that one of its spans names. The window is on the trace's event time,
like the trace list. Usage rows of the session's traces give attempts, tokens and cost; unknown
cost stays unknown and is counted.

Bounded: `limit + 1` sessions, most traces first, then by hash, so truncation is visible. There
is no index on the JSONB key (no migration in this phase); the project and window predicates use
the existing indexes, and the scan of span attributes is limited to that project.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Row, and_, func, select
from sqlalchemy.orm import Session

from app.models import Span, Trace, UsageRecord
from app.services.ingestion.sessions import HASH_KEY


def list_sessions(
    session: Session, project_id: int, started_from: datetime, started_to: datetime, limit: int
) -> list[Row[Any]]:
    s, t, u = Span, Trace, UsageRecord
    count = func.count()
    tagged = (
        select(s.trace_id.label("trace_id"), s.attributes[HASH_KEY].astext.label("session"))
        .where(s.project_id == project_id, s.attributes.has_key(HASH_KEY))
        .distinct()
        .subquery()
    )
    in_window = (
        select(tagged.c.session, t.trace_id, t.status, t.started_at)
        .join(t, and_(t.project_id == project_id, t.trace_id == tagged.c.trace_id))
        .where(t.started_at >= started_from, t.started_at < started_to)
        .subquery()
    )
    traces = (
        select(
            in_window.c.session,
            count.label("traces"),
            count.filter(in_window.c.status == "error").label(
                "error_traces"
            ),  # traces.status is lowercase
            func.min(in_window.c.started_at).label("first_seen"),
            func.max(in_window.c.started_at).label("last_seen"),
        )
        .group_by(in_window.c.session)
        .subquery()
    )
    usage = (
        select(
            in_window.c.session,
            count.label("attempts"),
            func.sum(u.input_tokens).label("input_tokens"),
            count.filter(u.input_tokens.is_(None)).label("unknown_input_tokens"),
            func.sum(u.output_tokens).label("output_tokens"),
            count.filter(u.output_tokens.is_(None)).label("unknown_output_tokens"),
            func.sum(u.cost_usd).filter(u.cost_status == "computed").label("known_cost_usd"),
            count.filter(u.cost_status == "computed").label("computed_cost_calls"),
            count.filter(u.cost_status == "unknown").label("unknown_cost_calls"),
        )
        .join(u, and_(u.project_id == project_id, u.trace_id == in_window.c.trace_id))
        .group_by(in_window.c.session)
        .subquery()
    )
    stmt = (
        select(
            traces,
            func.coalesce(usage.c.attempts, 0).label("attempts"),
            usage.c.input_tokens,
            func.coalesce(usage.c.unknown_input_tokens, 0).label("unknown_input_tokens"),
            usage.c.output_tokens,
            func.coalesce(usage.c.unknown_output_tokens, 0).label("unknown_output_tokens"),
            usage.c.known_cost_usd,
            func.coalesce(usage.c.computed_cost_calls, 0).label("computed_cost_calls"),
            func.coalesce(usage.c.unknown_cost_calls, 0).label("unknown_cost_calls"),
        )
        .outerjoin(usage, usage.c.session == traces.c.session)
        .order_by(traces.c.traces.desc(), traces.c.session.asc())
        .limit(limit + 1)
    )
    return list(session.execute(stmt))
