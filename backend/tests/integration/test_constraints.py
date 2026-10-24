"""DB-level contract rules (ARCHITECTURE.md §4.1–4.6) enforced by real PostgreSQL."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, insert, select
from sqlalchemy.exc import IntegrityError

from app.models import Project, ProjectApiKey, Span, Trace, UsageRecord

pytestmark = pytest.mark.integration

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
ROOT_SPAN = "00f067aa0ba902b7"
CHILD_SPAN = "a3ce929d0e0e4736"
T0_NS = 1_761_300_000_000_000_000
T0 = datetime(2025, 10, 24, 10, 0, tzinfo=UTC)


def _project(conn, name="demo") -> int:
    return conn.execute(insert(Project).values(name=name).returning(Project.id)).scalar_one()


def _trace(conn, project_id, **kw):
    values = (
        dict(
            project_id=project_id,
            trace_id=TRACE_ID,
            start_ns=T0_NS,
            end_ns=T0_NS + 5_000_000,
            started_at=T0,
            ended_at=T0,
            status="ok",
            span_count=2,
        )
        | kw
    )
    conn.execute(insert(Trace).values(**values))


def _span(conn, project_id, **kw):
    values = (
        dict(
            project_id=project_id,
            trace_id=TRACE_ID,
            span_id=ROOT_SPAN,
            name="POST /v1/summarize",
            kind="SERVER",
            start_ns=T0_NS,
            end_ns=T0_NS + 5_000_000,
            status="UNSET",
            attributes_schema_version="llminsight.attrmap.v1",
        )
        | kw
    )
    conn.execute(insert(Span).values(**values))


def _usage_values(project_id, **kw):
    return (
        dict(
            project_id=project_id,
            canonical_call_key="resp:openai:chatcmpl-1",
            call_key_source="provider_response_id",
            trace_id=TRACE_ID,
            span_id=CHILD_SPAN,
            operation_name="chat",
            provider="openai",
            usage_source="provider_reported",
            cost_status="unknown",
            cost_unknown_reason="price_missing",
            provenance="synthetic",
            started_at=T0,
        )
        | kw
    )


@pytest.fixture
def graph(conn):
    """Project with one trace: root SERVER span + child CLIENT span."""
    pid = _project(conn)
    _trace(conn, pid)
    _span(conn, pid)
    _span(
        conn,
        pid,
        span_id=CHILD_SPAN,
        parent_span_id=ROOT_SPAN,
        name="chat gpt-4o-mini",
        kind="CLIENT",
        start_ns=T0_NS + 1_000_000,
        end_ns=T0_NS + 4_000_000,
    )
    return pid


def assert_rejected(conn, stmt, *constraints):
    savepoint = conn.begin_nested()
    with pytest.raises(IntegrityError) as exc:
        conn.execute(stmt)
    savepoint.rollback()
    assert exc.value.orig.diag.constraint_name in constraints


# ----------------------------------------------------------------- happy path


def test_valid_graph_defaults_and_generated_duration(conn, graph):
    spans = conn.execute(select(Span).order_by(Span.start_ns)).all()
    assert [s.span_id for s in spans] == [ROOT_SPAN, CHILD_SPAN]
    child = spans[1]
    assert child.duration_ns == 3_000_000
    assert child.parent_span_id == ROOT_SPAN
    assert child.attributes == {} and child.resource_attributes == {}
    assert child.provenance == "unknown"
    assert child.server_dropped_attributes_count == 0
    assert child.ingested_at.tzinfo is not None
    trace = conn.execute(select(Trace)).one()
    assert trace.service_name == "unknown_service"
    assert trace.provenance == "unknown"


def test_orphan_span_allowed(conn, graph):
    _span(conn, graph, span_id="1111111111111111", parent_span_id="2222222222222222")
    assert conn.execute(select(func.count()).select_from(Span)).scalar_one() == 3


def test_max_nanosecond_timestamp_fits_bigint(conn, graph):
    _span(conn, graph, span_id="3333333333333333", start_ns=2**63 - 2, end_ns=2**63 - 1)


def test_unknown_cost_stays_null_not_zero(conn, graph):
    conn.execute(insert(UsageRecord).values(**_usage_values(graph)))
    row = conn.execute(select(UsageRecord)).one()
    assert row.cost_usd is None
    assert row.cost_status == "unknown"
    assert row.input_tokens is None


@pytest.mark.parametrize("cost", [Decimal("0.0000012345"), Decimal("1234567890.0123456789")])
def test_computed_cost_decimal_round_trip_exact(conn, graph, cost):
    conn.execute(
        insert(UsageRecord).values(
            **_usage_values(
                graph,
                cost_status="computed",
                cost_unknown_reason=None,
                cost_usd=cost,
                price_version="openai-2026-10",
                model_price_id=1,
            )
        )
    )
    assert conn.execute(select(UsageRecord.cost_usd)).scalar_one() == cost


def test_same_ids_allowed_in_another_project(conn, graph):
    other = _project(conn, "other")
    _trace(conn, other)
    _span(conn, other)
    assert conn.execute(select(func.count()).select_from(Span)).scalar_one() == 3


def test_project_delete_cascades(conn, graph):
    conn.execute(
        insert(ProjectApiKey).values(project_id=graph, prefix="AbCd1234", key_hash="a" * 64)
    )
    conn.execute(insert(UsageRecord).values(**_usage_values(graph)))
    conn.execute(delete(Project).where(Project.id == graph))
    for model in (ProjectApiKey, Trace, Span, UsageRecord):
        assert conn.execute(select(func.count()).select_from(model)).scalar_one() == 0, model


# ----------------------------------------------------------------- projects / keys


def test_project_rules(conn):
    _project(conn, "dup")
    assert_rejected(conn, insert(Project).values(name="dup"), "uq_projects_name")
    assert_rejected(conn, insert(Project).values(name=""), "ck_projects_name_len")
    assert_rejected(conn, insert(Project).values(name="x" * 101), "ck_projects_name_len")


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"prefix": "short"}, "ck_project_api_keys_prefix_format"),
        ({"prefix": "abc_1234"}, "ck_project_api_keys_prefix_format"),
        ({"key_hash": "A" * 64}, "ck_project_api_keys_key_hash_hmac_sha256_hex"),
        ({"key_hash": "a" * 63}, "ck_project_api_keys_key_hash_hmac_sha256_hex"),
        (
            {"created_at": T0, "revoked_at": T0 - timedelta(seconds=1)},
            "ck_project_api_keys_revoked_after_created",
        ),
        ({"project_id": 999_999}, "fk_project_api_keys_projects"),
    ],
)
def test_api_key_rules(conn, overrides, constraint):
    pid = _project(conn)
    values = {"project_id": pid, "prefix": "AbCd1234", "key_hash": "0" * 64} | overrides
    assert_rejected(conn, insert(ProjectApiKey).values(**values), constraint)


def test_api_key_prefix_unique(conn):
    pid = _project(conn)
    conn.execute(insert(ProjectApiKey).values(project_id=pid, prefix="AbCd1234", key_hash="0" * 64))
    assert_rejected(
        conn,
        insert(ProjectApiKey).values(project_id=pid, prefix="AbCd1234", key_hash="1" * 64),
        "uq_project_api_keys_prefix",
    )


# ----------------------------------------------------------------- traces


@pytest.mark.parametrize(
    ("overrides", "constraints"),
    [
        ({"trace_id": TRACE_ID.upper()}, ["ck_traces_trace_id_hex32"]),
        ({"trace_id": "0" * 32}, ["ck_traces_trace_id_hex32"]),
        ({"trace_id": TRACE_ID[:-1]}, ["ck_traces_trace_id_hex32"]),
        ({"status": "OK"}, ["ck_traces_status_enum"]),
        ({"provenance": "fake"}, ["ck_traces_provenance_enum"]),
        ({"start_ns": 0, "end_ns": 1}, ["ck_traces_start_ns_positive"]),
        ({"end_ns": T0_NS - 1}, ["ck_traces_end_after_start"]),
        ({"span_count": 0}, ["ck_traces_span_count_positive"]),
        ({"service_name": ""}, ["ck_traces_service_name_len"]),
        ({"service_name": "s" * 257}, ["ck_traces_service_name_len"]),
        ({"project_id": 999_999}, ["fk_traces_projects"]),
    ],
)
def test_trace_rules(conn, overrides, constraints):
    pid = _project(conn)
    values = (
        dict(
            project_id=pid,
            trace_id=TRACE_ID,
            start_ns=T0_NS,
            end_ns=T0_NS,
            started_at=T0,
            ended_at=T0,
            status="ok",
            span_count=1,
        )
        | overrides
    )
    assert_rejected(conn, insert(Trace).values(**values), *constraints)


def test_trace_unique_per_project(conn, graph):
    stmt = insert(Trace).values(
        project_id=graph,
        trace_id=TRACE_ID,
        start_ns=T0_NS,
        end_ns=T0_NS,
        started_at=T0,
        ended_at=T0,
        status="ok",
        span_count=1,
    )
    assert_rejected(conn, stmt, "uq_traces_project_id_trace_id")


def test_mixed_provenance_valid_on_trace(conn):
    pid = _project(conn)
    _trace(conn, pid, provenance="mixed")


# ----------------------------------------------------------------- spans


@pytest.mark.parametrize(
    ("overrides", "constraints"),
    [
        ({"span_id": "0" * 16}, ["ck_spans_span_id_hex16"]),
        ({"span_id": "ABCDEF0123456789"}, ["ck_spans_span_id_hex16"]),
        ({"parent_span_id": "0" * 16}, ["ck_spans_parent_span_id_hex16"]),
        ({"parent_span_id": "b7ad6b7169203331"}, ["ck_spans_parent_not_self"]),
        ({"kind": "client"}, ["ck_spans_kind_enum"]),
        ({"status": "ok"}, ["ck_spans_status_enum"]),
        ({"provenance": "mixed"}, ["ck_spans_provenance_enum"]),
        ({"attributes": []}, ["ck_spans_attributes_object"]),
        ({"resource_attributes": "x"}, ["ck_spans_resource_attributes_object"]),
        ({"name": ""}, ["ck_spans_name_len"]),
        ({"name": "n" * 257}, ["ck_spans_name_len"]),
        ({"status_message": "m" * 257}, ["ck_spans_status_message_maxlen"]),
        ({"error_type": "e" * 257}, ["ck_spans_error_type_maxlen"]),
        ({"scope_name": "s" * 257}, ["ck_spans_scope_name_maxlen"]),
        ({"scope_version": "v" * 65}, ["ck_spans_scope_version_maxlen"]),
        (
            {"server_dropped_attributes_count": -1},
            ["ck_spans_server_dropped_attributes_count_nonneg"],
        ),
        ({"start_ns": 0, "end_ns": 1}, ["ck_spans_start_ns_positive"]),
        # Both checks fail; PostgreSQL reports the first by name.
        ({"end_ns": T0_NS - 1}, ["ck_spans_duration_ns_nonneg", "ck_spans_end_after_start"]),
        ({"trace_id": "5" * 32}, ["fk_spans_traces"]),
    ],
)
def test_span_rules(conn, graph, overrides, constraints):
    values = (
        dict(
            project_id=graph,
            trace_id=TRACE_ID,
            span_id="b7ad6b7169203331",
            name="op",
            kind="INTERNAL",
            start_ns=T0_NS,
            end_ns=T0_NS + 1,
            status="UNSET",
            attributes_schema_version="llminsight.attrmap.v1",
        )
        | overrides
    )
    assert_rejected(conn, insert(Span).values(**values), *constraints)


def test_span_unique(conn, graph):
    stmt = insert(Span).values(
        project_id=graph,
        trace_id=TRACE_ID,
        span_id=ROOT_SPAN,
        name="dup",
        kind="SERVER",
        start_ns=T0_NS,
        end_ns=T0_NS,
        status="UNSET",
        attributes_schema_version="llminsight.attrmap.v1",
    )
    assert_rejected(conn, stmt, "uq_spans_project_id_trace_id_span_id")


# ----------------------------------------------------------------- usage_records

COMPUTED = {
    "cost_status": "computed",
    "cost_unknown_reason": None,
    "cost_usd": Decimal("0.01"),
    "price_version": "v1",
    "model_price_id": 1,
}


@pytest.mark.parametrize(
    ("overrides", "constraints"),
    [
        # Unknown cost is never stored as 0 (ADR-11).
        ({"cost_usd": Decimal("0")}, ["ck_usage_records_cost_computed_has_value"]),
        (COMPUTED | {"cost_usd": None}, ["ck_usage_records_cost_computed_has_value"]),
        ({"cost_unknown_reason": None}, ["ck_usage_records_cost_unknown_has_reason"]),
        (
            COMPUTED | {"cost_unknown_reason": "price_missing"},
            ["ck_usage_records_cost_unknown_has_reason"],
        ),
        (COMPUTED | {"price_version": None}, ["ck_usage_records_cost_computed_has_price"]),
        (COMPUTED | {"model_price_id": None}, ["ck_usage_records_cost_computed_has_price"]),
        (COMPUTED | {"cost_usd": Decimal("-0.01")}, ["ck_usage_records_cost_usd_nonneg"]),
        ({"cost_status": "zero"}, ["ck_usage_records_cost_status_enum"]),
        ({"cost_unknown_reason": "other"}, ["ck_usage_records_cost_unknown_reason_enum"]),
        ({"input_tokens": -1}, ["ck_usage_records_input_tokens_nonneg"]),
        ({"output_tokens": -1}, ["ck_usage_records_output_tokens_nonneg"]),
        ({"cached_input_tokens": -1}, ["ck_usage_records_cached_input_tokens_nonneg"]),
        ({"cache_write_input_tokens": -1}, ["ck_usage_records_cache_write_input_tokens_nonneg"]),
        ({"reasoning_output_tokens": -1}, ["ck_usage_records_reasoning_output_tokens_nonneg"]),
        ({"attempt_id": 0}, ["ck_usage_records_attempt_id_positive"]),
        ({"operation_name": "gen_ai.client.inference"}, ["ck_usage_records_operation_name_enum"]),
        ({"call_key_source": "uuid"}, ["ck_usage_records_call_key_source_enum"]),
        ({"usage_source": "guess"}, ["ck_usage_records_usage_source_enum"]),
        ({"provenance": "mixed"}, ["ck_usage_records_provenance_enum"]),
        ({"provider": ""}, ["ck_usage_records_provider_len"]),
        ({"canonical_call_key": "k" * 301}, ["ck_usage_records_canonical_call_key_len"]),
        ({"call_id": "c" * 65}, ["ck_usage_records_call_id_maxlen"]),
        ({"provider_response_id": "r" * 257}, ["ck_usage_records_provider_response_id_maxlen"]),
        ({"requested_model": "m" * 257}, ["ck_usage_records_requested_model_maxlen"]),
        ({"response_model": "m" * 257}, ["ck_usage_records_response_model_maxlen"]),
        ({"trace_id": TRACE_ID.upper()}, ["ck_usage_records_trace_id_hex32"]),
        ({"span_id": "0" * 16}, ["ck_usage_records_span_id_hex16"]),
        ({"span_id": "9999999999999999"}, ["fk_usage_records_spans"]),
    ],
)
def test_usage_rules(conn, graph, overrides, constraints):
    assert_rejected(
        conn, insert(UsageRecord).values(**_usage_values(graph, **overrides)), *constraints
    )


def test_usage_identity_unique(conn, graph):
    conn.execute(insert(UsageRecord).values(**_usage_values(graph)))
    # Same canonical key on another span (e.g. double instrumentation) -> rejected.
    assert_rejected(
        conn,
        insert(UsageRecord).values(**_usage_values(graph, span_id=ROOT_SPAN)),
        "uq_usage_records_project_id_canonical_call_key",
    )
    # A second usage row for the same span -> rejected.
    assert_rejected(
        conn,
        insert(UsageRecord).values(**_usage_values(graph, canonical_call_key="span:x")),
        "uq_usage_records_project_id_trace_id_span_id",
    )
