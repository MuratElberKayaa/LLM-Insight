"""Answer extraction and the opt-in terminal preview of the Gemini content demo (ADR-39).

`extract_answer` reads `candidates[0].content.parts` explicitly and keeps only non-thought
text. It never touches the response's convenience text accessor, and a part of any other
type (function call, inline data, ...) withholds the whole answer, so only reviewed content
reaches validation or the preview.

The preview goes to stderr only, on request, on a real terminal, bounded and escaped. It is
never exported, stored or returned by an API. **A terminal's scrollback, a screen recording
or a capture tool can still keep what it shows.**
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TextIO

MAX_INPUT_PREVIEW_CHARS = 1200
MAX_OUTPUT_PREVIEW_CHARS = 2000
MAX_OUTPUT_PREVIEW_LINES = 40
LINE_PREFIX = "preview| "
TRUNCATION_MARKER = "[preview truncated]"
# Control, format (bidi, zero-width, tag characters), surrogate, private-use, unassigned,
# line separator and paragraph separator.
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})
_CI_VARIABLES = ("CI", "GITHUB_ACTIONS")


@dataclass(frozen=True)
class AnswerText:
    text: str | None  # None: no reviewable text (see non_text_parts)
    thought_parts_excluded: int
    non_text_parts: int


def extract_answer(response: object) -> AnswerText:
    """The user-facing text of the first candidate. Thought parts are never included."""
    candidates = getattr(response, "candidates", None)
    if not isinstance(candidates, list) or not candidates:
        return AnswerText(None, 0, 0)
    parts = getattr(getattr(candidates[0], "content", None), "parts", None)
    if not isinstance(parts, list) or not parts:
        return AnswerText(None, 0, 0)
    kept: list[str] = []
    thoughts = 0
    others = 0
    for part in parts:
        if getattr(part, "thought", None):
            thoughts += 1
            continue
        text = getattr(part, "text", None)
        if isinstance(text, str):
            kept.append(text)
        else:
            others += 1
    if others or not kept:
        return AnswerText(None, thoughts, others)
    return AnswerText("".join(kept), thoughts, others)


def preview_problem(err: TextIO, env: Mapping[str, str]) -> str | None:
    """Why a preview may not be written, or None. Refuses CI and anything but a terminal."""
    if any((env.get(name) or "").strip() for name in _CI_VARIABLES):
        return "ci_environment"
    try:
        terminal = err.isatty()
    except (OSError, ValueError):
        terminal = False
    return None if terminal else "stderr_not_a_terminal"


def escape_char(ch: str) -> str:
    """`ch` unchanged if it is safe on a terminal, else visible `\\xNN` / `\\uNNNN` text."""
    if unicodedata.category(ch) not in _UNSAFE_CATEGORIES:
        return ch
    code = ord(ch)
    if code < 0x100:
        return f"\\x{code:02x}"
    if code < 0x10000:
        return f"\\u{code:04x}"
    return f"\\U{code:08x}"


def bounded_escape(text: str, *, max_chars: int, max_lines: int) -> tuple[str, bool]:
    """Escaped `text`, at most `max_chars` displayed characters and `max_lines` lines. A
    newline is the only control character kept. An escape sequence is never cut."""
    kept: list[str] = []
    used = 0
    lines = 1
    for ch in text:
        if ch == "\n":
            if lines >= max_lines:
                return "".join(kept), True
            lines += 1
            kept.append(ch)
            continue
        piece = escape_char(ch)
        if used + len(piece) > max_chars:
            return "".join(kept), True
        kept.append(piece)
        used += len(piece)
    return "".join(kept), False


@dataclass(frozen=True)
class RenderedPreview:
    text: str
    input_chars: int
    output_chars: int | None
    truncated: bool
    output_shown: bool


def _section(title: str, body: str, truncated: bool) -> list[str]:
    lines = [f"--- {title} ---", *body.split("\n")]
    if truncated:
        lines.append(TRUNCATION_MARKER)
    return lines


def render_preview(
    input_text: str, answer: AnswerText | None, *, incomplete: bool = False
) -> RenderedPreview:
    """`answer` None: the input only (a dry run). A truncated provider answer is labelled."""
    shown_input, input_cut = bounded_escape(
        input_text.strip(), max_chars=MAX_INPUT_PREVIEW_CHARS, max_lines=MAX_OUTPUT_PREVIEW_LINES
    )
    lines = _section("SYNTHETIC INPUT (local preview, not exported)", shown_input, input_cut)
    output_chars: int | None = None
    output_cut = False
    output_shown = False
    if answer is not None and answer.text is not None:
        output_chars = len(answer.text)
        shown_output, output_cut = bounded_escape(
            answer.text.strip(),
            max_chars=MAX_OUTPUT_PREVIEW_CHARS,
            max_lines=MAX_OUTPUT_PREVIEW_LINES,
        )
        title = "MODEL ANSWER, TRUNCATED BY THE PROVIDER" if incomplete else "MODEL ANSWER"
        lines += _section(f"{title} (local preview, not exported)", shown_output, output_cut)
        output_shown = True
    text = "".join(f"{LINE_PREFIX}{line}\n" for line in lines)
    return RenderedPreview(
        text, len(input_text), output_chars, input_cut or output_cut, output_shown
    )


def write_preview(err: TextIO, text: str) -> bool:
    """Write to stderr; a terminal that cannot encode a character shows it escaped. A write
    failure is reported, never raised: the preview must not change the run's outcome."""
    encoding = getattr(err, "encoding", None) or "utf-8"
    try:
        err.write(text.encode(encoding, "backslashreplace").decode(encoding))
        err.flush()
    except (OSError, ValueError, LookupError):
        return False
    return True
