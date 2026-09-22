# Copyright (c) 2026 Zhambyl Yermagambet
"""Run the installed policy in a package with no sibling host paths."""

import shutil
import subprocess  # noqa: S404 -- Execute the fixed installed policy command in a temporary package.
import sys
from pathlib import Path

from baqylau_dev.models import Gate

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name("fixtures")
GATE_TIMEOUT = 20
TEST_DIRECTORY = "tests"


def create_package(directory: Path) -> Path:
    """Copy fixture source as real Python files into a separate package.

    Returns:
        The new source file, ready for intentional violation edits.

    """
    (directory / "src").mkdir()
    (directory / TEST_DIRECTORY).mkdir()
    source = directory / "src" / "sample_feature.py"
    shutil.copyfile(FIXTURES / "feature.py.txt", source)
    shutil.copyfile(FIXTURES / "test_feature.py.txt", directory / TEST_DIRECTORY / "test_feature.py")
    shutil.copyfile(FIXTURES / "test_init.py.txt", directory / TEST_DIRECTORY / "__init__.py")
    shutil.copyfile(FIXTURES / "package.toml", directory / "baqylau-dev.toml")
    shutil.copyfile(FIXTURES / "pyproject.toml.txt", directory / "pyproject.toml")
    _copy_backend(directory)
    return source


def _copy_backend(directory: Path) -> None:
    shutil.copyfile(FIXTURES / "backend.py.txt", directory / "src" / "feature_backend.py")
    shutil.copyfile(FIXTURES / "extension.json", directory / "extension.json")
    (directory / TEST_DIRECTORY / "e2e").mkdir()
    shutil.copyfile(FIXTURES / "test_worker.py.txt", directory / TEST_DIRECTORY / "e2e" / "test_worker.py")
    shutil.copyfile(FIXTURES / "test_init.py.txt", directory / TEST_DIRECTORY / "e2e" / "__init__.py")


def invoke(root: Path, gate: Gate, *, executable: str = sys.executable) -> subprocess.CompletedProcess[str]:
    """Run one complete real gate with its installed tools.

    Returns:
        Captured output and the actual command exit status.

    """
    return subprocess.run(  # noqa: S603 -- Fixed module and gate; the root is an isolated test directory.
        (executable, "-m", "baqylau_dev", "check", "--root", str(root), "--gate", gate),
        cwd=root, capture_output=True, text=True, check=False, timeout=GATE_TIMEOUT,
    )
