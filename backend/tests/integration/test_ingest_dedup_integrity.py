"""Task E: idempotency, retry attempts, call-key namespace and conflicting payloads under one
identity (real PostgreSQL). All telemetry is SYNTHETIC.

Atomic rollback and commit-before-200 are covered by test_ingest_db_failure.py (T30-S1) and
test_ingest_protocol.py (T01-S1, T06-S6); this file does not repeat them.
"""

import logging

import pytest
from opentelemetry.proto.trace.v1.trace_pb2 import Status as SpanStatus

from tests.integration.ingest_helpers import counts, post, rows
from tests.otlp import FIXTURE_ROOT, MS, T0, body, inference_attrs, load_fixture, request, span

pytestmark = pytest.mark.integration

A, B, C = "aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb", "cccccccccccccccc"


def stats(client):
    return client.app.state.ingestor.stats


def usage(db):
    return rows(
        db,
        "SELECT span_id, canonical_call_key, call_key_source, input_tokens, provider"
        " FROM usage_records ORDER BY span_id",
    )


# ---------------------------------------------------------------- E1: repeated exports
def test_e1_identical_reexports_are_idempotent_and_counted_as_reexports(api_client, make_key, db):
    k = make_key()
    payload = body(load_fixture())
    for _ in range(3):
        assert post(api_client, payload, k.key).status_code == 200
    assert counts(db) == (1, 3, 1)
    st = stats(api_client)
    assert (st.spans_reexported, st.span_content_conflicts, st.usage_key_collisions) == (6, 0, 0)


# ---------------------------------------------------------------- E2: retry attempts
def test_e2_failed_attempt_then_successful_retry_are_two_rows(client, make_key, db):
    k = make_key()
    failed = inference_attrs(
        gen_ai__response__id=None,
        gen_ai__usage__input_tokens=None,
        gen_ai__usage__output_tokens=None,
        error__type="timeout",
    )
    ok = inference_attrs(llminsight__attempt=2, gen_ai__response__id="chatcmpl-RETRY-2")
    spans = [
        span(span_id=A, attrs=failed, status_code=SpanStatus.STATUS_CODE_ERROR),
        span(span_id=B, start=T0 + 20 * MS, attrs=ok),
    ]
    assert post(client, body(request(*spans)), k.key).status_code == 200
    assert [(u["span_id"], u["call_key_source"]) for u in usage(db)] == [
        (A, "sdk_attempt"),
        (B, "provider_response_id"),
    ]


def test_e2_attempts_without_any_shared_identifier_stay_separate(client, make_key, db):
    """Third-party spans (no call_id, no response ID): distinct spans are distinct attempts."""
    k = make_key()
    bare = inference_attrs(
        gen_ai__response__id=None, llminsight__call_id=None, llminsight__attempt=None
    )
    spans = [span(span_id=s, start=T0 + i * MS, attrs=bare) for i, s in enumerate((A, B))]
    assert post(client, body(request(*spans)), k.key).status_code == 200
    assert [u["call_key_source"] for u in usage(db)] == ["span_fallback", "span_fallback"]


def test_e2_same_call_distinct_response_ids_are_two_physical_calls(client, make_key, db):
    k = make_key()
    spans = [
        span(
            span_id=s,
            start=T0 + i * MS,
            attrs=inference_attrs(llminsight__attempt=i + 1, gen_ai__response__id=f"resp-{i}"),
        )
        for i, s in enumerate((A, B))
    ]
    assert post(client, body(request(*spans)), k.key).status_code == 200
    assert len(usage(db)) == 2


# ---------------------------------------------------------------- E3: provider namespace
def test_e3_same_response_id_under_different_providers_is_not_merged(client, make_key, db):
    k = make_key()
    spans = [
        span(span_id=A, attrs=inference_attrs(gen_ai__provider__name="openai")),
        span(span_id=B, start=T0 + MS, attrs=inference_attrs(gen_ai__provider__name="azure")),
    ]
    assert post(client, body(request(*spans)), k.key).status_code == 200
    assert [u["canonical_call_key"] for u in usage(db)] == [
        "resp:openai:chatcmpl-SYNTHETIC-T1",
        "resp:azure:chatcmpl-SYNTHETIC-T1",
    ]
    assert stats(client).usage_key_collisions == 0


