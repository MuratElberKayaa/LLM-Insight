"""Client-side copy of the R08 value rules (TELEMETRY_CONTRACT.md §3.1). The live-backend
integration test checks the same values end to end (server_dropped_attributes_count = 0)."""

from __future__ import annotations

import pytest

from llm_insight_sdk import _guards as g

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
