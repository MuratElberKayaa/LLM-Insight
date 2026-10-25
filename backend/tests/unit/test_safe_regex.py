"""RE2-only regex rules (Phase 4C, A2): ReDoS resistance, refused syntax, bounds, no logging."""

import random
import time

import pytest

from app.services.evaluation import rules, safe_regex
from app.services.evaluation.engine import evaluate

LIMIT = rules.MAX_OUTPUT_BYTES

# Each is catastrophic on a backtracking engine (exponential or high-polynomial time).
PATHOLOGICAL = [
    (r"(a+)+$", "a" * (LIMIT - 1) + "!"),
    (r"(a|aa)*b", "a" * LIMIT),
    (r"(x+x+)+y", "x" * LIMIT),
    (r"(a|a)*c", "a" * LIMIT),
    (r"^(\w+\s?)*$", "word " * (LIMIT // 5 - 1) + "!"),
    (r"(.*a){12}", "a" * LIMIT),
]


@pytest.mark.parametrize(
    ("pattern", "text"), PATHOLOGICAL, ids=[f"pathological-{i}" for i in range(len(PATHOLOGICAL))]
)
@pytest.mark.parametrize("mode", ["full", "search"])
def test_pathological_patterns_finish_in_linear_time(pattern, text, mode):
    compiled = safe_regex.compile_pattern(pattern)
    started = time.perf_counter()
    safe_regex.matches(compiled, text, mode)
    assert time.perf_counter() - started < 1.0  # ~ms on RE2; minutes or more when backtracking


def test_worst_case_nfa_at_the_caps_stays_bounded():
    """A program near `MAX_PROGRAM_SIZE` that exhausts the DFA cache (forcing RE2's NFA) on
    the largest evaluated input: measured ~0.5 s on the dev machine; 5 s is the CI bound."""
    rng = random.Random(1)  # noqa: S311 - test data, not cryptography
    text = "".join(rng.choice("ab") for _ in range(LIMIT))
    compiled = safe_regex.compile_pattern(r"(?:[ab]{0,40}a[ab]{0,40}b){1,9}c")
    assert 1000 < compiled.programsize <= safe_regex.MAX_PROGRAM_SIZE
    started = time.perf_counter()
    safe_regex.matches(compiled, text, "search")
    assert time.perf_counter() - started < 5.0


def test_engine_evaluates_a_pathological_regex_rule_quickly():
    rule = {"version": 1, "type": "regex", "pattern": r"(a+)+$", "match": "full"}
    started = time.perf_counter()
    outcome = evaluate(rule, "aaa", "a" * (LIMIT - 1) + "!")
    assert time.perf_counter() - started < 1.0
    assert outcome.passed is False and outcome.code == "regex_no_match"


@pytest.mark.parametrize(
    "pattern",
    [
        r"(a)\1",  # backreference
        r"(?P<n>a)(?P=n)",  # named backreference
        r"(?=a)a",  # lookahead
        r"(?!a)b",
        r"(?<=a)b",  # lookbehind
        r"(?<!a)b",
        r"a++",  # possessive
        r"(?>a)",  # atomic group
        r"\Z",  # Python-only escape
        r"a{1001}",  # repetition beyond RE2's bound
        r"(",
        "",
        "x" * 1001,
        "a‮b",  # bidi override
        "a\x00b",  # control character
        r"[\p{L}\p{N}]{1,400}",  # compiles beyond the program size cap
    ],
)
def test_refused_patterns(pattern):
    with pytest.raises(safe_regex.RegexRefused):
        safe_regex.compile_pattern(pattern)


def test_refusal_does_not_log_or_echo_the_pattern(capfd):
    pattern = "(?P<n>leak-pattern-41)(?P=n)"
    with pytest.raises(safe_regex.RegexRefused) as info:
        safe_regex.compile_pattern(pattern)
    out, err = capfd.readouterr()
    assert "leak-pattern-41" not in str(info.value)
    assert "leak-pattern-41" not in out and "leak-pattern-41" not in err  # log_errors is off


def test_full_versus_search_semantics_and_inline_flags():
    c = safe_regex.compile_pattern(r"[A-Z]{3}-[0-9]{4}")
    assert safe_regex.matches(c, "TCK-0042", "full")
    assert not safe_regex.matches(c, "Ticket TCK-0042", "full")
    assert safe_regex.matches(c, "Ticket TCK-0042", "search")
    i = safe_regex.compile_pattern(r"(?i)refund")
    assert safe_regex.matches(i, "We approved the REFUND.", "search")
    dot = safe_regex.compile_pattern(r"a.b")
    assert not safe_regex.matches(dot, "a\nb", "full")  # `.` excludes newline by default
    assert safe_regex.matches(safe_regex.compile_pattern(r"(?s)a.b"), "a\nb", "full")


def test_unicode_text_is_matched_as_utf8_code_points():
    c = safe_regex.compile_pattern(r"çağrı [0-9]+")
    assert safe_regex.matches(c, "çağrı 42", "full")
    assert safe_regex.matches(safe_regex.compile_pattern(r"^.{5}$"), "çağrı", "full")


def test_matching_is_deterministic_and_reuses_the_compiled_program():
    first = safe_regex.compile_pattern(r"(?i)refund")
    assert safe_regex.compile_pattern(r"(?i)refund") is first  # re2's LRU cache
    results = {safe_regex.matches(first, "Refund approved", "search") for _ in range(50)}
    assert results == {True}


def test_unknown_mode_is_an_error():
    with pytest.raises(ValueError):
        safe_regex.matches(safe_regex.compile_pattern("a"), "a", "contains")  # type: ignore[arg-type]
