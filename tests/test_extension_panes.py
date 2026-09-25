# Copyright (c) 2026 Zhambyl Yermagambet
"""Open an extension pane once, focus it on reopen and after a restart, and keep other windows (P07-T02)."""

from __future__ import annotations

from dataclasses import replace

from terminal import extension_panes
from terminal.models.values import WindowId
from tests.fake_terminal import FakeTerminal

HOST_WINDOW = WindowId("host")
REF = extension_panes.ExtensionPaneRef(
    "test.logs", "test.logs.main", '{"kind":"installation"}', "runtime-one", "Logs",
)


def open_pane(terminal: FakeTerminal, ref: extension_panes.ExtensionPaneRef = REF) -> object:
    """Open or focus the fixture pane beside the host window.

    Returns:
        The open response, or None after a focus.

    """
    plugin = terminal.plugin()
    request = extension_panes.ExtensionPaneOpen(ref, HOST_WINDOW, "")
    return extension_panes.open_extension_pane(plugin.panes, tuple(plugin.metadata.windows()), request)


def test_first_open_tags_the_pane() -> None:
    """The pane runs the extension pane client and carries every identity tag."""
    terminal = FakeTerminal()

    opened = open_pane(terminal)

    assert opened is not None
    request = terminal.opened_panes[0]
    assert request.command[1].endswith(extension_panes.EXTENSION_PANE_CLIENT)
    assert dict(request.tags) == dict(REF.tags())


def test_reopen_focuses_the_same_pane() -> None:
    """A second open for the same view and scope focuses the pane; a new runtime reuses it.

    Each open reads the terminal's windows again, as a restarted daemon does, so
    the tags alone find the pane.
    """
    terminal = FakeTerminal()
    open_pane(terminal)

    assert open_pane(terminal, replace(REF, runtime_revision="runtime-two")) is None
    assert len(terminal.opened_panes) == 1


def test_other_scope_opens_its_own_pane() -> None:
    """Another scope is another pane; the first pane stays."""
    terminal = FakeTerminal()
    open_pane(terminal)
    workspace = extension_panes.ExtensionPaneRef(
        REF.owner, REF.view, '{"kind":"workspace","workspace_id":"one"}', "runtime-one", REF.title,
    )

    assert open_pane(terminal, workspace) is not None
    assert len(terminal.opened_panes) == len(("installation", "workspace"))
    assert not terminal.closed_panes
