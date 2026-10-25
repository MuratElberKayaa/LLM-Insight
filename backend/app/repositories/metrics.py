"""Read-only aggregate queries for M01–M15 (METRICS_CATALOG.md; Phase 2C).

Every query is filtered by `project_id` first, windowed on **event time** (`[from, to)` on
`usage_records.started_at` or `traces.started_at`, never ingest time) and built from
SQLAlchemy expressions with bound parameters. Index use:
- usage-level metrics: `ix_usage_records_project_id_started_at` for the window; the span of
  each usage row through `uq_spans_project_id_trace_id_span_id` (status, duration, prompt)
- trace-level metrics: `ix_traces_project_id_started_at`; root spans through
  `ix_spans_project_id_trace_id_start_ns`
- the `model` filter on traces: `uq_usage_records_project_id_trace_id_span_id`

Per-provenance buckets and the exact total come from one statement each:
`GROUP BY ROLLUP(provenance)` yields one row per bucket plus a total row (`is_total`). The
total is computed by PostgreSQL over all rows, so distinct counts and percentiles are exact,
never a sum of per-bucket values.

Percentiles are PostgreSQL `percentile_cont` (linear interpolation) over `duration_ns`.
Results that list groups (error types, price versions, model groups) are bounded.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import (
    ColumnElement,
    DateTime,
    Interval,
    Row,
    Select,
    and_,
    exists,
    false,
    func,
    literal,
    or_,
    select,
)
from sqlalchemy.orm import Session, aliased

from app.models import ModelPrice, Span, Trace, UsageRecord

PERCENTILES = (0.5, 0.95, 0.99)
MAX_ERROR_TYPES = 50
MAX_PRICE_VERSIONS = 200
EPOCH_ORIGIN = datetime(1970, 1, 1, tzinfo=UTC)  # trend buckets are aligned to UTC

USAGE_PROVENANCES = ("real", "synthetic", "unknown")
TRACE_PROVENANCES = ("real", "synthetic", "mixed", "unknown")
COST_UNKNOWN_REASONS = ("price_missing", "usage_missing", "usage_inconsistent")
USAGE_SOURCES = ("provider_reported", "estimated", "unknown")
PROMPT_VERSION_KEY = "gen_ai.prompt.version"


@dataclass(frozen=True)
class MetricsFilter:
    project_id: int
    started_from: datetime  # inclusive, event time
    started_to: datetime  # exclusive, event time
    model: str | None = None  # response_model or requested_model, exact
    status: str | None = None  # "ok" | "error", applied to each metric's own unit
    prompt_version: str | None = None
    provenance: str | None = None  # applied at each metric's own provenance level


def _span_of_usage():
    """The inference span of a usage row (one per row, by the spans unique key)."""
    u = UsageRecord
    return and_(
        Span.project_id == u.project_id, Span.trace_id == u.trace_id, Span.span_id == u.span_id
    )


def prompt_version_of_span() -> ColumnElement[Any]:
    return Span.attributes[PROMPT_VERSION_KEY].astext


def _usage_conditions(f: MetricsFilter) -> list[ColumnElement[bool]]:
    u = UsageRecord
    conds: list[ColumnElement[bool]] = [
        u.project_id == f.project_id,
        u.started_at >= f.started_from,
        u.started_at < f.started_to,
    ]
    if f.model is not None:
        conds.append(or_(u.response_model == f.model, u.requested_model == f.model))
    if f.status == "error":
        conds.append(Span.status == "ERROR")
    elif f.status == "ok":
        conds.append(Span.status != "ERROR")  # UNSET and OK: the attempt did not fail
    if f.prompt_version is not None:
        conds.append(prompt_version_of_span() == f.prompt_version)
    if f.provenance is not None:
        # `mixed` exists only for traces: no usage row (one physical attempt) is mixed.
        conds.append(u.provenance == f.provenance if f.provenance != "mixed" else false())
    return conds


def _trace_conditions(f: MetricsFilter) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = [
        Trace.project_id == f.project_id,
        Trace.started_at >= f.started_from,
        Trace.started_at < f.started_to,
    ]
    if f.status is not None:
        conds.append(Trace.status == f.status)
    if f.prompt_version is not None:
        conds.append(Trace.prompt_version == f.prompt_version)
    if f.provenance is not None:
        conds.append(Trace.provenance == f.provenance)
    if f.model is not None:
        u = aliased(UsageRecord)
        conds.append(
            exists().where(
                u.project_id == Trace.project_id,
                u.trace_id == Trace.trace_id,
                or_(u.response_model == f.model, u.requested_model == f.model),
            )
        )
    return conds


def _percentiles(column: Any) -> list[Any]:
    return [
        func.percentile_cont(p).within_group(column).label(f"p{round(p * 100)}")
        for p in PERCENTILES
    ]


# ------------------------------------------------ usage level (M02–M04, M06, M08–M12, M14)
def _token_columns() -> list[Any]:
    """M08 sums (NULL skipped, unknown counted) and the M09 subsets with their reporters."""
    u = UsageRecord
    count = func.count()
    return [
        func.sum(u.input_tokens).label("input_tokens"),
        count.filter(u.input_tokens.is_(None)).label("unknown_input_tokens"),
        func.sum(u.output_tokens).label("output_tokens"),
        count.filter(u.output_tokens.is_(None)).label("unknown_output_tokens"),
        func.sum(u.cached_input_tokens).label("cached_input_tokens"),
        count.filter(u.cached_input_tokens.is_not(None)).label("calls_reporting_cached"),
        func.sum(u.cache_write_input_tokens).label("cache_write_input_tokens"),
        count.filter(u.cache_write_input_tokens.is_not(None)).label("calls_reporting_cache_write"),
        func.sum(u.reasoning_output_tokens).label("reasoning_output_tokens"),
        count.filter(u.reasoning_output_tokens.is_not(None)).label("calls_reporting_reasoning"),
    ]


def _usage_source_columns() -> list[Any]:
    count = func.count()
    return [count.filter(UsageRecord.usage_source == s).label(f"source_{s}") for s in USAGE_SOURCES]


def usage_summary_stmt(f: MetricsFilter) -> Select[Any]:
    u = UsageRecord
    count = func.count()
    columns = [
        func.grouping(u.provenance).label("is_total"),
        u.provenance,
        func.count(func.distinct(u.canonical_call_key)).label("attempts"),  # M02
        count.filter(Span.status == "ERROR").label("failed"),  # M04
        func.count(func.distinct(u.call_id)).label("logical_calls"),  # M03
        count.filter(u.call_id.is_(None)).label("attempts_without_call_id"),
        *_token_columns(),  # M08, M09
        func.sum(u.cost_usd).filter(u.cost_status == "computed").label("known_cost_usd"),  # M10
        count.filter(u.cost_status == "computed").label("computed_cost_calls"),
        count.filter(u.cost_status == "unknown").label("unknown_cost_calls"),  # M11
        *[
            count.filter(u.cost_unknown_reason == r).label(f"unknown_{r}")
            for r in COST_UNKNOWN_REASONS
        ],
        *_usage_source_columns(),  # M14
        *_percentiles(Span.duration_ns),  # M06
    ]
    return (
        select(*columns)
        .select_from(u)
        .join(Span, _span_of_usage())
        .where(*_usage_conditions(f))
        .group_by(func.rollup(u.provenance))
    )


def usage_summary(session: Session, f: MetricsFilter) -> list[Row[Any]]:
    return list(session.execute(usage_summary_stmt(f)))


def error_types(session: Session, f: MetricsFilter) -> list[Row[Any]]:
    """M04 breakdown: failed attempts per `error_type` (NULL = not reported), largest first."""
    u = UsageRecord
    stmt = (
        select(Span.error_type, func.count().label("count"))
        .select_from(u)
        .join(Span, _span_of_usage())
        .where(*_usage_conditions(f), Span.status == "ERROR")
        .group_by(Span.error_type)
        .order_by(func.count().desc(), Span.error_type.asc().nulls_last())
        .limit(MAX_ERROR_TYPES + 1)
    )
    return list(session.execute(stmt))


def price_versions(session: Session, f: MetricsFilter) -> list[Row[Any]]:
    """M13: the price versions applied per priced model (computed rows only, which always
    reference their exact `model_prices` row)."""
    u = UsageRecord
    stmt = (
        select(
            ModelPrice.provider,
            ModelPrice.model,
            u.price_version,
            func.count().label("calls"),
        )
        .select_from(u)
        .join(Span, _span_of_usage())
        .join(ModelPrice, ModelPrice.id == u.model_price_id)
        .where(*_usage_conditions(f), u.cost_status == "computed")
        .group_by(ModelPrice.provider, ModelPrice.model, u.price_version)
        .order_by(ModelPrice.provider, ModelPrice.model, u.price_version)
        .limit(MAX_PRICE_VERSIONS + 1)
    )
    return list(session.execute(stmt))


# ---------------------------------------------------------------- trace level (M01, M05, M07)
def _root_exists() -> ColumnElement[bool]:
    root = aliased(Span)
    return exists().where(
        root.project_id == Trace.project_id,
        root.trace_id == Trace.trace_id,
        root.parent_span_id.is_(None),
    )


def trace_summary(session: Session, f: MetricsFilter) -> list[Row[Any]]:
    """M01 and M05 per trace provenance, plus the traces that have no stored root span."""
    count = func.count()
    stmt = (
        select(
            func.grouping(Trace.provenance).label("is_total"),
            Trace.provenance,
            func.count(func.distinct(Trace.trace_id)).label("traces"),  # M01
            count.filter(Trace.status == "error").label("error_traces"),  # M05
            count.filter(~_root_exists()).label("traces_without_root"),  # M07 exclusions
        )
        .where(*_trace_conditions(f))
        .group_by(func.rollup(Trace.provenance))
    )
    return list(session.execute(stmt))


def root_latency(session: Session, f: MetricsFilter) -> list[Row[Any]]:
    """M07: percentiles of root-span durations (no parent) of the traces in the window.
    Orphans and other child spans are excluded; a trace with several roots contributes
    each root."""
    stmt = (
        select(
            func.grouping(Trace.provenance).label("is_total"),
            Trace.provenance,
            func.count().label("root_spans"),
            *_percentiles(Span.duration_ns),
        )
        .select_from(Trace)
        .join(
            Span,
            and_(
                Span.project_id == Trace.project_id,
                Span.trace_id == Trace.trace_id,
                Span.parent_span_id.is_(None),
            ),
        )
        .where(*_trace_conditions(f))
        .group_by(func.rollup(Trace.provenance))
    )
    return list(session.execute(stmt))


# ------------------------------------------------ trend (the same metrics per time bucket)
def _bucket(column: Any, width: timedelta) -> Any:
    return func.date_bin(
        literal(width, Interval()), column, literal(EPOCH_ORIGIN, DateTime(timezone=True))
    ).label("bucket")


def usage_trend(session: Session, f: MetricsFilter, width: timedelta) -> list[Row[Any]]:
    u = UsageRecord
    bucket = _bucket(u.started_at, width)
    count = func.count()
    stmt = (
        select(
            bucket,
            func.count(func.distinct(u.canonical_call_key)).label("attempts"),
            count.filter(Span.status == "ERROR").label("failed"),
            func.sum(u.cost_usd).filter(u.cost_status == "computed").label("known_cost_usd"),
            count.filter(u.cost_status == "unknown").label("unknown_cost_calls"),
        )
        .select_from(u)
        .join(Span, _span_of_usage())
        .where(*_usage_conditions(f))
        .group_by(bucket)
        .order_by(bucket)
    )
    return list(session.execute(stmt))


def trace_trend(session: Session, f: MetricsFilter, width: timedelta) -> list[Row[Any]]:
    bucket = _bucket(Trace.started_at, width)
    stmt = (
        select(
            bucket,
            func.count(func.distinct(Trace.trace_id)).label("traces"),
            func.count().filter(Trace.status == "error").label("error_traces"),
        )
        .where(*_trace_conditions(f))
        .group_by(bucket)
        .order_by(bucket)
    )
    return list(session.execute(stmt))


# ---------------------------------------------------------------- M15 (models/compare)
def model_groups(session: Session, f: MetricsFilter, limit: int) -> list[Row[Any]]:
    """M02, M04, M06, M08–M11 and M14 per (provider, requested_model, response_model,
    prompt_version), the largest groups first; `limit + 1` rows so truncation is visible.
    The M08/M09 token columns were added in Phase 3C (additive)."""
    u = UsageRecord
    count = func.count()
    prompt = prompt_version_of_span().label("prompt_version")
    keys = (u.provider, u.requested_model, u.response_model, prompt)
    stmt = (
        select(
            *keys,
            func.count(func.distinct(u.canonical_call_key)).label("attempts"),
            count.filter(Span.status == "ERROR").label("failed"),
            *_percentiles(Span.duration_ns),
            *_token_columns(),
            *_usage_source_columns(),
            func.sum(u.cost_usd).filter(u.cost_status == "computed").label("known_cost_usd"),
            count.filter(u.cost_status == "computed").label("computed_cost_calls"),
            count.filter(u.cost_status == "unknown").label("unknown_cost_calls"),
            *[
                count.filter(u.cost_unknown_reason == r).label(f"unknown_{r}")
                for r in COST_UNKNOWN_REASONS
            ],
            *[count.filter(u.provenance == p).label(f"prov_{p}") for p in USAGE_PROVENANCES],
        )
        .select_from(u)
        .join(Span, _span_of_usage())
        .where(*_usage_conditions(f))
        .group_by(*keys)
        .order_by(
            func.count(func.distinct(u.canonical_call_key)).desc(),
            *(k.asc().nulls_first() for k in keys),
        )
        .limit(limit + 1)
    )
    return list(session.execute(stmt))
