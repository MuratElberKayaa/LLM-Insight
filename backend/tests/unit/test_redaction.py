"""Phase 4B human-input checks (`services/redaction.py`, T35 unit part). All values are
SYNTHETIC: fake addresses on reserved domains, fake numbers and fake key-shaped strings."""

import pytest

from app.services import redaction

# Built at runtime so no key-shaped literal sits in the source (secret scanners stay quiet).
FAKE_OPENAI_KEY = "sk-" + "SYNTHETIC" + "a1b2c3d4e5f6g7h8"
FAKE_LLI_KEY = "lli_" + "Ab12Cd34" + "_" + "SYNTHETICsecretvalue0123"


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("contact jane.doe@example.invalid please", "email"),
        ("call +90 532 123 45 67 today", "phone"),
        ("call +1 (555) 010-9999 today", "phone"),
        ("mobile 0532 123 45 67", "number"),
        ("national id 12345678901", "number"),
        ("card 4111 1111 1111 1111", "number"),
        ("iban TR33 0006 1005 1978 6457 8413 26", "iban"),
        ("iban GB82WEST12345698765432", "iban"),
        (f"key {FAKE_OPENAI_KEY} leaked", "credential"),
        (f"ingest {FAKE_LLI_KEY}", "credential"),
        ("Authorization: Bearer abcDEF123456789xyz", "credential"),
        ("password=hunter2hunter2", "credential"),
        ("db postgresql://user:pw@localhost/x", "credential"),
    ],
)
def test_sensitive_values_are_masked(text, category):
    out = redaction.redact_free_text(text)
    assert f"[REDACTED:{category}]" in out.text
    assert out.masked.get(category, 0) >= 1
    for fragment in ("jane.doe", "532 123", "12345678901", "4111", "hunter2", FAKE_OPENAI_KEY):
        assert fragment not in out.text


def test_plain_text_is_unchanged_and_reports_nothing():
    text = (
        "validate_output failed rule json_schema:summary_v1 on 2026-10-25;\n"
        "prompt v2 returned 3 fields, expected 4 (gpt-4o-mini-2024-07-18)."
    )
    out = redaction.redact_free_text(text)
    assert out.text == text
    assert out.masked == {} and out.masked_total == 0


def test_assignment_after_masked_key_is_counted_once():
    out = redaction.redact_free_text(f"secret: {FAKE_OPENAI_KEY}")
    assert out.masked == {"credential": 1}
    assert FAKE_OPENAI_KEY not in out.text


def test_masks_never_rematch():
    """A mask has no digit and no `@`, so a later pass cannot mask a mask."""
    out = redaction.redact_free_text("a@example.invalid 12345678901 a@example.invalid")
    assert out.masked == {"email": 2, "number": 1}
    assert out.text == "[REDACTED:email] [REDACTED:number] [REDACTED:email]"


@pytest.mark.parametrize("char", ["\u202e", "\u200b", "\x00", "\x1b", "\u2028", "\ue000"])
def test_unsafe_characters_detected(char):
    assert redaction.has_unsafe_text_characters(f"a{char}b")
    assert redaction.has_unsafe_label_characters(f"a{char}b")


def test_tab_and_newline_allowed_in_free_text_only():
    assert not redaction.has_unsafe_text_characters("line 1\n\tline 2")
    assert redaction.has_unsafe_label_characters("line 1\nline 2")


def test_newlines_normalized():
    assert redaction.normalize_newlines("a\r\nb\rc\n") == "a\nb\nc\n"


@pytest.mark.parametrize(
    "value",
    ["jane@example.invalid", "+90 532 123 45 67", FAKE_OPENAI_KEY, "token=abcdef123"],
)
def test_labels_with_contact_data_or_secrets_are_sensitive(value):
    assert redaction.label_is_sensitive(value)


@pytest.mark.parametrize("value", ["Reviewer A (QA)", "qa-team", "regression set v1"])
def test_ordinary_labels_are_not_sensitive(value):
    assert not redaction.label_is_sensitive(value)


def test_iter_json_strings_covers_keys_and_nested_values():
    doc = {"a": ["x", {"b@example.invalid": 1}], "c": {"d": "y"}, "n": 5}
    assert set(redaction.iter_json_strings(doc)) == {
        "a",
        "x",
        "b@example.invalid",
        "c",
        "d",
        "y",
        "n",
    }
