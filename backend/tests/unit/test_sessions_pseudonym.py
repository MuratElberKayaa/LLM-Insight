"""Phase 10C (ADR-56): the session pseudonym and its place in normalization. SYNTHETIC values."""

import hashlib
import hmac
import re

import pytest

from app.services.ingestion import sessions
from app.services.ingestion.normalize import normalize
from tests.otlp import inference_attrs, kvs, request, span

PEPPER = b"unit-test-pepper-not-a-secret"
RAW = "conv_SYNTHETIC_0001"


def test_hash_format_and_independent_recomputation():
    h = sessions.pseudonymizer(PEPPER, 7)(RAW)
    assert re.fullmatch(sessions.HASH_PATTERN, h)
    key = hmac.new(PEPPER, b"llminsight.session.v1", hashlib.sha256).digest()
    expected = "s1_" + hmac.new(key, b"7\x00" + RAW.encode(), hashlib.sha256).hexdigest()[:32]
    assert h == expected


def test_same_project_is_stable_other_projects_and_peppers_differ():
    a = sessions.pseudonymizer(PEPPER, 7)
    assert a(RAW) == a(RAW)
    assert a(RAW) != sessions.pseudonymizer(PEPPER, 8)(RAW)
    assert a(RAW) != sessions.pseudonymizer(b"another-pepper", 7)(RAW)
    assert a(RAW) != a(RAW + "x")
    # project 1 + "2\x00…" cannot collide with project 12 + "\x00…": the separator is fixed
    assert sessions.pseudonymizer(PEPPER, 1)("2") != sessions.pseudonymizer(PEPPER, 12)("")


@pytest.mark.parametrize(
    ("attrs", "raw", "present"),
    [
        ({}, None, False),
        ({"gen_ai.conversation.id": RAW}, RAW, True),
        ({"gen_ai.conversation.id": ""}, None, True),
        ({"gen_ai.conversation.id": "x" * 257}, None, True),
        ({"gen_ai.conversation.id": "a\nb"}, None, True),
        ({"gen_ai.conversation.id": 42}, None, True),
        ([("gen_ai.conversation.id", "a"), ("gen_ai.conversation.id", "b")], None, True),
    ],
)
def test_raw_value_rules(attrs, raw, present):
    assert sessions.raw_session_id(kvs(attrs)) == (raw, present)


def one(attrs, pseudonymize):
    spans, violations = normalize(request(span(attrs=attrs)), pseudonymize)
    assert not violations
    return spans[0]


def test_normalize_replaces_the_raw_id_and_does_not_count_it_as_dropped():
    p = sessions.pseudonymizer(PEPPER, 7)
    s = one(inference_attrs(gen_ai__conversation__id=RAW), p)
    assert s.attributes[sessions.HASH_KEY] == p(RAW)
    assert "gen_ai.conversation.id" not in s.attributes
    assert RAW not in repr(s)
    assert s.server_dropped_attributes_count == 0


def test_without_a_pseudonymizer_the_id_is_dropped_as_before():
    s = one(inference_attrs(gen_ai__conversation__id=RAW), None)
    assert sessions.HASH_KEY not in s.attributes and s.server_dropped_attributes_count == 1


def test_an_invalid_id_is_dropped_and_counted():
    s = one(inference_attrs(gen_ai__conversation__id="x" * 300), sessions.pseudonymizer(PEPPER, 7))
    assert sessions.HASH_KEY not in s.attributes and s.server_dropped_attributes_count == 1


def test_a_client_supplied_hash_is_never_accepted():
    forged = "s1_" + "0" * 32
    s = one(inference_attrs(llminsight__session__hash=forged), sessions.pseudonymizer(PEPPER, 7))
    assert sessions.HASH_KEY not in s.attributes and s.server_dropped_attributes_count == 1
    # Even beside a real ID, the stored hash is the server's own.
    s = one(
        inference_attrs(llminsight__session__hash=forged, gen_ai__conversation__id=RAW),
        sessions.pseudonymizer(PEPPER, 7),
    )
    assert s.attributes[sessions.HASH_KEY] == sessions.pseudonymizer(PEPPER, 7)(RAW)
    assert s.server_dropped_attributes_count == 1  # the forged key
