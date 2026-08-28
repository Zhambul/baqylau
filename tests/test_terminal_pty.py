"""The headless terminal: what it really does with a program on a real tty.

Substitutability is pinned next door (test_terminal_contract.py). What is pinned
here is the one claim that makes this a terminal rather than a pipe with escape
codes filtered out of it — that `read_screen` answers with what is VISIBLE.
"""

from __future__ import annotations

import os
import sys
import time
from contextlib import nullcontext
from types import SimpleNamespace
from typing import cast

import pytest
import psutil

from terminal.impl.pty import plugin as pty_module
from terminal.impl.pty.plugin import PtyInput, PtyWindows, pty_plugin
from terminal.models import (
    ScreenReadRequest,
    TabOpenRequest,
    TextInsertRequest,
    TextInputMode,
    TextSubmitRequest,
)
from terminal.models.input import KeySendRequest
from terminal.models.values import WindowId
from terminal.models.tabs import TabCloseRequest, TabRenameRequest

TIMEOUT_SECONDS = 10.0


@pytest.fixture
def terminal():
    plugin = pty_plugin(PtyWindows())
    opened: list[str] = []
    yield plugin, opened
    for window_id in opened:
        plugin.tabs.close_tab(TabCloseRequest(window_id))


def _open(terminal, command):
    plugin, opened = terminal
    response = plugin.tabs.open_tab(TabOpenRequest("/tmp", command, ""))
    assert response.succeeded and response.window_id, response.reason
    opened.append(response.window_id)
    return response.window_id


def _await_screen(plugin, window_id, contains):
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while True:
        screen = plugin.viewport.read_screen(ScreenReadRequest(window_id))
        assert screen.succeeded, screen.reason
        if contains in (screen.text or ""):
            return screen.text or ""
        if time.monotonic() >= deadline:
            raise AssertionError(f"never showed {contains!r}; screen reads:\n{screen.text}")
        time.sleep(0.05)


def test_window_identity_does_not_repeat_after_terminal_restart():
    first = pty_plugin(PtyWindows())
    second = pty_plugin(PtyWindows())
    first_window = first.tabs.open_tab(
        TabOpenRequest("/tmp", ("/bin/cat",), "")
    )
    second_window = second.tabs.open_tab(
        TabOpenRequest("/tmp", ("/bin/cat",), "")
    )
    try:
        assert first_window.succeeded and first_window.window_id
        assert second_window.succeeded and second_window.window_id
        assert first_window.window_id != second_window.window_id
    finally:
        if first_window.window_id is not None:
            first.tabs.close_tab(TabCloseRequest(first_window.window_id))
        if second_window.window_id is not None:
            second.tabs.close_tab(TabCloseRequest(second_window.window_id))


def test_terminal_lifecycle_closes_every_owned_window():
    plugin = pty_plugin(PtyWindows())
    first = plugin.tabs.open_tab(TabOpenRequest("/tmp", ("/bin/cat",), ""))
    second = plugin.tabs.open_tab(TabOpenRequest("/tmp", ("/bin/cat",), ""))
    assert first.succeeded and second.succeeded

    plugin.close()

    assert plugin.metadata.windows() == ()


def test_a_headless_tab_rename_is_a_completed_noop(terminal):
    plugin, _ = terminal
    window_id = _open(terminal, ("/bin/cat",))

    response = plugin.tabs.rename_tab(TabRenameRequest(window_id, "New title"))

    assert response.succeeded
    assert response.reason is None


def test_headless_terminal_publishes_its_terminal_type(terminal):
    window_id = _open(
        terminal,
        (sys.executable, "-c", "import os; print(os.environ['TERM'])"),
    )

    screen = _await_screen(terminal[0], window_id, "xterm-256color")

    assert "xterm-256color" in screen


