# Copyright (c) 2026 Zhambyl Yermagambet
"""Fetch and paint one extension terminal view at the pane size, with the pane's focus and scroll."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from http import HTTPStatus
from urllib.parse import quote

import _extension_paint
import _http
import _model_terminal
from _daemon_exchange import connection, get_exchange
from _extension_focus import FocusItem

REQUEST_TIMEOUT_SECONDS = 5.0
WAITING = "waiting for the baqylau daemon…"
REMOVED = "This extension view is no longer available. The pane closes."
FAILED = "This extension view failed. The pane tries again after the next change."
FOCUS_QUERY = "&block=%s&item=%s"


@dataclass(frozen=True)
class ExtensionPaneTarget:
    """Name the daemon and one extension view with its scope."""

    host: str
    port: int
    extension_id: str
    view_id: str
    scope: str

    def path(self, columns: int, rows: int, focus: FocusItem | None = None) -> str:
        """Build the view path at one pane size, with the focused item.

        Returns:
            The request path.

        """
        identity = (self.extension_id, self.view_id, self.scope)
        names = tuple(quote(name, safe="") for name in identity)
        path = _http.EXTENSION_VIEW_PATH % (*names, columns, rows)
        if focus is None:
            return path
        block_part = quote(focus.block_id, safe="")
        item_part = quote(focus.item_id, safe="")
        return path + FOCUS_QUERY % (block_part, item_part)

    def changes_path(self, generation: str, cursor: int) -> str:
        """Build the path of the extension's record changes in this scope after one cursor.

        Returns:
            The change stream path.

        """
        identity = (self.extension_id, self.scope, generation)
        names = tuple(quote(name, safe="") for name in identity)
        return _http.EXTENSION_CHANGES_PATH % (*names, cursor)


@dataclass
class PaneState:
    """Keep the pane's focus, scroll offset, status line, and focusable items between paints."""

    focus: FocusItem | None = None
    offset: int = 0
    status: str = ""
    focusable: tuple[FocusItem, ...] = field(default_factory=tuple)


def paint_once(target: ExtensionPaneTarget, state: PaneState) -> bool:
    """Fetch the view at the current size and focus, then paint the visible rows.

    Returns:
        False when the host says the view is no longer active.

    """
    size = shutil.get_terminal_size()
    path = target.path(size.columns, size.lines, state.focus)
    status, payload = _fetch(target, path)
    if status == HTTPStatus.NOT_FOUND:
        _extension_paint.show([REMOVED])
        return False
    if status != HTTPStatus.OK:
        _extension_paint.show([FAILED if status == HTTPStatus.SERVICE_UNAVAILABLE else WAITING])
        return True
    view = _model_terminal.TerminalViewReply.model_validate_json(payload).view
    _extension_paint.paint_view(view, state, size)
    return True


def _fetch(target: ExtensionPaneTarget, path: str) -> tuple[int | None, bytes]:
    active_connection = connection(target.host, target.port, REQUEST_TIMEOUT_SECONDS)
    try:
        return get_exchange(active_connection, path)
    except OSError:
        return None, b""
    finally:
        active_connection.close()
