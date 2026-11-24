"""CI regression gate on the golden set (Phase 10E, ADR-58). Run from backend/:

    python -m app.eval_gate --config evals/gate.json [--report out.json]

Offline and deterministic: the SYNTHETIC golden cases (`golden-synthetic-v1`) are answered by
the scripted mock provider and evaluated by the deterministic rule engine. No database, no
settings, no secret, no network and no model call. Exit 0 when the gate passes, 1 when it
fails, 2 on a configuration error. The report is printed (and written when `--report` is set).

The config names the baseline and candidate prompt versions and the thresholds:

    {"dataset": "golden-synthetic-v1", "baseline": "golden-prompt-v1",
     "candidate": "golden-prompt-v1", "max_regressions": 0, "min_pass_rate": "0.6"}

A real project would point the candidate at the prompt version under review; a regression on
any golden case then fails CI.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

from app.services.evaluation import engine
from app.services.experiments import gate, golden
from app.services.experiments.mock_provider import GOLDEN_PROVIDER

KEYS = {"dataset", "baseline", "candidate", "max_regressions", "min_pass_rate"}


class ConfigError(Exception):
    pass


def load_config(path: Path) -> dict[str, Any]:
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read {path.name}: {type(exc).__name__}") from None
    if not isinstance(cfg, dict) or set(cfg) != KEYS:
        raise ConfigError(f"the config needs exactly the keys {sorted(KEYS)}")
    if cfg["dataset"] != golden.DATASET_NAME:
        raise ConfigError(f"only the dataset {golden.DATASET_NAME} is supported offline")
    versions = {golden.PROMPT_VERSION_A, golden.PROMPT_VERSION_B}
    if cfg["baseline"] not in versions or cfg["candidate"] not in versions:
        raise ConfigError(f"prompt versions must be in {sorted(versions)}")
    if type(cfg["max_regressions"]) is not int or cfg["max_regressions"] < 0:
        raise ConfigError("max_regressions must be a non-negative integer")
    try:
        rate = Fraction(Decimal(str(cfg["min_pass_rate"])))
    except ArithmeticError:
        raise ConfigError("min_pass_rate must be a decimal string") from None
    if not isinstance(cfg["min_pass_rate"], str) or not 0 <= rate <= 1:
        raise ConfigError("min_pass_rate must be a decimal string in [0, 1]")
    cfg["min_pass_rate"] = rate
    return cfg


def golden_results(versions: Sequence[str]) -> list[gate.CaseResult]:
    provider = GOLDEN_PROVIDER
    out = []
    for case in golden.CASES:
        for version in versions:
            answer = provider.call(
                model="gpt-5-mini", prompt_version=version, sanitized_input=case.sanitized_input
            )
            if answer.error is not None or answer.output is None:
                out.append(
                    gate.CaseResult(case.key, version, None, answer.error or "provider_error")
                )
                continue
            outcome = engine.evaluate(case.rule, case.expected_output, answer.output)
            out.append(gate.CaseResult(case.key, version, outcome.passed, outcome.code))
    return out


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def run(argv: Sequence[str], out: Any = None) -> int:
    out = sys.stdout if out is None else out  # resolved per call, not at import
    parser = argparse.ArgumentParser(prog="python -m app.eval_gate")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
        if cfg["baseline"] == cfg["candidate"]:
            # A self-comparison is a valid "no change" gate: every case is compared with itself.
            results = golden_results([cfg["baseline"]])
            results += [gate.CaseResult(r.case, "__candidate__", r.passed, r.code) for r in results]
            candidate = "__candidate__"
        else:
            results = golden_results([cfg["baseline"], cfg["candidate"]])
            candidate = cfg["candidate"]
        verdict = gate.evaluate(
            results,
            baseline=cfg["baseline"],
            candidate=candidate,
            max_regressions=cfg["max_regressions"],
            min_pass_rate=cfg["min_pass_rate"],
        )
    except (ConfigError, ValueError) as exc:
        print(f"eval-gate: configuration error: {exc}", file=sys.stderr)
        return 2
    verdict["candidate"]["prompt_version"] = cfg["candidate"]
    report = _jsonable(
        {
            **verdict,
            "dataset": cfg["dataset"],
            "provider": "scripted mock (SYNTHETIC)",
            "language_model_used": False,
            "evaluated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )
    text = json.dumps(report, indent=2, sort_keys=True)
    print(text, file=out)
    if args.report is not None:
        args.report.write_text(text + "\n", encoding="utf-8")
    status = "PASS" if report["passed"] else f"FAIL ({', '.join(report['failed_on'])})"
    print(f"eval-gate: {status}", file=out)
    return 0 if report["passed"] else 1


def main() -> None:
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
