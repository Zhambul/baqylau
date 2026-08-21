# terminal/impl/kitty/plugin.py — kitty as a TerminalPlugin.
#
# The five sub-protocols of terminal/contract.py, implemented over the remote
# control channel in remote.py. Everything kitty-shaped lives here or in
# match.py: the `ls` tree's JSON shape, the launch flags, the layout kitty
# needs before a biased split works, the raw-socket fast paths.

from typing import Any

from terminal.contract import (
    TerminalInput,
    TerminalMetadata,
    TerminalPanes,
    TerminalPlugin,
    TerminalTabs,
    TerminalViewport,
)
from terminal.impl.kitty import match
from terminal.impl.kitty.remote import TAB_COLOR_NONE, KittyRemote, current_window_id
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
from terminal.models.input import (
    KeySendRequest,
    KeySendResponse,
    TextSubmitRequest,
    TextSubmitResponse,
)
from terminal.models.metadata import WindowTagRequest, WindowTagResponse
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
from terminal.models.values import RGB, WindowInfo
from terminal.models.viewport import ScreenReadRequest, ScreenReadResponse

# The split line orientation, in kitty's launch vocabulary.
SPLIT_LOCATIONS = {"vertical": "vsplit", "horizontal": "hsplit"}


def _hex(color: RGB) -> str:
    return f"#{color.red:02x}{color.green:02x}{color.blue:02x}"


def _color_value(value: str) -> int | None:
    """One set-tab-color VALUE, kitten-CLI grammar → RC-payload wire form:
    "NONE" → None (JSON null), "#rrggbb" → the 24-bit RGB integer — exactly
    what the kitten client itself puts in the @kitty-cmd payload (captured
    live: `active_bg=#ff00aa` travels as `"active_bg": 16711850`,
    `inactive_fg=NONE` as `"inactive_fg": null`)."""
    return None if value == TAB_COLOR_NONE else int(value.lstrip("#"), 16)


class KittyTabs(TerminalTabs):
    def __init__(self, remote: KittyRemote) -> None:
        self.remote = remote

    def open_tab(self, request: TabOpenRequest) -> TabOpenResponse:
        # Deliberately NOT `--keep-focus`: when kitty is a background app (the
        # web dashboard's launch — the user is in a browser), kitty's
        # keep-focus path "restores" focus to the previous window via
        # focus_os_window(raise=True), which ACTIVATES the kitty app over the
        # browser (verified against a plain-config kitty 0.45: a plain launch
        # leaves the browser frontmost, --keep-focus yanks kitty to the front).
        arguments = ["launch", "--type=tab", "--cwd", request.working_directory]
        for name, value in request.environment:
            arguments += ["--env", f"{name}={value}"]
        # The request's title is NOT pinned here. A launched tab's title
        # follows its window's own OSC title, which the harness running in it
        # publishes; an explicit title at launch would freeze that out.
        # `rename_tab` is the gesture for deliberately taking it over.
        #
        # The one launch whose stdout is READ, not silenced: kitty prints the
        # new window's id, and the dashboard matches the session that boots in
        # that window by it — exact where a working-directory heuristic is
        # ambiguous. Window ids start at 1, so a rc-0 launch that printed
        # nothing is still a success with no id.
        printed = self.remote.capture(*arguments, *request.command)
        if printed is None:
            return TabOpenResponse(False, None, "terminal launch failed")
        return TabOpenResponse(True, printed.strip() or None)

    def close_tab(self, request: TabCloseRequest) -> TabCloseResponse:
        failed = self.remote.run("close-tab", "--match", match.tab_of(request.window_id))
        return TabCloseResponse(not failed, "terminal close failed" if failed else None)

    def rename_tab(self, request: TabRenameRequest) -> TabRenameResponse:
        # No raw-socket fast path, deliberately unlike set_tab_color: this is a
        # rare user action off the dashboard, not the blocking hook path.
        failed = self.remote.run("set-tab-title", "--match",
                                 match.tab_of(request.window_id), request.title)
        return TabRenameResponse(not failed, "terminal title failed" if failed else None)

    def set_tab_color(self, request: TabColorSetRequest) -> TabColorSetResponse:
        appearance = request.appearance
        failed = self._paint(request.window_id,
                             _hex(appearance.active_background),
                             _hex(appearance.active_foreground),
                             _hex(appearance.inactive_background),
                             _hex(appearance.inactive_foreground))
        return TabColorSetResponse(not failed, "terminal tab paint failed" if failed else None)

    def clear_tab_color(self, request: TabColorClearRequest) -> TabColorClearResponse:
        failed = self._paint(request.window_id, *(TAB_COLOR_NONE,) * 4)
        return TabColorClearResponse(not failed, "terminal tab clear failed" if failed else None)

    def _paint(self, window_id: str, active_bg: str, active_fg: str,
               inactive_bg: str, inactive_fg: str) -> int:
        """Colour the tab containing `window_id`; 0 when kitty acknowledged it.

        The colour goes on BOTH the active and inactive tab so a background
        session stays visible; callers pass a darkened inactive background of
        the same hue so the focused tab still stands out.

        Raw socket first (~0.1ms vs the ~20-100ms kitten subprocess — this runs
        on the BLOCKING hook path several times per turn), kitten subprocess as
        the no-socket fallback. The raw exchange REQUESTS the response and maps
        it to the same exit-code contract the subprocess gives (`ok` → 0, else
        1): the tab row is persisted only on 0, and a fire-and-forget "success"
        here would report paints that never landed — the stranded-colour bug
        class. Only a socket MISS (None) falls back; a definitive ok:false from
        kitty is the answer, not a reason to retry slower."""
        colors: dict[str, int | None] | None
        try:
            colors = {"active_bg": _color_value(active_bg),
                      "active_fg": _color_value(active_fg),
                      "inactive_bg": _color_value(inactive_bg),
                      "inactive_fg": _color_value(inactive_fg)}
        except (ValueError, AttributeError):   # unparseable value: let kitten
            colors = None                      # produce its own rc
        if colors is not None:
            response = self.remote.raw("set-tab-color",
                                       {"match": match.tab_of(window_id), "colors": colors},
                                       want_response=True)
            if isinstance(response, dict):
                return 0 if response.get("ok") else 1
        return self.remote.run("set-tab-color", "--match", match.tab_of(window_id),
                               f"active_bg={active_bg}", f"active_fg={active_fg}",
                               f"inactive_bg={inactive_bg}", f"inactive_fg={inactive_fg}")


