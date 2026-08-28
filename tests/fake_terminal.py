"""A terminal double: one object implementing all five sub-protocols.

It keeps a real window list, so gestures that READ the terminal back (pane
rediscovery, the scoreboard's height settle, liveness) exercise the same path
they do against a live terminal instead of a canned answer.
"""

from __future__ import annotations

from dataclasses import replace

from terminal.contract import TerminalPlugin
from terminal.models import (
    KeySendResponse,
    PaneCloseResponse,
    PaneOpenResponse,
    PaneResizeResponse,
    ScreenReadResponse,
    TabCloseResponse,
    TabColorClearResponse,
    TabColorSetResponse,
    TabOpenResponse,
    TabRenameResponse,
    TextInsertResponse,
    TextSubmitResponse,
    ViewportScrollResponse,
    WindowFocusResponse,
    WindowInfo,
    WindowTagResponse,
)

DEFAULT_PANE_COLUMNS = 40
DEFAULT_PANE_LINES = 3


def window(window_id, tab_id="tab-one", tags=None, columns=80, lines=24,
           is_first_in_tab=True, tab_is_active=True, tab_is_focused=True,
           is_active_in_tab=True):
    return WindowInfo(
        window_id=str(window_id),
        tab_id=str(tab_id),
        tags=dict(tags or {}),
        columns=columns,
        lines=lines,
        is_first_in_tab=is_first_in_tab,
        tab_is_active=tab_is_active,
        tab_is_focused=tab_is_focused,
        is_active_in_tab=is_active_in_tab,
    )


class FakeTerminal:
    def __init__(self, windows=(), current_window=None, screen_text="",
                 pane_processes_die=False):
        # `pane_processes_die` reproduces the one failure a terminal reports as a
        # SUCCESS: it makes the window, hands it the argv, and the process exits
        # immediately — so the launch succeeded and the window is gone a moment
        # later. That is exactly how every pane died for a day (session
        # 11b25475) while `open_pane` kept answering True.
        self.pane_processes_die = pane_processes_die
        self.windows_on_screen = list(windows)
        self.current_window = current_window
        self.screen_text = screen_text
        self.opened_panes = []
        self.opened_tabs = []
        self.tagged = []
        self.closed_panes = []
        self.closed_tabs = []
        self.renamed_tabs = []
        self.resized = []
        self.focused = []
        self.painted = []
        self.cleared = []
        self.submitted = []
        self.inserted = []
        self.keys = []
        self.scrolled = []
        self._next_window_id = 100

    def plugin(self) -> TerminalPlugin:
        return TerminalPlugin("fake", self, self, self, self, self)

    # --- metadata ------------------------------------------------------------
    def windows(self):
        return tuple(self.windows_on_screen)

    def tag_window(self, request):
        self.tagged.append((request.window_id, dict(request.tags)))
        self._replace_window(request.window_id, lambda found: replace(
            found, tags={**found.tags, **request.tags}
        ))
        return WindowTagResponse(True)

    def current_window_id(self):
        return self.current_window

    # --- panes ---------------------------------------------------------------
    def open_pane(self, request):
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
        return PaneOpenResponse(True, opened.window_id)

    def close_pane(self, request):
        self.closed_panes.append(request.window_id)
        self.windows_on_screen = [found for found in self.windows_on_screen
                                  if found.window_id != request.window_id]
        return PaneCloseResponse(True)

    def resize_pane(self, request):
        self.resized.append((request.window_id, request.axis, request.cells))
        if request.axis == "vertical":
            self._replace_window(request.window_id,
                                 lambda found: replace(found, lines=found.lines + request.cells))
        else:
            self._replace_window(request.window_id,
                                 lambda found: replace(found, columns=found.columns + request.cells))
        return PaneResizeResponse(True)

    def focus_window(self, request):
        self.focused.append(request.window_id)
        return WindowFocusResponse(True)

    # --- tabs ----------------------------------------------------------------
    def open_tab(self, request):
        self.opened_tabs.append(request)
        return TabOpenResponse(True, "window-two")

    def close_tab(self, request):
        self.closed_tabs.append(request.window_id)
        return TabCloseResponse(True)

    def rename_tab(self, request):
        self.renamed_tabs.append((request.window_id, request.title))
        return TabRenameResponse(True)

    def set_tab_color(self, request):
        self.painted.append((request.window_id, request.appearance))
        return TabColorSetResponse(True)

    def clear_tab_color(self, request):
        self.cleared.append(request.window_id)
        return TabColorClearResponse(True)

    # --- input / viewport ----------------------------------------------------
    def insert_text(self, request):
        self.inserted.append((request.window_id, request.text, request.mode))
        return TextInsertResponse(True)

    def submit_text(self, request):
        self.submitted.append((request.window_id, request.text, request.mode))
        return TextSubmitResponse(True)

    def send_key(self, request):
        self.keys.append((request.window_id, request.key))
        return KeySendResponse(True)

    def read_screen(self, request):
        if self.screen_text is None:
            return ScreenReadResponse(False, None, "terminal screen read failed")
        return ScreenReadResponse(True, self.screen_text)

    def scroll(self, request):
        self.scrolled.append((request.window_id, request.to_bottom, request.up_lines))
        return ViewportScrollResponse(True)

    # --- internals -----------------------------------------------------------
    def _tab_of(self, window_id):
        return next((found.tab_id for found in self.windows_on_screen
                     if found.window_id == str(window_id)), "tab-one")

    def _replace_window(self, window_id, change):
        for index, found in enumerate(self.windows_on_screen):
            if found.window_id == str(window_id):
                self.windows_on_screen[index] = change(found)


class FakeSessions:
    """The one column the terminal adapter reads out of the session store."""

    def __init__(self, windows_by_session=None):
        self.windows_by_session = dict(windows_by_session or {})

    def find(self, session_id):
        window_id = self.windows_by_session.get(str(session_id))
        return _SessionRow(window_id) if window_id is not None else None


class _SessionRow:
    def __init__(self, terminal_window_id):
        self.terminal_window_id = terminal_window_id
