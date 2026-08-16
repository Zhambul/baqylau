"""The terminal implementation boundary: substitutability, and what may know a terminal's name.

The old suite pinned substitutability of the WIDER duck-type — every method had
an inert default and the one real terminal added nothing public beyond it. The
shape of that guarantee survives; what it is taken against is now the contract
consumers actually call, so "the interface" and "what is used" are one file.
"""

from __future__ import annotations

import inspect

import pytest

from conftest import REPOSITORY_ROOT  # noqa: F401  (path setup for the imports below)
from fake_terminal import FakeTerminal, window
from terminal.contract import (
    TerminalInput,
    TerminalMetadata,
    TerminalPanes,
    TerminalPlugin,
    TerminalTabs,
    TerminalViewport,
)
from terminal.impl import resolve
from terminal.impl.kitty.plugin import kitty_plugin
from terminal.impl.null import null_plugin
from terminal.models import (
    ACTIVITY_PANE_TAG,
    PaneAnchor,
    PaneOpenRequest,
    RGB,
    ScreenReadRequest,
    TabAppearance,
    TabColorSetRequest,
    TabOpenRequest,
    TextSubmitRequest,
    WindowTagRequest,
)

SUB_PROTOCOLS = {
    "tabs": TerminalTabs,
    "panes": TerminalPanes,
    "metadata": TerminalMetadata,
    "input": TerminalInput,
    "viewport": TerminalViewport,
}


def protocol_methods(protocol) -> set[str]:
    return {name for name in vars(protocol) if not name.startswith("_")}


class FakeRemote:
    """A kitty that records what was said to it and answers plainly."""

    def __init__(self, tree=(), printed=""):
        self.calls = []
        self.raw_calls = []
        self.tree = list(tree)
        self.printed = printed

    def run(self, *arguments):
        self.calls.append(arguments)
        return 0

    def capture(self, *arguments, timeout=None):
        self.calls.append(arguments)
        return self.printed

    def ls(self):
        return self.tree

    def app_focused(self, tree=None):
        return False

    def send_text(self, win, text, bracketed=False):
        self.calls.append(("send-text", win, text, bracketed))
        return True

    def get_text(self, win_id, extent="screen", ansi=False):
        return "screen text"

    def raw(self, cmd, payload, want_response=False, timeout=None):
        # None = a socket miss, which is what makes the callers fall back
        self.raw_calls.append((cmd, payload, want_response))


def flag_value(arguments, flag):
    arguments = list(arguments)
    return arguments[arguments.index(flag) + 1]


# --- substitutability --------------------------------------------------------
@pytest.mark.parametrize("plugin", [null_plugin(), kitty_plugin(FakeRemote())], ids=["none", "kitty"])
def test_every_terminal_implements_the_five_sub_protocols_and_nothing_more(plugin):
    assert isinstance(plugin, TerminalPlugin)
    for field, protocol in SUB_PROTOCOLS.items():
        implementation = getattr(plugin, field)
        declared = protocol_methods(protocol)
        public = {
            name
            for name, member in inspect.getmembers(implementation, callable)
            if not name.startswith("_")
        }
        assert declared <= public, f"{plugin.name}.{field} is missing {declared - public}"
        assert public <= declared, f"{plugin.name}.{field} adds {public - declared}"


def test_the_terminal_that_is_not_there_fails_every_operation_in_shape():
    plugin = null_plugin()
    responses = [
        plugin.tabs.open_tab(TabOpenRequest("/work", ("claude",), "")),
        plugin.metadata.tag_window(WindowTagRequest("1", {})),
        plugin.input.submit_text(TextSubmitRequest("1", "hello", "paste")),
        plugin.viewport.read_screen(ScreenReadRequest("1")),
    ]
    assert [response.succeeded for response in responses] == [False] * 4
    assert all(response.reason for response in responses)
    # a read answers with emptiness, not an exception: services stay unconditional
    assert plugin.metadata.windows() == ()
    assert plugin.metadata.current_window_id() is None


