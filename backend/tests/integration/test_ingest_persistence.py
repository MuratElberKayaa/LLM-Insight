"""What ingest stores (real PostgreSQL): tree, resources, idempotency, usage identity,
provenance, metadata-only storage and project isolation. All telemetry is SYNTHETIC.

T02, T03 (+S1, S2), T04, T16 (backend half), T23-S1/S2 (ingest half), T26 (storage half),
T27 (storage half), T28 (ingest half), T48-S1/S2 (storage half).
"""

from datetime import UTC, datetime

import pytest
from opentelemetry.proto.trace.v1.trace_pb2 import Span

from tests.integration.ingest_helpers import counts, post, rows
from tests.otlp import (
    FIXTURE_LLM,
    FIXTURE_ROOT,
    FIXTURE_TRACE_ID,
    FIXTURE_VALIDATE,
    MS,
    T0,
    body,
    inference_attrs,
    load_fixture,
    multi,
    request,
    span,
)

pytestmark = pytest.mark.integration

TRACE_B = "4bf92f3577b34da6a3ce929d0e0e4736"


def spans_by_id(db, trace_id=FIXTURE_TRACE_ID):
    return {
        r["span_id"]: r
        for r in rows(db, "SELECT * FROM spans WHERE trace_id = :t ORDER BY start_ns", t=trace_id)
    }


def trace_row(db, trace_id=FIXTURE_TRACE_ID, project_id=None):
    sql = "SELECT * FROM traces WHERE trace_id = :t"
    if project_id is not None:
        sql += " AND project_id = :p"
    (r,) = rows(db, sql, t=trace_id, p=project_id)
    return r


# ---------------------------------------------------------------- fixture expectations


def test_fixture_expected_ingest_result(client, make_key, db):
    """docs/fixtures/README.md 'Expected ingest result', items 1–5."""
    k = make_key()
    assert post(client, body(load_fixture()), k.key).status_code == 200

    t = trace_row(db)
    assert t["service_name"] == "demo-summarizer"
    assert t["status"] == "ok" and t["prompt_version"] == "v1"
    assert t["provenance"] == "synthetic" and t["span_count"] == 3
    assert t["end_ns"] - t["start_ns"] == 1_240_000_000

    s = spans_by_id(db)
    assert s[FIXTURE_LLM]["parent_span_id"] == FIXTURE_ROOT
    assert s[FIXTURE_VALIDATE]["parent_span_id"] == FIXTURE_ROOT
    assert s[FIXTURE_ROOT]["parent_span_id"] is None
    assert s[FIXTURE_LLM]["duration_ns"] == 1_200_000_000
    assert {r["server_dropped_attributes_count"] for r in s.values()} == {0}
    assert s[FIXTURE_LLM]["provenance"] == "synthetic"
    assert s[FIXTURE_ROOT]["provenance"] == s[FIXTURE_VALIDATE]["provenance"] == "unknown"
    assert {r["attributes_schema_version"] for r in s.values()} == {"llminsight.attrmap.v1"}
    assert s[FIXTURE_LLM]["attributes"]["gen_ai.response.finish_reasons"] == ["stop"]
    assert s[FIXTURE_VALIDATE]["attributes"]["llminsight.validation.passed"] is True

    (u,) = rows(db, "SELECT * FROM usage_records")
    assert u["span_id"] == FIXTURE_LLM
    assert u["canonical_call_key"] == "resp:openai:chatcmpl-SYNTHETIC-0001"
    assert u["call_key_source"] == "provider_response_id"
    assert u["call_id"] == "5f0c6e1a-3b7d-4c2e-9a41-7d2b8e6f1c03" and u["attempt_id"] == 1
    assert (u["operation_name"], u["provenance"], u["provider"]) == ("chat", "synthetic", "openai")
    assert u["requested_model"] == "gpt-4o-mini"
    assert u["response_model"] == "gpt-4o-mini-2024-07-18"
    assert (u["input_tokens"], u["cached_input_tokens"], u["output_tokens"]) == (1250, 1024, 180)
    assert u["reasoning_output_tokens"] is None and u["cache_write_input_tokens"] is None
    assert u["usage_source"] == "provider_reported"
    assert u["cost_usd"] is None and u["cost_status"] == "unknown"
    assert u["cost_unknown_reason"] == "price_missing"
    assert u["price_version"] is None and u["model_price_id"] is None

    # item 5 / T04: identical request again -> 200 and counts unchanged
    assert post(client, body(load_fixture()), k.key).status_code == 200
    assert counts(db) == (1, 3, 1)


