"""Answer extraction and terminal preview of the Gemini content demo (Phase 7B, ADR-39).
Offline: responses come from the SYNTHETIC fixture bodies, parsed by the pinned library."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any

import pytest
from google.genai import types as genai_types

from tests.gemini_fixtures import OUTPUT_SENTINEL, THOUGHT_SENTINEL, candidate, gemini_body

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))  # demo_app lives at the repository root, outside the SDK

from demo_app import content_preview as cp  # noqa: E402


def response(**kw: Any) -> genai_types.GenerateContentResponse:
    return genai_types.GenerateContentResponse.model_validate(gemini_body(**kw))


def with_parts(parts: list[dict[str, Any]], finish: str = "STOP") -> Any:
    body = candidate(finish)
    body["content"] = {"parts": parts, "role": "model"}
    return response(candidates=[body])


class Terminal(io.StringIO):
    encoding = "utf-8"

    def isatty(self) -> bool:
        return True


# ---------------------------------------------------------------- extraction
def test_thought_parts_are_excluded_and_counted():
    answer = cp.extract_answer(response())
    assert answer.text == OUTPUT_SENTINEL
    assert THOUGHT_SENTINEL not in (answer.text or "")
    assert (answer.thought_parts_excluded, answer.non_text_parts) == (1, 0)


def test_several_text_parts_are_joined_in_order():
    answer = cp.extract_answer(with_parts([{"text": "one "}, {"text": "two"}]))
    assert answer.text == "one two"


def test_a_thought_only_answer_has_no_text():
    answer = cp.extract_answer(with_parts([{"text": THOUGHT_SENTINEL, "thought": True}]))
    assert answer.text is None and answer.thought_parts_excluded == 1


def test_an_empty_text_part_with_a_signature_is_harmless():
    answer = cp.extract_answer(
        with_parts([{"text": "visible"}, {"text": "", "thoughtSignature": "U0lH"}])
    )
    assert answer.text == "visible" and answer.non_text_parts == 0


@pytest.mark.parametrize(
    "part",
    [
        {"functionCall": {"name": "lookup", "args": {}}},
        {"inlineData": {"mimeType": "text/plain", "data": "U1lO"}},
        {"executableCode": {"language": "PYTHON", "code": "x"}},
    ],
)
def test_any_non_text_part_withholds_the_whole_answer(part):
    answer = cp.extract_answer(with_parts([{"text": OUTPUT_SENTINEL}, part]))
    assert answer.text is None and answer.non_text_parts == 1


@pytest.mark.parametrize(
    "value",
    [
        None,
        object(),
        response(candidates=[]),
        with_parts([]),
    ],
)
def test_a_response_without_parts_has_no_text(value):
    answer = cp.extract_answer(value)
    assert (answer.text, answer.thought_parts_excluded, answer.non_text_parts) == (None, 0, 0)


def test_extraction_never_uses_the_convenience_accessor():
    source = Path(cp.__file__).read_text(encoding="utf-8")
    assert "response.text" not in source and "provider_response" not in source
    assert "model_dump" not in source and "to_json_dict" not in source


# ---------------------------------------------------------------- escaping
@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("\x1b[31m", "\\x1b[31m"),  # CSI colour
        ("\x1b]0;title\x07", "\\x1b]0;title\\x07"),  # OSC title + BEL
        ("\r", "\\x0d"),
        ("\t", "\\x09"),
        ("\x00", "\\x00"),
        ("\x7f", "\\x7f"),
        ("\x9b", "\\x9b"),  # C1 CSI
        ("‮", "\\u202e"),  # right-to-left override
        ("⁦", "\\u2066"),  # isolate
        ("‏", "\\u200f"),
        ("​", "\\u200b"),  # zero-width space
        ("‍", "\\u200d"),
        ("﻿", "\\ufeff"),
        (" ", "\\u2028"),  # line separator
        (" ", "\\u2029"),
        ("\ud800", "\\ud800"),  # lone surrogate
        ("\U000e0041", "\\U000e0041"),  # tag character
    ],
)
def test_terminal_unsafe_characters_become_visible_text(raw, shown):
    text, truncated = cp.bounded_escape(f"a{raw}b", max_chars=100, max_lines=5)
    assert text == f"a{shown}b" and truncated is False
    assert raw not in text


def test_printable_text_and_newlines_are_kept():
    text, _ = cp.bounded_escape("Köln — ok\nşğı", max_chars=100, max_lines=5)
    assert text == "Köln — ok\nşğı"


# ---------------------------------------------------------------- bounds
def test_length_is_bounded_after_escaping_and_never_cuts_an_escape():
    text, truncated = cp.bounded_escape("\x1b" * 100, max_chars=10, max_lines=5)
    assert truncated is True and text == "\\x1b" * 2


def test_line_count_is_bounded():
    text, truncated = cp.bounded_escape("a\n" * 100, max_chars=10_000, max_lines=3)
    assert truncated is True and text.count("\n") == 2


def test_exact_limits_are_not_truncated():
    assert cp.bounded_escape("x" * 10, max_chars=10, max_lines=1) == ("x" * 10, False)


def test_rendered_output_stays_within_the_preview_bounds():
    long_answer = cp.AnswerText("y" * 5000, 0, 0)
    rendered = cp.render_preview("input", long_answer)
    assert rendered.truncated is True and rendered.output_chars == 5000
    body = [line for line in rendered.text.splitlines() if "y" in line and "---" not in line]
    assert sum(len(line) - len(cp.LINE_PREFIX) for line in body) == cp.MAX_OUTPUT_PREVIEW_CHARS
    assert cp.TRUNCATION_MARKER in rendered.text


def test_every_line_carries_the_prefix():
    rendered = cp.render_preview("first\nsecond", cp.AnswerText("third\nfourth", 0, 0))
    assert all(line.startswith(cp.LINE_PREFIX) for line in rendered.text.splitlines())


def test_a_provider_truncated_answer_is_labelled():
    rendered = cp.render_preview("input", cp.AnswerText("partial", 0, 0), incomplete=True)
    assert "TRUNCATED BY THE PROVIDER" in rendered.text
    complete = cp.render_preview("input", cp.AnswerText("full", 0, 0))
    assert "TRUNCATED" not in complete.text


def test_a_dry_run_preview_contains_the_input_only():
    rendered = cp.render_preview("the input", None)
    assert "the input" in rendered.text and "MODEL ANSWER" not in rendered.text
    assert rendered.output_chars is None and rendered.output_shown is False


def test_no_output_is_rendered_when_the_answer_was_withheld():
    rendered = cp.render_preview("input", cp.AnswerText(None, 1, 1))
    assert "MODEL ANSWER" not in rendered.text and rendered.output_shown is False


# ---------------------------------------------------------------- when a preview may be written
def test_a_real_terminal_outside_ci_is_allowed():
    assert cp.preview_problem(Terminal(), {}) is None


def test_a_plain_stream_is_refused():
    assert cp.preview_problem(io.StringIO(), {}) == "stderr_not_a_terminal"


@pytest.mark.parametrize("name", ["CI", "GITHUB_ACTIONS"])
def test_ci_is_refused_even_on_a_terminal(name):
    assert cp.preview_problem(Terminal(), {name: "true"}) == "ci_environment"


def test_an_empty_ci_variable_is_treated_as_unset():
    assert cp.preview_problem(Terminal(), {"CI": "  "}) is None


def test_a_closed_stream_is_refused():
    stream = io.StringIO()
    stream.close()
    assert cp.preview_problem(stream, {}) == "stderr_not_a_terminal"


def test_write_reports_failure_instead_of_raising():
    stream = io.StringIO()
    stream.close()
    assert cp.write_preview(stream, "text") is False


def test_write_escapes_what_the_terminal_cannot_encode():
    raw = io.BytesIO()
    wrapper = io.TextIOWrapper(raw, encoding="ascii", write_through=True)
    assert cp.write_preview(wrapper, "café") is True
    assert raw.getvalue() == b"caf\\xe9"
