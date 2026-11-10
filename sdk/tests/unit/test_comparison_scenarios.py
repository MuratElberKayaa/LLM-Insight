"""Comparison scenarios `demo_app.comparison_scenarios` (Phase 8A-4): pinned SYNTHETIC inputs,
identity, safe form and deterministic validators. Offline; nothing here builds a client."""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest

from llm_insight_sdk import _guards, gemini_live, groq_live
from llm_insight_sdk.live import input_token_bound

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))  # demo_app lives at the repository root, outside the SDK

from demo_app import comparison_scenarios as cs  # noqa: E402
from demo_app import gemini_scenarios  # noqa: E402

TICKET, USAGE, SUMMARY = "cmp-ticket-triage", "cmp-json-aggregate", "cmp-release-summary"
# The pins, and the drafts of reports/phase-8a-0.md §6 they must equal (size, prefix, suffix).
PINS = {
    TICKET: ("5d9040f3a67d37e5edaf4dab90a9a59ff236061c729c7b62dcd0a39812c267ff", 621),
    USAGE: ("89684d9402c71ad6681fbd77ad6199473612a5859da9580fa18dd537bcb31a37", 661),
    SUMMARY: ("815eef603170295620d27a787ff3e8345384cc43e56b016bb4d6976ee135ea47", 595),
}
DRAFTS = {
    TICKET: ("5d9040f3", "c267ff", 621),
    USAGE: ("89684d94", "b31a37", 661),
    SUMMARY: ("815eef60", "35ea47", 595),
}
GOOD = {
    TICKET: json.dumps({"category": "login", "priority": "high", "reason": "Sign-in fails."}),
    USAGE: json.dumps(
        {
            "total_requests": 1000,
            "highest_error_day": "Wed",
            "error_rate_percent": 3.0,
            "observation": "Errors peaked on Wednesday.",
        }
    ),
    SUMMARY: "Boards load faster and tasks can be paused. Known issue: large imports can stall.",
}


def changed(scenario_id: str, **fields: object) -> cs.ComparisonScenario:
    return dataclasses.replace(cs.SCENARIOS[scenario_id], **fields)  # type: ignore[arg-type]


def codes(scenario_id: str, scenario: cs.ComparisonScenario) -> set[str]:
    return {r.code for r in cs.check_scenario(scenario_id, scenario)}


# ---------------------------------------------------------------- registry and pins
def test_the_registry_holds_exactly_the_three_reviewed_scenarios():
    assert list(cs.SCENARIOS) == [TICKET, USAGE, SUMMARY]
    assert cs.registry_problems() == []
    with pytest.raises(TypeError):
        cs.SCENARIOS["extra"] = cs.SCENARIOS[TICKET]  # type: ignore[index]


@pytest.mark.parametrize("scenario_id", [TICKET, USAGE, SUMMARY])
def test_each_composed_input_matches_its_pin_and_the_reviewed_draft(scenario_id):
    scenario = cs.SCENARIOS[scenario_id]
    text = cs.compose_input(scenario)
    sha, size = PINS[scenario_id]
    prefix, suffix, draft_size = DRAFTS[scenario_id]
    assert cs.input_sha256(text) == scenario.input_sha256 == sha
    assert sha.startswith(prefix) and sha.endswith(suffix)
    assert len(text.encode("utf-8")) == scenario.input_utf8_bytes == size == draft_size
    assert text.startswith(gemini_scenarios.PREAMBLE + "\n\n")
    assert cs.check_scenario(scenario_id, scenario) == []


@pytest.mark.parametrize("scenario_id", [TICKET, USAGE, SUMMARY])
def test_each_input_fits_the_shared_caps_and_uses_the_uniform_output_cap(scenario_id):
    scenario = cs.SCENARIOS[scenario_id]
    assert input_token_bound(cs.compose_input(scenario)) <= cs.INPUT_TOKEN_CAP
    assert scenario.max_output_tokens == cs.UNIFORM_OUTPUT_TOKENS == 1024


def test_the_shared_caps_equal_both_provider_guards():
    assert cs.INPUT_TOKEN_CAP == groq_live.MAX_INPUT_TOKENS_CAP == gemini_live.MAX_INPUT_TOKENS_CAP
    assert cs.OUTPUT_TOKEN_CAP == groq_live.MAX_OUTPUT_TOKENS_CAP
    assert cs.OUTPUT_TOKEN_CAP == gemini_live.MAX_OUTPUT_TOKENS_CAP
    assert cs.MIN_OUTPUT_TOKENS == groq_live.MIN_OUTPUT_TOKENS == gemini_live.MIN_OUTPUT_TOKENS


# ---------------------------------------------------------------- identity (finding F2)
def test_every_scenario_has_its_own_prompt_name_and_version():
    versions = [s.prompt_version for s in cs.SCENARIOS.values()]
    names = [s.prompt_name for s in cs.SCENARIOS.values()]
    assert versions == ["cmp-v1.ticket-triage", "cmp-v1.json-aggregate", "cmp-v1.release-summary"]
    assert len(set(versions)) == 3 and len(set(names)) == 3


