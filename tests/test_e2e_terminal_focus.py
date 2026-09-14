# Copyright (c) 2026 Zhambyl Yermagambet
"""Test real terminal focus observations."""

from terminal.models.values import WindowId
from tests import fake_terminal_models
from tests.e2e.testkit import terminal_focus

ORIGIN_ID = "origin"
ORIGIN_TAB = "origin-tab"


def test_current_focus_uses_the_active_window() -> None:
    """Use the focused window when pytest runs in another tab."""
    origin = fake_terminal_models.window(
        ORIGIN_ID,
        tab_id=ORIGIN_TAB,
        tab_is_active=False,
        tab_is_focused=False,
        is_active_in_tab=True,
    )
    focused = fake_terminal_models.window(
        "focused",
        tab_id="focused-tab",
        tab_is_focused=True,
        is_active_in_tab=True,
    )

    result = terminal_focus.current_focus((origin, focused), WindowId(ORIGIN_ID))

    assert result.window_id == "focused"
    assert result.tab_id == "focused-tab"
    assert result.kitty_focused


def test_current_focus_reports_a_background_app() -> None:
    """Use the pytest window when no Kitty window has focus."""
    origin = fake_terminal_models.window(
        ORIGIN_ID,
        tab_id=ORIGIN_TAB,
        tab_is_focused=False,
        is_active_in_tab=True,
    )

    result = terminal_focus.current_focus((origin,), WindowId(ORIGIN_ID))

    assert result.window_id == ORIGIN_ID
    assert result.tab_id == ORIGIN_TAB
    assert not result.kitty_focused
