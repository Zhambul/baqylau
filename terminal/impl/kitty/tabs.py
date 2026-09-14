# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide kitty tab operations."""

from terminal.contract import TerminalTabs
from terminal.impl.kitty import match, remote as kitty_remote_api
from terminal.models.tab_results import (
    TabCloseResponse,
    TabColorClearResponse,
    TabColorSetResponse,
    TabOpenResponse,
    TabRenameResponse,
)
from terminal.models.tabs import (
    TabCloseRequest,
    TabColorClearRequest,
    TabColorSetRequest,
    TabOpenRequest,
    TabRenameRequest,
)
from terminal.models.values import RGB, WindowId

MATCH_OPTION = "--match"
HEX_NUMBER_BASE = 16


def _opened_window_id(printed: str) -> WindowId | None:
    stripped = printed.strip()
    return WindowId(stripped) if stripped else None


def _hex(rgb: RGB) -> str:
    hex_value = bytes((rgb.red, rgb.green, rgb.blue)).hex()
    return f"#{hex_value}"


def _color_value(color_text: str) -> int | None:
    return None if color_text == kitty_remote_api.TAB_COLOR_NONE else int(color_text.lstrip("#"), HEX_NUMBER_BASE)


class KittyTabs(TerminalTabs):
    """Provide kitty tab operations."""

    def __init__(self, kitty_remote: kitty_remote_api.KittyRemote) -> None:
        """Initialize the object."""
        self.kitty_remote = kitty_remote

    def open_tab(self, tab_open_request: TabOpenRequest) -> TabOpenResponse:
        """Open a tab.

        Returns:
            The launch result with its reported window identifier, or a failure reason.

        """
        arguments = ["launch", "--type=tab", "--cwd", tab_open_request.working_directory]
        arguments.append("--keep-focus")
        for environment_variable in tab_open_request.environment:
            arguments.extend(("--env", f"{environment_variable.name}={environment_variable.content}"))
        printed = self.kitty_remote.capture(*arguments, *tab_open_request.command)
        if printed is None:
            return TabOpenResponse(succeeded=False, window_id=None, reason="terminal launch failed")
        return TabOpenResponse(succeeded=True, window_id=_opened_window_id(printed))

    def close_tab(self, tab_close_request: TabCloseRequest) -> TabCloseResponse:
        """Close a tab.

        Returns:
            The close result with a reason on failure.

        """
        succeeded = not self.kitty_remote.run("close-tab", MATCH_OPTION, match.tab_of(tab_close_request.window_id))
        return TabCloseResponse(succeeded, None if succeeded else "terminal close failed")

    def rename_tab(self, tab_rename_request: TabRenameRequest) -> TabRenameResponse:
        """Rename a tab.

        Returns:
            The title update result with a reason on failure.

        """
        succeeded = not self.kitty_remote.run(
            "set-tab-title",
            MATCH_OPTION,
            match.tab_of(tab_rename_request.window_id),
            tab_rename_request.title,
        )
        return TabRenameResponse(succeeded, None if succeeded else "terminal title failed")

    def set_tab_color(self, tab_color_set_request: TabColorSetRequest) -> TabColorSetResponse:
        """Set a tab color.

        Returns:
            The color update result with a reason on failure.

        """
        appearance = tab_color_set_request.appearance
        succeeded = not self._paint(
            tab_color_set_request.window_id,
            _hex(appearance.active_background),
            _hex(appearance.active_foreground),
            _hex(appearance.inactive_background),
            _hex(appearance.inactive_foreground),
        )
        return TabColorSetResponse(succeeded, None if succeeded else "terminal tab paint failed")

    def clear_tab_color(self, tab_color_clear_request: TabColorClearRequest) -> TabColorClearResponse:
        """Clear a tab color.

        Returns:
            The color reset result with a reason on failure.

        """
        succeeded = not self._paint(tab_color_clear_request.window_id, *(kitty_remote_api.TAB_COLOR_NONE,) * 4)
        return TabColorClearResponse(succeeded, None if succeeded else "terminal tab clear failed")

    def _paint(self, window_id: WindowId, active_bg: str, active_fg: str, inactive_bg: str, inactive_fg: str) -> int:
        colors: dict[str, int | None] | None
        try:
            colors = {
                "active_bg": _color_value(active_bg),
                "active_fg": _color_value(active_fg),
                "inactive_bg": _color_value(inactive_bg),
                "inactive_fg": _color_value(inactive_fg),
            }
        except (ValueError, AttributeError):
            colors = None
        if colors is not None:
            response = self.kitty_remote.raw(
                "set-tab-color",
                kitty_remote_api.SetTabColorRcPayload(match=match.tab_of(window_id), colors=colors),
                want_response=True,
            )
            if isinstance(response, kitty_remote_api.KittyRcResponse):
                return 0 if response.ok else 1
        return self.kitty_remote.run(
            "set-tab-color",
            MATCH_OPTION,
            match.tab_of(window_id),
            f"active_bg={active_bg}",
            f"active_fg={active_fg}",
            f"inactive_bg={inactive_bg}",
            f"inactive_fg={inactive_fg}",
        )
