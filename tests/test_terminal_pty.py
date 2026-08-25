"""The headless terminal: what it really does with a program on a real tty.

Substitutability is pinned next door (test_terminal_contract.py). What is pinned
here is the one claim that makes this a terminal rather than a pipe with escape
codes filtered out of it — that `read_screen` answers with what is VISIBLE.
"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest

from terminal.impl.pty import plugin as pty_module
from terminal.impl.pty.plugin import PtyInput, PtyWindows, pty_plugin
from terminal.models import ScreenReadRequest, TabOpenRequest, TextSubmitRequest
from terminal.models.input import KeySendRequest
from terminal.models.tabs import TabCloseRequest

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
        write=lambda payload: events.append(("write", payload)) or True,
    )
    windows = SimpleNamespace(get=lambda _window_id: window)
    monkeypatch.setattr(
        pty_module.time,
        "sleep",
        lambda seconds: events.append(("sleep", seconds)),
    )

    result = PtyInput(windows).submit_text(
        TextSubmitRequest("window-one", "queued prompt", "paste")
    )

    assert result.succeeded
    assert events == [
        (
            "write",
            b"\x1b[200~queued prompt\x1b[201~",
        ),
        ("sleep", pty_module.SUBMIT_ENTER_DELAY_SECONDS),
        ("write", b"\r"),
    ]


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
