# Copyright (c) 2026 Zhambyl Yermagambet
"""Follow one extension's record changes in the pane's scope and ask for a repaint after each one.

The pane runs the follower on a daemon thread and stops it when the pane closes.
The follower keeps the stream position, so a reconnect after a daemon restart
does not read old changes again.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

import _daemon
from _model_base import WireModel
from pydantic import ValidationError

if TYPE_CHECKING:
    import threading
    from collections.abc import Iterator

    from _extension_input import Repaint
    from _extension_pane import ExtensionPaneTarget

RETRY_SECONDS = 2.0
STREAM_STALL_SECONDS = 35.0
BOUNDARY_EVENTS = frozenset(("changes", "reset"))
ERROR_EVENT = "error"


@dataclass(frozen=True)
class StreamFrame:
    """Keep one server-sent frame: its event name and its data line."""

    event_name: str
    frame_text: str


class ChangeBoundaryDocument(WireModel):
    cursor: int
    projection_generation: str | None = None


@dataclass
class ChangePosition:
    """Keep the live generation and the last change cursor that the pane saw."""

    generation: str = "default"
    cursor: int = 0

    def move(self, frame_text: str) -> None:
        """Take the generation and the cursor of one reset or change frame."""
        try:
            boundary = ChangeBoundaryDocument.model_validate_json(frame_text)
        except ValidationError:
            return
        if boundary.projection_generation is not None:
            self.generation = boundary.projection_generation
        self.cursor = boundary.cursor


def follow(target: ExtensionPaneTarget, repaint: Repaint, closed: threading.Event) -> None:
    """Read the change stream, and connect again after each drop until the pane closes."""
    position = ChangePosition()
    while not closed.is_set():
        with suppress(OSError):
            follow_once(target, position, repaint)
        closed.wait(RETRY_SECONDS)


def follow_once(target: ExtensionPaneTarget, position: ChangePosition, repaint: Repaint) -> None:
    """Read one stream connection until it ends, and ask for a repaint at each boundary."""
    path = target.changes_path(position.generation, position.cursor)
    stream = _daemon.lines(path, target.host, target.port, STREAM_STALL_SECONDS)
    for frame in _frames(stream):
        if frame.event_name == ERROR_EVENT:
            return
        if frame.event_name in BOUNDARY_EVENTS:
            position.move(frame.frame_text)
            repaint.request()


def _frames(lines: Iterator[str]) -> Iterator[StreamFrame]:
    event_name = ""
    for line in lines:
        if line.startswith("event: "):
            event_name = line.removeprefix("event: ")
        elif line.startswith("data: "):
            yield StreamFrame(event_name, line.removeprefix("data: "))
