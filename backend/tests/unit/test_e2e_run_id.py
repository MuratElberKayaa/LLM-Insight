"""UI-R2: the Playwright run id (`scripts/dev/e2e_fixtures.py`) never trips the backend's label
check. The labels the e2e suite builds from it (dataset names, the reviewer handle) are refused
when they look like contact data; a random `token_hex(6)` with 10+ digits in a row did exactly
that in about 2% of runs. The id is now built so that it cannot, and the backend check is the
oracle here, unchanged. No database and no network: the module is loaded, `setup` is not run."""

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

from app.services import redaction

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "scripts" / "dev" / "e2e_fixtures.py"
# Every label the e2e specs and the fixture script build from the run id.
SUFFIXES = (
    "",
    "-main",
    "-foreign",
    "-providers",
    "-comparison",
    "-complete",
    "-foreign-ds",
    "-golden-ab",
    "-golden-fixture",
    "-regression",
    "-replay",
    "-t43-refused",
)


@pytest.fixture(scope="module")
def fx() -> ModuleType:
    spec = importlib.util.spec_from_file_location("e2e_fixtures_under_test", FIXTURES)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _labels(run: str) -> list[str]:
    return [f"e2e-{run}{s}" for s in SUFFIXES]


def test_the_old_flaky_id_is_refused_by_the_backend(fx):
    # The failing run of 2026-11-23: this is what the construction must rule out.
    assert redaction.label_is_sensitive("e2e-27148722459d-complete")
    assert fx.longest_digit_run("27148722459d") == 11


def test_shape_still_matches_the_teardown_pattern(fx):
    for _ in range(500):
        run = fx.new_run_id()
        assert fx.RUN_RE.match(run), run
        assert all(run[i] in "abcdef" for i in (0, 4, 8)), run
        assert fx.longest_digit_run(run) <= fx.MAX_RUN_DIGITS, run


@pytest.mark.parametrize("run", ["a999b999c999", "f000e000d000", "a123b456c789"])
def test_worst_case_ids_pass_the_label_check(fx, run):
    # The most digits the construction allows, at every position it allows them.
    assert fx.RUN_RE.match(run)
    for label in _labels(run):
        assert not redaction.label_is_sensitive(label), label


def test_random_ids_pass_the_label_check(fx):
    for _ in range(2000):
        run = fx.new_run_id()
        for label in _labels(run):
            assert not redaction.label_is_sensitive(label), label


def test_ids_are_random(fx):
    assert len({fx.new_run_id() for _ in range(200)}) == 200


def test_setup_uses_the_safe_generator():
    source = FIXTURES.read_text(encoding="utf-8")
    setup = source[source.index("def setup(") :]
    setup = setup[: setup.index("\ndef ")]
    assert "run = new_run_id()" in setup
    assert not re.search(r"run = secrets\.token_hex", setup)
