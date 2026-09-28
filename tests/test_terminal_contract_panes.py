# Copyright (c) 2026 Zhambyl Yermagambet
"""Tests for terminal panes."""

from __future__ import annotations

import pytest

from terminal.impl.kitty.plugin import kitty_plugin
from terminal.models.panes import PaneAnchor, PaneOpenRequest, SplitAxis
from terminal.models.values import ACTIVITY_PANE_TAG, WindowId
from tests.terminal_contract_remote import FakeRemote, RemoteArgument

WINDOW_ID_TEXT = "7"
PANE_SIZE_PERCENT = 25


def flag_value(arguments: tuple[RemoteArgument, ...], flag: str) -> RemoteArgument:
    """Return the value after one command flag.

    Returns:
        The value after one command flag.

    """
    argument_list = list(arguments)
    return argument_list[argument_list.index(flag) + 1]


def test_pane_anchor_rendering() -> None:
    """Verify the implementation renders a pane anchor."""
    remote = FakeRemote(printed="101")
    plugin = kitty_plugin(remote)
    window_id = WindowId(WINDOW_ID_TEXT)
    plugin.panes.open_pane(
        PaneOpenRequest(
            ("python3", "mirror.py"),
            "",
            "mirror",
            SplitAxis.VERTICAL,
            PANE_SIZE_PERCENT,
            PaneAnchor(window_id=window_id),
            WINDOW_ID_TEXT,
            {ACTIVITY_PANE_TAG: "session-one"},
        ),
    )
    plugin.panes.open_pane(
        PaneOpenRequest(
            ("python3", "scoreboard.py"),
            "",
            "scoreboard",
            SplitAxis.HORIZONTAL,
            5,
            PaneAnchor(tag=(ACTIVITY_PANE_TAG, "session-one")),
            WINDOW_ID_TEXT,
            {},
        ),
    )

    launches = [call for call in remote.calls if call[0] == "launch"]
    assert_window_launch(launches[0])
    assert_tag_launch(launches[1])
    assert flag_value(launches[0], "--match") == "window_id:7"
    assert ("goto-layout", "--match", "window_id:7", "splits") in remote.calls


def assert_window_launch(launch: tuple[RemoteArgument, ...]) -> None:
    """Verify a window anchor launch."""
    assert flag_value(launch, "--next-to") == "id:7"
    assert "--location=vsplit" in launch
    assert flag_value(launch, "--bias") == "25"


def assert_tag_launch(launch: tuple[RemoteArgument, ...]) -> None:
    """Verify a tag anchor launch."""
    assert flag_value(launch, "--next-to") == f"var:{ACTIVITY_PANE_TAG}=session-one"
    assert "--location=hsplit" in launch


def test_pane_keeps_focus_only_while_frontmost() -> None:
    """Verify a pane launch keeps focus only while kitty is frontmost."""
    for focused, expected in ((True, True), (False, False)):
        remote = FakeRemote(tree=[{"is_focused": focused}], printed="101")
        kitty_plugin(remote).panes.open_pane(
            PaneOpenRequest(
                ("python3", "mirror.py"),
                "",
                "mirror",
                SplitAxis.VERTICAL,
                PANE_SIZE_PERCENT,
                PaneAnchor(window_id=WindowId(WINDOW_ID_TEXT)),
                WINDOW_ID_TEXT,
                {ACTIVITY_PANE_TAG: "session-one"},
            ),
        )

        launch = next(call for call in remote.calls if call[0] == "launch")
        assert ("--keep-focus" in launch) is expected


def test_an_anchor_names_exactly_one_thing() -> None:
    """Verify an anchor names exactly one target."""
    with pytest.raises(ValueError, match="exactly one"):
        PaneAnchor()
    with pytest.raises(ValueError, match="exactly one"):
        PaneAnchor(window_id=WindowId(WINDOW_ID_TEXT), tag=("a", "b"))
