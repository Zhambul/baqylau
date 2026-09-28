# Copyright (c) 2026 Zhambyl Yermagambet
"""The pane selector lists a window's views, moves its selection, and asks the daemon to open one (P07-T02)."""

from __future__ import annotations

import re

from tests import client_test_servers, test_client_loading

selector = test_client_loading.load_shared("_extension_selector")
daemon = client_test_servers.daemon
ANSI = re.compile(r"\x1b\[[0-9;]*m")
VIEWS_JSON = (
    b'{"views":[{"extension_id":"test.logs","view_id":"test.logs.main","title":"Logs","scope":"{}"},'
    b'{"extension_id":"test.git","view_id":"test.git.tree","title":"Files","scope":"{}"}]}'
)
WIDTH = 60


def plain_rows(views: tuple[object, ...], selected: int) -> list[str]:
    """Paint the selector without styles.

    Returns:
        The visible rows.

    """
    rows = selector.selector_rows(views, selected, WIDTH)
    return [ANSI.sub("", row).rstrip() for row in rows]


def test_views_come_from_the_window(daemon: client_test_servers._Capture) -> None:
    """The selector asks for the views of its own window."""
    daemon.replies["/api/extension-terminal/views"] = VIEWS_JSON

    views = selector.read_views("127.0.0.1", daemon.port, "window 7")

    assert [view.title for view in views] == ["Logs", "Files"]
    assert daemon.delivery("/api/extension-terminal/views").path.endswith("window_id=window%207")


def test_selection_moves_and_wraps() -> None:
    """Up and down move one entry and wrap; other keys do not move."""
    assert selector.moved(0, "down", 2) == 1
    assert selector.moved(1, "down", 2) == 0
    assert selector.moved(0, "up", 2) == 1
    assert selector.moved(1, "open", 2) == 1
    assert selector.moved(0, "down", 0) == 0


def test_rows_mark_the_selected_view() -> None:
    """The selected view has the mark; an empty list says that no view is active."""
    views = selector.AvailableViews.model_validate_json(VIEWS_JSON).views
    painted = plain_rows(views, 1)
    empty = plain_rows((), 0)

    assert painted[2].startswith(f"{selector.SELECTED_MARK}Files")
    assert painted[1].startswith("  Logs")
    assert empty[1] == selector.EMPTY
