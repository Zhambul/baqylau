# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide terminal pane rendering operations."""

from __future__ import annotations

import shutil
import sys
from typing import TYPE_CHECKING, Protocol

import _extension_sections
import _handoff
import _render

if TYPE_CHECKING:
    from contextlib import ExitStack

    import _model

FALLBACK_WIDTH = 80


def terminal_width() -> int:
    """Return the current terminal width.

    Returns:
        The current terminal width.

    """
    return shutil.get_terminal_size((FALLBACK_WIDTH, 24)).columns or FALLBACK_WIDTH


class _PaneRenderingContext(Protocol):
    kind: str
    session_id: str
    sections: _extension_sections.SectionCache
    model: _model.SessionModel
    width: int
    _busy: bool
    _resized: bool
    _opened: frozenset[str]
    _published: dict[str, str]

    def _busy_state(self) -> ExitStack:
        """Keep the pane busy for one update."""

    def _copy_link(self, name: str) -> str:
        """Build a copy link."""

    def _view_link(self, entry_id: str) -> str:
        """Build a view link."""

    def _publish(self) -> None:
        """Publish copy targets."""

    def _picture(self) -> str:
        """Build the current pane picture."""


class PaneRendering:
    """Provide terminal pane rendering operations."""

    def paint(self: _PaneRenderingContext) -> None:
        """Paint one complete terminal pane."""
        if self._busy:
            self._resized = True
            return
        with self._busy_state():
            self.width = terminal_width()
            picture = self._picture()
            sys.stdout.write(picture)
            sys.stdout.flush()
            self._resized = False

    def _picture(self: _PaneRenderingContext) -> str:
        if self.kind == "mirror":
            painted = _render.mirror_rows(
                self.model,
                self.width,
                copy=self._copy_link,
                view=self._view_link,
                opened=self._opened,
            )
            sections = _extension_sections.mirror_rows(self.sections.current(self.width), self.width)
            picture = _render.mirror_screen(painted, self.width, sections)
            self._publish()
            return picture
        chips = _extension_sections.score_chips(self.sections.current(self.width))
        return _render.scoreboard(self.model, self.width, chips)

    def _copy_link(self: _PaneRenderingContext, name: str) -> str:
        return _render.COPY_SCHEME % (self.session_id, self.kind, name)

    def _view_link(self: _PaneRenderingContext, entry_id: str) -> str:
        return _render.VIEW_SCHEME % (self.session_id, self.kind, entry_id)

    def _publish(self: _PaneRenderingContext) -> None:
        """Publish current copy targets for click handlers."""
        targets = _render.copy_targets(self.model)
        if targets != self._published:
            _handoff.publish(self.session_id, self.kind, targets)
            self._published = dict(targets)
