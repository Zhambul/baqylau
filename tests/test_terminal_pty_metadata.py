# Copyright (c) 2026 Zhambyl Yermagambet
"""Verify PTY metadata and visible screen behavior."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import psutil

from terminal.impl.pty import metadata as pty_metadata, window as pty_window
from terminal.models.values import WindowProcess
from tests import terminal_pty_fixture as pty_fixture, terminal_pty_input_support as input_support

if TYPE_CHECKING:
    import pytest

pytest_plugins = ("tests.terminal_pty_fixture",)
MINIMUM_SHELL_AND_CHILD_PROCESSES = 2


def test_window_metadata_reports_descendant(terminal: pty_fixture.PtyFixture) -> None:
    """Verify window metadata reports a descendant process.

    Raises:
        AssertionError: If the sleep child is absent until the timeout.

    """
    plugin, _ = terminal
    pty_fixture.open_terminal(terminal, ("/bin/sh", pty_fixture.PYTHON_COMMAND_OPTION, "/bin/sleep 30 & wait"))
    deadline = time.monotonic() + pty_fixture.TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        processes = plugin.metadata.windows()[0].processes
        commands = [process.command for process in processes if process.command]
        if any(Path(command[0]).name == "sleep" for command in commands):
            break
        time.sleep(pty_fixture.POLL_SECONDS)
    else:
        msg = f"PTY metadata omitted its sleep child: {processes}"
        raise AssertionError(msg)
    assert len(processes) >= MINIMUM_SHELL_AND_CHILD_PROCESSES


def test_window_metadata_falls_back_when_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify metadata falls back when process details are temporarily denied."""
    monkeypatch.setattr(psutil, "Process", lambda _process_id: input_support.DeniedProcess())
    window = cast(
        "pty_window.PtyWindow",
        SimpleNamespace(
            process=SimpleNamespace(pid=input_support.DENIED_PROCESS_ID),
            command=("codex", "resume"),
        ),
    )
    assert pty_metadata.window_processes(window) == (
        WindowProcess(input_support.DENIED_PROCESS_ID, ("codex", "resume")),
    )


def test_terminal_replies_to_program_queries(terminal: pty_fixture.PtyFixture) -> None:
    """Verify terminal replies to program queries."""
    query_program = (
        "import os, tty; "
        "tty.setraw(0); "
        "queries = ["
        r"b'\x1b[6n', b'\x1b[c', b'\x1b[?u\x1b[c', "
        r"b'\x1b]10;?\x1b\\', b'\x1b]11;?\x07']; "
        "replies = []; "
        "[(os.write(1, query), replies.append(os.read(0, 64))) "
        "for query in queries]; "
        "os.write(1, b'QUERY REPLIES ' + b' '.join(reply.hex().encode() "
        r"for reply in replies) + b'\n')"
    )
    window_id = pty_fixture.open_terminal(
        terminal,
        (sys.executable, pty_fixture.PYTHON_COMMAND_OPTION, query_program),
    )
    screen = pty_fixture.await_screen(terminal[0], window_id, "QUERY REPLIES")
    assert "1b5b" in screen
    assert "1b5d31303b7267623a" in screen
    assert "1b5d31313b7267623a" in screen
    # The emulator has no kitty keyboard protocol, so `CSI ? u` gets no `CSI ? 0 u` reply.
    assert "1b5b3f3075" not in screen


def test_screen_is_what_is_visible_not_everything(terminal: pty_fixture.PtyFixture) -> None:
    """Verify screen reads return visible text, not all past output."""
    plugin, _ = terminal
    window_id = pty_fixture.open_terminal(
        terminal,
        (
            "/bin/sh",
            pty_fixture.PYTHON_COMMAND_OPTION,
            r"printf 'gone forever\n'; sleep 0.2; printf '\033[2J\033[H'; printf 'what is here now\n'; sleep 30",
        ),
    )
    screen = pty_fixture.await_screen(plugin, window_id, "what is here now")
    assert "gone forever" not in screen
