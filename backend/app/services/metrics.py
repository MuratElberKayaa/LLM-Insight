"""Aggregate metrics M01–M15 (METRICS_CATALOG.md), dashboard-agnostic (Phase 2C).

Output rules, shared by `/api/v1/overview` and `/api/v1/models/compare`:
- **Counts are true counts** (0 when nothing matched). **Values are `null` when unknown**:
  a rate with no denominator, a percentile with no sample, a token sum with no reported
  value, a known cost with no computed row. Unknown is never 0 (ADR-11, T18, T29).
- Every metric carries `total` plus `by_provenance` (`real` / `synthetic` / `unknown`, and
  `mixed` for trace-level metrics). Buckets with no rows are present with zero counts.
  `total` is computed by PostgreSQL over all rows, never as a sum of buckets.
- Money is a decimal string (USD) from `Decimal`, never a float. A known cost is always
  shown with the unknown-cost count, and `cost_complete` is true only when no row in the
  population has an unknown cost.
- Latency is in milliseconds from `duration_ns` (`percentile_cont`, linear), with the sample
  size `n` and a low-sample flag (p95: n < 20, p99: n < 100).
- Time to first chunk (M20) is not recorded by the MVP (no streaming; the attribute is not in
  `llminsight.attrmap.v1`), so it is reported as unavailable, never estimated.
- Provenance is asserted by the instrumentation: `provenance_verified` is always false.
"""

from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.repositories import metrics as repo
from app.repositories.metrics import (
    COST_UNKNOWN_REASONS,
    TRACE_PROVENANCES,
    USAGE_PROVENANCES,
    USAGE_SOURCES,
    MetricsFilter,
)

LOW_SAMPLE_MIN = {"p95": 20, "p99": 100}
RATE_DIGITS = 6
COST_QUANTUM = Decimal("1E-10")  # NUMERIC(20,10), the cost column's scale
HOURLY_TREND_MAX_WINDOW = timedelta(days=2)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

TTFC_UNAVAILABLE = {
    "available": False,
    "reason": "not_recorded",
    "detail": "streaming time-to-first-chunk is not captured by the MVP (M20, P1)",
}


# ---------------------------------------------------------------- small formatters
def money(v: Decimal | None) -> str | None:
    return None if v is None else format(v, "f")


def rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, RATE_DIGITS)


def as_int(v: Any) -> int | None:
    return None if v is None else int(v)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def mean_cost(total: Decimal | None, calls: int) -> str | None:
    """M12: known cost ÷ computed calls, rounded once, half-even, to the cost scale."""
    if total is None or calls == 0:
        return None
    return money((total / calls).quantize(COST_QUANTUM, rounding=ROUND_HALF_EVEN))


def latency(row: Any, n: int) -> dict[str, Any]:
    def ms(v: float | None) -> float | None:
        return None if v is None or n == 0 else round(v / 1_000_000, 6)

    return {
        "n": n,
        "p50_ms": ms(row.p50 if row is not None else None),
        "p95_ms": ms(row.p95 if row is not None else None),
        "p99_ms": ms(row.p99 if row is not None else None),
        "low_sample": {k: n < minimum for k, minimum in LOW_SAMPLE_MIN.items()},
    }


def _split(rows: list[Any], buckets: tuple[str, ...]) -> tuple[Any, dict[str, Any]]:
    """(total row, {bucket: row or None}) from a ROLLUP(provenance) result."""
    total = next((r for r in rows if r.is_total), None)
    by = {b: next((r for r in rows if not r.is_total and r.provenance == b), None) for b in buckets}
    return total, by


def _per_bucket(total: Any, by: dict[str, Any], build) -> dict[str, Any]:
    return {"total": build(total), "by_provenance": {b: build(r) for b, r in by.items()}}


# ---------------------------------------------------------------- usage-level metric blocks
def _n(r: Any) -> int:
    return 0 if r is None else int(r.attempts)


def _attempts(r: Any) -> int:  # M02
    return _n(r)


def _logical_calls(r: Any) -> dict[str, Any]:  # M03
    n = _n(r)
    distinct = 0 if r is None else int(r.logical_calls)
    return {
        "value": distinct if distinct > 0 else None,  # "n/a" when no attempt carries call_id
        "attempts_without_call_id": 0 if r is None else int(r.attempts_without_call_id),
        "n": n,
    }