# ---------------------------------------------------------------- T02 / T27 tree


def test_t02_parent_child_tree_reconstructs(client, make_key, db):
    k = make_key()
    post(client, body(load_fixture()), k.key)
    s = spans_by_id(db)
    children = {sid: r["parent_span_id"] for sid, r in s.items()}
    roots = [sid for sid, p in children.items() if p is None]
    assert roots == [FIXTURE_ROOT]
    assert sorted(sid for sid, p in children.items() if p == FIXTURE_ROOT) == sorted(
        [FIXTURE_LLM, FIXTURE_VALIDATE]
    )


def test_t27_orphan_span_is_stored_without_inventing_a_parent(client, make_key, db):
    k = make_key()
    orphan = span(span_id="1111111111111111", parent="9999999999999999", name="orphan")
    assert post(client, body(request(orphan)), k.key).status_code == 200
    (r,) = rows(db, "SELECT span_id, parent_span_id FROM spans")
    assert r == {"span_id": "1111111111111111", "parent_span_id": "9999999999999999"}
    t = trace_row(db)
    # no root: service name comes from the earliest span; nothing was fabricated
    assert t["span_count"] == 1 and t["service_name"] == "synthetic-test"


def test_child_arriving_before_parent_in_separate_batches(client, make_key, db):
    k = make_key()
    child = span(span_id="2222222222222222", parent="1111111111111111", start=T0 + 5 * MS)
    root = span(span_id="1111111111111111", kind=Span.SPAN_KIND_SERVER, start=T0, end=T0 + 50 * MS)
    post(client, body(request(child, resource={"service.name": "child-svc"})), k.key)
    assert trace_row(db)["service_name"] == "child-svc"
    post(client, body(request(root, resource={"service.name": "root-svc"})), k.key)
    t = trace_row(db)
    assert t["span_count"] == 2 and t["service_name"] == "root-svc"  # root's resource wins
    assert (t["start_ns"], t["end_ns"]) == (T0, T0 + 50 * MS)


# ---------------------------------------------------------------- T03 resources / scopes


def test_t03_missing_service_name_is_unknown_service(client, make_key, db):
    k = make_key()
    post(client, body(request(span(), resource={})), k.key)
    assert trace_row(db)["service_name"] == "unknown_service"
    (r,) = rows(db, "SELECT resource_attributes FROM spans")
    assert r["resource_attributes"] == {}  # nothing invented in the stored resource


def test_t03_s1_multiple_resource_spans_and_scopes(client, make_key, db):
    k = make_key()
    msg = multi(
        (
            {"service.name": "svc-a", "service.version": "1.2.3"},
            [
                (("scope-1", "1.0"), [span(span_id="1111111111111111")]),
                (("scope-2", "2.0"), [span(span_id="2222222222222222", parent="1111111111111111")]),
            ],
        ),
        (
            {"service.name": "svc-b"},
            [(("scope-3", "3.0"), [span(trace_id=TRACE_B, span_id="3333333333333333")])],
        ),
    )
    assert post(client, body(msg), k.key).status_code == 200
    got = {
        r["span_id"]: (r["scope_name"], r["scope_version"], r["resource_attributes"])
        for r in rows(
            db, "SELECT span_id, scope_name, scope_version, resource_attributes FROM spans"
        )
    }
    assert got == {
        "1111111111111111": (
            "scope-1",
            "1.0",
            {"service.name": "svc-a", "service.version": "1.2.3"},
        ),
        "2222222222222222": (
            "scope-2",
            "2.0",
            {"service.name": "svc-a", "service.version": "1.2.3"},
        ),
        "3333333333333333": ("scope-3", "3.0", {"service.name": "svc-b"}),
    }
    assert trace_row(db)["service_name"] == "svc-a"
    assert trace_row(db, TRACE_B)["service_name"] == "svc-b"


