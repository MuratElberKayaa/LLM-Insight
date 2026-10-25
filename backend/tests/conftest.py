import sys
from pathlib import Path

# Make `tests.helpers` importable regardless of the pytest rootdir/import mode.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Report hooks that keep local credentials out of every failure report (tests/redaction.py).
from tests.redaction import pytest_make_collect_report, pytest_runtest_makereport  # noqa: E402,F401
