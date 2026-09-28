# Copyright (c) 2026 Zhambyl Yermagambet
"""Load and follow one terminal pane session model."""

from typing import Protocol

import _daemon
import _http
import _model
from pydantic import ValidationError

STREAM_STALL_SECONDS = 35.0


class PaneReader(Protocol):
    """Provide pane operations required by stream transport."""

    model: _model.SessionModel

    def apply(self, event: str, event_payload: str) -> None:
        """Apply one stream event."""
        ...

    def deferred_repaint(self) -> None:
        """Apply a deferred repaint."""
        ...


def _snapshot(path: str, host: str, port: int) -> _model.SnapshotDocument | None:
    payload = _daemon.get(path, host, port)
    if payload is None:
        return None
    try:
        return _model.SnapshotDocument.model_validate_json(payload)
    except ValidationError:
        return None


def _page(path: str, host: str, port: int) -> _model.EntryPageDocument | None:
    payload = _daemon.get(path, host, port)
    if payload is None:
        return None
    try:
        return _model.EntryPageDocument.model_validate_json(payload)
    except ValidationError:
        return None


def connect(pane: PaneReader, host: str, port: int, session_id: str) -> bool:
    """Load a snapshot and its matching entry page.

    Returns:
        True if a valid snapshot was loaded, even if the entry page was unavailable.

    """
    snapshot = _snapshot(_http.SESSION_DATA_PATH % session_id, host, port)
    if snapshot is None:
        return False
    pane.model.apply_snapshot(snapshot)
    page = _page(
        _http.SESSION_ENTRIES_PATH % (session_id, pane.model.cursor),
        host,
        port,
    )
    if page is not None:
        pane.model.apply_page(page)
    return True


def follow(pane: PaneReader, host: str, port: int, session_id: str) -> None:
    """Apply stream events until the connection ends."""
    event_name = ""
    model = pane.model
    stream_path = _http.SESSION_STREAM_PATH % (session_id, model.cursor, model.entry_cursor)
    for line in _daemon.lines(
        f"{stream_path}&include_application=false",
        host,
        port,
        STREAM_STALL_SECONDS,
    ):
        if line.startswith("event: "):
            event_name = line[len("event: ") :]
        elif line.startswith("data: "):
            if event_name == "error":
                return
            pane.apply(event_name, line[len("data: ") :])
        else:
            pane.deferred_repaint()
