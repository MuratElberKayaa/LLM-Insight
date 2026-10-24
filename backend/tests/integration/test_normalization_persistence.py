"""Phase 1C Task A rules as persisted in PostgreSQL. All telemetry is SYNTHETIC."""

import pytest

from tests.integration.ingest_helpers import counts, post, rows, violations_of
from tests.otlp import FIXTURE_ROOT, FIXTURE_TRACE_ID, MS, T0, body, inference_attrs, request, span

pytestmark = pytest.mark.integration

LLM_A, LLM_B = "1111111111111111", "2222222222222222"


def trace_row(db):
    (r,) = rows(db, "SELECT * FROM traces")
    return r


def test_a1_a2_token_counters_and_max_tokens_are_persisted(client, make_key, db):
    k = make_key()
    attrs = inference_attrs(
        gen_ai__request__max_tokens=512,
        gen_ai__usage__cache_read__input_tokens=40,
        gen_ai__usage__cache_write__input_tokens=0,
        gen_ai__usage__reasoning__output_tokens=5,
    )
    assert post(client, body(request(span(attrs=attrs))), k.key).status_code == 200
    (s,) = rows(db, "SELECT attributes, server_dropped_attributes_count FROM spans")
    assert s["attributes"]["gen_ai.request.max_tokens"] == 512
    assert s["server_dropped_attributes_count"] == 0
    (u,) = rows(db, "SELECT * FROM usage_records")
    assert (u["input_tokens"], u["output_tokens"]) == (100, 20)
    assert (u["cached_input_tokens"], u["cache_write_input_tokens"]) == (40, 0)
    assert u["reasoning_output_tokens"] == 5


def test_a3_credential_attributes_are_dropped_counted_and_absent_from_storage(client, make_key, db):
    k = make_key()
    attrs = {
        **inference_attrs(),
        "auth.token": "TOKEN-VALUE-1",
        "accessToken": "TOKEN-VALUE-2",
        "x-api-key": "TOKEN-VALUE-3",
        "db.password": "TOKEN-VALUE-4",
    }
    assert post(client, body(request(span(attrs=attrs))), k.key).status_code == 200
    (s,) = rows(db, "SELECT attributes::text AS a, server_dropped_attributes_count AS n FROM spans")
    assert "TOKEN-VALUE" not in s["a"] and s["n"] == 4


def test_a6_trace_provenance_unknown_vs_mixed(client, make_key, db):
    k = make_key()
    no_claim = inference_attrs(llminsight__provenance=None, gen_ai__response__id="r-a")
    no_claim2 = inference_attrs(llminsight__provenance=None, gen_ai__response__id="r-b")
    post(client, body(request(span(span_id=LLM_A, attrs=no_claim))), k.key)
    post(client, body(request(span(span_id=LLM_B, start=T0 + MS, attrs=no_claim2))), k.key)
    assert trace_row(db)["provenance"] == "unknown"  # all inference spans unknown

    real = inference_attrs(llminsight__provenance="real", gen_ai__response__id="r-c")
    post(client, body(request(span(span_id="3333333333333333", attrs=real))), k.key)
    assert trace_row(db)["provenance"] == "mixed"  # real + unknown


def test_a7_empty_span_name_is_stored_as_unknown_span(client, make_key, db):
    k = make_key()
    assert post(client, body(request(span(name=""))), k.key).status_code == 200
    assert rows(db, "SELECT name FROM spans") == [{"name": "unknown_span"}]


def test_a8_overlong_ids_are_400_and_nothing_truncated_is_stored(client, make_key, db):
    k = make_key()
    s = span(raw_ids={"trace_id": bytes.fromhex(FIXTURE_TRACE_ID) + b"\x01"})
    r = post(client, body(request(s)), k.key)
    assert r.status_code == 400
    assert [v.reason for v in violations_of(r)] == ["INVALID_TRACE_ID"]
    assert counts(db) == (0, 0, 0)


@pytest.mark.parametrize(
    "versions,expected",
    [
        (["v2", "v2"], "v2"),
        (["v2", "v2 with spaces"], None),  # the bad one is dropped -> not all spans agree
        (["x" * 65], None),
        (["summarizer/v3"], "summarizer/v3"),
    ],
    ids=["agree", "one-free-text", "too-long", "slash"],
)
def test_a9_only_valid_prompt_versions_reach_the_trace(client, make_key, db, versions, expected):
    k = make_key()
    spans = [
        span(
            span_id=f"{i + 1:016x}",
            parent=FIXTURE_ROOT,
            start=T0 + i * MS,
            attrs=inference_attrs(gen_ai__prompt__version=v, gen_ai__response__id=f"r{i}"),
        )
        for i, v in enumerate(versions)
    ]
    assert post(client, body(request(*spans)), k.key).status_code == 200
    assert trace_row(db)["prompt_version"] == expected
    stored = [
        r["v"] for r in rows(db, "SELECT attributes->>'gen_ai.prompt.version' AS v FROM spans")
    ]
    assert all(v is None or (len(v) <= 64 and " " not in v) for v in stored)