class KittyPanes(TerminalPanes):
    def __init__(self, remote: KittyRemote, metadata: TerminalMetadata) -> None:
        self.remote = remote
        self.metadata = metadata

    def open_pane(self, request: PaneOpenRequest) -> PaneOpenResponse:
        # A biased split only sizes correctly in the splits layout, and
        # arranging that is this layer's business. `--match window_id:`
        # re-layouts the tab holding the anchor: a daemon-origin call without
        # focus must not re-layout whatever tab the user is looking at.
        self.remote.run("goto-layout", "--match", match.tab_of(request.same_tab_as), "splits")
        arguments = ["launch"]
        # `--next-to` alone CANNOT cross tabs: kitty resolves it only within
        # the ACTIVE tab, so an open anchored to a window in an unfocused tab
        # silently split whatever tab the user was looking at instead (observed
        # live 2026-07-11 — the two-mirrors bug). `--match window_id:N` selects
        # the TAB first; --next-to then picks the right window inside it.
        arguments += ["--match", match.tab_of(request.same_tab_as)]
        arguments += [f"--location={SPLIT_LOCATIONS[request.split]}"]
        arguments += ["--next-to", match.anchor(request.anchor)]
        arguments += ["--bias", str(request.size_percent)]
        if request.keep_focus and self.remote.app_focused():
            # --keep-focus only while kitty IS the frontmost app: that is the
            # case it exists for (don't yank the user's cursor out of the
            # harness window into the new pane) and the only case where it is
            # safe — on a BACKGROUND kitty the flag's focus-restore raises the
            # OS window and macOS activates kitty over the user's current app
            # (the web-launch steal). Background cost: the pane holds inner
            # focus until the user clicks back into the session window —
            # strictly better than stealing app focus.
            arguments += ["--keep-focus"]
        arguments += ["--cwd", request.working_directory or "current"]
        for name, value in request.tags.items():
            arguments += ["--var", f"{name}={value}"]
        if request.title:
            arguments += ["--title", request.title]
        printed = self.remote.capture(*arguments, *request.command)
        if printed is None:
            return PaneOpenResponse(False, None, "terminal pane launch failed")
        return PaneOpenResponse(True, printed.strip() or None)

    def close_pane(self, request: PaneCloseRequest) -> PaneCloseResponse:
        failed = self.remote.run("close-window", "--match", match.window(request.window_id))
        return PaneCloseResponse(not failed, "terminal pane close failed" if failed else None)

    def resize_pane(self, request: PaneResizeRequest) -> PaneResizeResponse:
        failed = self.remote.run("resize-window", "--match", match.window(request.window_id),
                                 "--axis", request.axis,
                                 "--increment", str(request.cells))
        return PaneResizeResponse(not failed, "terminal pane resize failed" if failed else None)

    def focus_window(self, request: WindowFocusRequest) -> WindowFocusResponse:
        # An INNER-tab focus move: `action nth_window <i>` maps to
        # Tab.nth_window(i), and boss.combine dispatches a Tab action to the
        # MATCHED window's tab (window.tabref()), so it never touches the
        # active tab and never calls focus_os_window — a BACKGROUND kitty is
        # not raised. A plain `focus-window` cannot substitute: its rc
        # hardcodes set_active_window(switch_os_window_if_needed=True), which
        # activates the app whenever no kitty OS window is focused (the
        # web-launch focus steal).
        index = self._position_in_tab(request.window_id)
        if index is None:
            return WindowFocusResponse(False, "window is not on screen")
        # `first_window` is kitty's own name for index 0 — the host pane, and
        # the only position this is asked for in practice.
        action = ["first_window"] if index == 0 else ["nth_window", str(index)]
        failed = self.remote.run("action", "--match", match.tab_of(request.window_id), *action)
        return WindowFocusResponse(not failed, "terminal focus failed" if failed else None)

    def _position_in_tab(self, window_id: str) -> int | None:
        """The window's index among its tab's windows, or None when it is gone."""
        windows = self.metadata.windows()
        tab_id = next((window.tab_id for window in windows
                       if window.window_id == str(window_id)), None)
        if tab_id is None:
            return None
        siblings = [window for window in windows if window.tab_id == tab_id]
        return next(index for index, window in enumerate(siblings)
                    if window.window_id == str(window_id))