def test_window_close_kills_a_tool_that_escaped_into_its_own_session(
    terminal,
    tmp_path,
):
    plugin, _ = terminal
    child_pid_path = tmp_path / "escaped-child.pid"
    window_id = _open(
        terminal,
        (
            sys.executable,
            "-c",
            (
                "import pathlib, subprocess, time; "
                "child = subprocess.Popen(['/bin/sleep', '30'], start_new_session=True); "
                f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid)); "
                "time.sleep(30)"
            ),
        ),
    )
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while not child_pid_path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert child_pid_path.exists()
    child_pid = int(child_pid_path.read_text())

    assert plugin.tabs.close_tab(TabCloseRequest(window_id)).succeeded

    deadline = time.monotonic() + TIMEOUT_SECONDS
    while psutil.pid_exists(child_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not psutil.pid_exists(child_pid), "detached tool outlived its PTY window"


def test_window_close_kills_an_observed_tool_after_its_parent_exits(
    terminal,
    tmp_path,
):
    plugin, _ = terminal
    child_pid_path = tmp_path / "orphaned-child.pid"
    release_path = tmp_path / "release-parent"
    window_id = _open(
        terminal,
        (
            sys.executable,
            "-c",
            (
                "import pathlib, subprocess, time; "
                "child = subprocess.Popen(['/bin/sleep', '30'], start_new_session=True); "
                f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid)); "
                f"release = pathlib.Path({str(release_path)!r}); "
                "\nwhile not release.exists(): time.sleep(0.05)"
            ),
        ),
    )
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while not child_pid_path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert child_pid_path.exists()
    child_pid = int(child_pid_path.read_text())

    processes = plugin.metadata.windows()[0].processes
    assert any(process.process_id == child_pid for process in processes)
    release_path.write_text("release\n")
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while plugin.metadata.windows() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert plugin.metadata.windows() == ()

    assert plugin.tabs.close_tab(TabCloseRequest(window_id)).succeeded

    deadline = time.monotonic() + TIMEOUT_SECONDS
    while psutil.pid_exists(child_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not psutil.pid_exists(child_pid), "orphaned tool outlived its PTY window"


def test_window_metadata_reports_descendant_processes(terminal):
    """A hook identifies the CLI child, not the shell that launched it."""
    plugin, _ = terminal
    _open(terminal, ("/bin/sh", "-c", "/bin/sleep 30 & wait"))
    deadline = time.monotonic() + TIMEOUT_SECONDS

    while time.monotonic() < deadline:
        processes = plugin.metadata.windows()[0].processes
        if any(
            process.command and os.path.basename(process.command[0]) == "sleep"
            for process in processes
        ):
            break
        time.sleep(0.05)
    else:
        raise AssertionError(f"PTY metadata omitted its sleep child: {processes}")

    assert len(processes) >= 2


def test_window_metadata_falls_back_when_process_details_are_temporarily_denied(
    monkeypatch,
):
    class DeniedProcess:
        pid = 731

        def children(self, recursive):
            assert recursive is True
            return ()

        def cmdline(self):
            raise SystemError("the operating system denied process details")

    monkeypatch.setattr(pty_module.psutil, "Process", lambda _pid: DeniedProcess())
    window = SimpleNamespace(
        process=SimpleNamespace(pid=731),
        command=("codex", "resume"),
    )

    assert pty_module._window_processes(window) == (
        pty_module.WindowProcess(731, ("codex", "resume")),
    )


def test_terminal_replies_to_program_queries(terminal):
    query_program = (
        "import os, tty; "
        "tty.setraw(0); "
        "queries = ["
        "b'\\x1b[6n', b'\\x1b[c', b'\\x1b[?u', "
        "b'\\x1b]10;?\\x1b\\\\', b'\\x1b]11;?\\x07']; "
        "replies = []; "
        "[(os.write(1, query), replies.append(os.read(0, 64))) "
        "for query in queries]; "
        "os.write(1, b'QUERY REPLIES ' + b' '.join(reply.hex().encode() "
        "for reply in replies) + b'\\n')"
    )
    window_id = _open(terminal, (sys.executable, "-c", query_program))

    screen = _await_screen(plugin=terminal[0], window_id=window_id, contains="QUERY REPLIES")

    assert "1b5b" in screen
    assert "1b5d31303b7267623a" in screen
    assert "1b5d31313b7267623a" in screen


def test_the_screen_is_what_is_visible_not_everything_that_was_printed(terminal):
    """The reason this terminal runs an emulator. A pty carries paint
    operations, and a clear-and-repaint is two of them: everything a program
    ever wrote is a different question from what a user would see, and it is the
    second one every caller of `read_screen` is asking."""
    plugin, _ = terminal
    window_id = _open(terminal, (
        "/bin/sh", "-c",
        "printf 'gone forever\\n'; sleep 0.2; printf '\\033[2J\\033[H'; printf 'what is here now\\n'; sleep 30",
    ))

    screen = _await_screen(plugin, window_id, "what is here now")
    assert "gone forever" not in screen


def test_typing_and_keying_reach_the_program(terminal):
    plugin, _ = terminal
    window_id = _open(terminal, ("/bin/cat",))

    assert plugin.input.submit_text(TextSubmitRequest(window_id, "typed line", "type")).succeeded
    _await_screen(plugin, window_id, "typed line")
    # A pasted delivery arrives whole, wrapped in the bracketed-paste markers a
    # program uses to tell one from fast typing. `cat` has no opinion about them
    # and echoes the text either way, which is exactly what proves it landed.
    assert plugin.input.submit_text(TextSubmitRequest(window_id, "pasted line", "paste")).succeeded
    _await_screen(plugin, window_id, "pasted line")
    # ctrl+d ends cat's input, so the program exiting is the assertion that the
    # key arrived as a key rather than as the two characters spelling its name.
    assert plugin.input.send_key(KeySendRequest(window_id, "ctrl+d")).succeeded
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while plugin.metadata.windows() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert plugin.metadata.windows() == (), "cat outlived the ctrl+d that should have ended it"


def test_text_submit_keeps_enter_in_a_separate_terminal_read(monkeypatch):
    events = []
    window = SimpleNamespace(
        revision=7,
        write=lambda payload: events.append(("write", payload)) or True,
        wait_for_screen_change=lambda revision, timeout: events.append(
            ("paint", revision, timeout)
        )
        or True,
    )
    windows = SimpleNamespace(get=lambda _window_id: window, lock=nullcontext())
    del monkeypatch

    result = PtyInput(windows).submit_text(
        TextSubmitRequest("window-one", "queued prompt", "paste")
    )

    assert result.succeeded
    assert events == [
        (
            "write",
            b"\x1b[200~queued prompt\x1b[201~",
        ),
        ("paint", 7, pty_module.SUBMIT_PAINT_TIMEOUT_SECONDS),
        ("write", b"\r"),
    ]


def test_text_insert_never_writes_enter():
    events: list[bytes] = []

    def write(payload: bytes) -> bool:
        events.append(payload)
        return True

    window = SimpleNamespace(write=write)
    windows = SimpleNamespace(get=lambda _window_id: window, lock=nullcontext())

    result = PtyInput(cast(PtyWindows, windows)).insert_text(
        TextInsertRequest(
            WindowId("window-one"), "saved draft", TextInputMode.PASTE
        )
    )

    assert result.succeeded
    assert events == [b"\x1b[200~saved draft\x1b[201~"]


def test_a_key_this_terminal_cannot_send_is_refused_rather_than_guessed(terminal):
    """A guess arrives as some OTHER keystroke, which reads exactly like a
    program that ignored the gesture — the most expensive failure this rig has."""
    plugin, _ = terminal
    window_id = _open(terminal, ("/bin/cat",))

    refused = plugin.input.send_key(KeySendRequest(window_id, "f13"))
    assert not refused.succeeded
    assert "f13" in (refused.reason or "")


def test_what_a_pty_does_not_have_it_says_so(terminal):
    """Tabs, splits and focus are a terminal APPLICATION's, and answering
    "succeeded" for one would report chrome that does not exist."""
    plugin, _ = terminal
    window_id = _open(terminal, ("/bin/cat",))

    assert not plugin.viewport.read_screen(ScreenReadRequest(window_id, ansi=True)).succeeded
    assert plugin.metadata.current_window_id() is None
    window = plugin.metadata.windows()[0]
    assert window.window_id == window_id
    assert window.is_first_in_tab and not window.tab_is_focused
