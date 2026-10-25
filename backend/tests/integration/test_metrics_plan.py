"""Index use of the aggregate queries (Phase 2C), checked with EXPLAIN on generated rows.

Two projects × 60 days × 300 traces/day (SYNTHETIC rows inserted with SQL, not ingest): a
one-day window of one project is ~0.8 % of the rows, so a sequential scan would be the wrong
plan. The plan must reach the rows through the window indexes, and the span of each usage
row through the spans unique key.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories import metrics as repo

pytestmark = pytest.mark.integration

DAY0 = datetime(2026, 8, 1, tzinfo=UTC)
GENERATE = """
INSERT INTO projects (id, name) VALUES (1, 'plan-a'), (2, 'plan-b');
INSERT INTO traces (project_id, trace_id, service_name, start_ns, end_ns, started_at, ended_at,
                    status, provenance, span_count)
SELECT p, lpad(to_hex(p * 1000000 + i), 32, '0'), 'synthetic', s, s + 5000000,
       to_timestamp(s / 1e9), to_timestamp((s + 5000000) / 1e9), 'ok', 'synthetic', 2
FROM generate_series(1, 2) p, generate_series(1, 18000) i,
     LATERAL (SELECT (extract(epoch FROM timestamptz '2026-08-01Z')::bigint
                      + (i - 1) * 288) * 1000000000 AS s) t;
INSERT INTO spans (project_id, trace_id, span_id, parent_span_id, name, kind, start_ns, end_ns,
                   status, attributes, attributes_schema_version, provenance)
SELECT t.project_id, t.trace_id, lpad(to_hex(k), 16, '0'),
       CASE WHEN k = 1 THEN NULL ELSE lpad(to_hex(1), 16, '0') END,
       'synthetic', CASE WHEN k = 1 THEN 'SERVER' ELSE 'CLIENT' END, t.start_ns,
       t.start_ns + k * 1000000, 'UNSET',
       CASE WHEN k = 2 THEN '{"gen_ai.prompt.version": "v1"}'::jsonb ELSE '{}'::jsonb END,
       'llminsight.attrmap.v1', 'synthetic'
FROM traces t, generate_series(1, 2) k;
INSERT INTO usage_records (project_id, canonical_call_key, call_key_source, trace_id, span_id,
                           operation_name, provider, requested_model, input_tokens,
                           output_tokens, usage_source, cost_status, cost_unknown_reason,
                           provenance, started_at)
SELECT s.project_id, 'span:' || s.trace_id || ':' || s.span_id, 'span_fallback', s.trace_id,
       s.span_id, 'chat', 'synthetic.plan', 'synthetic-model', 10, 1, 'provider_reported',
       'unknown', 'price_missing', 'synthetic', to_timestamp(s.start_ns / 1e9)
FROM spans s WHERE s.kind = 'CLIENT';
ANALYZE projects; ANALYZE traces; ANALYZE spans; ANALYZE usage_records;
"""


def _plan(conn, stmt) -> str:
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    rows = conn.exec_driver_sql("EXPLAIN (FORMAT JSON) " + sql.replace("%", "%%")).scalar_one()
    return json.dumps(rows)


def _scans(plan: str) -> set[tuple[str, str, str]]:
    """(node type, relation, index) of every scan node in a JSON plan."""
    found: set[tuple[str, str, str]] = set()

    def walk(node):
        if "Relation Name" in node:
            found.add((node["Node Type"], node["Relation Name"], node.get("Index Name", "")))
        for child in node.get("Plans", []):
            walk(child)

    walk(json.loads(plan)[0]["Plan"])
    return found


@pytest.fixture
def generated(db):
    with db.begin() as c:
        c.exec_driver_sql(GENERATE)
    return db


def _window_filter(**kw) -> repo.MetricsFilter:
    start = DAY0 + timedelta(days=30)
    return repo.MetricsFilter(1, start, start + timedelta(days=1), **kw)


def test_usage_aggregates_use_the_event_time_index(generated):
    with generated.connect() as c:
        assert c.exec_driver_sql("SELECT count(*) FROM usage_records").scalar_one() == 36000
        plan = _plan(c, repo.usage_summary_stmt(_window_filter()))
    scans = _scans(plan)
    assert any(r == "usage_records" and i == "ix_usage_records_project_id_started_at"
               for _, r, i in scans), scans  # fmt: skip
    # each usage row's span is an index lookup on (project_id, trace_id, ...)
    assert any(r == "spans" and i.startswith(("uq_spans_", "ix_spans_")) for _, r, i in scans)
    assert not any(t == "Seq Scan" for t, _, _ in scans), scans


def test_trace_aggregates_use_the_trace_window_index(generated):
    from sqlalchemy import func, select

    from app.models import Trace

    f = _window_filter()
    stmt = (
        select(func.count())
        .select_from(Trace)
        .where(*repo._trace_conditions(f))  # the shared trace-level WHERE clause
    )
    with generated.connect() as c:
        plan = _plan(c, stmt)
    scans = _scans(plan)
    assert ("Index Only Scan", "traces", "ix_traces_project_id_started_at") in scans or any(
        r == "traces" and i == "ix_traces_project_id_started_at" for _, r, i in scans
    ), scans
    assert not any(t == "Seq Scan" for t, _, _ in scans), scans


def test_aggregate_results_on_the_generated_rows(generated):
    """One day = 300 traces per project; each has one inference attempt (k = 2)."""
    from sqlalchemy.orm import Session

    with Session(generated) as s:
        total = next(r for r in repo.usage_summary(s, _window_filter()) if r.is_total)
        traces = next(r for r in repo.trace_summary(s, _window_filter()) if r.is_total)
        v1 = next(
            r for r in repo.usage_summary(s, _window_filter(prompt_version="v1")) if r.is_total
        )
    assert total.attempts == 300 and traces.traces == 300 and traces.traces_without_root == 0
    assert v1.attempts == 300  # the inference span carries prompt v1
    assert total.p50 == 2_000_000.0  # every inference span lasts 2 ms
