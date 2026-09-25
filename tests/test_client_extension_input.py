# Copyright (c) 2026 Zhambyl Yermagambet
"""The extension pane moves its focus, scrolls, and sends the focused item's action (P07-T03)."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

from tests import test_client_loading

if TYPE_CHECKING:
    import pytest

focus = test_client_loading.load_shared("_extension_focus")
actions = test_client_loading.load_shared("_extension_actions")
pane = test_client_loading.load_shared("_extension_pane")
paint = test_client_loading.load_shared("_extension_paint")
user_input = test_client_loading.load_shared("_extension_input")
terminal = test_client_loading.load_shared("_model_terminal")
VIEW = terminal.TerminalViewDocument.model_validate_json(
    b'{"title":"Repo","blocks":[{"kind":"section","block_id":"s","title":"Files","children":['
    b'{"kind":"file_tree","block_id":"tree","nodes":[{"item_id":"src","label":"src","action_id":"open"}]}]},'
    b'{"kind":"list","block_id":"threads","entries":[{"item_id":"t1","label":{"spans":[{"text":"First"}]}}]},'
    b'{"kind":"table","block_id":"jobs","columns":["Job"],"rows":[{"item_id":"j1","cells":[{"spans":[]}]}]}]}',
)
SOURCE = "src"
TARGET = pane.ExtensionPaneTarget("127.0.0.1", 1, "test.git", "test.git.tree", "{}")


def test_focus_follows_paint_order() -> None:
    """Tree nodes inside a section, list entries, and table rows take focus in paint order."""
    focusable = focus.focus_items(VIEW)
    names = [(entry.block_id, entry.item_id) for entry in focusable]

    assert names == [("tree", SOURCE), ("threads", "t1"), ("jobs", "j1")]
    assert focus.moved(focusable, None, "").item_id == SOURCE
    assert focus.moved(focusable, focusable[0], "up").item_id == SOURCE
    assert focus.moved(focusable, focusable[2], "down").item_id == "j1"
    gone = focus.FocusItem("old", "removed")
    assert focus.moved(focusable, gone, "").item_id == SOURCE


def test_pages_scroll_under_status_line() -> None:
    """A page moves the offset; the window never starts past the last page and ends with the status line."""
    state = pane.PaneState(status="Sent: src")
    painted = [f"row {index}" for index in range(10)]

    user_input.apply_key("page_down", state, TARGET)
    window = paint.visible_rows(painted, state, 4)

    assert window == ["row 7", "row 8", "row 9", "Sent: src"]
    user_input.apply_key("page_up", state, TARGET)
    assert state.offset == 0


def test_enter_without_action_says_so() -> None:
    """An item without an action sends nothing."""
    state = pane.PaneState(focus=focus.FocusItem("threads", "t1"))

    user_input.apply_key("open", state, TARGET)

    assert state.status == user_input.NO_ACTION


def test_action_status_follows_the_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    """Accepted, refused, and unreachable each have their own status line."""
    selected = focus.FocusItem("tree", SOURCE, "open")
    replies = iter((HTTPStatus.ACCEPTED, HTTPStatus.FORBIDDEN, None))
    monkeypatch.setattr(actions, "_post", lambda *_: next(replies))

    size = (80, 24)
    statuses = [actions.send_action(TARGET, selected, size) for _ in range(3)]

    refused = actions.REFUSED % HTTPStatus.FORBIDDEN
    assert statuses == [actions.SENT % SOURCE, refused, actions.DOWN]