def test_t03_s2_alias_precedence_is_persisted(client, make_key, db):
    k = make_key()
    attrs = [
        ("gen_ai.operation.name", "chat"),
        ("gen_ai.system", "legacy-name"),
        ("gen_ai.provider.name", "openai"),
        ("gen_ai.usage.prompt_tokens", 999),
        ("gen_ai.usage.input_tokens", 10),
        ("gen_ai.usage.completion_tokens", 5),  # alias only -> mapped
        ("gen_ai.response.id", "chatcmpl-SYN-alias"),
    ]
    post(client, body(request(span(span_id=FIXTURE_LLM, kind=3, attrs=attrs))), k.key)
    (s,) = rows(db, "SELECT attributes, server_dropped_attributes_count AS d FROM spans")
    assert s["attributes"]["gen_ai.provider.name"] == "openai"
    assert s["attributes"]["gen_ai.usage.input_tokens"] == 10
    assert s["attributes"]["gen_ai.usage.output_tokens"] == 5
    assert not any(a in s["attributes"] for a in ("gen_ai.system", "gen_ai.usage.prompt_tokens"))
    assert s["d"] == 2  # both shadowed aliases dropped and counted
    (u,) = rows(db, "SELECT provider, input_tokens, output_tokens FROM usage_records")
    assert u == {"provider": "openai", "input_tokens": 10, "output_tokens": 5}


# ---------------------------------------------------------------- T26 timestamps (storage)


def test_t26_ns_epoch_stored_losslessly_and_as_utc_timestamptz(client, make_key, db):
    k = make_key()
    start, end = 1_792_836_000_123_456_789, 1_792_836_001_987_654_321
    post(client, body(request(span(start=start, end=end, attrs=inference_attrs(), kind=3))), k.key)
    (s,) = rows(db, "SELECT start_ns, end_ns, duration_ns FROM spans")
    assert s == {"start_ns": start, "end_ns": end, "duration_ns": end - start}
    t = trace_row(db)
    assert t["started_at"] == datetime(2026, 10, 24, 10, 0, 0, 123456, tzinfo=UTC)
    assert t["ended_at"] == datetime(2026, 10, 24, 10, 0, 1, 987654, tzinfo=UTC)
    (u,) = rows(db, "SELECT started_at FROM usage_records")
    assert u["started_at"] == t["started_at"]


def test_largest_bigint_timestamp_is_storable_end_to_end(client, make_key, db):
    k = make_key()
    r = post(client, body(request(span(start=2**63 - 2, end=2**63 - 1))), k.key)
    assert r.status_code == 200
    assert trace_row(db)["ended_at"].year == 2262


# ---------------------------------------------------------------- T04 idempotency


def test_t04_same_payload_twice_leaves_counts_unchanged(client, make_key, db):
    k = make_key()
    payload = body(load_fixture())
    assert post(client, payload, k.key).status_code == 200
    before = rows(db, "SELECT span_id, ingested_at FROM spans ORDER BY span_id")
    assert post(client, payload, k.key).status_code == 200
    assert (
        post(client, body(load_fixture(), gzipped=True), k.key, Content_Encoding="gzip").status_code
        == 200
    )
    assert counts(db) == (1, 3, 1)
    assert rows(db, "SELECT span_id, ingested_at FROM spans ORDER BY span_id") == before


def test_reexport_with_changed_content_keeps_first_write(client, make_key, db):
    k = make_key()
    first = span(span_id="1111111111111111", name="first", attrs={"http.route": "/a"})
    second = span(span_id="1111111111111111", name="second", attrs=inference_attrs(), kind=3)
    post(client, body(request(first)), k.key)
    post(client, body(request(second)), k.key)
    (s,) = rows(db, "SELECT name, attributes FROM spans")
    assert s == {"name": "first", "attributes": {"http.route": "/a"}}
    # the re-export carried inference attributes, but the stored span is not inference
    assert counts(db) == (1, 1, 0)


def test_duplicate_span_inside_one_batch_is_stored_once(client, make_key, db):
    k = make_key()
    a = span(span_id="1111111111111111", name="a", attrs=inference_attrs(), kind=3)
    b = span(span_id="1111111111111111", name="b", attrs=inference_attrs(), kind=3)
    assert post(client, body(request(a, b)), k.key).status_code == 200
    assert counts(db) == (1, 1, 1)
    assert rows(db, "SELECT name FROM spans") == [{"name": "a"}]


# ---------------------------------------------------------------- usage identity


