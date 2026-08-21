# terminal/impl/pty/plugin.py — a pseudo-terminal as a TerminalPlugin.
#
# The five sub-protocols of terminal/contract.py over ptys this process owns.
# Everything a terminal APPLICATION provides — tabs, splits, tab colours, window
# focus — has no counterpart here and answers with the contract's failure shape
# and a reason, the same way the null plugin does for everything. What a pty
# genuinely has, it really implements: a program, a screen, keys, a size.
#
# Selected only by pinning `BAQYLAU_TERMINAL=pty`, never by detection. A pty is
# available on every POSIX machine, so a detector for it would fire wherever
# no real terminal is installed, and the daemon would launch harnesses into windows
# nobody can see — worse than having no terminal, which is at least visible as
# nothing happening.

from __future__ import annotations

import os
from itertools import count

from terminal.contract import (
    TerminalInput,
    TerminalMetadata,
    TerminalPanes,
    TerminalPlugin,
    TerminalTabs,
    TerminalViewport,
)
from terminal.impl.pty import keys
from terminal.impl.pty.window import PtyWindow, open_window
from terminal.models.input import (
    KeySendRequest,
    KeySendResponse,
    TextSubmitRequest,
    TextSubmitResponse,
)
from terminal.models.metadata import WindowTagRequest, WindowTagResponse
from terminal.models.panes import (
    PaneCloseRequest,
    PaneCloseResponse,
    PaneOpenRequest,
    PaneOpenResponse,
    PaneResizeRequest,
    PaneResizeResponse,
    WindowFocusRequest,
    WindowFocusResponse,
)
from terminal.models.tabs import (
    TabCloseRequest,
    TabCloseResponse,
    TabColorClearRequest,
    TabColorClearResponse,
    TabColorSetRequest,
    TabColorSetResponse,
    TabOpenRequest,
    TabOpenResponse,
    TabRenameRequest,
    TabRenameResponse,
)
from terminal.models.values import WindowInfo
from terminal.models.viewport import ScreenReadRequest, ScreenReadResponse

NO_CHROME = "a pty has no tabs to show"
NO_SPLITS = "a pty has no panes to split"
NO_FOCUS = "a pty has no keyboard focus to move"
NO_WINDOW = "no such pty window"
# What an ANSI screen read would take: pyte keeps per-cell attributes, so the
# SGR runs could be reconstructed from the grid. Nothing asks a pty for one —
# the callers that read formatting are probing a screen a user is looking at —
# so it reports the limit instead of answering a different question than it was
# asked.
NO_ANSI = "the pty terminal reads plain screens only"

# How a program launched into one of these windows learns WHICH window it is in.
#
# Every window fact in the system originates in the launched process: a hook runs
# inside the session's own window and is the only thing that can observe which
# one that is, so it reads this from its environment and ships the answer as
# evidence (`client/_wire.py` WINDOW_ID_VARIABLES, which carries one name per
# terminal we can drive). A terminal that exported nothing left every session's
# window unknown, and with it every gesture that needs one — send-text,
# interrupt, backgrounding — declining with "session is not live" forever.
# A terminal with a window manager of its own hands its programs such a variable
# for free; a pseudo-terminal has no window manager and no such convention, so
# this establishes one.
WINDOW_ID_VARIABLE = "BAQYLAU_PTY_WINDOW_ID"


class PtyWindows:
    """The open windows, and the environment their programs inherit.

    The environment is injected rather than read from `os.environ` here, for the
    same reason the channel to a terminal application is: the caller may need to
    hand the program a different one than this process holds — a test rig
    scrubbing the session identity it was itself started with, for one.
    """

    def __init__(self, environment: dict[str, str] | None = None) -> None:
        self.environment = dict(os.environ if environment is None else environment)
        self.windows: dict[str, PtyWindow] = {}
        self._ids = count(1)

    def launch(
        self,
        command: tuple[str, ...],
        working_directory: str,
        environment: tuple[tuple[str, str], ...],
    ) -> PtyWindow | None:
        window_id = str(next(self._ids))
        child_environment = dict(self.environment)
        child_environment.update({str(name): str(value) for name, value in environment})
        # Last, so the window's own identity cannot be overridden by a caller's
        # environment: this is the one value the terminal knows and the program
        # cannot be told by anyone else.
        child_environment[WINDOW_ID_VARIABLE] = window_id
        window = open_window(window_id, tuple(command), working_directory, child_environment)
        if window is not None:
            self.windows[window_id] = window
        return window

    def get(self, window_id: str) -> PtyWindow | None:
        return self.windows.get(str(window_id))

    def close(self, window_id: str) -> bool:
        window = self.windows.pop(str(window_id), None)
        if window is None:
            return False
        return window.close()


class PtyTabs(TerminalTabs):
    def __init__(self, store: PtyWindows) -> None:
        self.store = store

    def open_tab(self, request: TabOpenRequest) -> TabOpenResponse:
        # The request's title is not applied, for the reason TabOpenRequest gives
        # for leaving it to the program — and there is nothing here to show it.
        window = self.store.launch(
            request.command, request.working_directory, request.environment
        )
        if window is None:
            return TabOpenResponse(False, None, "pty launch failed")
        return TabOpenResponse(True, window.window_id)

    def close_tab(self, request: TabCloseRequest) -> TabCloseResponse:
        closed = self.store.close(request.window_id)
        return TabCloseResponse(closed, None if closed else NO_WINDOW)

    def rename_tab(self, request: TabRenameRequest) -> TabRenameResponse:
        return TabRenameResponse(False, NO_CHROME)

    def set_tab_color(self, request: TabColorSetRequest) -> TabColorSetResponse:
        return TabColorSetResponse(False, NO_CHROME)

    def clear_tab_color(self, request: TabColorClearRequest) -> TabColorClearResponse:
        return TabColorClearResponse(False, NO_CHROME)


