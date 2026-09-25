# Copyright (c) 2026 Zhambyl Yermagambet
"""A new package from the shared template passes the shared gates and its unit test (P02-T05)."""

import subprocess  # noqa: S404 -- Run the installed policy command without a shell.
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

GATE_SECONDS = 300
PACKAGE = "package"
NEW = ("baqylau_dev", "new", "--id", "example.fresh", "--root")


def run(arguments: tuple[str, ...], directory: Path) -> subprocess.CompletedProcess[str]:
    """Run one installed Python module in a directory.

    Returns:
        The finished process.

    """
    return subprocess.run(  # noqa: S603 -- The current Python with fixed module arguments.
        (sys.executable, "-m", *arguments), cwd=directory, capture_output=True, text=True, check=False,
        timeout=GATE_SECONDS,
    )


def created(directory: Path, *parts: str) -> Path:
    """Create a new package with the chosen parts.

    Returns:
        The package directory.

    """
    package = directory / PACKAGE
    finished = run((*NEW, str(package), *parts), directory)
    assert finished.returncode == 0, finished.stderr
    return package


@pytest.mark.parametrize("parts", [(), ("--web", "--terminal")])
def test_new_package_passes_the_gates(tmp_path: Path, parts: tuple[str, ...]) -> None:
    """The shared lint gates and the unit test pass in the new package, with no host path."""
    package = created(tmp_path, *parts)

    gates = ("baqylau_dev", "check", "--gate", "lint", "--root", str(package))
    lint = run(gates, package)
    unit = run(("pytest", "-q", "--ignore=tests/e2e", "-p", "no:cacheprovider"), package)

    assert lint.returncode == 0, lint.stdout + lint.stderr
    assert unit.returncode == 0, unit.stdout + unit.stderr


class Weakening(NamedTuple):
    """One change to a new package's file and the refusal that it causes."""

    name: str
    old: str
    new: str
    refusal: str


WEAKENINGS = (
    Weakening("pyproject.toml", "[tool.pytest", '[tool.ruff.lint]\nignore = ["D"]\n\n[tool.pytest', "cannot override"),
    Weakening("baqylau-dev.toml", 'policy_version = "', 'policy_version = "0.0.0-', "policy version differs"),
)


@pytest.mark.parametrize("weakening", WEAKENINGS)
def test_parity_refuses_a_weaker_package(tmp_path: Path, weakening: Weakening) -> None:
    """A new package that lowers a rule or names another policy release fails the parity gate."""
    package = created(tmp_path)
    path = package / weakening.name
    changed = path.read_text(encoding="utf-8").replace(weakening.old, weakening.new, 1)
    path.write_text(changed, encoding="utf-8")

    parity = run(("baqylau_dev", "check", "--gate", "parity", "--root", str(package)), package)

    assert parity.returncode != 0
    assert weakening.refusal in parity.stderr + parity.stdout


def test_new_refuses_a_directory_with_files(tmp_path: Path) -> None:
    """The template never writes over files."""
    package = tmp_path / PACKAGE
    package.mkdir()
    (package / "notes.txt").write_text("kept\n", encoding="utf-8")

    finished = run((*NEW, str(package)), tmp_path)

    assert finished.returncode != 0
    assert "is not empty" in finished.stderr