def _errors(r: Any) -> dict[str, Any]:  # M04
    n = _n(r)
    failed = 0 if r is None else int(r.failed)
    return {"n": n, "failed": failed, "succeeded": n - failed, "rate": rate(failed, n)}


def _tokens(r: Any) -> dict[str, Any]:  # M08, M09
    def subset(total: str, reporting: str) -> dict[str, Any]:
        return {
            "sum": None if r is None else as_int(getattr(r, total)),
            "calls_reporting": 0 if r is None else int(getattr(r, reporting)),
        }

    return {
        "n": _n(r),
        "input_tokens": None if r is None else as_int(r.input_tokens),
        "calls_with_unknown_input_tokens": 0 if r is None else int(r.unknown_input_tokens),
        "output_tokens": None if r is None else as_int(r.output_tokens),
        "calls_with_unknown_output_tokens": 0 if r is None else int(r.unknown_output_tokens),
        # Subsets of the totals above (never added to them): cache reads and writes are
        # inside input_tokens, reasoning is inside output_tokens.
        "subsets": {
            "cached_input_tokens": subset("cached_input_tokens", "calls_reporting_cached"),
            "cache_write_input_tokens": subset(
                "cache_write_input_tokens", "calls_reporting_cache_write"
            ),
            "reasoning_output_tokens": subset(
                "reasoning_output_tokens", "calls_reporting_reasoning"
            ),
        },
    }


def _cost(r: Any) -> dict[str, Any]:  # M10, M11, M12
    n = _n(r)
    computed = 0 if r is None else int(r.computed_cost_calls)
    unknown = 0 if r is None else int(r.unknown_cost_calls)
    known = None if r is None else r.known_cost_usd
    return {
        "n": n,
        "currency": "USD",
        "known_cost_usd": money(known),
        "computed_cost_calls": computed,
        "unknown_cost_calls": unknown,
        "unknown_cost_by_reason": {
            reason: 0 if r is None else int(getattr(r, f"unknown_{reason}"))
            for reason in COST_UNKNOWN_REASONS
        },
        "cost_complete": None if n == 0 else unknown == 0,
        "known_cost_per_call_usd": mean_cost(known, computed),
    }


def _usage_sources(r: Any) -> dict[str, int]:  # M14
    return {s: 0 if r is None else int(getattr(r, f"source_{s}")) for s in USAGE_SOURCES}


# ---------------------------------------------------------------- trace-level metric blocks
def _traces(r: Any) -> int:  # M01
    return 0 if r is None else int(r.traces)


def _trace_errors(r: Any) -> dict[str, Any]:  # M05
    n = _traces(r)
    errors = 0 if r is None else int(r.error_traces)
    return {"n": n, "error_traces": errors, "rate": rate(errors, n)}


# ---------------------------------------------------------------- trend
def trend_width(start: datetime, end: datetime) -> timedelta:
    return timedelta(hours=1) if end - start <= HOURLY_TREND_MAX_WINDOW else timedelta(days=1)