def test_e3_separator_inside_the_provider_cannot_forge_a_collision(client, make_key, db):
    """Phase 1 final: a provider name containing ':' fails the provider format and is dropped
    (namespace `unknown`); the percent-encoding in usage.py stays as a second layer
    (unit-tested in test_provider_namespace_is_escaped_so_keys_cannot_collide)."""
    k = make_key()
    spans = [
        span(
            span_id=A, attrs=inference_attrs(gen_ai__provider__name="a:b", gen_ai__response__id="c")
        ),
        span(
            span_id=B,
            start=T0 + MS,
            attrs=inference_attrs(gen_ai__provider__name="a", gen_ai__response__id="b:c"),
        ),
    ]
    assert post(client, body(request(*spans)), k.key).status_code == 200
    keys = [u["canonical_call_key"] for u in usage(db)]
    assert keys == ["resp:unknown:c", "resp:a:b:c"]
    assert [u["provider"] for u in usage(db)] == ["unknown", "a"]
    assert stats(client).usage_key_collisions == 0


def test_e3_missing_provider_shares_the_unknown_namespace_and_is_counted(client, make_key, db):
    """Documented limitation: without gen_ai.provider.name both spans fall into
    `resp:unknown:…`. The second is not silently lost: it is a counted collision, and its
    span is still stored."""
    k = make_key()
    no_provider = inference_attrs(gen_ai__provider__name=None)
    spans = [span(span_id=A, attrs=no_provider), span(span_id=B, start=T0 + MS, attrs=no_provider)]
    assert post(client, body(request(*spans)), k.key).status_code == 200
    assert [u["canonical_call_key"] for u in usage(db)] == ["resp:unknown:chatcmpl-SYNTHETIC-T1"]
    assert counts(db) == (1, 2, 1)
    assert stats(client).usage_key_collisions == 1


# ---------------------------------------------------------------- E4: differing payloads
def test_e4_reexport_with_different_content_keeps_first_write_and_is_counted(
    api_client, make_key, db, caplog
):
    caplog.set_level(logging.WARNING)
    k = make_key()
    first = span(span_id=A, name="first", attrs=inference_attrs())
    changed = span(
        span_id=A, name="CHANGED-NAME", attrs=inference_attrs(gen_ai__usage__input_tokens=999)
    )
    assert post(api_client, body(request(first)), k.key).status_code == 200
    assert post(api_client, body(request(changed)), k.key).status_code == 200
    assert rows(db, "SELECT name FROM spans") == [{"name": "first"}]
    assert [u["input_tokens"] for u in usage(db)] == [100]  # usage never rewritten
    st = stats(api_client)
    assert (st.spans_reexported, st.span_content_conflicts) == (1, 1)
    assert "span re-export content conflicts=1" in caplog.text
    assert "CHANGED-NAME" not in caplog.text and "999" not in caplog.text  # counts only


def test_e4_differing_duplicate_inside_one_batch_is_counted(api_client, make_key, db):
    k = make_key()
    a1 = span(span_id=A, name="one")
    a2 = span(span_id=A, name="two")
    a3 = span(span_id=A, name="one")  # identical to the first: a plain duplicate
    assert post(api_client, body(request(a1, a2, a3)), k.key).status_code == 200
    assert rows(db, "SELECT name FROM spans") == [{"name": "one"}]
    assert stats(api_client).span_content_conflicts == 1


def test_e4_same_response_id_with_different_tokens_keeps_the_first_row(api_client, make_key, db):
    k = make_key()
    p1 = span(span_id=A, attrs=inference_attrs(gen_ai__usage__input_tokens=100))
    p2 = span(span_id=B, start=T0 + MS, attrs=inference_attrs(gen_ai__usage__input_tokens=5000))
    assert post(api_client, body(request(p1)), k.key).status_code == 200
    assert post(api_client, body(request(p2)), k.key).status_code == 200
    assert [(u["span_id"], u["input_tokens"]) for u in usage(db)] == [(A, 100)]
    assert stats(api_client).usage_key_collisions == 1
    assert counts(db) == (1, 2, 1)  # both spans are visible in the trace tree


def test_e4_conflict_detection_is_project_scoped(api_client, make_key, db):
    a, b = make_key("a"), make_key("b")
    assert post(api_client, body(request(span(span_id=C, name="in-a"))), a.key).status_code == 200
    assert post(api_client, body(request(span(span_id=C, name="in-b"))), b.key).status_code == 200
    assert counts(db) == (2, 2, 0)
    st = stats(api_client)
    assert (st.spans_reexported, st.span_content_conflicts) == (0, 0)


def test_e2_retry_of_the_same_span_after_503_is_a_reexport_not_a_new_attempt(
    api_client, make_key, db
):
    """An exporter retry re-sends the same span IDs: it adds nothing, wherever the first
    attempt ended (rolled back or committed)."""
    k = make_key()
    payload = body(request(span(span_id=A, parent=FIXTURE_ROOT, attrs=inference_attrs())))
    assert post(api_client, payload, k.key).status_code == 200
    assert post(api_client, payload, k.key).status_code == 200
    assert counts(db) == (1, 1, 1)