def test_retry_attempts_are_separate_usage_rows_and_reexport_adds_nothing(client, make_key, db):
    """Same call_id, attempt 1 failed (no response ID), attempt 2 succeeded (T14 shape)."""
    k = make_key()
    a1 = span(
        span_id="1111111111111111",
        kind=3,
        status_code=2,
        attrs=inference_attrs(
            gen_ai__response__id=None,
            gen_ai__usage__input_tokens=None,
            gen_ai__usage__output_tokens=None,
            error__type="rate_limit_exceeded",
        ),
    )
    a2 = span(
        span_id="2222222222222222",
        kind=3,
        start=T0 + 20 * MS,
        attrs=inference_attrs(llminsight__attempt=2, gen_ai__response__id="chatcmpl-SYN-retry-ok"),
    )
    payload = body(request(a1, a2))
    assert post(client, payload, k.key).status_code == 200
    assert post(client, payload, k.key).status_code == 200
    got = rows(
        db,
        "SELECT attempt_id, call_key_source, canonical_call_key, input_tokens, usage_source,"
        " cost_unknown_reason FROM usage_records ORDER BY attempt_id",
    )
    assert got == [
        {
            "attempt_id": 1,
            "call_key_source": "sdk_attempt",
            "canonical_call_key": "sdk:5f0c6e1a-3b7d-4c2e-9a41-000000000001:1",
            "input_tokens": None,
            "usage_source": "unknown",
            "cost_unknown_reason": "usage_missing",
        },
        {
            "attempt_id": 2,
            "call_key_source": "provider_response_id",
            "canonical_call_key": "resp:openai:chatcmpl-SYN-retry-ok",
            "input_tokens": 100,
            "usage_source": "provider_reported",
            "cost_unknown_reason": "price_missing",
        },
    ]
    (logical,) = rows(db, "SELECT count(DISTINCT call_id) AS n FROM usage_records")
    assert logical["n"] == 1  # 2 physical attempts, 1 logical call
    (e,) = rows(db, "SELECT error_type, status FROM spans WHERE span_id = '1111111111111111'")
    assert e == {"error_type": "rate_limit_exceeded", "status": "ERROR"}


def test_t23_s1_double_instrumentation_same_response_id_is_one_usage_row(client, make_key, db):
    k = make_key()
    sdk_span = span(span_id="1111111111111111", kind=3, attrs=inference_attrs())
    other_lib = span(
        span_id="2222222222222222",
        kind=3,
        attrs=inference_attrs(llminsight__call_id=None, llminsight__attempt=None),
    )
    ingestor = client.app.state.ingestor
    before = ingestor.stats.usage_key_collisions
    assert post(client, body(request(sdk_span)), k.key).status_code == 200
    assert post(client, body(request(other_lib)), k.key).status_code == 200
    assert counts(db) == (1, 2, 1)
    (u,) = rows(db, "SELECT span_id FROM usage_records")
    assert u["span_id"] == "1111111111111111"  # first row kept
    assert ingestor.stats.usage_key_collisions == before + 1


def test_t23_s1_collision_inside_one_batch(client, make_key, db):
    k = make_key()
    a = span(span_id="1111111111111111", kind=3, attrs=inference_attrs())
    b = span(span_id="2222222222222222", kind=3, attrs=inference_attrs(llminsight__attempt=None))
    assert post(client, body(request(a, b)), k.key).status_code == 200
    assert counts(db) == (1, 2, 1)
    assert client.app.state.ingestor.stats.usage_key_collisions == 1


def test_t23_s2_key_source_precedence_is_persisted(client, make_key, db):
    k = make_key()
    spans = [
        span(span_id="1111111111111111", kind=3, attrs=inference_attrs()),
        span(
            span_id="2222222222222222",
            kind=3,
            attrs=inference_attrs(gen_ai__response__id=None, llminsight__attempt=2),
        ),
        span(
            span_id="3333333333333333",
            kind=3,
            attrs=inference_attrs(
                gen_ai__response__id=None, llminsight__call_id=None, llminsight__attempt=None
            ),
        ),
    ]
    post(client, body(request(*spans)), k.key)
    got = {
        r["span_id"]: (r["call_key_source"], r["canonical_call_key"])
        for r in rows(db, "SELECT span_id, call_key_source, canonical_call_key FROM usage_records")
    }
    assert got == {
        "1111111111111111": ("provider_response_id", "resp:openai:chatcmpl-SYNTHETIC-T1"),
        "2222222222222222": ("sdk_attempt", "sdk:5f0c6e1a-3b7d-4c2e-9a41-000000000001:2"),
        "3333333333333333": (
            "span_fallback",
            f"span:{FIXTURE_TRACE_ID}:3333333333333333",
        ),
    }


