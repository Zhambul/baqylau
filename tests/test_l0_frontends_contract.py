# L0 — the Frontend interface contract (pure in-process unit tests).
#
# Everything above frontends/ is written against frontends/base.py, and the
# roadmap adds more terminals (iTerm2, ghostty) behind the same interface —
# so substitutability must be PINNED, not assumed:
#
#   1. The inert "none" stub (the base class itself) really is inert: every
#      public method is callable with representative args and returns its
#      documented failure-shaped default (rc 1 / [] / None / "" / False),
#      never raises. New interface methods must be added to the call table
#      here or the coverage assertion fails — an untested stub method is
#      exactly the untestable hole this module exists to close.
#   2. KittyFrontend adds NO public API beyond the interface. Its only public
#      extras are the documented constructor attrs `listen`/`kitten` (the
#      socket + client binary), and nothing outside frontends/ (plus the
#      claude_kitty compat shim) may reference them — a caller reaching for a
#      kitty-only attr would break every other frontend silently.
#   3. frontends.get() honours $CLAUDE_FRONTEND ("none" → the stub, "kitty" →
#      KittyFrontend, unknown → the stub, unset → kitty).
import inspect
import os
import re
import sys

from conftest import REPO

sys.path.insert(0, REPO)

import frontends                                             # noqa: E402
from frontends.base import Frontend                          # noqa: E402
from frontends.kitty import KittyFrontend                    # noqa: E402


def _public_methods(cls):
    return {n for n, v in inspect.getmembers(cls, callable)
            if not n.startswith("_")}


# Representative args for every public interface method → the inert default
# the callers are written to handle. `...` in expected means "just must not
# raise" (iterators compare by identity).
CALLS = {
    # presence
    "available":          ((), False),
    "usable":             ((), False),
    "current_window":     ((), ""),
    "export_env":         ((), None),
    # tab colour
    "set_tab_color":      (("7", "#ff0000", "#000000", "#7f0000"), 1),
    "clear_tab_color":    (("7",), 1),
    # window enumeration
    "ls":                 ((), []),
    "iter_windows":       ((), ...),
    "find_window":        (("claude_session", "sid-1"), None),
    "window_for_session": (("sid-1",), None),
    # pane management
    "goto_splits_layout": (("7",), 1),
    "launch_pane":        ((["echo", "hi"], "vsplit"), 1),
    "close_pane":         ((), 1),
    "set_user_vars":      (("7", {"claude_session": "sid-1"}), 1),
    "resize_pane":        ((("claude_mirror", "sid-1"), "horizontal", 4), 1),
    # viewport scroll / read
    "scroll_window":      (("7", 12), 1),
    "scroll_window_fast": (("7", 12), False),
    "scroll_window_end":  (("7",), False),
    "get_text":           (("7",), None),
    # geometry
    "split_geometry":     ((("claude_mirror", "sid-1"),), None),
}


def test_stub_covers_every_public_method():
    """The CALLS table IS the interface: adding a Frontend method without a
    contract entry (or vice versa) fails here."""
    assert _public_methods(Frontend) == set(CALLS)


def test_stub_is_inert():
    fe = Frontend()
    assert fe.name == "none"
    for name, (args, expected) in sorted(CALLS.items()):
        got = getattr(fe, name)(*args)                       # must not raise
        if expected is ...:
            assert list(got) == []                           # empty iterator
        else:
            assert got == expected, "%s() -> %r, want %r" % (name, got, expected)


def test_kitty_adds_no_public_methods():
    """Every public method KittyFrontend defines exists on Frontend — no
    kitty-only method can leak into callers. (Instance attrs `listen` and
    `kitten` are the documented constructor extras, checked separately.)"""
    extra = {n for n in KittyFrontend.__dict__
             if not n.startswith("_") and n != "name"
             and callable(KittyFrontend.__dict__[n])} - _public_methods(Frontend)
    assert extra == set(), "kitty-only public methods leaked: %s" % extra


