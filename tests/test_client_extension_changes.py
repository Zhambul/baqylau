# Copyright (c) 2026 Zhambyl Yermagambet
"""The extension pane repaints after each record change of its extension and scope (P07-T03)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests import test_client_loading

if TYPE_CHECKING:
    from collections.abc import Iterator

    import pytest

changes = test_client_loading.load_shared("_extension_changes")
pane = test_client_loading.load_shared("_extension_pane")
daemon = test_client_loading.load_shared("_daemon")
user_input = test_client_loading.load_shared("_extension_input")
TARGET = pane.ExtensionPaneTarget("127.0.0.1", 1, "test.git", "test.git.tree", '{"kind":"installation"}')
SCOPE_PART = "%7B%22kind%22%3A%22installation%22%7D"
FRAMES = (
    "event: reset",
    'data: {"history_revision":"default","projection_generation":"g2","cursor":0}',
    "",
    ": heartbeat",
    "event: changes",
    'data: {"records":[],"cursor":7}',
    "",
)


class RecordedStream:
    """Answer each stream request with the given lines and keep the paths."""

    def __init__(self, lines: tuple[str, ...]) -> None:
        """Keep the lines."""
        self.lines = lines
        self.paths: list[str] = []

    def read(self, path: str, _host: str, _port: int, _timeout: float) -> Iterator[str]:
        """Record the path and give the lines.

        Yields:
            The stream lines.

        """
        self.paths.append(path)
        yield from self.lines


def test_each_boundary_asks_for_a_repaint(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reset and a change frame each ask for a repaint; a heartbeat does not."""
    stream = RecordedStream(FRAMES)
    monkeypatch.setattr(daemon, "lines", stream.read)
    repaint = user_input.Repaint()
    position = changes.ChangePosition()

    changes.follow_once(TARGET, position, repaint)

    assert repaint.requested
    assert (position.generation, position.cursor) == ("g2", 7)
    query = f"scope={SCOPE_PART}&projection_generation=default&cursor=0"
    assert stream.paths == [f"/api/extensions/test.git/changes?{query}"]


def test_reconnect_starts_after_the_last_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new connection names the generation and cursor that the pane saw last."""
    stream = RecordedStream(())
    monkeypatch.setattr(daemon, "lines", stream.read)
    position = changes.ChangePosition(generation="g2", cursor=7)

    changes.follow_once(TARGET, position, user_input.Repaint())

    assert stream.paths[0].endswith("&projection_generation=g2&cursor=7")


def test_error_frame_ends_the_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """An error frame ends the connection before later frames, and asks for no repaint."""
    stream = RecordedStream(("event: error", 'data: {"error":"stream failed"}', *FRAMES))
    monkeypatch.setattr(daemon, "lines", stream.read)
    repaint = user_input.Repaint()

    changes.follow_once(TARGET, changes.ChangePosition(), repaint)

    assert not repaint.requested
