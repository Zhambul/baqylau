# Copyright (c) 2026 Zhambyl Yermagambet
"""List the extension views of a window, move a selection, and open the selected view in a pane."""

from __future__ import annotations

from urllib.parse import quote

import _daemon
import _http
import _render_styles as styles
from _render_extension_text import span
from _render_rows import _RowOptions, rows
from pydantic import BaseModel, ConfigDict

TITLE = "Open an extension view"
EMPTY = "No extension view is active for this window."
HINT = "↑/↓ select · Enter open · q close"
SELECTED_MARK = "\u203a "
UNSELECTED_MARK = "  "
MOVES = frozenset(("up", "down"))


class AvailableView(BaseModel):
    model_config = ConfigDict(extra="ignore")

    extension_id: str
    view_id: str
    title: str
    scope: str


class AvailableViews(BaseModel):
    model_config = ConfigDict(extra="ignore")

    views: tuple[AvailableView, ...] = ()


class PaneOpen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    extension_id: str
    view_id: str
    scope: str
    window_id: str
    working_directory: str

    def json_bytes(self) -> bytes:
        """Encode the request body.

        Returns:
            The JSON bytes.

        """
        return self.model_dump_json().encode("utf-8")


def read_views(host: str, port: int, window_id: str) -> tuple[AvailableView, ...] | None:
    """Read the views that the daemon offers for the window.

    Returns:
        The views, or None when the daemon did not answer.

    """
    path = _http.EXTENSION_VIEWS_PATH % quote(window_id, safe="")
    payload = _daemon.get(path, host, port)
    return None if payload is None else AvailableViews.model_validate_json(payload).views


def moved(selected: int, key: str, count: int) -> int:
    """Move the selection one entry, wrapping at both ends.

    Returns:
        The new selected index.

    """
    if key not in MOVES or count == 0:
        return selected
    step = -1 if key == "up" else 1
    return (selected + step) % count


def selector_rows(views: tuple[AvailableView, ...], selected: int, width: int) -> list[str]:
    """Paint the list with a mark on the selected view.

    Returns:
        The screen rows.

    """
    painted = rows([span(TITLE, bold=True)], width)
    if not views:
        return painted + rows([span(EMPTY, "muted")], width)
    for index, view in enumerate(views):
        painted.extend(_view_row(view, width, selected=index == selected))
    return painted + rows([span(HINT, "muted")], width)


def _view_row(view: AvailableView, width: int, *, selected: bool) -> list[str]:
    mark = styles.Span(SELECTED_MARK if selected else UNSELECTED_MARK, styles.USER)
    owner = span(f"  {view.extension_id}", "muted")
    return rows([mark, span(view.title), owner], width, TRUNCATE)


TRUNCATE = _RowOptions(mode=styles.TRUNCATE_LAYOUT)