def test_no_comparison_identity_collides_with_the_gemini_demo():
    gemini = gemini_scenarios.SCENARIOS.values()
    assert not {s.prompt_version for s in cs.SCENARIOS.values()} & {
        g.prompt_version for g in gemini
    }
    assert not {s.prompt_name for s in cs.SCENARIOS.values()} & {g.prompt_name for g in gemini}
    assert not set(cs.SCENARIOS) & set(gemini_scenarios.SCENARIOS)


@pytest.mark.parametrize("scenario_id", [TICKET, USAGE, SUMMARY])
def test_the_identity_passes_the_telemetry_rules_unchanged(scenario_id):
    scenario = cs.SCENARIOS[scenario_id]
    assert _guards.check(scenario.prompt_version, _guards.PROMPT_VERSION) == scenario.prompt_version
    assert _guards.check(scenario.prompt_name, _guards.PROMPT_NAME) == scenario.prompt_name


def test_a_shared_prompt_version_is_a_registry_problem(monkeypatch):
    first = cs._REGISTERED[0]
    clash = dataclasses.replace(cs._REGISTERED[1], prompt_version=first.prompt_version)
    monkeypatch.setattr(cs, "_REGISTERED", (first, clash, cs._REGISTERED[2]))
    assert "scenario_identity_invalid" in {p.code for p in cs.registry_problems()}


def test_a_shared_prompt_name_is_caught_only_by_the_registry_check(monkeypatch):
    first = cs._REGISTERED[0]
    clash = dataclasses.replace(cs._REGISTERED[1], prompt_name=first.prompt_name)
    assert cs.check_scenario(clash.id, clash) == []  # each entry alone is valid
    monkeypatch.setattr(cs, "_REGISTERED", (first, clash, cs._REGISTERED[2]))
    assert {p.code for p in cs.registry_problems()} == {"scenario_identity_invalid"}


def test_an_id_outside_the_cmp_namespace_is_refused_even_when_consistent():
    entry = changed(TICKET, id="ticket-triage")  # its derived prompt version is unchanged
    assert entry.prompt_version == cs.prompt_version_for("ticket-triage", 1)
    assert codes("ticket-triage", entry) == {"scenario_identity_invalid"}


def test_version_zero_is_refused_even_with_a_matching_prompt_version():
    entry = changed(TICKET, version=0, prompt_version=cs.prompt_version_for(TICKET, 0))
    assert codes(TICKET, entry) == {"scenario_identity_invalid"}


def test_the_prompt_version_follows_the_scenario_version():
    assert cs.prompt_version_for(TICKET, 2) == "cmp-v2.ticket-triage"
    assert "scenario_identity_invalid" in codes(TICKET, changed(TICKET, version=2))


# ---------------------------------------------------------------- tampering
@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({"fixture": cs.SCENARIOS[TICKET].fixture + " "}, "scenario_hash_mismatch"),
        ({"task": "Reply with anything."}, "scenario_hash_mismatch"),
        ({"label": "TICKETS"}, "scenario_hash_mismatch"),
        ({"input_sha256": "0" * 64}, "scenario_hash_mismatch"),
        ({"input_utf8_bytes": 620}, "scenario_input_out_of_bounds"),
        ({"id": "ticket-triage"}, "scenario_identity_invalid"),
        ({"version": 0}, "scenario_identity_invalid"),
        ({"prompt_version": "v1"}, "scenario_identity_invalid"),
        ({"prompt_version": "cmp-v1.json-aggregate"}, "scenario_identity_invalid"),
        ({"prompt_name": "bad name"}, "scenario_identity_invalid"),
        ({"max_output_tokens": 512}, "scenario_output_cap_invalid"),
        ({"max_output_tokens": 2048}, "scenario_output_cap_invalid"),
        ({"fixture": "Ticket with a control\x07 character"}, "scenario_fixture_invalid"),
        ({"fixture": "Reach me at someone@example.invalid"}, "scenario_fixture_invalid"),
        ({"fixture": "See https://example.invalid/x"}, "scenario_fixture_invalid"),
        ({"fixture": "Key sk-abc"}, "scenario_fixture_invalid"),
        ({"fixture": "   "}, "scenario_fixture_invalid"),
    ],
)
def test_each_tampered_entry_is_refused(fields, code):
    assert code in codes(TICKET, changed(TICKET, **fields))


def test_a_registry_key_other_than_the_id_is_refused():
    assert "scenario_identity_invalid" in codes(USAGE, cs.SCENARIOS[TICKET])