def test_no_caller_outside_frontends_uses_kitty_internals():
    """Grep-style: no repo module outside frontends/ (and the documented
    claude_kitty compat shim, and tests) imports KittyFrontend/frontends.kitty
    or touches a frontend's `.listen`/`.kitten` attrs — the tabstatus
    FE.listen leak was fixed once; keep it fixed."""
    pat = re.compile(r"KittyFrontend|frontends\.kitty"
                     r"|\bfe\.(listen|kitten)\b|\bFE\.(listen|kitten)\b")
    offenders = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs
                   if d not in (".git", "__pycache__", "frontends", "tests",
                                ".claude")]
        for f in files:
            if not f.endswith(".py") or f == "claude_kitty.py":
                continue
            path = os.path.join(root, f)
            with open(path, encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    code = line.split("#", 1)[0]             # comments may cite
                    if pat.search(code):
                        offenders.append("%s:%d: %s"
                                         % (os.path.relpath(path, REPO), i,
                                            line.strip()))
    assert offenders == [], "\n".join(offenders)


def test_get_honours_claude_frontend(monkeypatch):
    monkeypatch.setenv("CLAUDE_FRONTEND", "none")
    fe = frontends.get()
    assert type(fe) is Frontend and fe.name == "none"

    monkeypatch.setenv("CLAUDE_FRONTEND", "some-future-terminal")
    assert type(frontends.get()) is Frontend                 # unknown → stub

    monkeypatch.setenv("CLAUDE_FRONTEND", "kitty")
    monkeypatch.setenv("KITTY_LISTEN_ON", "unix:/tmp/does-not-exist")
    assert type(frontends.get()) is KittyFrontend

    monkeypatch.delenv("CLAUDE_FRONTEND", raising=False)
    assert type(frontends.get()) is KittyFrontend            # unset → kitty


def test_module_window_for_session_delegates_to_class(monkeypatch):
    """The module-level window_for_session (kept only for the claude_kitty
    compat shim) must be the SAME scan as Frontend.window_for_session — one
    implementation, identical answers on the same tree."""
    from frontends import kitty as fk
    tree = [{"tabs": [{"windows": [
        {"id": 3, "user_vars": {"claude_mirror": "sid-1"}},
        {"id": 7, "user_vars": {"claude_session": "sid-1"}},
        {"id": 9, "user_vars": {}},
    ]}]}]
    monkeypatch.setattr(fk, "kitten_ls", lambda kitten, listen: tree)
    fe = KittyFrontend(listen="unix:/tmp/x", kitten="/bin/true")
    assert fk.window_for_session("/bin/true", "unix:/tmp/x", "sid-1") == "7"
    assert fe.window_for_session("sid-1") == "7"
    assert fk.window_for_session("/bin/true", "unix:/tmp/x", "nope") is None
    assert fe.window_for_session("nope") is None


def test_kitty_wire_constants_unchanged():
    """The named constants must keep the wire values captured live — renaming
    them was behavior-preserving; changing them would not be."""
    from frontends import kitty as fk
    assert fk.KITTEN_TIMEOUT_S == 10
    assert fk.KITTEN_QUERY_TIMEOUT_S == 5
    assert fk.RC_SOCKET_TIMEOUT_S == 0.5
    assert fk.KITTY_RC_VERSION == [0, 26, 0]
    assert fk.RC_CMD_KEY == b"@kitty-cmd"
    assert fk.RC_CMD_DCS == b"\x1bP@kitty-cmd"
    assert fk.RC_ST == b"\x1b\\"
    assert len(fk.RC_CMD_KEY) == 10          # the old bare "+ 10" reply offset


def test_model_tail_scan_bytes():
    sys.path.insert(0, REPO)
    from plugins.claude_code import model as cm
    assert cm.TAIL_SCAN_BYTES == 262144
