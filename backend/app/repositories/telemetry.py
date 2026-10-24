"""Idempotent trace/span/usage writes for one ingest batch (TELEMETRY_CONTRACT.md §1.4, §4).

Runs inside the caller's transaction; the caller commits. Order inside the transaction:
1. derived `traces` rows exist (INSERT ... ON CONFLICT DO NOTHING; the FK target of spans)
2. spans: INSERT ... ON CONFLICT DO NOTHING; first write wins, re-exports change nothing.
   A re-export whose content differs from the stored span is counted (never merged)
3. usage rows only for spans *inserted by this batch*, priced once against the stored
   `model_prices` (Phase 2B), then ON CONFLICT DO NOTHING on both unique keys; a skipped
   row is a call-identity collision (e.g. double instrumentation). Stored usage rows are
   never updated, so their cost never changes after the first write
4. every touched trace is recomputed from all of its stored spans

Rows are written in sorted key order so concurrent batches lock rows in the same order.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Span, Trace, UsageRecord
from app.repositories import pricing
from app.services.ingestion import attrmap
from app.services.ingestion.normalize import NormalizedSpan
from app.services.ingestion.usage import ns_to_datetime, price_model_keys, price_rows, usage_row

# Everything a span stores from the payload. Two payloads with the same
# (project_id, trace_id, span_id) and equal values here are one span exported twice.
_CONTENT_FIELDS = (
    "parent_span_id",
    "name",
    "kind",
    "start_ns",
    "end_ns",
    "status",
    "error_type",
    "scope_name",
    "scope_version",
    "resource_attributes",
    "attributes",
    "provenance",
    "server_dropped_attributes_count",
)


@dataclass(frozen=True)
class PersistResult:
    spans_received: int
    spans_inserted: int
    usage_inserted: int
    usage_key_collisions: int
    traces_touched: int
    spans_reexported: int = 0  # span identity already stored: nothing written
    span_content_conflicts: int = 0  # ... and the payload differed (first write kept)


def _content(s: NormalizedSpan) -> tuple[Any, ...]:
    return tuple(getattr(s, f) for f in _CONTENT_FIELDS)


def _dedupe(spans: Sequence[NormalizedSpan]) -> tuple[list[NormalizedSpan], int]:
    """First occurrence of a (trace_id, span_id) in the batch wins, like a later re-export.
    Also returns how many later occurrences carried different content."""
    seen: dict[tuple[str, str], NormalizedSpan] = {}
    differing = 0
    for s in spans:
        first = seen.setdefault((s.trace_id, s.span_id), s)
        if first is not s and _content(first) != _content(s):
            differing += 1
    return [seen[k] for k in sorted(seen)], differing


def _differing_reexports(
    session: Session, project_id: int, reexported: list[NormalizedSpan]
) -> int:
    """Compare re-exported spans with the stored rows (one SELECT, only when a conflict
    happened). The stored row is never changed."""
    if not reexported:
        return 0
    stmt = select(Span.trace_id, Span.span_id, *(getattr(Span, f) for f in _CONTENT_FIELDS)).where(
        Span.project_id == project_id,
        tuple_(Span.trace_id, Span.span_id).in_([(s.trace_id, s.span_id) for s in reexported]),
    )
    stored = {(r[0], r[1]): tuple(r[2:]) for r in session.execute(stmt)}
    return sum(
        1
        for s in reexported
        if (s.trace_id, s.span_id) in stored and stored[(s.trace_id, s.span_id)] != _content(s)
    )


def _ensure_traces(session: Session, project_id: int, spans: list[NormalizedSpan]) -> list[str]:
    bounds: dict[str, list[int]] = {}
    for s in spans:
        b = bounds.setdefault(s.trace_id, [s.start_ns, s.end_ns, 0])
        b[0], b[1], b[2] = min(b[0], s.start_ns), max(b[1], s.end_ns), b[2] + 1
    trace_ids = sorted(bounds)
    # Provisional values satisfy the table checks; step 4 overwrites them before COMMIT.
    rows = [
        {
            "project_id": project_id,
            "trace_id": tid,
            "start_ns": start,
            "end_ns": end,
            "started_at": ns_to_datetime(start),
            "ended_at": ns_to_datetime(end),
            "status": "ok",
            "span_count": count,
        }
        for tid, (start, end, count) in ((t, bounds[t]) for t in trace_ids)
    ]
    session.execute(insert(Trace).on_conflict_do_nothing(), rows)
    return trace_ids


def _span_values(project_id: int, s: NormalizedSpan) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "trace_id": s.trace_id,
        "span_id": s.span_id,
        "parent_span_id": s.parent_span_id,
        "name": s.name,
        "kind": s.kind,
        "start_ns": s.start_ns,
        "end_ns": s.end_ns,
        "status": s.status,
        # spans.status_message is never written by ingest (R21): it stays NULL.
        "error_type": s.error_type,
        "scope_name": s.scope_name,
        "scope_version": s.scope_version,
        "resource_attributes": s.resource_attributes,
        "attributes": s.attributes,
        "attributes_schema_version": attrmap.SCHEMA_VERSION,
        "provenance": s.provenance,
        "server_dropped_attributes_count": s.server_dropped_attributes_count,
    }


def _insert_spans(
    session: Session, project_id: int, spans: list[NormalizedSpan]
) -> set[tuple[str, str]]:
    stmt = (
        insert(Span)
        .on_conflict_do_nothing(index_elements=["project_id", "trace_id", "span_id"])
        .returning(Span.trace_id, Span.span_id)
    )
    result = session.execute(stmt, [_span_values(project_id, s) for s in spans])
    return {(r.trace_id, r.span_id) for r in result}


def _insert_usage(session: Session, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    # No conflict target: both UNIQUE(project_id, canonical_call_key) and
    # UNIQUE(project_id, trace_id, span_id) resolve to DO NOTHING.
    stmt = insert(UsageRecord).on_conflict_do_nothing().returning(UsageRecord.id)
    return len(session.execute(stmt, rows).all())


# Derived trace summary (ARCHITECTURE.md §4.4, TELEMETRY_CONTRACT.md §5). Constant SQL with
# bound parameters only. Timestamps are converted exactly: whole seconds via to_timestamp,
# the microsecond remainder as an interval.
_RECOMPUTE = text(
    """
    UPDATE traces AS t SET
        service_name = agg.service_name,
        start_ns = agg.start_ns,
        end_ns = agg.end_ns,
        started_at = to_timestamp(agg.start_ns / 1000000000)
            + ((agg.start_ns % 1000000000) / 1000) * interval '1 microsecond',
        ended_at = to_timestamp(agg.end_ns / 1000000000)
            + ((agg.end_ns % 1000000000) / 1000) * interval '1 microsecond',
        status = CASE WHEN agg.has_error THEN 'error' ELSE 'ok' END,
        prompt_version = CASE
            WHEN agg.n_inference > 0 AND agg.n_inference_without_pv = 0 AND agg.n_pv = 1
            THEN agg.pv END,
        provenance = CASE
            WHEN agg.n_inference = 0 THEN 'unknown'
            -- all inference spans unknown: uniform, so not "mixed" (§5 table is silent here)
            WHEN agg.n_real = 0 AND agg.n_synthetic = 0 THEN 'unknown'
            WHEN agg.n_real = agg.n_inference THEN 'real'
            WHEN agg.n_synthetic = agg.n_inference THEN 'synthetic'
            ELSE 'mixed' END,
        span_count = agg.span_count,
        updated_at = now()
    FROM (
        SELECT
            s.trace_id,
            min(s.start_ns) AS start_ns,
            max(s.end_ns) AS end_ns,
            count(*) AS span_count,
            bool_or(s.status = 'ERROR') AS has_error,
            -- root span's resource (earliest root), else the earliest span's
            coalesce(
                (array_agg(nullif(s.resource_attributes ->> 'service.name', '')
                    ORDER BY (s.parent_span_id IS NOT NULL), s.start_ns, s.span_id))[1],
                'unknown_service') AS service_name,
            count(*) FILTER (WHERE s.inference) AS n_inference,
            count(*) FILTER (WHERE s.inference AND s.provenance = 'real') AS n_real,
            count(*) FILTER (WHERE s.inference AND s.provenance = 'synthetic') AS n_synthetic,
            count(*) FILTER (WHERE s.inference AND s.pv IS NULL) AS n_inference_without_pv,
            count(DISTINCT s.pv) FILTER (WHERE s.inference) AS n_pv,
            min(s.pv) FILTER (WHERE s.inference) AS pv
        FROM (
            SELECT trace_id, span_id, parent_span_id, start_ns, end_ns, status, provenance,
                   resource_attributes,
                   attributes ->> 'gen_ai.operation.name' = ANY(:ops) AS inference,
                   attributes ->> 'gen_ai.prompt.version' AS pv
            FROM spans
            WHERE project_id = :project_id AND trace_id = ANY(:trace_ids)
        ) AS s
        GROUP BY s.trace_id
    ) AS agg
    WHERE t.project_id = :project_id AND t.trace_id = agg.trace_id
    """
)


def _recompute_traces(session: Session, project_id: int, trace_ids: list[str]) -> None:
    session.execute(
        _RECOMPUTE,
        {
            "project_id": project_id,
            "trace_ids": trace_ids,
            "ops": sorted(attrmap.INFERENCE_OPERATIONS),
        },
    )


def persist_batch(
    session: Session, project_id: int, spans: Sequence[NormalizedSpan]
) -> PersistResult:
    unique, differing_in_batch = _dedupe(spans)
    trace_ids = _ensure_traces(session, project_id, unique)
    inserted = _insert_spans(session, project_id, unique)
    reexported = [s for s in unique if (s.trace_id, s.span_id) not in inserted]
    differing = differing_in_batch + _differing_reexports(session, project_id, reexported)
    candidates = [
        row
        for s in unique
        if (s.trace_id, s.span_id) in inserted and (row := usage_row(project_id, s)) is not None
    ]
    if candidates:
        prices = pricing.load_prices(session, price_model_keys(candidates))
        price_rows(candidates, {(s.trace_id, s.span_id): s for s in unique}, prices)
    usage_inserted = _insert_usage(session, candidates)
    _recompute_traces(session, project_id, trace_ids)
    return PersistResult(
        spans_received=len(spans),
        spans_inserted=len(inserted),
        usage_inserted=usage_inserted,
        usage_key_collisions=len(candidates) - usage_inserted,
        traces_touched=len(trace_ids),
        spans_reexported=len(reexported),
        span_content_conflicts=differing,
    )