@pytest.mark.parametrize(
    "fixture",
    [
        '{"dataset":"x","days":[],"unit":"requests"}',
        '{"dataset":"x","days":[{"day":"Mon","errors":1,"requests":2}],"unit":"r","model":"x"}',
        '{"dataset":"x","days":[{"day":"Mon","errors":1,"requests":0}],"unit":"requests"}',
        '{"dataset":"x","days":[{"day":"Mon","errors":true,"requests":2}],"unit":"requests"}',
        '{"dataset":"x","days":[{"day":"Mon","errors":2,"requests":9},'
        '{"day":"Tue","errors":2,"requests":9}],"unit":"requests"}',
        "not json",
    ],
)
def test_the_usage_fixture_must_keep_its_exact_unambiguous_shape(fixture):
    assert cs._usage_fixture_problem(fixture) is True


def test_the_shipped_usage_fixture_has_one_highest_error_day():
    assert cs._usage_fixture_problem(cs.SCENARIOS[USAGE].fixture) is False
    assert cs._usage_expected() == (1000, "Wed", 3.0)


@pytest.mark.parametrize("scenario_id", [TICKET, USAGE, SUMMARY])
def test_every_fixture_is_labelled_synthetic_or_fictional(scenario_id):
    fixture = cs.SCENARIOS[scenario_id].fixture
    assert "SYNTHETIC" in fixture or "fictional" in fixture


# ---------------------------------------------------------------- validators
@pytest.mark.parametrize("scenario_id", [TICKET, USAGE, SUMMARY])
def test_a_good_answer_passes_every_rule(scenario_id):
    result = cs.validate_answer(cs.SCENARIOS[scenario_id], GOOD[scenario_id])
    assert result.status == "passed" and result.reason is None
    assert result.rules and all(ok for _, ok in result.rules)


def test_the_ticket_rules_each_fail_on_their_own():
    scenario = cs.SCENARIOS[TICKET]

    def failed(answer: dict[str, object]) -> set[str]:
        result = cs.validate_answer(scenario, json.dumps(answer))
        return {name for name, ok in result.rules if not ok}

    good = json.loads(GOOD[TICKET])
    assert failed({**good, "category": "billing"}) == {"expected_category"}
    assert failed({**good, "category": "outage"}) == {"category_enum", "expected_category"}
    assert failed({**good, "priority": "urgent"}) == {"priority_enum"}
    assert failed({**good, "reason": "x" * 121}) == {"reason_length"}
    assert failed({**good, "extra": 1}) == {"exact_keys"}
    duplicate = cs.validate_answer(
        scenario, '{"category":"login","category":"login","priority":"low","reason":"r"}'
    )
    assert ("no_duplicate_keys", False) in duplicate.rules


def test_the_usage_rules_check_the_exact_aggregate():
    scenario = cs.SCENARIOS[USAGE]
    good = json.loads(GOOD[USAGE])

    def failed(**changes: object) -> set[str]:
        result = cs.validate_answer(scenario, json.dumps({**good, **changes}))
        return {name for name, ok in result.rules if not ok}

    assert failed(total_requests=999) == {"expected_total"}
    assert failed(total_requests="1000") == {"total_requests_integer", "expected_total"}
    assert failed(total_requests=True) == {"total_requests_integer", "expected_total"}
    assert failed(highest_error_day="Thu") == {"expected_day"}
    assert failed(error_rate_percent=3.04) == set()
    assert failed(error_rate_percent=3.06) == {"expected_rate"}
    assert failed(error_rate_percent="3.0") == {"expected_rate"}
    assert failed(observation="") == {"observation_length"}


def test_the_summary_rules():
    scenario = cs.SCENARIOS[SUMMARY]

    def failed(text: str) -> set[str]:
        return {name for name, ok in cs.validate_answer(scenario, text).rules if not ok}

    assert failed("Boards load faster. Tasks pause. Calendar removed.") == {
        "max_two_sentences",
        "mentions_known_issue",
    }
    assert failed("**Boards** load faster; imports can stall.") == {"no_markdown"}
    assert failed("Imports stall. " + "x" * 280) == {"max_chars"}
    assert failed("") >= {"non_empty"}


def test_an_answer_that_is_not_a_json_object_fails_every_json_rule():
    result = cs.validate_answer(cs.SCENARIOS[TICKET], "category: login")
    assert result.status == "failed" and not any(ok for _, ok in result.rules)


def test_a_fenced_json_answer_is_recorded_as_stripped():
    result = cs.validate_answer(cs.SCENARIOS[TICKET], f"```json\n{GOOD[TICKET]}\n```")
    assert result.status == "passed" and result.fence_stripped is True


def test_an_oversized_answer_is_never_parsed():
    result = cs.validate_answer(cs.SCENARIOS[TICKET], "x" * (cs.MAX_ANSWER_CHARS + 1))
    assert (result.status, result.reason, result.rules) == ("failed", "output_too_large", ())


def test_a_validation_result_carries_no_answer_text():
    secret = "ANSWER-SENTINEL-not-in-the-result"
    result = cs.validate_answer(cs.SCENARIOS[SUMMARY], f"{secret}. Imports stall.")
    assert secret not in json.dumps(result.to_doc())
