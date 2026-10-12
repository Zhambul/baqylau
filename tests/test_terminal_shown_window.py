# Copyright (c) 2026 Zhambyl Yermagambet
"""A display read of a session's window uses the last terminal read.

The session snapshot asked kitty for its windows and scanned every process on
each request, only for its live flag. A busy kitty made one snapshot take more
than five seconds, so the panes gave up and asked again in a loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from domain.ids import WindowId
from terminal.adapter import TerminalAdapter
from terminal.impl.kitty.metadata import KittyMetadata
from terminal.impl.kitty.remote_tree import KittyOSWindow, KittyTab, KittyWindowInfo
from terminal.models.values import SESSION_WINDOW_TAG
from tests.fake_terminal import FakeSessions
from tests.plugin_tests import terminal_plugin_values, vocabulary as fixture

if TYPE_CHECKING:
    from terminal.contract import TerminalPlugin
    from terminal.impl.kitty.remote import KittyRemote

KITTY_WINDOW = 7


class _CountingRemote:
    def __init__(self) -> None:
        self.reads = 0

    def ls(self) -> list[KittyOSWindow]:
        self.reads += 1
        session_window = KittyWindowInfo(id=KITTY_WINDOW, user_vars={SESSION_WINDOW_TAG: fixture.SESSION_ONE_ID})
        return [KittyOSWindow(tabs=[KittyTab(id=1, windows=[session_window])])]


@dataclass(frozen=True)
class _Plugin:
    metadata: KittyMetadata


def test_display_read_asks_kitty_nothing() -> None:
    """After the engine's read, the live flag finds the window without a new kitty query."""
    remote = _CountingRemote()
    plugin = _Plugin(KittyMetadata(cast("KittyRemote", remote)))
    sessions = FakeSessions({fixture.SESSION_ONE_ID: str(KITTY_WINDOW)})
    adapter = TerminalAdapter(cast("TerminalPlugin", plugin), sessions)
    assert adapter.shown_window(terminal_plugin_values.PRIMARY_SESSION) is None

    adapter.windows()

    assert adapter.shown_window(terminal_plugin_values.PRIMARY_SESSION) == WindowId(str(KITTY_WINDOW))
    assert remote.reads == 1
