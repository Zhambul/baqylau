# Copyright (c) 2026 Zhambyl Yermagambet
"""A terminal double: one object implementing all five sub-protocols.

It keeps a real window list, so gestures that READ the terminal back (pane
rediscovery, the scoreboard's height settle, liveness) exercise the same path
they do against a live terminal instead of a canned answer.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from terminal.contract import TerminalPlugin
from terminal.models import input as input_models, metadata, pane_results, panes, tab_results, tabs, values, viewport
from tests.fake_terminal_models import window as window
from tests.fake_terminal_sessions import FakeSessions as FakeSessions

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

type TaggedWindow = tuple[values.WindowId, dict[str, str]]

DEFAULT_PANE_COLUMNS = 40
DEFAULT_PANE_LINES = 3


@dataclass(init=False, repr=False, eq=False)
class _FakeTerminalState:
    """Store fake terminal state."""

    pane_processes_die: bool
    windows_on_screen: list[values.WindowInfo]
    current_window: values.WindowId | None
    screen_text: str | None
    opened_panes: list[panes.PaneOpenRequest]
    opened_tabs: list[tabs.TabOpenRequest]
    tagged: list[TaggedWindow]
    closed_panes: list[values.WindowId]
    closed_tabs: list[values.WindowId]
    renamed_tabs: list[tuple[values.WindowId, str]]
    resized: list[tuple[values.WindowId, panes.SplitAxis, int]]
    focused: list[values.WindowId]
    painted: list[tuple[values.WindowId, values.TabAppearance]]
    cleared: list[values.WindowId]
    submitted: list[tuple[values.WindowId, str, input_models.TextInputMode]]
    inserted: list[tuple[values.WindowId, str, input_models.TextInputMode]]
    keys: list[tuple[values.WindowId, str]]
    screen_reads: list[viewport.ScreenReadRequest]

    def __init__(
        self,
        windows: Iterable[values.WindowInfo] = (),
        current_window: values.WindowId | str | None = None,
        screen_text: str | None = "",
        *,
        pane_processes_die: bool = False,
    ) -> None:
        # `pane_processes_die` reproduces the one failure a terminal reports as a
        # SUCCESS: it makes the window, hands it the argv, and the process exits
        # immediately — so the launch succeeded and the window is gone a moment
        # later. That is exactly how every pane died for a day (session
        # 11b25475) while `open_pane` kept answering True.
        """Initialize the object."""
        self.pane_processes_die = pane_processes_die
        self.windows_on_screen = list(windows)
        self.current_window = None if current_window is None else values.WindowId(str(current_window))
        self.screen_text = screen_text
        self.opened_panes: list[panes.PaneOpenRequest] = []
        self.opened_tabs: list[tabs.TabOpenRequest] = []
        self.tagged: list[TaggedWindow] = []
        self.closed_panes: list[values.WindowId] = []
        self.closed_tabs: list[values.WindowId] = []
        self.renamed_tabs: list[tuple[values.WindowId, str]] = []
        self.resized: list[tuple[values.WindowId, panes.SplitAxis, int]] = []
        self.focused: list[values.WindowId] = []
        self.painted: list[tuple[values.WindowId, values.TabAppearance]] = []
        self.cleared: list[values.WindowId] = []
        self.submitted: list[tuple[values.WindowId, str, input_models.TextInputMode]] = []
        self.inserted: list[tuple[values.WindowId, str, input_models.TextInputMode]] = []
        self.screen_reads = []
        self.keys: list[tuple[values.WindowId, str]] = []
        self._next_window_id = 100


class _FakeTerminalMetadata(_FakeTerminalState):
    """Provide fake terminal metadata operations."""

    def windows(self) -> tuple[values.WindowInfo, ...]:
        """Read the fake windows on screen.

        Returns:
            The current window records.

        """
        return tuple(self.windows_on_screen)

    def last_windows(self) -> tuple[values.WindowInfo, ...]:
        """Read the fake windows on screen, which the fake keeps in memory.

        Returns:
            The current window records.

        """
        return self.windows()

    def tag_window(self, request: metadata.WindowTagRequest) -> metadata.WindowTagResponse:
        """Record and apply tags to a matching window.

        Returns:
            A successful tag response, including when no window matches.

        """
        self.tagged.append((request.window_id, dict(request.tags)))
        self._replace_window(
            request.window_id,
            lambda found: replace(
                found,
                tags={**found.tags, **request.tags},
            ),
        )
        return metadata.WindowTagResponse(succeeded=True)

    def current_window_id(self) -> values.WindowId | None:
        """Read the configured current window.

        Returns:
            The window identifier, or None if no window is current.

        """
        return self.current_window

    def _tab_of(self, window_id: str | int) -> str:
        for found in self.windows_on_screen:
            if found.window_id == str(window_id):
                return found.tab_id
        return "tab-one"

    def _replace_window(
        self,
        window_id: str | int,
        change: Callable[[values.WindowInfo], values.WindowInfo],
    ) -> None:
        for index, found in enumerate(self.windows_on_screen):
            if found.window_id == str(window_id):
                self.windows_on_screen[index] = change(found)

    def _resize_window(self, window_id: str | int, axis: str, cells: int) -> None:
        """Add cells to one window along the requested axis."""
        for index, found in enumerate(self.windows_on_screen):
            if found.window_id != str(window_id):
                continue
            if axis == "vertical":
                self.windows_on_screen[index] = replace(found, lines=found.lines + cells)
                return
            self.windows_on_screen[index] = replace(found, columns=found.columns + cells)
            return


class _FakeTerminalPanes(_FakeTerminalMetadata):
    """Provide fake pane operations."""

    def open_pane(self, request: panes.PaneOpenRequest) -> pane_results.PaneOpenResponse:
        """Record a pane request and create its window record.

        Returns:
            A successful response with the new window identifier.

        """
        self.opened_panes.append(request)
        self._next_window_id += 1
        opened = window(
            self._next_window_id,
            tab_id=self._tab_of(request.same_tab_as),
            tags=request.tags,
            columns=DEFAULT_PANE_COLUMNS,
            lines=DEFAULT_PANE_LINES,
            is_first_in_tab=False,
        )
        if not self.pane_processes_die:
            self.windows_on_screen.append(opened)
        return pane_results.PaneOpenResponse(succeeded=True, window_id=opened.window_id)

    def close_pane(self, request: panes.PaneCloseRequest) -> pane_results.PaneCloseResponse:
        """Record a close request and remove the matching pane.

        Returns:
            A successful pane close response.

        """
        self.closed_panes.append(request.window_id)
        self.windows_on_screen = [found for found in self.windows_on_screen if found.window_id != request.window_id]
        return pane_results.PaneCloseResponse(succeeded=True)

    def resize_pane(self, request: panes.PaneResizeRequest) -> pane_results.PaneResizeResponse:
        """Record a resize request and change the matching window size.

        Returns:
            A successful pane resize response.

        """
        self.resized.append((request.window_id, request.axis, request.cells))
        self._resize_window(request.window_id, request.axis, request.cells)
        return pane_results.PaneResizeResponse(succeeded=True)

    def focus_window(self, request: panes.WindowFocusRequest) -> pane_results.WindowFocusResponse:
        """Record the requested window focus.

        Returns:
            A successful focus response.

        """
        self.focused.append(request.window_id)
        return pane_results.WindowFocusResponse(succeeded=True)


class _FakeTerminalTabs(_FakeTerminalState):
    """Provide fake tab operations."""

    def open_tab(self, request: tabs.TabOpenRequest) -> tab_results.TabOpenResponse:
        """Record a tab open request.

        Returns:
            A successful response with a fixed window identifier.

        """
        self.opened_tabs.append(request)
        return tab_results.TabOpenResponse(succeeded=True, window_id=values.WindowId("window-two"))

    def close_tab(self, request: tabs.TabCloseRequest) -> tab_results.TabCloseResponse:
        """Record a tab close request.

        Returns:
            A successful tab close response.

        """
        self.closed_tabs.append(request.window_id)
        return tab_results.TabCloseResponse(succeeded=True)

    def rename_tab(self, request: tabs.TabRenameRequest) -> tab_results.TabRenameResponse:
        """Record the requested tab title.

        Returns:
            A successful tab rename response.

        """
        self.renamed_tabs.append((request.window_id, request.title))
        return tab_results.TabRenameResponse(succeeded=True)

    def set_tab_color(self, request: tabs.TabColorSetRequest) -> tab_results.TabColorSetResponse:
        """Record the requested tab appearance.

        Returns:
            A successful color set response.

        """
        self.painted.append((request.window_id, request.appearance))
        return tab_results.TabColorSetResponse(succeeded=True)

    def clear_tab_color(
        self,
        request: tabs.TabColorClearRequest,
    ) -> tab_results.TabColorClearResponse:
        """Record the tab color clear request.

        Returns:
            A successful color clear response.

        """
        self.cleared.append(request.window_id)
        return tab_results.TabColorClearResponse(succeeded=True)


class _FakeTerminalInput(_FakeTerminalState):
    """Provide fake input and screen operations."""

    def insert_text(self, request: input_models.TextInsertRequest) -> input_models.TextInsertResponse:
        """Record text insertion without sending it.

        Returns:
            A successful text insert response.

        """
        self.inserted.append((request.window_id, request.text, request.mode))
        return input_models.TextInsertResponse(succeeded=True)

    def submit_text(self, request: input_models.TextSubmitRequest) -> input_models.TextSubmitResponse:
        """Record the text submission.

        Returns:
            A successful text submit response.

        """
        self.submitted.append((request.window_id, request.text, request.mode))
        return input_models.TextSubmitResponse(succeeded=True)

    def send_key(self, request: input_models.KeySendRequest) -> input_models.KeySendResponse:
        """Record the requested key.

        Returns:
            A successful key send response.

        """
        self.keys.append((request.window_id, request.key))
        return input_models.KeySendResponse(succeeded=True)

    def read_screen(self, request: viewport.ScreenReadRequest) -> viewport.ScreenReadResponse:
        """Return screen.

        Returns:
            Screen.

        """
        self.screen_reads.append(request)
        if self.screen_text is None:
            return viewport.ScreenReadResponse(succeeded=False, text=None, reason="terminal screen read failed")
        return viewport.ScreenReadResponse(succeeded=True, text=self.screen_text)


class FakeTerminal(
    _FakeTerminalPanes,
    _FakeTerminalTabs,
    _FakeTerminalInput,
):
    """Provide a fake terminal with all terminal protocols."""

    def plugin(self) -> TerminalPlugin:
        """Build the fake terminal plugin.

        Returns:
            The fake terminal plugin.

        """
        return TerminalPlugin("fake", self, self, self, self, self)
