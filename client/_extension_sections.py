# Copyright (c) 2026 Zhambyl Yermagambet
"""Read and paint the extension sections of a session mirror or scoreboard.

A section refreshes at most every two seconds, and at once when the pane width
changes. When the daemon does not answer, the pane keeps its last sections.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from urllib.parse import quote

import _daemon
import _http
import _model_terminal
import _render_styles as styles
from _render_extension_blocks import view_rows
from _render_extension_text import TONE_COLORS, span
from _render_rows import rows
from _render_styles import _ScorePart

REFRESH_SECONDS = 2.0
REQUEST_TIMEOUT_SECONDS = 1.0
SECTION_ROWS = 24
UNAVAILABLE = "%s is not available."


@dataclass
class SectionCache:
    """Keep the last sections of one session pane."""

    host: str
    port: int
    session_id: str
    kind: str
    _reply: _model_terminal.PaneSectionsReply = field(default_factory=_model_terminal.PaneSectionsReply)
    _fetched_at: float | None = None
    _width: int = 0

    def current(self, width: int) -> _model_terminal.PaneSectionsReply:
        """Refresh when the sections are old or the width changed.

        Returns:
            The newest sections that the daemon gave.

        """
        now = time.monotonic()
        fresh = self._fetched_at is not None and now - self._fetched_at < REFRESH_SECONDS
        if fresh and width == self._width:
            return self._reply
        self._fetched_at = now
        self._width = width
        session = quote(self.session_id, safe="")
        path = _http.EXTENSION_SECTIONS_PATH % (session, self.kind, width, SECTION_ROWS)
        payload = _daemon.get(path, self.host, self.port, REQUEST_TIMEOUT_SECONDS)
        if payload is not None:
            self._reply = _model_terminal.PaneSectionsReply.model_validate_json(payload)
        return self._reply


def mirror_rows(reply: _model_terminal.PaneSectionsReply, width: int) -> tuple[str, ...]:
    """Paint each section in full, and one line for each view that failed.

    Returns:
        The rows below the mirror's task panel.

    """
    painted: list[str] = []
    for view in reply.views:
        painted.extend(view_rows(view, width))
    for title in reply.unavailable:
        painted.extend(rows([span(UNAVAILABLE % title, "muted")], width))
    return tuple(painted)


def score_chips(reply: _model_terminal.PaneSectionsReply) -> tuple[_ScorePart, ...]:
    """Show each section as one short chip: its first status, else its title.

    The scoreboard has a fixed height, so a section never adds a row.

    Returns:
        The chips of the scoreboard's detail row.

    """
    chips = [_chip(view) for view in reply.views]
    chips.extend(_ScorePart(f"{title} unavailable", styles.FAILURE) for title in reply.unavailable)
    return tuple(chips)


def _chip(view: _model_terminal.TerminalViewDocument) -> _ScorePart:
    status = next((block for block in view.blocks if block.kind == "status"), None)
    if status is None:
        return _ScorePart(view.title, styles.MUTED)
    return _ScorePart(f"{view.title}: {status.label}", TONE_COLORS[status.tone])
