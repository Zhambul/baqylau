# Copyright (c) 2026 Zhambyl Yermagambet
"""A nested quality configuration cannot change the shared rules of its folder (P02-T04)."""

from pathlib import Path

import pytest

from tests.dev_tools import package_fixture as fixtures

ENCODING = "utf-8"


def write(directory: Path, relative: str, text: str) -> None:
    """Write one file in the fixture package."""
    path = directory / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=ENCODING)


@pytest.mark.parametrize(("relative", "text", "reason"), [
    ("src/tools/ruff.toml", "line-length = 300\n", "not permitted: src/tools/ruff.toml"),
    ("tests/e2e/setup.cfg", "[flake8]\nmax-line-length = 300\n", "not permitted: tests/e2e/setup.cfg"),
    ("src/tools/pyproject.toml", "[tool.ruff]\nline-length = 300\n", "cannot override the shared Python quality rules"),
])
def test_nested_rules_are_refused(tmp_path: Path, relative: str, text: str, reason: str) -> None:
    """A nested rule file, or a nested project file with tool rules, stops the gate."""
    fixtures.create_package(tmp_path)
    write(tmp_path, relative, text)

    completed = fixtures.invoke(tmp_path, "ruff")

    assert completed.returncode != 0
    assert reason in completed.stdout + completed.stderr


def test_installed_folders_are_not_package_rules(tmp_path: Path) -> None:
    """A tool file inside an installed folder is not a package rule."""
    fixtures.create_package(tmp_path)
    write(tmp_path, "node_modules/some-tool/ruff.toml", "line-length = 300\n")

    completed = fixtures.invoke(tmp_path, "ruff")

    assert "local quality configuration" not in completed.stdout + completed.stderr
