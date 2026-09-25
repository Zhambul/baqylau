# Copyright (c) 2026 Zhambyl Yermagambet
"""Paint a fetched extension view in the pane: the focused item, the scroll window, and the status line."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import _render_extension_blocks
import _render_wrap
from _extension_focus import focus_items, moved

if TYPE_CHECKING:
    import os

    import _model_terminal
    from _extension_focus import FocusItem
    from _extension_pane import PaneState


def paint_view(view: _model_terminal.TerminalViewDocument, state: PaneState, size: os.terminal_size) -> None:
    """Keep the focus on an item that still exists, then paint the visible rows."""
    state.focusable = focus_items(view)
    state.focus = moved(state.focusable, state.focus, "")
    painted = _render_extension_blocks.view_rows(view, size.columns, _identity(state.focus))
    show(visible_rows(painted, state, size.lines))


def visible_rows(painted: list[str], state: PaneState, height: int) -> list[str]:
    """Show the rows from the scroll offset, and the status line at the bottom.

    Returns:
        The rows that fit in the pane.

    """
    room = max(1, height - 1)
    last_start = max(0, len(painted) - room)
    state.offset = min(state.offset, last_start)
    return [*painted[state.offset:state.offset + room], state.status]


def show(rows: list[str]) -> None:
    """Replace the pane content with the rows."""
    sys.stdout.write(_render_wrap.screen(rows))
    sys.stdout.flush()


def _identity(focus: FocusItem | None) -> tuple[str, str] | None:
    return None if focus is None else (focus.block_id, focus.item_id)
