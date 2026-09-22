# Copyright (c) 2026 Zhambyl Yermagambet
"""Check that environment commands do not inherit daemon or installer settings."""

import os
import sys
from pathlib import Path

import pytest

from extensions.environment_commands import EnvironmentCommands

REQUIRED_OPTIONS = frozenset(("--offline", "--no-config", "--no-cache", "--require-hashes", "--only-binary"))


def test_installer_has_fixed_authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PATH, Python, project, and index settings cannot redirect preparation."""
    for name in ("PATH", "PYTHONPATH", "VIRTUAL_ENV", "UV_CONFIG_FILE", "PIP_INDEX_URL"):
        monkeypatch.setenv(name, "/not-the-selected-environment")
    commands = EnvironmentCommands(tmp_path / "private", tmp_path / "artifact")
    command = commands.sync_command(tmp_path / "lock", tmp_path / "wheels")
    expected = {"PATH": os.defpath, "TMPDIR": str(commands.directory), "LC_ALL": "C"}
    assert command.arguments[:4] == (sys.executable, "-I", "-m", "uv")
    assert command.environment == expected
    assert set(command.arguments) >= REQUIRED_OPTIONS
    assert command.directory == commands.artifact_directory


def test_python_path_keeps_the_venv_link(tmp_path: Path) -> None:
    """Resolving the interpreter link would bypass its private site-packages."""
    commands = EnvironmentCommands(tmp_path, tmp_path / "artifact")
    commands.executable.parent.mkdir(parents=True)
    commands.executable.symlink_to(sys.executable)
    command = commands.probe_command()
    assert command.arguments[0] == str(commands.executable)
    assert command.arguments[0] != str(commands.executable.resolve())