def test_no_usage_rows_for_non_inference_parents(client, make_key, db):
    k = make_key()
    root = span(
        span_id="1111111111111111",
        kind=Span.SPAN_KIND_SERVER,
        # a parent that carries usage-looking attributes must still produce no usage row
        attrs={"http.route": "/v1/summarize", "gen_ai.usage.input_tokens": 1000},
    )
    tool = span(
        span_id="2222222222222222",
        parent="1111111111111111",
        attrs={"gen_ai.operation.name": "execute_tool"},
    )
    child = span(
        span_id="3333333333333333", parent="1111111111111111", kind=3, attrs=inference_attrs()
    )
    post(client, body(request(root, tool, child)), k.key)
    assert counts(db) == (1, 3, 1)
    (u,) = rows(db, "SELECT span_id, input_tokens FROM usage_records")
    assert u == {"span_id": "3333333333333333", "input_tokens": 100}  # no parent double count


def test_usage_aggregation_over_a_mixed_batch(client, make_key, db):
    k = make_key()
    spans = [
        span(
            span_id=f"{i + 1:016x}",
            kind=3,
            attrs=inference_attrs(
                gen_ai__response__id=f"chatcmpl-SYN-agg-{i}",
                gen_ai__usage__input_tokens=100 * (i + 1),
                gen_ai__usage__output_tokens=10 * (i + 1),
                gen_ai__usage__cache_read__input_tokens=50 if i == 0 else None,
            ),
        )
        for i in range(3)
    ]
    spans.append(
        span(
            span_id="00000000000000ff",
            kind=3,
            attrs=inference_attrs(
                gen_ai__response__id="chatcmpl-SYN-agg-missing",
                gen_ai__usage__input_tokens=None,
                gen_ai__usage__output_tokens=None,
            ),
        )
    )
    post(client, body(request(*spans)), k.key)
    (agg,) = rows(
        db,
        "SELECT count(*) AS n, sum(input_tokens) AS i, sum(output_tokens) AS o,"
        " sum(cached_input_tokens) AS c, count(*) FILTER (WHERE input_tokens IS NULL) AS unknown,"
        " count(*) FILTER (WHERE cost_usd IS NOT NULL) AS priced FROM usage_records",
    )
    # SUM skips NULL: unknown usage is never counted as 0 tokens; nothing is priced yet
    assert agg == {"n": 4, "i": 600, "o": 60, "c": 50, "unknown": 1, "priced": 0}


# ---------------------------------------------------------------- provenance


def test_t48_s1_mixed_trace_and_t48_s2_real_without_response_id(client, make_key, db):
    k = make_key()
    real = span(
        span_id="1111111111111111",
        kind=3,
        attrs=inference_attrs(llminsight__provenance="real", gen_ai__response__id="chatcmpl-r1"),
    )
    synthetic = span(
        span_id="2222222222222222",
        kind=3,
        attrs=inference_attrs(gen_ai__response__id="chatcmpl-s1"),
    )
    claimed_real = span(
        trace_id=TRACE_B,
        span_id="3333333333333333",
        kind=3,
        attrs=inference_attrs(llminsight__provenance="real", gen_ai__response__id=None),
    )
    post(client, body(request(real, synthetic, claimed_real)), k.key)
    assert trace_row(db)["provenance"] == "mixed"
    prov = {
        r["span_id"]: r["provenance"]
        for r in rows(db, "SELECT span_id, provenance FROM usage_records")
    }
    assert prov == {
        "1111111111111111": "real",
        "2222222222222222": "synthetic",
        "3333333333333333": "unknown",
    }
    assert trace_row(db, TRACE_B)["provenance"] == "unknown"  # downgraded, never "real"


@pytest.mark.parametrize(
    "provs,expected",
    [
        ([], "unknown"),
        ([None], "unknown"),  # all-unknown inference spans: uniform, not "mixed"
        (["synthetic", None], "mixed"),
        (["real", "real"], "real"),
        (["synthetic"], "synthetic"),
        (["real", None], "mixed"),
    ],
)
def test_trace_provenance_derivation(client, make_key, db, provs, expected):
    k = make_key()
    spans = [span(span_id="00000000000000aa", kind=2, attrs={"http.route": "/x"})]
    for i, p in enumerate(provs):
        spans.append(
            span(
                span_id=f"{i + 1:016x}",
                kind=3,
                attrs=inference_attrs(
                    llminsight__provenance=p, gen_ai__response__id=f"chatcmpl-p{i}"
                ),
            )
        )
    post(client, body(request(*spans)), k.key)
    assert trace_row(db)["provenance"] == expected