def test_a_terminal_is_detected_and_pinned_by_name(monkeypatch):
    monkeypatch.setenv("BAQYLAU_TERMINAL", "none")
    assert resolve().name == "none"
    monkeypatch.setenv("BAQYLAU_TERMINAL", "nothing-like-it")
    with pytest.raises(ValueError, match="unsupported terminal"):
        resolve()
    # detection, not a default: no client binary anywhere means no terminal, and
    # bootstrap — not every call site — decides what to do about that
    monkeypatch.delenv("BAQYLAU_TERMINAL")
    monkeypatch.setattr("terminal.impl.kitty.remote.shutil.which", lambda name: None)
    monkeypatch.setattr("terminal.impl.kitty.remote.os.access", lambda path, mode: False)
    monkeypatch.delenv("KITTY_KITTEN_BIN", raising=False)
    assert resolve() is None


# --- the anchor stays intent until the last moment ---------------------------
def test_a_pane_anchor_is_rendered_by_the_implementation_not_the_caller():
    remote = FakeRemote(printed="101")
    plugin = kitty_plugin(remote)

    plugin.panes.open_pane(PaneOpenRequest(
        command=("python3", "mirror.py"),
        working_directory="",
        title="mirror",
        split="vertical",
        size_percent=25,
        anchor=PaneAnchor(window_id="7"),
        same_tab_as="7",
        tags={ACTIVITY_PANE_TAG: "session-one"},
    ))
    plugin.panes.open_pane(PaneOpenRequest(
        command=("python3", "scoreboard.py"),
        working_directory="",
        title="scoreboard",
        split="horizontal",
        size_percent=5,
        anchor=PaneAnchor(tag=(ACTIVITY_PANE_TAG, "session-one")),
        same_tab_as="7",
        tags={},
    ))

    launches = [call for call in remote.calls if call[0] == "launch"]
    assert flag_value(launches[0], "--next-to") == "id:7"
    assert flag_value(launches[1], "--next-to") == f"var:{ACTIVITY_PANE_TAG}=session-one"
    assert "--location=vsplit" in launches[0]
    assert "--location=hsplit" in launches[1]
    assert flag_value(launches[0], "--bias") == "25"
    # the tab is selected before the anchor is resolved: an anchor in an
    # unfocused tab must not split whatever tab the user is looking at
    assert flag_value(launches[0], "--match") == "window_id:7"
    assert ("goto-layout", "--match", "window_id:7", "splits") in remote.calls


def test_an_anchor_names_exactly_one_thing():
    with pytest.raises(ValueError):
        PaneAnchor()
    with pytest.raises(ValueError):
        PaneAnchor(window_id="7", tag=("a", "b"))


def test_the_window_tree_is_flattened_into_terminal_agnostic_rows():
    remote = FakeRemote(tree=[{
        "is_focused": True,
        "tabs": [{
            "id": 3,
            "is_active": True,
            "is_focused": True,
            "windows": [
                {"id": 7, "columns": 75, "lines": 40, "user_vars": {"baqylau_session": "session-one"}},
                {"id": 8, "columns": 25, "lines": 35, "user_vars": {ACTIVITY_PANE_TAG: "session-one"}},
            ],
        }],
    }])

    windows = kitty_plugin(remote).metadata.windows()

    assert [found.window_id for found in windows] == ["7", "8"]
    assert [found.is_first_in_tab for found in windows] == [True, False]
    assert windows[0].tab_id == "3" and windows[0].tab_is_focused
    assert windows[1].tags == {ACTIVITY_PANE_TAG: "session-one"}
    assert (windows[0].columns, windows[0].lines) == (75, 40)


def test_a_tab_colour_is_a_validated_colour_until_the_implementation_sees_it():
    remote = FakeRemote()
    appearance = TabAppearance(RGB(198, 120, 221), RGB(26, 6, 32), RGB(74, 43, 82), RGB(192, 196, 204))

    kitty_plugin(remote).tabs.set_tab_color(TabColorSetRequest("7", appearance))

    # the raw fast path is tried first, and only the implementation ever knows
    # what a colour looks like on the wire
    assert remote.raw_calls[0][0] == "set-tab-color"
    assert remote.raw_calls[0][1]["colors"]["active_bg"] == 0xC678DD
    assert remote.raw_calls[0][1]["match"] == "window_id:7"
    with pytest.raises(ValueError):
        RGB(256, 0, 0)


def test_the_fake_terminal_used_across_the_suite_matches_the_contract():
    plugin = FakeTerminal(windows=[window("window-one")]).plugin()
    for field, protocol in SUB_PROTOCOLS.items():
        implementation = getattr(plugin, field)
        for name in protocol_methods(protocol):
            assert callable(getattr(implementation, name))
