"""Phase 10E (ADR-58): the offline regression gate. SYNTHETIC golden set and scripted mock only."""

import json
from fractions import Fraction

import pytest

from app import eval_gate
from app.services.experiments import gate

R = gate.CaseResult


def results(base: dict[str, bool | None], cand: dict[str, bool | None]) -> list[gate.CaseResult]:
    out = [R(k, "a", v, "x") for k, v in base.items()]
    return out + [R(k, "b", v, "x") for k, v in cand.items()]


def test_counts_regressions_lost_and_improvements():
    v = gate.evaluate(
        results(
            {"c1": True, "c2": True, "c3": False, "c4": None},
            {"c1": False, "c2": None, "c3": True, "c4": True},
        ),
        baseline="a",
        candidate="b",
    )
    assert (v["regressions"], v["lost"], v["improvements"]) == (["c1"], ["c2"], ["c3"])
    assert v["passed"] is False and v["failed_on"] == ["regressions"]
    assert v["candidate"]["passed"] == 2 and v["candidate"]["not_evaluated"] == 1


def test_a_lost_pass_counts_against_the_allowance():
    r = results({"c1": True, "c2": True}, {"c1": True, "c2": None})
    assert gate.evaluate(r, baseline="a", candidate="b")["passed"] is False
    assert gate.evaluate(r, baseline="a", candidate="b", max_regressions=1)["passed"] is True


def test_the_pass_rate_floor_is_exact():
    r = results({"c1": True, "c2": True, "c3": False}, {"c1": True, "c2": True, "c3": False})
    assert (
        gate.evaluate(r, baseline="a", candidate="b", min_pass_rate=Fraction(2, 3))["passed"]
        is True
    )
    v = gate.evaluate(r, baseline="a", candidate="b", min_pass_rate=Fraction(667, 1000))
    assert v["passed"] is False and v["failed_on"] == ["pass_rate"]


def test_no_evaluated_candidate_case_fails_the_floor():
    r = results({"c1": None}, {"c1": None})
    assert gate.evaluate(r, baseline="a", candidate="b")["failed_on"] == ["pass_rate"]


@pytest.mark.parametrize(
    "bad",
    [
        lambda: gate.evaluate(results({"c1": True}, {"c2": True}), baseline="a", candidate="b"),
        lambda: gate.evaluate(results({"c1": True}, {"c1": True}), baseline="a", candidate="a"),
        lambda: gate.evaluate(
            [R("c1", "a", True, "x"), R("c1", "a", True, "x")], baseline="a", candidate="b"
        ),
        lambda: gate.evaluate(
            results({"c1": True}, {"c1": True}), baseline="a", candidate="b", max_regressions=-1
        ),
        lambda: gate.evaluate([], baseline="a", candidate="b"),
    ],
)
def test_malformed_inputs_are_refused(bad):
    with pytest.raises(ValueError):
        bad()


def write(tmp_path, **over):
    cfg = {
        "dataset": "golden-synthetic-v1",
        "baseline": "golden-prompt-v1",
        "candidate": "golden-prompt-v1",
        "max_regressions": 0,
        "min_pass_rate": "0.6",
        **over,
    }
    p = tmp_path / "gate.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


def run(argv, capsys):
    code = eval_gate.run(argv)
    out = capsys.readouterr().out
    return code, out


def test_the_shipped_config_passes(capsys):
    from pathlib import Path

    code, out = run(["--config", str(Path(__file__).parents[2] / "evals" / "gate.json")], capsys)
    assert code == 0 and out.rstrip().endswith("eval-gate: PASS")


def test_golden_v1_to_v2_fails_on_the_documented_cases(tmp_path, capsys):
    report_path = tmp_path / "report.json"
    code, out = run(
        [
            "--config",
            str(write(tmp_path, candidate="golden-prompt-v2")),
            "--report",
            str(report_path),
        ],
        capsys,
    )
    assert code == 1 and "eval-gate: FAIL (regressions, pass_rate)" in out
    report = json.loads(report_path.read_text(encoding="utf-8"))
    # golden.py's table: v1 passes G01 G04 G05 G07 G08 G09 G10; v2 passes G01 G02 G03 G05.
    assert report["regressions"] == ["G04", "G07", "G08", "G09", "G10"]
    assert report["improvements"] == ["G02", "G03"] and report["lost"] == []
    assert (report["baseline"]["passed"], report["candidate"]["passed"]) == (7, 4)
    assert report["paired_test"]["p_value"] == "0.453125"  # 2 · (1 + 7 + 21) / 128
    assert report["language_model_used"] is False and "SYNTHETIC" in report["provider"]


@pytest.mark.parametrize(
    "over",
    [
        {"dataset": "other"},
        {"baseline": "golden-prompt-v9"},
        {"max_regressions": -1},
        {"max_regressions": True},
        {"min_pass_rate": 0.6},  # a float, not a decimal string
        {"min_pass_rate": "1.5"},
        {"min_pass_rate": "x"},
        {"extra": 1},
    ],
)
def test_configuration_errors_exit_2(tmp_path, capsys, over):
    code, _ = run(["--config", str(write(tmp_path, **over))], capsys)
    assert code == 2


def test_an_unreadable_config_exits_2(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    assert run(["--config", str(bad)], capsys)[0] == 2
    assert run(["--config", str(tmp_path / "missing.json")], capsys)[0] == 2
