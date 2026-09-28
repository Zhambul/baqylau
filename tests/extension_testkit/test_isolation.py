# Copyright (c) 2026 Zhambyl Yermagambet
"""The kit's default values never reach the user's port, data, configuration, or Keychain (P08-T01)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from baqylau_extension_testkit.host_process import EXECUTABLE_VARIABLE, HostExecutable, HostStartError
from baqylau_extension_testkit.isolation import (
    USER_DATA,
    USER_PORT,
    PrivateRoots,
    UnsafeFixtureError,
    check_data,
    check_port,
    free_port,
)

if TYPE_CHECKING:
    from pathlib import Path

PRIVATE_VARIABLE = "BAQYLAU_TEST_PRIVATE"


def test_user_port_and_data_are_refused(tmp_path: Path) -> None:
    """Port 8377 and the user's data directory, or a directory in it, are refused."""
    with pytest.raises(UnsafeFixtureError):
        check_port(USER_PORT)
    with pytest.raises(UnsafeFixtureError):
        check_data(USER_DATA)
    with pytest.raises(UnsafeFixtureError):
        check_data(USER_DATA / "test")
    assert check_data(tmp_path) == tmp_path
    assert free_port() != USER_PORT


def test_child_environment_is_private(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a few caller values pass; homes, configuration, and the Keychain backend are private."""
    monkeypatch.setenv(PRIVATE_VARIABLE, "value")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/Users/someone/.claude")
    roots = PrivateRoots(tmp_path)

    environment = roots.environment()

    assert PRIVATE_VARIABLE not in environment
    assert environment["HOME"] == str(roots.home)
    assert environment["CLAUDE_CONFIG_DIR"].startswith(str(tmp_path))
    assert environment["PYTHON_KEYRING_BACKEND"] == "keyring.backends.fail.Keyring"


def test_executable_must_be_named(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The kit does not guess a host checkout; a missing or plain file is refused."""
    monkeypatch.delenv(EXECUTABLE_VARIABLE, raising=False)
    with pytest.raises(HostStartError):
        HostExecutable.from_environment()
    plain = tmp_path / "baqylau-dashboard"
    plain.write_text("", encoding="utf-8")
    monkeypatch.setenv(EXECUTABLE_VARIABLE, str(plain))
    with pytest.raises(HostStartError):
        HostExecutable.from_environment()
