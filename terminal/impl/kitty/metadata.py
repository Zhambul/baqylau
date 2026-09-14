# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide kitty terminal metadata."""

from typing import override

from terminal.contract import TerminalMetadata
from terminal.impl.kitty import match, remote as kitty_remote_api
from terminal.models.metadata import WindowTagRequest, WindowTagResponse
from terminal.models.values import TabId, WindowId, WindowInfo, WindowProcess
from terminal.processes import ProcessTree

MATCH_OPTION = "--match"


def _tab_windows(tab: kitty_remote_api.KittyTab, process_tree: ProcessTree) -> list[WindowInfo]:
    windows = tab.windows or []
    return [_window_info(tab, window, position, process_tree) for position, window in enumerate(windows)]


def window_processes(
    window: kitty_remote_api.KittyWindowInfo,
    process_tree: ProcessTree,
) -> tuple[WindowProcess, ...]:
    """Return the program of the window and every process below it.

    Kitty names the program it started. The process that reports for a session
    can be a child of that program, so the children answer for the window too.

    Returns:
        The processes of the window.

    """
    found: list[WindowProcess] = []
    for process in window.foreground_processes or ():
        command = tuple(process.cmdline or ())
        found.append(WindowProcess(process_id=process.pid, command=command))
        found.extend(process_tree.descendants(process.pid))
    return tuple(found)


def _window_info(
    tab: kitty_remote_api.KittyTab,
    window: kitty_remote_api.KittyWindowInfo,
    position: int,
    process_tree: ProcessTree,
) -> WindowInfo:
    return WindowInfo(
        window_id=WindowId(str(window.id)),
        tab_id=TabId(str(tab.id)),
        tags=window.user_vars or {},
        columns=int(window.columns or 0),
        lines=int(window.lines or 0),
        is_first_in_tab=position == 0,
        tab_is_active=bool(tab.is_active),
        tab_is_focused=bool(tab.is_focused),
        is_active_in_tab=bool(window.is_active),
        processes=window_processes(window, process_tree),
    )


class KittyMetadata(TerminalMetadata):
    """Provide kitty terminal metadata operations."""

    def __init__(self, kitty_remote: kitty_remote_api.KittyRemote) -> None:
        """Initialize the object."""
        self.kitty_remote = kitty_remote
        self._last_windows: tuple[WindowInfo, ...] = ()

    def windows(self) -> tuple[WindowInfo, ...]:
        """Return the known terminal windows.

        Returns:
            The known terminal windows.

        """
        tree = self.kitty_remote.ls()
        if tree is None:
            return self._last_windows
        process_tree = ProcessTree.sample()
        found = []
        for operating_system_window in tree:
            for tab in operating_system_window.tabs or []:
                found.extend(_tab_windows(tab, process_tree))
        self._last_windows = tuple(found)
        return self._last_windows

    def tag_window(self, window_tag_request: WindowTagRequest) -> WindowTagResponse:
        """Set tags on a window.

        Returns:
            The tag update result with a reason on failure.

        """
        assignments = [f"{tag_name}={tag_content}" for tag_name, tag_content in window_tag_request.tags.items()]
        succeeded = not self.kitty_remote.run(
            "set-user-vars",
            MATCH_OPTION,
            match.window(window_tag_request.window_id),
            *assignments,
        )
        return WindowTagResponse(succeeded, None if succeeded else "terminal window tagging failed")

    @override
    def current_window_id(self) -> WindowId | None:
        """Return the current window identifier.

        Returns:
            The current window identifier.

        """
        found = kitty_remote_api.current_window_id()
        return WindowId(found) if found else None
