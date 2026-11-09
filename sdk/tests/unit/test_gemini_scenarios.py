"""The fixed SYNTHETIC scenario registry and its deterministic validators (Phase 7B, ADR-39).
No network and no provider: pure functions over fixed text."""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from llm_insight_sdk.live import input_token_bound

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))  # demo_app lives at the repository root, outside the SDK

from demo_app import gemini_scenarios as gs  # noqa: E402

TICKET = gs.SCENARIOS["support-ticket-classification"]
WEEKLY = gs.SCENARIOS["json-data-interpretation"]
SUMMARY = gs.SCENARIOS["short-text-summary"]

# Pinned independently of the registry: a text change must change both places.
PINNED = {
    "support-ticket-classification": (
        "18d3061e26e4b09759b469d674c0aba18a87a3610e83534ef679d758c2cbdf0d",
        601,
        512,
        (1, "ticket-001", "demo.ticket-classify", "v1"),
    ),
    "json-data-interpretation": (
        "1e9f1ba31ae538115071dc95e5fd81aeef150685ccbc27112d157116105d4951",
        657,
        768,
        (1, "weekly-usage-001", "demo.json-interpret", "v1"),
    ),
    "short-text-summary": (
        "740179ed99445b9564589b5c07518f4cea890fbb98dfa4ed112a680728be61b5",
        611,
        512,
        (1, "release-note-001", "demo.short-summary", "v1"),
    ),
}


def rules(result: gs.ValidationResult) -> dict[str, bool]:
    return dict(result.rules)


def ticket_answer(**changes) -> str:
    doc = {"category": "billing", "priority": "high", "reason": "A duplicate charge."}
    doc.update(changes)
    return json.dumps(doc)


def weekly_answer(**changes) -> str:
    doc = {
        "total_requests": 700,
        "highest_error_day": "Wed",
        "error_rate_percent": 3.0,
        "observation": "Errors peaked on Wednesday.",
    }
    doc.update(changes)
    return json.dumps(doc)


GOOD_SUMMARY = "Search is faster and offline edits sync. A known issue remains: long exports fail."


# ---------------------------------------------------------------- registry
def test_exactly_three_registered_scenarios():
    assert set(gs.SCENARIOS) == set(PINNED)


@pytest.mark.parametrize("scenario_id", sorted(PINNED))
def test_composed_input_identity_is_pinned(scenario_id):
    sha, size, cap, identity = PINNED[scenario_id]
    scenario = gs.SCENARIOS[scenario_id]
    text = gs.compose_input(scenario)
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == sha
    assert gs.input_sha256(text) == sha == scenario.input_sha256
    assert len(text.encode("utf-8")) == size
    assert input_token_bound(text) == size + 64 <= 1024
    assert scenario.max_output_tokens == cap
    assert (
        scenario.version,
        scenario.fixture_id,
        scenario.prompt_name,
        scenario.prompt_version,
    ) == identity
    assert gs.check_scenario(scenario_id, scenario) == []


@pytest.mark.parametrize("scenario", gs.SCENARIOS.values(), ids=lambda s: s.id)
def test_composition_is_exactly_the_documented_form(scenario):
    assert gs.compose_input(scenario) == (
        f"{gs.PREAMBLE}\n\n{scenario.task}\n\n{scenario.label}:\n{scenario.fixture}"
    )
    assert gs.compose_input(scenario).startswith("SYNTHETIC LLM Insight demo data.")


def test_the_registry_cannot_be_changed_at_runtime():
    with pytest.raises(TypeError):
        gs.SCENARIOS["x"] = TICKET  # type: ignore[index]
    with pytest.raises(dataclasses.FrozenInstanceError):
        TICKET.fixture = "changed"  # type: ignore[misc]


