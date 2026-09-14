# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide kitty pane operations."""

from collections.abc import Sequence

from terminal.contract import TerminalMetadata, TerminalPanes
from terminal.impl.kitty import match, remote as kitty_remote_api
from terminal.impl.kitty.tabs import _opened_window_id
from terminal.models.pane_results import (
    PaneCloseResponse,
    PaneOpenResponse,
    PaneResizeResponse,
    WindowFocusResponse,
)
from terminal.models.panes import (
    PaneCloseRequest,
    PaneOpenRequest,
    PaneResizeRequest,
    WindowFocusRequest,
)
from terminal.models.values import TabId, WindowId, WindowInfo

MATCH_OPTION = "--match"


def _tab_id_for_window(windows: Sequence[WindowInfo], wanted_window_id: WindowId) -> TabId | None:
    for window in windows:
        if window.window_id == wanted_window_id:
            return window.tab_id
    return None


def _window_position(windows: Sequence[WindowInfo], wanted_window_id: WindowId) -> int:
    for index, window in enumerate(windows):
        if window.window_id == wanted_window_id:
            return index
    raise StopIteration


def _pane_open_arguments(pane_open_request: PaneOpenRequest, anchor_tab: str) -> list[str]:
    arguments = ["launch", MATCH_OPTION, anchor_tab]
    split_location = "vsplit" if pane_open_request.split == "vertical" else "hsplit"
    arguments.extend((f"--location={split_location}", "--next-to", match.anchor(pane_open_request.anchor)))
    arguments.extend(("--bias", str(pane_open_request.size_percent)))
    if pane_open_request.keep_focus:
        arguments.append("--keep-focus")
    arguments.extend(("--cwd", pane_open_request.working_directory or "current"))
    for tag_name, tag_content in pane_open_request.tags.items():
        arguments.extend(("--var", f"{tag_name}={tag_content}"))
    if pane_open_request.title:
        arguments.extend(("--title", pane_open_request.title))
    return arguments


class KittyPanes(TerminalPanes):
    """Provide kitty pane operations."""

    def __init__(self, kitty_remote: kitty_remote_api.KittyRemote, terminal_metadata: TerminalMetadata) -> None:
        """Initialize the object."""
        self.kitty_remote = kitty_remote
        self.terminal_metadata = terminal_metadata

    def open_pane(self, pane_open_request: PaneOpenRequest) -> PaneOpenResponse:
        """Open a pane.

        Returns:
            The launch result with its reported window identifier, or a failure reason.

        """
        anchor_tab = match.tab_of(WindowId(pane_open_request.same_tab_as))
        self.kitty_remote.run("goto-layout", MATCH_OPTION, anchor_tab, "splits")
        arguments = _pane_open_arguments(pane_open_request, anchor_tab)
        printed = self.kitty_remote.capture(*arguments, *pane_open_request.command)
        if printed is None:
            return PaneOpenResponse(succeeded=False, window_id=None, reason="terminal pane launch failed")
        return PaneOpenResponse(succeeded=True, window_id=_opened_window_id(printed))

    def close_pane(self, pane_close_request: PaneCloseRequest) -> PaneCloseResponse:
        """Close a pane.

        Returns:
            The close result with a reason on failure.

        """
        succeeded = not self.kitty_remote.run("close-window", MATCH_OPTION, match.window(pane_close_request.window_id))
        return PaneCloseResponse(succeeded, None if succeeded else "terminal pane close failed")

    def resize_pane(self, pane_resize_request: PaneResizeRequest) -> PaneResizeResponse:
        """Resize a pane.

        Returns:
            The resize result with a reason on failure.

        """
        succeeded = not self.kitty_remote.run(
            "resize-window",
            MATCH_OPTION,
            match.window(pane_resize_request.window_id),
            "--axis",
            pane_resize_request.axis,
            "--increment",
            str(pane_resize_request.cells),
        )
        return PaneResizeResponse(succeeded, None if succeeded else "terminal pane resize failed")

    def focus_window(self, window_focus_request: WindowFocusRequest) -> WindowFocusResponse:
        """Focus a window without selecting its tab.

        Returns:
            The focus result, or a failure reason if the window is absent or the command fails.

        """
        index = self._position_in_tab(window_focus_request.window_id)
        if index is None:
            return WindowFocusResponse(succeeded=False, reason="window is not on screen")
        action = ["first_window"] if index == 0 else ["nth_window", str(index)]
        tab_match = match.tab_of(window_focus_request.window_id)
        succeeded = not self.kitty_remote.run("action", MATCH_OPTION, tab_match, *action)
        return WindowFocusResponse(succeeded, None if succeeded else "terminal focus failed")

    def _position_in_tab(self, window_id: WindowId) -> int | None:
        windows = self.terminal_metadata.windows()
        wanted_window_id = window_id
        tab_id = _tab_id_for_window(windows, wanted_window_id)
        if tab_id is None:
            return None
        siblings = [window for window in windows if window.tab_id == tab_id]
        return _window_position(siblings, wanted_window_id)