def test_prompt_version_only_when_all_inference_spans_agree(client, make_key, db):
    k = make_key()

    def llm(sid, pv, rid):
        return span(
            span_id=sid,
            kind=3,
            attrs=inference_attrs(gen_ai__prompt__version=pv, gen_ai__response__id=rid),
        )

    post(
        client,
        body(request(llm("1111111111111111", "v1", "r1"), llm("2222222222222222", "v1", "r2"))),
        k.key,
    )
    assert trace_row(db)["prompt_version"] == "v1"
    post(client, body(request(llm("3333333333333333", "v2", "r3"))), k.key)
    assert trace_row(db)["prompt_version"] is None  # disagreement


def test_trace_status_error_if_any_span_errors(client, make_key, db):
    k = make_key()
    post(client, body(request(span(span_id="1111111111111111"))), k.key)
    assert trace_row(db)["status"] == "ok"
    post(client, body(request(span(span_id="2222222222222222", status_code=2))), k.key)
    assert trace_row(db)["status"] == "error"


# ---------------------------------------------------------------- T16 / ADR-19 metadata only


SENSITIVE = {
    "gen_ai.input.messages": '[{"role":"user","content":"SECRET-PROMPT"}]',
    "gen_ai.output.messages": "SECRET-COMPLETION",
    "gen_ai.system_instructions": "SECRET-SYSTEM",
    "gen_ai.tool.definitions": "SECRET-TOOLS",
    "gen_ai.prompt.variable": "SECRET-VARIABLE",
    "http.request.header.authorization": "Bearer SECRET-BEARER",
    "openai.api_key": "sk-SECRETSECRETSECRET",
    "url.full": "https://example.test/?q=SECRET-QUERY",
    "user.email": "SECRET@example.test",
}


def test_t16_content_and_credentials_are_never_persisted_and_are_counted(client, make_key, db):
    k = make_key()
    s = span(span_id=FIXTURE_LLM, kind=3, attrs={**inference_attrs(), **SENSITIVE})
    ev = s.events.add(name="exception", time_unix_nano=T0)
    ev.attributes.add(key="exception.message").value.string_value = "SECRET-EXCEPTION"
    ev.attributes.add(key="exception.type").value.string_value = "ValueError"
    msg = request(
        s, resource={"service.name": "svc", "process.command_line": "run --token SECRET-CLI"}
    )
    assert post(client, body(msg), k.key).status_code == 200

    (row,) = rows(db, "SELECT * FROM spans")
    assert row["server_dropped_attributes_count"] == len(SENSITIVE) + 1  # + resource attr
    assert row["error_type"] == "ValueError"
    for table in ("spans", "traces", "usage_records", "projects", "project_api_keys"):
        dump = rows(db, f"SELECT row_to_json(x)::text AS j FROM {table} AS x")  # noqa: S608
        text = " ".join(d["j"] for d in dump)
        assert "SECRET" not in text, table


def test_project_attribute_sent_by_client_is_ignored(client, make_key, db):
    a, b = make_key("project-a"), make_key("project-b")
    s = span(attrs={"llminsight.project_id": b.project_id, "project": "project-b"})
    post(client, body(request(s, resource={"service.name": "x", "llminsight.project": "b"})), a.key)
    assert counts(db, a.project_id) == (1, 1, 0) and counts(db, b.project_id) == (0, 0, 0)


# ---------------------------------------------------------------- T28 project isolation (ingest)


def test_t28_same_ids_in_two_projects_are_isolated(client, make_key, db):
    a, b = make_key("project-a"), make_key("project-b")
    assert post(client, body(load_fixture()), a.key).status_code == 200
    assert post(client, body(load_fixture()), b.key).status_code == 200
    assert counts(db, a.project_id) == (1, 3, 1)
    assert counts(db, b.project_id) == (1, 3, 1)
    # identical response IDs are a collision only within a project
    assert client.app.state.ingestor.stats.usage_key_collisions == 0
    # a later batch for project A only touches A's derived trace
    extra = span(span_id="00000000000000ee", status_code=2)
    post(client, body(request(extra)), a.key)
    assert trace_row(db, project_id=a.project_id)["status"] == "error"
    assert trace_row(db, project_id=a.project_id)["span_count"] == 4
    assert trace_row(db, project_id=b.project_id)["status"] == "ok"
    assert trace_row(db, project_id=b.project_id)["span_count"] == 3
