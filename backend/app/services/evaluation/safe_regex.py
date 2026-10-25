"""Regular expressions of `regex` rules, on RE2 only (rule contract v1, Phase 4C).

A pattern comes from a person (or anyone holding the admin token), so it is untrusted. Python's
`re` backtracks: `(a+)+$` against a long run of `a`s takes exponential time (ReDoS). RE2
(`google-re2`) compiles to automata and matches in time linear in the input, and it refuses
the constructs that would need backtracking (backreferences, lookaround, possessive and
atomic groups) instead of falling back to another engine. The workload is bounded by:

- the pattern: 1–`MAX_PATTERN_CHARS` characters, no control or invisible characters, a
  compiled program of at most `MAX_PROGRAM_SIZE` instructions, and `RE2_MAX_MEM` bytes for
  the compiled program and its DFA cache (RE2 falls back to its NFA, still linear, when the
  DFA cache is full);
- the input: the caller passes at most `limits.MAX_OUTPUT_BYTES` of UTF-8 text.

RE2 writes a rejected pattern to stderr by default; `log_errors` is off so no pattern text
ever reaches a log. Errors are reported with a fixed message, never the pattern.

This is the only module that evaluates user patterns. User patterns never reach Python's
`re` (static test `tests/unit/test_evaluation_boundaries.py`).
"""

from typing import Any, Literal

import re2

from app.services.redaction import has_unsafe_label_characters

MAX_PATTERN_CHARS = 1000
# NFA matching costs O(input x program). Measured worst case on the dev machine (Phase 4C
# report §4): a 1471-instruction program that exhausts the DFA cache on 32 KiB of input took
# ~0.53 s; ordinary patterns run on the DFA in well under a millisecond.
MAX_PROGRAM_SIZE = 1500
RE2_MAX_MEM = 2 << 20  # 2 MiB: compiled program plus DFA cache, per pattern
MATCH_MODES = ("full", "search")

MatchMode = Literal["full", "search"]


class RegexRefused(ValueError):
    """The pattern is not accepted. `str()` is a fixed sentence without the pattern."""


def _options() -> Any:
    options = re2.Options()
    options.log_errors = False  # never print the pattern
    options.max_mem = RE2_MAX_MEM
    options.never_capture = True  # only match / no match is needed
    # Defaults kept on purpose: UTF-8, Perl-like syntax, case-sensitive, `.` excludes \n,
    # leftmost-first semantics. Inline flags such as (?i) or (?s) are part of the pattern.
    return options


def compile_pattern(pattern: object) -> Any:
    """The compiled RE2 program, or `RegexRefused`. Compiled programs are cached by `re2`
    (an LRU of 128 entries), so repeated evaluation does not recompile."""
    if not isinstance(pattern, str) or not 1 <= len(pattern) <= MAX_PATTERN_CHARS:
        raise RegexRefused(f"the pattern must be 1-{MAX_PATTERN_CHARS} characters")
    if has_unsafe_label_characters(pattern):
        raise RegexRefused("the pattern contains control or invisible characters")
    try:
        compiled = re2.compile(pattern, _options())
    except re2.error:
        raise RegexRefused(
            "the pattern is not valid RE2 syntax (backreferences, lookaround and possessive"
            " or atomic groups are not supported)"
        ) from None
    if compiled.programsize > MAX_PROGRAM_SIZE:
        raise RegexRefused("the pattern compiles to a program that is too large")
    return compiled


def matches(compiled: Any, text: str, mode: MatchMode) -> bool:
    """`full`: the whole text must match. `search`: a match anywhere in the text."""
    if mode == "full":
        return compiled.fullmatch(text) is not None
    if mode == "search":
        return compiled.search(text) is not None
    raise ValueError("unknown match mode")
