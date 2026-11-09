"""Client-side copy of the R08 value rules (TELEMETRY_CONTRACT.md §3.1). The live-backend
integration test checks the same values end to end (server_dropped_attributes_count = 0)."""

from __future__ import annotations

import logging

import pytest

from llm_insight_sdk import _guards as g
from llm_insight_sdk import instrumentation

VALID = [
    ("mock-chat-small", g.MODEL),
    ("mock-chat-small-2026-10-01", g.MODEL),
    ("gpt-4o-mini-2024-07-18", g.MODEL),
    ("ft:gpt-4o-mini:org:x:id", g.MODEL),
    ("models/gemini-1.5-pro", g.MODEL),
    ("claude-3-sonnet@20240229", g.MODEL),
    ("llminsight.mock", g.PROVIDER),
    ("openai", g.PROVIDER),
    ("chat", g.OPERATION),
    ("text_completion", g.OPERATION),
    ("stop", g.KEYWORD),
    ("0.1.0", g.VERSION),
    ("1.45.1+local", g.VERSION),
    ("summarize", g.PROMPT_NAME),
    ("v1", g.PROMPT_VERSION),
    ("2026-10-01", g.PROMPT_VERSION),
    ("429", g.ERROR_TYPE),
    ("timeout", g.ERROR_TYPE),
    ("_OTHER", g.ERROR_TYPE),
    ("llm_insight_sdk.mock.MockRateLimitError", g.ERROR_TYPE),
    ("mockresp_0123456789abcdef0123456789abcdef", g.RESPONSE_ID),
    ("chatcmpl-12345678901234567890", g.RESPONSE_ID),  # opaque: digit runs allowed
    ("5f0c6e1a-3b7d-4c2e-9a41-7d2b8e6f1c03", g.CALL_ID),
    ("demo-summarizer", g.LABEL),
    ("Demo Summarizer (Türkçe)", g.LABEL),
    # Phase 8A-1: identifiers the future free-plan providers would produce stay valid.
    ("groq", g.PROVIDER),
    ("openrouter", g.PROVIDER),
    ("openai/gpt-oss-20b", g.MODEL),
    ("google/gemma-4-26b-a4b-it:free", g.MODEL),
    ("chatcmpl-f51b2cd2-bef7-417e-964e-a08f0b513c22", g.RESPONSE_ID),
    ("gen-1762685000-AbCdEfGhIjKlMnOp", g.RESPONSE_ID),
]


@pytest.mark.parametrize(("value", "rule"), VALID)
def test_valid_metadata_passes_unchanged(value, rule):
    assert g.check(value, rule) == value


SECRETS = [
    "sk-" + "a" * 24,
    "lli_AbCd1234_" + "s" * 43,
    "AKIA" + "A" * 16,
    "ghp_" + "a" * 30,
    "xox" + "b-" + "1" * 12,  # split so the repository secret scan does not match
    "AIza" + "a" * 35,
    "gsk" + "_" + "Ab3" * 18,
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0",
    "-----BEGIN " + "PRIVATE KEY",
    "Bearer abcdef1234567890",
    "https://user:pass@example.com",
    "api_key=abcdef",
    "password: hunter22",
]


@pytest.mark.parametrize("secret", SECRETS)
@pytest.mark.parametrize("rule", [g.MODEL, g.LABEL, g.ERROR_TYPE, g.RESPONSE_ID, g.PROMPT_NAME])
def test_credentials_are_withheld_in_every_rule(secret, rule):
    assert g.check(secret, rule) is None
    assert g.check(f"prefix {secret} suffix", g.LABEL) is None


def _groq_key(length: int = 52) -> str:
    """A SYNTHETIC, non-working Groq-shaped key, assembled so no literal sits in the source."""
    return "gsk" + "_" + ("Ab3" * length)[:length]