def _bucket_starts(start: datetime, end: datetime, width: timedelta) -> list[datetime]:
    first = EPOCH + ((start - EPOCH) // width) * width  # aligned like date_bin(width, t, epoch)
    out, b = [], first
    while b < end:
        out.append(b)
        b += width
    return out


def trend(session: Session, f: MetricsFilter) -> dict[str, Any]:
    width = trend_width(f.started_from, f.started_to)
    usage = {r.bucket: r for r in repo.usage_trend(session, f, width)}
    traces = {r.bucket: r for r in repo.trace_trend(session, f, width)}
    items = []
    for b in _bucket_starts(f.started_from, f.started_to, width):
        u, t = usage.get(b), traces.get(b)
        items.append(
            {
                "start": iso(b),
                "traces": 0 if t is None else int(t.traces),
                "error_traces": 0 if t is None else int(t.error_traces),
                "inference_attempts": 0 if u is None else int(u.attempts),
                "failed_attempts": 0 if u is None else int(u.failed),
                "known_cost_usd": None if u is None else money(u.known_cost_usd),
                "unknown_cost_calls": 0 if u is None else int(u.unknown_cost_calls),
            }
        )
    return {"bucket": "hour" if width == timedelta(hours=1) else "day", "items": items}


# ---------------------------------------------------------------- endpoints' payloads
def filters_echo(f: MetricsFilter) -> dict[str, Any]:
    return {
        "model": f.model,
        "status": f.status,
        "prompt_version": f.prompt_version,
        "provenance": f.provenance,
    }


def provenance_label(f: MetricsFilter) -> str:
    """Headline label (METRICS_CATALOG.md rule 4): never "real" unless filtered to it."""
    return "all provenance" if f.provenance is None else f"{f.provenance} only"


def overview(session: Session, f: MetricsFilter) -> dict[str, Any]:
    u_total, u_by = _split(repo.usage_summary(session, f), USAGE_PROVENANCES)
    t_total, t_by = _split(repo.trace_summary(session, f), TRACE_PROVENANCES)
    l_total, l_by = _split(repo.root_latency(session, f), TRACE_PROVENANCES)
    error_type_rows = repo.error_types(session, f)
    price_rows = repo.price_versions(session, f)

    def root_latency(r: Any) -> dict[str, Any]:
        return latency(r, 0 if r is None else int(r.root_spans))

    return {
        "provenance_label": provenance_label(f),
        "provenance_verified": False,
        "traces": {
            "count": _per_bucket(t_total, t_by, _traces),  # M01
            "error_rate": _per_bucket(t_total, t_by, _trace_errors),  # M05
            "latency": {  # M07
                **_per_bucket(l_total, l_by, root_latency),
                "traces_without_root": 0 if t_total is None else int(t_total.traces_without_root),
            },
        },
        "inference": {
            "attempts": _per_bucket(u_total, u_by, _attempts),  # M02
            "logical_calls": _per_bucket(u_total, u_by, _logical_calls),  # M03
            "errors": {  # M04
                **_per_bucket(u_total, u_by, _errors),
                "by_error_type": [
                    {"error_type": r.error_type, "count": int(r.count)}
                    for r in error_type_rows[: repo.MAX_ERROR_TYPES]
                ],
                "by_error_type_truncated": len(error_type_rows) > repo.MAX_ERROR_TYPES,
            },
            "latency": _per_bucket(u_total, u_by, lambda r: latency(r, _n(r))),  # M06
            "time_to_first_chunk": TTFC_UNAVAILABLE,  # M20 (P1): not recorded
            "tokens": _per_bucket(u_total, u_by, _tokens),  # M08, M09
            "cost": _per_bucket(u_total, u_by, _cost),  # M10, M11, M12
            "price_versions": {  # M13
                "items": [
                    {
                        "provider": r.provider,
                        "model": r.model,
                        "price_version": r.price_version,
                        "calls": int(r.calls),
                    }
                    for r in price_rows[: repo.MAX_PRICE_VERSIONS]
                ],
                "truncated": len(price_rows) > repo.MAX_PRICE_VERSIONS,
            },
            "usage_source": _per_bucket(u_total, u_by, _usage_sources),  # M14
        },
        "trend": trend(session, f),
    }


def compare(session: Session, f: MetricsFilter, limit: int) -> dict[str, Any]:
    """M15: M02, M04, M06, M10, M11 per provider / requested model / response model /
    prompt version, largest groups first."""
    rows = repo.model_groups(session, f, limit)
    groups = []
    for r in rows[:limit]:
        n = int(r.attempts)
        failed = int(r.failed)
        unknown = int(r.unknown_cost_calls)
        groups.append(
            {
                "provider": r.provider,
                "requested_model": r.requested_model,
                "response_model": r.response_model,
                "prompt_version": r.prompt_version,
                "attempts": n,
                "errors": {"failed": failed, "succeeded": n - failed, "rate": rate(failed, n)},
                "latency": latency(r, n),
                "cost": {
                    "currency": "USD",
                    "known_cost_usd": money(r.known_cost_usd),
                    "computed_cost_calls": int(r.computed_cost_calls),
                    "unknown_cost_calls": unknown,
                    "unknown_cost_by_reason": {
                        reason: int(getattr(r, f"unknown_{reason}"))
                        for reason in COST_UNKNOWN_REASONS
                    },
                    "cost_complete": unknown == 0,
                },
                "by_provenance": {p: int(getattr(r, f"prov_{p}")) for p in USAGE_PROVENANCES},
            }
        )
    return {
        "provenance_label": provenance_label(f),
        "provenance_verified": False,
        "groups": groups,
        "groups_truncated": len(rows) > limit,
        "limit": limit,
    }
