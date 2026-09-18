# Copyright (c) 2026 Zhambyl Yermagambet
"""Tests for terminal tabs."""

from __future__ import annotations

import os
import tempfile

import pytest

from terminal.impl.kitty.plugin import kitty_plugin
from terminal.impl.kitty.remote import SetTabColorRcPayload, resolve_listen_on
from terminal.models.tabs import TabCloseRequest, TabColorSetRequest, TabOpenRequest
from terminal.models.values import RGB, WindowId
from tests.terminal_contract_data import tab_appearance
from tests.terminal_contract_remote import FakeRemote

ACTIVE_BACKGROUND = 0xC678DD
INVALID_RGB_COMPONENT = 256
EXPECTED_RED = 0xC6
EXPECTED_GREEN = 0x78
EXPECTED_BLUE = 0xDD


@pytest.mark.parametrize("hexadecimal", ["c678dd", "#c678dd", "C678DD"])
def test_rgb_reads_three_color_bytes(hexadecimal: str) -> None:
    """Read all three color components, with or without a prefix."""
    assert RGB.from_hex(hexadecimal) == RGB(EXPECTED_RED, EXPECTED_GREEN, EXPECTED_BLUE)


@pytest.mark.parametrize("hexadecimal", ["", "00", "0000", "00000000", "xyzxyz"])
def test_rgb_rejects_invalid_color_bytes(hexadecimal: str) -> None:
    """Reject malformed text and values with the wrong component count."""
    with pytest.raises(ValueError, match=r"six digits|non-hexadecimal"):
        RGB.from_hex(hexadecimal)


def test_tab_launch_focus() -> None:
    """Verify a tab launch keeps the current focus only while kitty is frontmost."""
    for focused, expected in ((True, True), (False, False)):
        remote = FakeRemote(tree=[{"is_focused": focused}], printed="7")
        kitty_plugin(remote).tabs.open_tab(TabOpenRequest("/work", ("claude",), ""))

        launch = next(call for call in remote.calls if call[0] == "launch")
        assert ("--keep-focus" in launch) is expected


def test_tab_launch_without_a_focus_answer_does_not_keep_focus() -> None:
    """Verify an unreadable focus state degrades to not stealing focus."""
    remote = FakeRemote(tree=None, printed="7")
    kitty_plugin(remote).tabs.open_tab(TabOpenRequest("/work", ("claude",), ""))

    launch = next(call for call in remote.calls if call[0] == "launch")
    assert "--keep-focus" not in launch


def test_tab_colour_validation() -> None:
    """Verify a tab colour is valid before wire rendering."""
    remote = FakeRemote()
    kitty_plugin(remote).tabs.set_tab_color(TabColorSetRequest(WindowId("7"), tab_appearance()))

    assert remote.raw_calls[0][0] == "set-tab-color"
    payload = remote.raw_calls[0][1]
    assert isinstance(payload, SetTabColorRcPayload)
    assert payload.colors["active_bg"] == ACTIVE_BACKGROUND
    assert payload.match == "window_id:7"
    with pytest.raises(ValueError, match="between"):
        RGB(INVALID_RGB_COMPONENT, 0, 0)


@pytest.mark.kitty
def test_real_tab_launch() -> None:
    """Run the opt-in kitty tab smoke test."""
    if not os.environ.get("CLAUDE_E2E_KITTY"):
        pytest.skip("real-kitty smoke tests are opt-in (CLAUDE_E2E_KITTY=1)")
    if not resolve_listen_on():
        pytest.skip("no kitty socket to talk to")

    tabs = kitty_plugin().tabs
    opened = tabs.open_tab(TabOpenRequest(tempfile.gettempdir(), ("sleep", "30"), "baqylau-smoke"))
    assert opened.succeeded, opened.reason
    assert opened.window_id is not None
    windows = kitty_plugin().metadata.windows()
    window_ids = {window.window_id for window in windows}
    assert opened.window_id in window_ids
    closed = tabs.close_tab(TabCloseRequest(opened.window_id))
    assert closed.succeeded, closed.reason