class KittyMetadata(TerminalMetadata):
    def __init__(self, remote: KittyRemote) -> None:
        self.remote = remote

    def windows(self) -> tuple[WindowInfo, ...]:
        """The `ls` tree, flattened. This is the ONE place kitty's JSON shape
        (`tabs`, `windows`, `user_vars`, `is_active`, `is_focused`) is read."""
        found = []
        for operating_system_window in self.remote.ls() or []:
            for tab in operating_system_window.get("tabs") or []:
                windows = tab.get("windows") or []
                for position, window in enumerate(windows):
                    found.append(WindowInfo(
                        window_id=str(window.get("id")),
                        tab_id=str(tab.get("id")),
                        tags=dict(window.get("user_vars") or {}),
                        columns=int(window.get("columns") or 0),
                        lines=int(window.get("lines") or 0),
                        is_first_in_tab=position == 0,
                        tab_is_active=bool(tab.get("is_active")),
                        # kitty's tab `is_focused` already means "active AND
                        # its OS window holds keyboard focus" — verified
                        # empirically against a web-launched tab with kitty
                        # backgrounded, which reads active but not focused.
                        tab_is_focused=bool(tab.get("is_focused")),
                    ))
        return tuple(found)

    def tag_window(self, request: WindowTagRequest) -> WindowTagResponse:
        assignments = [f"{name}={value}" for name, value in request.tags.items()]
        failed = self.remote.run("set-user-vars", "--match",
                                 match.window(request.window_id), *assignments)
        return WindowTagResponse(not failed, "terminal window tagging failed" if failed else None)

    def current_window_id(self) -> str | None:
        return current_window_id() or None


class KittyInput(TerminalInput):
    def __init__(self, remote: KittyRemote) -> None:
        self.remote = remote

    def submit_text(self, request: TextSubmitRequest) -> TextSubmitResponse:
        delivered = self.remote.send_text(request.window_id, request.text,
                                          bracketed=request.mode == "paste")
        return TextSubmitResponse(delivered, None if delivered else "terminal input failed")

    def send_key(self, request: KeySendRequest) -> KeySendResponse:
        # Real key EVENTS, encoded for the window's current keyboard mode
        # (send-text's raw bytes bypass the kitty keyboard protocol, so a TUI
        # never sees \x1b as Escape). rc 0 only says the call was accepted —
        # kitty reports no per-window delivery errors for send-key.
        failed = self.remote.run("send-key", "--match",
                                 match.window(request.window_id), request.key)
        return KeySendResponse(not failed, "terminal key input failed" if failed else None)


class KittyViewport(TerminalViewport):
    def __init__(self, remote: KittyRemote) -> None:
        self.remote = remote

    def read_screen(self, request: ScreenReadRequest) -> ScreenReadResponse:
        # Raw socket first (~0.4ms; it runs on every click-to-view toggle),
        # kitten subprocess as the fallback.
        payload: dict[str, Any] = {"match": match.window(request.window_id), "extent": "screen"}
        if request.ansi:
            payload["ansi"] = True
        response = self.remote.raw("get-text", payload, want_response=True)
        if isinstance(response, dict) and response.get("ok") and isinstance(response.get("data"), str):
            return ScreenReadResponse(True, response["data"])
        text = self.remote.get_text(request.window_id, ansi=request.ansi)
        if text is None:
            return ScreenReadResponse(False, None, "terminal screen read failed")
        return ScreenReadResponse(True, text)

def kitty_plugin(remote: KittyRemote | None = None) -> TerminalPlugin:
    remote = remote if remote is not None else KittyRemote()
    metadata = KittyMetadata(remote)
    return TerminalPlugin(
        name="kitty",
        tabs=KittyTabs(remote),
        panes=KittyPanes(remote, metadata),
        metadata=metadata,
        input=KittyInput(remote),
        viewport=KittyViewport(remote),
    )
