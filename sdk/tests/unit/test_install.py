"""The SDK is an installed, independent package (ADR-01)."""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
from pathlib import Path

import llm_insight_sdk

SDK_DIR = Path(__file__).resolve().parents[2]


def test_tests_use_the_installed_package_not_the_source_tree():
    installed = Path(llm_insight_sdk.__file__).resolve()
    assert "site-packages" in installed.parts
    assert SDK_DIR / "src" not in installed.parents


def test_import_from_outside_the_source_directory(tmp_path):
    code = (
        "import llm_insight_sdk, llm_insight_sdk.mock, sys;"
        "print(llm_insight_sdk.__version__);"
        "print(llm_insight_sdk.__file__);"
        "print(sorted(m for m in sys.modules if m == 'app' or m.startswith('app.')))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], cwd=tmp_path, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    version, path, backend_modules = result.stdout.splitlines()
    assert version == llm_insight_sdk.__version__
    assert "site-packages" in path
    assert backend_modules == "[]"  # never imports the backend package


def test_distribution_metadata_and_pinned_dependencies():
    dist = importlib.metadata.distribution("llm-insight-sdk")
    assert dist.version == llm_insight_sdk.__version__
    runtime = sorted(r for r in dist.requires or [] if "extra ==" not in r)
    assert runtime == [
        "opentelemetry-api==1.45.1",
        "opentelemetry-exporter-otlp-proto-http==1.45.1",
        "opentelemetry-sdk==1.45.1",
    ]
    files = {str(f).replace("\\", "/") for f in dist.files or []}
    assert "llm_insight_sdk/py.typed" in files


def test_source_never_imports_the_backend():
    for path in (SDK_DIR / "src" / "llm_insight_sdk").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "from app" not in text and "import app" not in text, path.name