class PtyPanes(TerminalPanes):
    def __init__(self, store: PtyWindows) -> None:
        self.store = store

    def open_pane(self, request: PaneOpenRequest) -> PaneOpenResponse:
        return PaneOpenResponse(False, None, NO_SPLITS)

    def close_pane(self, request: PaneCloseRequest) -> PaneCloseResponse:
        closed = self.store.close(request.window_id)
        return PaneCloseResponse(closed, None if closed else NO_WINDOW)

    def resize_pane(self, request: PaneResizeRequest) -> PaneResizeResponse:
        # The one pane operation a pty really has: a window size is a property
        # of the tty, and a program watching SIGWINCH reflows for it.
        window = self.store.get(request.window_id)
        if window is None:
            return PaneResizeResponse(False, NO_WINDOW)
        columns, lines = window.screen.columns, window.screen.lines
        if request.axis == "horizontal":
            columns = max(1, columns + request.cells)
        else:
            lines = max(1, lines + request.cells)
        resized = window.resize(columns, lines)
        return PaneResizeResponse(resized, None if resized else "pty resize failed")

    def focus_window(self, request: WindowFocusRequest) -> WindowFocusResponse:
        return WindowFocusResponse(False, NO_FOCUS)


class PtyMetadata(TerminalMetadata):
    def __init__(self, store: PtyWindows) -> None:
        self.store = store

    def windows(self) -> tuple[WindowInfo, ...]:
        """Every open window, each alone in a tab of its own — which is what a
        pty is: one program on one tty, with nothing beside it.

        A window whose program has EXITED is not listed: in a real terminal that
        window is gone, and a caller asking what is on screen is asking about
        windows a user could be looking at. It is still readable by id until it
        is closed, so the screen a program died on survives for whoever needs to
        see why."""
        return tuple(
            WindowInfo(
                window_id=window.window_id,
                tab_id=window.window_id,
                tags=dict(window.tags),
                columns=window.screen.columns,
                lines=window.screen.lines,
                is_first_in_tab=True,
                tab_is_active=True,
                # Nothing here holds keyboard focus: there is no keyboard and no
                # user. Reporting focus would make the mirror believe a window
                # the user is looking at is on screen.
                tab_is_focused=False,
            )
            for window in self.store.windows.values()
            if window.process.poll() is None
        )

    def tag_window(self, request: WindowTagRequest) -> WindowTagResponse:
        window = self.store.get(request.window_id)
        if window is None:
            return WindowTagResponse(False, NO_WINDOW)
        # Stored IN the window, so a tag has exactly the window's lifetime —
        # the property the contract asks of this operation.
        window.tags.update({str(name): str(value) for name, value in request.tags.items()})
        return WindowTagResponse(True)

    def current_window_id(self) -> str | None:
        # The process asking is never inside one of these: it OWNS them.
        return None


class PtyInput(TerminalInput):
    def __init__(self, store: PtyWindows) -> None:
        self.store = store

    def submit_text(self, request: TextSubmitRequest) -> TextSubmitResponse:
        window = self.store.get(request.window_id)
        if window is None:
            return TextSubmitResponse(False, NO_WINDOW)
        payload = request.text.encode("utf-8")
        if request.mode == "paste":
            payload = keys.BRACKETED_PASTE_START + payload + keys.BRACKETED_PASTE_END
        # The Enter stays a separate keystroke, so it submits rather than
        # becoming a newline in the draft (TextSubmitRequest).
        delivered = window.write(payload) and window.write(keys.NAMED_KEYS["enter"])
        return TextSubmitResponse(delivered, None if delivered else "pty input failed")

    def send_key(self, request: KeySendRequest) -> KeySendResponse:
        window = self.store.get(request.window_id)
        if window is None:
            return KeySendResponse(False, NO_WINDOW)
        payload = keys.chord(request.key)
        if payload is None:
            return KeySendResponse(False, f"the pty terminal cannot send {request.key!r}")
        delivered = window.write(payload)
        return KeySendResponse(delivered, None if delivered else "pty key input failed")


class PtyViewport(TerminalViewport):
    def __init__(self, store: PtyWindows) -> None:
        self.store = store

    def read_screen(self, request: ScreenReadRequest) -> ScreenReadResponse:
        if request.ansi:
            return ScreenReadResponse(False, None, NO_ANSI)
        window = self.store.get(request.window_id)
        if window is None:
            return ScreenReadResponse(False, None, NO_WINDOW)
        return ScreenReadResponse(True, window.display())


def pty_plugin(windows: PtyWindows | None = None) -> TerminalPlugin:
    windows = windows if windows is not None else PtyWindows()
    return TerminalPlugin(
        name="pty",
        tabs=PtyTabs(windows),
        panes=PtyPanes(windows),
        metadata=PtyMetadata(windows),
        input=PtyInput(windows),
        viewport=PtyViewport(windows),
    )