def test_fixtures_match_no_repository_secret_detector():
    spec = importlib.util.spec_from_file_location(
        "secret_scan_for_test", REPO / "scripts" / "qa" / "secret_scan.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for scenario in gs.SCENARIOS.values():
        data = gs.compose_input(scenario).encode("utf-8")
        assert [name for name, rx in module.PATTERNS.items() if rx.search(data)] == []


# ---------------------------------------------------------------- check_scenario
def changed(scenario: gs.Scenario, **fields) -> gs.Scenario:
    return dataclasses.replace(scenario, **fields)


def codes(key: str, scenario: gs.Scenario) -> set[str]:
    return {r.code for r in gs.check_scenario(key, scenario)}


def test_a_changed_fixture_fails_its_pinned_hash():
    assert "scenario_hash_mismatch" in codes(TICKET.id, changed(TICKET, fixture="Different text."))


def test_an_oversized_input_is_refused():
    big = changed(TICKET, fixture="word " * 400)
    assert "scenario_input_out_of_bounds" in codes(TICKET.id, big)
    # Even when a registry limit is widened, the session's hard bound still applies.
    wide = changed(TICKET, fixture="word " * 400, max_input_bytes=100_000)
    assert "scenario_input_out_of_bounds" in codes(TICKET.id, wide)


@pytest.mark.parametrize(
    "fixture",
    [
        "not json at all",
        '{"dataset":"x","days":[],"unit":"requests"}',
        '{"dataset":"x","days":"oops","unit":"requests"}',
        '{"dataset":"x","days":[{"day":"Mon","errors":-1,"requests":1}],"unit":"r"}',
        '{"dataset":"x","days":[{"day":"Mon","errors":true,"requests":1}],"unit":"r"}',
        '{"dataset":"x","days":[{"day":"Mon","errors":1,"requests":0}],"unit":"r"}',
        '{"dataset":"x","dataset":"y","days":[{"day":"M","errors":1,"requests":1}],"unit":"r"}',
    ],
)
def test_a_malformed_json_fixture_is_refused(fixture):
    assert "scenario_fixture_invalid" in codes(WEEKLY.id, changed(WEEKLY, fixture=fixture))


@pytest.mark.parametrize(
    "extra",
    [
        '"generationConfig":{"temperature":2}',
        '"tools":[{"googleSearch":{}}]',
        '"model":"gemini-2.5-pro"',
        '"systemInstruction":"x"',
    ],
)
def test_provider_configuration_inside_a_fixture_is_refused(extra):
    fixture = f'{{"dataset":"x","days":[],"unit":"r",{extra}}}'
    assert "scenario_fixture_invalid" in codes(WEEKLY.id, changed(WEEKLY, fixture=fixture))


@pytest.mark.parametrize(
    "fixture",
    [
        "Contact someone@example.test about this.",
        "Card 4111 1111 1111 1111 was used.",
        "Key AIza" + "0" * 35,
        "Control \x1b[31m character.",
    ],
)
def test_pii_credential_and_control_shapes_are_refused_in_a_fixture(fixture):
    assert "scenario_fixture_invalid" in codes(TICKET.id, changed(TICKET, fixture=fixture))


@pytest.mark.parametrize("cap", [1, 15, 1025, 100_000])
def test_an_output_cap_outside_the_hard_caps_is_refused(cap):
    assert "scenario_output_cap_invalid" in codes(TICKET.id, changed(TICKET, max_output_tokens=cap))


def test_a_key_that_does_not_match_the_id_is_refused():
    assert "scenario_identity_invalid" in codes("something-else", TICKET)


def test_refusal_messages_never_quote_the_fixture():
    secret = "UNIQUE-FIXTURE-TEXT-MUST-NOT-APPEAR"
    for refusal in gs.check_scenario(TICKET.id, changed(TICKET, fixture=secret)):
        assert secret not in refusal.message


# ---------------------------------------------------------------- ticket rules
def test_ticket_answer_passes():
    result = gs.validate_answer(TICKET, ticket_answer())
    assert result.status == "passed" and result.reason is None
    assert all(ok for _, ok in result.rules) and result.fence_stripped is False


@pytest.mark.parametrize(
    ("answer", "rule"),
    [
        (ticket_answer(category="refund"), "category_enum"),
        (ticket_answer(category=["billing"]), "category_enum"),
        (ticket_answer(priority="urgent"), "priority_enum"),
        (ticket_answer(reason=""), "reason_length"),
        (ticket_answer(reason="x" * 121), "reason_length"),
        (ticket_answer(reason=7), "reason_length"),
        (ticket_answer(category="login"), "expected_category"),
        (ticket_answer(extra=1), "exact_keys"),
        ('{"category":"billing","priority":"low"}', "exact_keys"),
        ('{"category":"billing","category":"bug","priority":"low","reason":"r"}',
         "no_duplicate_keys"),
    ],
)  # fmt: skip
def test_ticket_rules_fail_individually(answer, rule):
    result = gs.validate_answer(TICKET, answer)
    assert result.status == "failed" and result.reason == "rules_failed"
    assert rules(result)[rule] is False


@pytest.mark.parametrize(
    "answer",
    [
        "not json",
        "[1, 2]",
        '"text"',
        'Here it is: {"category":"billing"}',
        '{"category":"billing"} trailing',
        '{"category": NaN}',
        '{"category": Infinity}',
        '{"category": -Infinity}',
    ],
)
def test_only_one_strict_json_object_is_accepted(answer):
    result = gs.validate_answer(TICKET, answer)
    assert result.status == "failed" and rules(result)["json_object"] is False


def test_deeply_nested_json_fails_without_raising():
    result = gs.validate_answer(TICKET, "[" * 4096)
    assert result.status == "failed" and rules(result)["json_object"] is False


@pytest.mark.parametrize(
    "wrapper",
    ["```json\n{}\n```", "```\n{}\n```", "  ```json\n{}\n```  \n", "```json\r\n{}\r\n```"],
)
def test_one_surrounding_fence_is_removed_and_reported(wrapper):
    result = gs.validate_answer(TICKET, wrapper.replace("{}", ticket_answer()))
    assert result.status == "passed" and result.fence_stripped is True


@pytest.mark.parametrize(
    "answer",
    [
        "```python\n{}\n```",
        "text ```json\n{}\n```",
        "```json\n{}\n``` text",
        "```json\n```json\n{}\n```\n```",
    ],
)
def test_anything_else_around_a_fence_fails(answer):
    result = gs.validate_answer(TICKET, answer.replace("{}", ticket_answer()))
    assert result.status == "failed" and rules(result)["json_object"] is False


# ---------------------------------------------------------------- JSON interpretation
def test_weekly_answer_passes_with_values_computed_from_the_fixture():
    assert gs._weekly_expected() == (700, "Wed", 3.0)
    assert gs.validate_answer(WEEKLY, weekly_answer()).status == "passed"
    assert gs.validate_answer(WEEKLY, weekly_answer(error_rate_percent=3)).status == "passed"
    assert gs.validate_answer(WEEKLY, weekly_answer(error_rate_percent=3.05)).status == "passed"


@pytest.mark.parametrize(
    ("changes", "rule"),
    [
        ({"total_requests": 701}, "expected_total"),
        ({"total_requests": 700.0}, "total_requests_integer"),
        ({"total_requests": True}, "total_requests_integer"),
        ({"highest_error_day": "Mon"}, "expected_day"),
        ({"error_rate_percent": 3.2}, "expected_rate"),
        ({"error_rate_percent": True}, "expected_rate"),
        ({"error_rate_percent": "3.0"}, "expected_rate"),
        ({"error_rate_percent": 10**400}, "expected_rate"),
        ({"observation": ""}, "observation_length"),
        ({"observation": "x" * 121}, "observation_length"),
    ],
)
def test_weekly_rules_fail_individually(changes, rule):
    result = gs.validate_answer(WEEKLY, weekly_answer(**changes))
    assert result.status == "failed" and rules(result)[rule] is False


# ---------------------------------------------------------------- summary
def test_summary_answer_passes():
    result = gs.validate_answer(SUMMARY, GOOD_SUMMARY)
    assert result.status == "passed" and result.fence_stripped is False


@pytest.mark.parametrize(
    ("answer", "rule"),
    [
        ("   ", "non_empty"),
        ("Export fails. " + "x" * 280, "max_chars"),
        ("One export. Two. Three.", "max_two_sentences"),
        ("One export. Two. Three without a stop", "max_two_sentences"),
        ("**Export** can time out.", "no_markdown"),
        ("# Export\ncan time out.", "no_markdown"),
        ("- Export can time out.", "no_markdown"),
        ("1. Export can time out.", "no_markdown"),
        ("See `export` issue.", "no_markdown"),
        ("See [export](u) issue.", "no_markdown"),
        ("Search is faster now.", "mentions_known_issue"),
    ],
)
def test_summary_rules_fail_individually(answer, rule):
    result = gs.validate_answer(SUMMARY, answer)
    assert result.status == "failed" and rules(result)[rule] is False


def test_a_decimal_number_is_not_a_sentence_end():
    answer = "Version 4.2 is faster. Exports of large notebooks can time out."
    assert rules(gs.validate_answer(SUMMARY, answer))["max_two_sentences"] is True


def test_the_known_issue_check_ignores_case():
    assert rules(gs.validate_answer(SUMMARY, "EXPORTS can fail."))["mentions_known_issue"] is True


# ---------------------------------------------------------------- general validator rules
@pytest.mark.parametrize("scenario", gs.SCENARIOS.values(), ids=lambda s: s.id)
def test_oversized_answers_are_refused_before_parsing(scenario, monkeypatch):
    def never(text):
        raise AssertionError("an oversized answer must not be parsed")

    probe = dataclasses.replace(scenario, validate=never)
    result = gs.validate_answer(probe, "x" * 4097)
    assert (result.status, result.reason, result.rules) == ("failed", "output_too_large", ())


@pytest.mark.parametrize(
    ("scenario", "answer"),
    [
        (TICKET, ticket_answer(category="SENTINEL-VALUE-XYZ", reason="SENTINEL-VALUE-XYZ")),
        (WEEKLY, weekly_answer(highest_error_day="SENTINEL-VALUE-XYZ")),
        (SUMMARY, "SENTINEL-VALUE-XYZ. Two. Three. No mention."),
    ],
)
def test_results_carry_rule_codes_and_booleans_only(scenario, answer):
    doc = gs.validate_answer(scenario, answer).to_doc()
    assert "SENTINEL-VALUE-XYZ" not in json.dumps(doc)
    assert set(doc) == {"status", "reason", "rules", "fence_stripped"}
    for rule in doc["rules"]:
        assert set(rule) == {"rule", "passed"} and isinstance(rule["passed"], bool)


def test_not_evaluated_carries_a_reason_and_no_rules():
    assert gs.not_evaluated("provider_incomplete").to_doc() == {
        "status": "not_evaluated",
        "reason": "provider_incomplete",
        "rules": [],
        "fence_stripped": False,
    }


def test_the_module_opens_no_file_and_reads_no_environment():
    source = Path(gs.__file__).read_text(encoding="utf-8")
    for word in ("open(", "os.environ", "import os", "read_text", "getenv", "argparse"):
        assert word not in source
