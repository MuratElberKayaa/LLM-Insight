"""Golden dataset `golden-synthetic-v1` (Phase 4C, B2): synthetic, stable, coherent, and the
engine reproduces every documented outcome."""

from datetime import UTC, datetime

from app.core import exact_json
from app.services import redaction
from app.services.case_rules import check_json_size
from app.services.evaluation import rules
from app.services.experiments import golden, runner
from app.services.experiments.mock_provider import GOLDEN_PROVIDER

VERSIONS = (golden.PROMPT_VERSION_A, golden.PROMPT_VERSION_B)


def records() -> list[runner.CaseRecord]:
    out = []
    for i, case in enumerate(golden.CASES, start=1):
        rule = exact_json.loads(exact_json.dumps(case.rule))
        expected = case.expected_output
        if expected is not None:
            expected = exact_json.loads(exact_json.dumps(expected))
        out.append(runner.CaseRecord(i, case.sanitized_input, expected, rule))
    return out


def test_at_least_ten_cases_with_stable_unique_keys_and_inputs():
    keys = [c.key for c in golden.CASES]
    assert len(keys) >= 10
    assert keys == [f"G{i:02d}" for i in range(1, len(keys) + 1)]
    assert len({c.sanitized_input for c in golden.CASES}) == len(keys)


def test_every_case_is_visibly_synthetic_and_passes_the_storage_rules():
    for case in golden.CASES:
        assert case.sanitized_input.startswith(f"SYNTHETIC {case.key}:")
        masked = redaction.redact_free_text(case.sanitized_input)
        assert masked.masked == {}, case.key  # stored exactly as written
        assert not redaction.has_unsafe_text_characters(case.sanitized_input)
        check_json_size("rule", case.rule)
        rules.validate_for_storage(
            exact_json.loads(exact_json.dumps(case.rule)),
            None
            if case.expected_output is None
            else exact_json.loads(exact_json.dumps(case.expected_output)),
        )
        for s in redaction.iter_json_strings([case.rule, case.expected_output]):
            assert not redaction.assertion_string_is_sensitive(s), case.key


def test_coverage_of_the_required_case_kinds():
    outcomes = {(v, c.expected[v]) for c in golden.CASES for v in VERSIONS}
    codes = {code for _, (_, code) in outcomes}
    types = {c.rule["type"] for c in golden.CASES}
    assert types == {"json_schema", "field_match", "exact", "regex"}
    assert {
        "pass",
        "schema_violation",
        "field_mismatch",
        "exact_mismatch",
        "regex_no_match",
    } <= codes
    assert {"output_not_json", "expected_output_missing", "provider_timeout"} <= codes
    formats = {c.rule.get("output_format") for c in golden.CASES if c.rule["type"] == "exact"}
    modes = {c.rule.get("match") for c in golden.CASES if c.rule["type"] == "regex"}
    assert formats == {"text", "json"} and modes == {"full", "search"}
    assert any(c.expected_output is None for c in golden.CASES)  # T40


def test_the_engine_reproduces_every_documented_outcome():
    results = runner.run(
        records(), VERSIONS, "synthetic-golden-model", GOLDEN_PROVIDER, [], datetime.now(UTC), None
    )
    assert len(results) == 2 * len(golden.CASES)
    for r in results:
        case = golden.CASES[r.test_case_id - 1]
        assert (r.passed, r.code) == case.expected[r.prompt_version], (case.key, r.prompt_version)


def test_scripted_answers_are_complete_and_synthetic():
    for case in golden.CASES:
        assert set(case.answers) == set(VERSIONS)
        for answer in case.answers.values():
            assert (answer.output is None) == (answer.error is not None)
            if answer.error is not None:
                assert answer.input_tokens is None and answer.duration_ms is None
    assert len(golden.SCRIPT) == 2 * len(golden.CASES)


def test_payloads_are_exact_json_and_standalone():
    payloads = golden.case_payloads()
    assert len(payloads) == len(golden.CASES)
    for p in payloads:
        assert p["approved"] is True and "origin_trace_id" not in p
        exact_json.dumps(p)  # no float anywhere
