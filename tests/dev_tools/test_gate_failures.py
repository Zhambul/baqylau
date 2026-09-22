# Copyright (c) 2026 Zhambyl Yermagambet
"""Require real type, dead-code, design, lint, and behavior failures."""

from pathlib import Path

import pytest
from baqylau_dev.models import Gate

from tests.dev_tools import package_fixture as fixtures

ENCODING = "utf-8"


@pytest.mark.parametrize(("gate", "source", "message"), [
    ("types", "def broken() -> int:\n    return 'wrong'\n", "return-value"),
    ("deadcode", "def unused_feature() -> int:\n    return 1\n", "unused function"),
    ("wemake", "def broken() -> None:\n    value = 1\n", "WPS110"),
    ("ruff", "import os\n", "unused-import"),
])
def test_python_gates_reject_violations(tmp_path: Path, gate: Gate, source: str, message: str) -> None:
    """Each advertised gate must observe and reject its own deliberate violation."""
    feature = fixtures.create_package(tmp_path)
    feature.write_text(f"{feature.read_text(encoding=ENCODING)}\n{source}", encoding=ENCODING)
    completed = fixtures.invoke(tmp_path, gate)
    assert completed.returncode != 0
    assert message in completed.stdout + completed.stderr


def test_external_tests_remain_strict(tmp_path: Path) -> None:
    """The host's legacy test exception cannot reach an external package."""
    fixtures.create_package(tmp_path)
    (tmp_path / "tests" / "test_untyped.py").write_text("def test_missing_types():\n    pass\n", encoding=ENCODING)
    completed = fixtures.invoke(tmp_path, "types")
    assert completed.returncode != 0
    assert "no-untyped-def" in completed.stdout


def test_tests_do_not_hide_dead_product_code(tmp_path: Path) -> None:
    """A test call is not a production caller for Vulture."""
    feature = fixtures.create_package(tmp_path)
    source = f"{feature.read_text(encoding=ENCODING)}\ndef unused_feature() -> int:\n    return 1\n"
    feature.write_text(source, encoding=ENCODING)
    (tmp_path / "tests" / "test_unused.py").write_text(
        "from sample_feature import unused_feature\n\ndef test_unused() -> None:\n    assert unused_feature() == 1\n",
        encoding=ENCODING,
    )
    completed = fixtures.invoke(tmp_path, "deadcode")
    assert completed.returncode != 0
    assert "unused_feature" in completed.stdout


def test_lint_reports_a_failed_type_gate(tmp_path: Path) -> None:
    """The aggregate gate cannot turn a child tool's failure into success."""
    feature = fixtures.create_package(tmp_path)
    feature.write_text("def broken() -> int:\n    return 'wrong'\n", encoding=ENCODING)
    completed = fixtures.invoke(tmp_path, "lint")
    assert completed.returncode != 0
    assert "return-value" in completed.stdout


def test_external_unit_failure_is_not_success(tmp_path: Path) -> None:
    """Return pytest's failure when package-owned behavior tests fail."""
    fixtures.create_package(tmp_path)
    (tmp_path / "tests" / "test_feature.py").write_text(
        "def test_failure() -> None:\n    assert False\n", encoding=ENCODING,
    )
    completed = fixtures.invoke(tmp_path, "unit")
    assert completed.returncode != 0
    assert "1 failed" in completed.stdout
