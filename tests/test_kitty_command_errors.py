# Copyright (c) 2026 Zhambyl Yermagambet
"""Check Kitty command failures without a live terminal."""

import subprocess  # noqa: S404 - Tests replace process calls with a mock.
from collections.abc import Callable
from unittest.mock import Mock

import pytest

from terminal.impl.kitty.remote import KittyRemote
from terminal.models.values import WindowId

QUERY_COMMAND = "ls"
WINDOW_ID = WindowId("7")
DRAFT = "draft"


@pytest.fixture
def remote() -> KittyRemote:
    """Build a remote with fixed command and socket paths.

    Returns:
        The remote used by the command tests.

    """
    return KittyRemote(listen="unix:/unused/kitty", kitten="/unused/kitten")


@pytest.mark.parametrize("error", [OSError("unavailable"), subprocess.TimeoutExpired("kitten", 1)])
@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (lambda remote: remote.run(QUERY_COMMAND), 1),
        (lambda remote: remote.capture(QUERY_COMMAND), None),
        (lambda remote: remote.insert_text(WINDOW_ID, DRAFT), False),
        (lambda remote: remote.send_text(WINDOW_ID, DRAFT), False),
    ],
)
def test_expected_command_failure(
    monkeypatch: pytest.MonkeyPatch,
    remote: KittyRemote,
    error: Exception,
    action: Callable[[KittyRemote], object],
    expected: object,
) -> None:
    """Return a failure result for an unavailable or timed-out command."""
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=error))
    assert action(remote) == expected


@pytest.mark.parametrize(
    "action",
    [
        lambda remote: remote.run(QUERY_COMMAND),
        lambda remote: remote.capture(QUERY_COMMAND),
        lambda remote: remote.insert_text(WINDOW_ID, DRAFT),
        lambda remote: remote.send_text(WINDOW_ID, DRAFT),
    ],
)
def test_command_preserves_code_errors(
    monkeypatch: pytest.MonkeyPatch,
    remote: KittyRemote,
    action: Callable[[KittyRemote], object],
) -> None:
    """Do not hide an unexpected process wrapper failure."""
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=RuntimeError("command defect")))
    with pytest.raises(RuntimeError, match="command defect"):
        action(remote)


def test_enter_command_failure(monkeypatch: pytest.MonkeyPatch, remote: KittyRemote) -> None:
    """Report an Enter command failure after successful text insertion."""
    monkeypatch.setattr(remote, "insert_text", Mock(return_value=True))
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=OSError("unavailable")))
    assert not remote.send_text(WINDOW_ID, DRAFT)