# The prefix appears as a placeholder in Groq's documentation; the length is not documented
# and third-party scanners disagree (40+, 48, 52), so the rule must not depend on one length.
@pytest.mark.parametrize("length", [20, 40, 48, 52, 56, 80])
def test_a_groq_key_is_withheld_at_every_reported_length(length):
    assert g.contains_credential(_groq_key(length))
    assert g.check(_groq_key(length), g.MODEL) is None
    assert g.check(_groq_key(length), g.RESPONSE_ID) is None


@pytest.mark.parametrize(
    "carrier", ["{} suffix", "prefix {}", "GROQ_{}", "model/{}", "{}:free", "a-{}", "x={}"]
)
def test_a_groq_key_is_withheld_when_embedded(carrier):
    assert g.contains_credential(carrier.format(_groq_key()))


@pytest.mark.parametrize(
    "value",
    [
        "gsk",
        "gsk_",
        "gsk_short",
        "gsk" + "_" + "a" * 19,  # one character below the rule's minimum
        "gsk-model-v1",
        "gsk_test_model_name_version_1",  # underscores break the key's alphanumeric run
        "GSK_" + "A" * 24,  # the prefix is lower-case
        "bigsk" + "_" + "a" * 24,  # inside a longer word
        "risk_" + "a" * 24,
        "task-gsk-router",
    ],
)
def test_groq_lookalikes_are_not_credentials(value):
    assert not g.contains_credential(value)
    assert g.check(value, g.MODEL) == value


class _RecordingSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value


def test_a_groq_key_in_metadata_never_reaches_the_span_or_the_logs(caplog):
    key = _groq_key()
    span, withheld = _RecordingSpan(), []
    recorder = instrumentation.InferenceRecorder(span, withheld.append)  # type: ignore[arg-type]
    with caplog.at_level(logging.DEBUG):
        recorder.record_response(response_model=key, response_id=key, finish_reasons=[key])
        recorder.set_metadata("server.address", key)
    assert key not in repr(span.attributes)
    assert key not in caplog.text
    assert key not in repr(withheld)  # the counter receives attribute names, never values
    assert set(withheld) == {
        "gen_ai.response.model",
        "gen_ai.response.id",
        "gen_ai.response.finish_reasons",
        "server.address",
    }


@pytest.mark.parametrize(
    ("value", "rule"),
    [
        ("alice@example.com", g.MODEL),
        ("user-12345678901", g.MODEL),
        ("call +90 532 111 22 33", g.LABEL),
        ("card 4111 1111 1111 1111", g.LABEL),
        ("iban TR33 0006 1005 1978 6457 8413 26", g.LABEL),
    ],
)
def test_personal_data_is_withheld(value, rule):
    assert g.check(value, rule) is None


@pytest.mark.parametrize("ch", ["\x00", "\n", "\r", "\t", "\u202e", "\u200b", "\ufeff", " "])
def test_unsafe_characters_are_withheld(ch):
    assert g.check(f"mock{ch}chat", g.MODEL) is None
    assert g.check(f"label{ch}x", g.LABEL) is None


@pytest.mark.parametrize(
    ("value", "rule"),
    [
        ("mock chat", g.MODEL),  # space
        ("m" * 257, g.MODEL),  # too long: dropped, never truncated
        ("a:b", g.PROVIDER),
        ("Chat", g.OPERATION),
        ("v 1", g.PROMPT_VERSION),
        ("v" * 65, g.PROMPT_VERSION),
        ("rate limited by provider", g.ERROR_TYPE),
        ("not-a-uuid", g.CALL_ID),
        ("", g.LABEL),
        (None, g.MODEL),
        (123, g.MODEL),
    ],
)
def test_format_and_type_violations_are_withheld(value, rule):
    assert g.check(value, rule) is None


def test_labels_are_truncated_only_after_the_checks():
    assert g.check("x" * 300, g.LABEL) == "x" * 256
    # a secret beyond the truncation point still withholds the whole value
    assert g.check("x" * 300 + " sk-" + "a" * 24, g.LABEL) is None
