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
#      socket + client binary), and nothing outside frontends/ may reference
#      them — a caller reaching for a kitty-only attr would break every
#      other frontend silently.
#   3. frontends.get() honours $CLAUDE_FRONTEND ("none" → the stub, "kitty" →
#      KittyFrontend, unknown → the stub, unset → kitty).
import inspect
import os
import re
import sys

from conftest import REPO

sys.path.insert(0, REPO)

import frontends                                             # noqa: E402
from frontends import kitty as fk                            # noqa: E402
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
    "app_id":             ((), ""),
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
    "focus_first_pane":   (("7",), 1),
    # control plane (writes)
    "send_text":          (("7", "hello"), False),
    "paste_text":         (("7", "hello"), False),
    "send_key":           (("7", "escape"), False),
    "launch_tab":         (("/tmp", ["claude"]), False),
    "close_tab":          (("7",), False),
    "set_tab_title":      (("7", "new name"), False),
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
    """Grep-style: no repo module outside frontends/ (and tests)
    imports KittyFrontend/frontends.kitty
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
            if not f.endswith(".py"):
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
    """The module-level window_for_session (kept only for the now-deleted
    claude_kitty compat shim — see frontends/kitty.py) must be the SAME
    scan as Frontend.window_for_session — one implementation, identical
    answers on the same tree."""
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


def _geometry_fe(monkeypatch, tree):
    from frontends import kitty as fk
    monkeypatch.setattr(fk, "kitten_ls", lambda kitten, listen: tree)
    return KittyFrontend(listen="unix:/tmp/x", kitten="/bin/true")


def test_split_geometry_groups_path(monkeypatch):
    """The neighbors walk sums ONE window per horizontal segment, resolving
    group ids through the tab's groups map: the mirror (30 cols) has two
    hsplit-stacked windows on its left, both reporting the full 90-col column
    — the plain sum would say 30+90+90=210; the correct row total is 120.
    The scorebar (exclude_var) shares the mirror's column and is dropped."""
    tree = [{"tabs": [{
        "groups": [{"id": 101, "windows": [1, 2]},   # hsplit stack (left col)
                   {"id": 103, "windows": [3, 4]}],  # mirror + scorebar
        "windows": [
            {"id": 1, "columns": 90, "user_vars": {},
             "neighbors": {"left": [], "right": [103]}},
            {"id": 2, "columns": 90, "user_vars": {},
             "neighbors": {"left": [], "right": [103]}},
            {"id": 3, "columns": 30,
             "user_vars": {"claude_mirror": "sid-1"},
             "neighbors": {"left": [101], "right": []}},
            {"id": 4, "columns": 30,
             "user_vars": {"claude_scorebar": "sid-1"},
             "neighbors": {"left": [101], "right": []}},
        ]}]}]
    fe = _geometry_fe(monkeypatch, tree)
    assert fe.split_geometry(("claude_mirror", "sid-1"),
                             exclude_var="claude_scorebar") == (30, 120)
    # Without the exclusion the fallback-free walk is unchanged (the scorebar
    # is stacked, never a horizontal neighbor) — but the pane must be found.
    assert fe.split_geometry(("claude_mirror", "sid-1")) == (30, 120)
    assert fe.split_geometry(("claude_mirror", "nope")) is None


def test_split_geometry_old_kitty_fallback(monkeypatch):
    """No `neighbors` key (older kitty) → the plain per-window sum, with
    exclude_var-tagged panes dropped from that sum."""
    tree = [{"tabs": [{"windows": [
        {"id": 1, "columns": 90, "user_vars": {}},
        {"id": 3, "columns": 30, "user_vars": {"claude_mirror": "sid-1"}},
        {"id": 4, "columns": 30, "user_vars": {"claude_scorebar": "sid-1"}},
    ]}]}]
    fe = _geometry_fe(monkeypatch, tree)
    assert fe.split_geometry(("claude_mirror", "sid-1"),
                             exclude_var="claude_scorebar") == (30, 120)
    assert fe.split_geometry(("claude_mirror", "sid-1")) == (30, 150)


def test_split_geometry_group_id_is_window_id(monkeypatch):
    """Never-regrouped windows: neighbor ids resolve as plain window ids when
    absent from the groups map."""
    tree = [{"tabs": [{"groups": [], "windows": [
        {"id": 1, "columns": 90, "user_vars": {},
         "neighbors": {"left": [], "right": [3]}},
        {"id": 3, "columns": 30, "user_vars": {"claude_mirror": "sid-1"},
         "neighbors": {"left": [1], "right": []}},
    ]}]}]
    fe = _geometry_fe(monkeypatch, tree)
    assert fe.split_geometry(("claude_mirror", "sid-1")) == (30, 120)


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


def _rc_fe(path):
    """A KittyFrontend aimed at the fake RC socket with NO usable kitten
    binary — any subprocess fallback would rc-1, so a 0 return proves the
    raw path answered."""
    return KittyFrontend(listen="unix:" + path, kitten="/nonexistent-kitten")


def test_set_tab_color_raw_frame(fake_rc_socket):
    """The exact @kitty-cmd envelope the raw tab paint writes — captured live
    from the real kitten client (colors as 24-bit ints, response REQUESTED so
    the caller's persist-only-on-rc-0 audit contract holds)."""
    rc = _rc_fe(fake_rc_socket.path).set_tab_color(
        "7", "#ff0000", "#000000", "#7f0000")
    assert rc == 0
    frames = fake_rc_socket.commands("set-tab-color")
    assert len(frames) == 1
    f = frames[0]
    assert f["version"] == fk.KITTY_RC_VERSION
    assert f["no_response"] is False          # fire-and-forget is banned here
    assert f["payload"] == {
        "match": "window_id:7",
        "colors": {"active_bg": 0xff0000, "active_fg": 0x000000,
                   "inactive_bg": 0x7f0000, "inactive_fg": 0xc0c4cc}}


def test_clear_tab_color_raw_frame(fake_rc_socket):
    """clear paints all four channels as JSON null (the NONE wire form)."""
    assert _rc_fe(fake_rc_socket.path).clear_tab_color("7") == 0
    f = fake_rc_socket.commands("set-tab-color")[-1]
    assert f["payload"]["colors"] == {
        "active_bg": None, "active_fg": None,
        "inactive_bg": None, "inactive_fg": None}


def test_set_tab_color_raw_ok_false_is_rc1_no_fallback(fake_rc_socket,
                                                       monkeypatch):
    """A definitive ok:false from kitty is the answer (rc 1) — retrying via
    the slow subprocess would just re-ask the same question."""
    fake_rc_socket.response = {"ok": False, "error": "no matching tabs"}
    calls = []
    monkeypatch.setattr(fk, "kitten_run",
                        lambda *a: calls.append(a) or 0)
    assert _rc_fe(fake_rc_socket.path).set_tab_color(
        "7", "#ff0000", "#000000", "#7f0000") == 1
    assert calls == []                        # no subprocess fallback fired


def test_set_tab_color_socket_miss_falls_back_to_kitten(monkeypatch, tmp_path):
    """No live socket → the kitten subprocess path, with the exact historical
    argv (the module-level set_tab_color)."""
    calls = []
    monkeypatch.setattr(fk, "kitten_run",
                        lambda *a: calls.append(list(a)) or 0)
    fe = KittyFrontend(listen="unix:%s/nope.sock" % tmp_path, kitten="/k")
    assert fe.set_tab_color("7", "#ff0000", "#000000", "#7f0000") == 0
    assert calls == [["/k", "unix:%s/nope.sock" % tmp_path, "set-tab-color",
                      "--match", "window_id:7",
                      "active_bg=#ff0000", "active_fg=#000000",
                      "inactive_bg=#7f0000", "inactive_fg=#c0c4cc"]]


def test_send_text_uses_stdin_with_split_enter(monkeypatch):
    """The control-plane composer: text rides STDIN (verbatim, no escape
    interpretation), never a shell/kitten-escape vector — and the Enter (CR)
    is a SEPARATE second write (one write let Claude Code's paste detection
    read text+CR as a pasted chunk: the CR became a draft newline, no
    submit)."""
    calls = []

    class _R:
        returncode = 0

    def fake_run(argv, input=None, **kw):
        calls.append((argv, input))
        return _R()

    monkeypatch.setattr(fk.subprocess, "run", fake_run)
    monkeypatch.setattr(fk.time, "sleep", lambda s: None)
    fe = KittyFrontend(listen="unix:/tmp/x", kitten="/k")
    assert fe.send_text("7", "hello world") is True
    argv = ["/k", "@", "--to", "unix:/tmp/x", "send-text",
            "--match", "id:7", "--stdin"]
    assert calls == [(argv, b"hello world"), (argv, b"\r")]


def test_send_text_rc_nonzero_is_false(monkeypatch):
    class _R:
        returncode = 1

    monkeypatch.setattr(fk.subprocess, "run", lambda *a, **k: _R())
    assert _rc_fe("/tmp/x").send_text("7", "hi") is False


def test_send_text_enter_write_failure_is_false(monkeypatch):
    """A message write that lands but an Enter write that fails is a FAILED
    send (the text sits in the draft, unsent) — the endpoint must see False
    so it audits `send failed` instead of reporting ok."""
    rcs = iter([0, 1])

    def fake_run(argv, input=None, **kw):
        class _R:
            returncode = next(rcs)
        return _R()

    monkeypatch.setattr(fk.subprocess, "run", fake_run)
    monkeypatch.setattr(fk.time, "sleep", lambda s: None)
    assert _rc_fe("/tmp/x").send_text("7", "hi") is False


def test_launch_tab_argv(monkeypatch):
    """launch_tab → `kitten @ launch --type=tab --cwd <cwd> <argv…>`, argv a
    list (never a shell string). Returns the NEW window's id from kitty's
    stdout (the dashboard's exact launched-session match key), bare True when
    the terminal prints nothing, None on failure. NO --keep-focus: on a
    background kitty (the dashboard launch) kitty's keep-focus path raises
    the previous window's OS window, ACTIVATING kitty over the browser —
    the opposite of what it promises (see kitten_launch_tab)."""
    calls = []

    def run_rc0(argv, **kw):
        calls.append(list(argv))

        class _R:
            returncode = 0
            stdout = b"77\n"
        return _R()

    monkeypatch.setattr(fk.subprocess, "run", run_rc0)
    fe = KittyFrontend(listen="unix:/tmp/x", kitten="/k")
    assert fe.launch_tab("/proj", ["claude", "fix the bug"]) == "77"
    assert calls == [["/k", "@", "--to", "unix:/tmp/x", "launch", "--type=tab",
                      "--cwd", "/proj", "claude", "fix the bug"]]

    def run_quiet(argv, **kw):
        class _R:
            returncode = 0
            stdout = b""
        return _R()

    monkeypatch.setattr(fk.subprocess, "run", run_quiet)
    assert fe.launch_tab("/proj", ["claude"]) is True

    def run_fail(argv, **kw):
        class _R:
            returncode = 1
            stdout = b"err"
        return _R()

    monkeypatch.setattr(fk.subprocess, "run", run_fail)
    assert fe.launch_tab("/proj", ["claude"]) is None


def test_launch_pane_keep_focus_only_when_app_focused(monkeypatch):
    """launch_pane passes --keep-focus ONLY while some kitty OS window is
    focused (kitty is the frontmost app): kitty's keep-focus focus-restore
    raises the OS window whenever the app is in the background, which on
    macOS ACTIVATES kitty over whatever the user is in — the web-launch
    steal came from exactly these pane opens at SessionStart. Background →
    plain launch (the pane holds inner focus; the app stays behind)."""
    calls = []
    monkeypatch.setattr(fk, "kitten_run", lambda *a: calls.append(list(a)) or 0)
    fe = KittyFrontend(listen="unix:/tmp/x", kitten="/k")

    monkeypatch.setattr(fk, "kitten_ls",
                        lambda k, listen: [{"is_focused": True, "tabs": []}])
    fe.launch_pane(["mirror.py"], "vsplit")
    assert "--keep-focus" in calls[-1]

    monkeypatch.setattr(fk, "kitten_ls",
                        lambda k, listen: [{"is_focused": False, "tabs": []}])
    fe.launch_pane(["mirror.py"], "vsplit")
    assert "--keep-focus" not in calls[-1]

    monkeypatch.setattr(fk, "kitten_ls", lambda k, listen: [])  # ls failure
    fe.launch_pane(["mirror.py"], "vsplit")                     # → don't steal
    assert "--keep-focus" not in calls[-1]

    # keep_focus=False callers never get the flag regardless of focus
    monkeypatch.setattr(fk, "kitten_ls",
                        lambda k, listen: [{"is_focused": True, "tabs": []}])
    fe.launch_pane(["mirror.py"], "vsplit", keep_focus=False)
    assert "--keep-focus" not in calls[-1]


def test_focus_first_pane_is_inner_tab_no_raise(monkeypatch):
    """focus_first_pane → `kitten @ action --match window_id:<win> first_window`
    — an INNER-tab focus move (Tab.nth_window(0)) that never calls
    focus_os_window, so a background kitty is NOT activated. It must NOT use
    `focus-window` (whose rc forces switch_os_window_if_needed=True — the
    web-launch app-focus steal). rc 0 → 0."""
    calls = []
    monkeypatch.setattr(fk, "kitten_run", lambda *a: calls.append(list(a)) or 0)
    fe = KittyFrontend(listen="unix:/tmp/x", kitten="/k")
    assert fe.focus_first_pane("42") == 0
    assert calls == [["/k", "unix:/tmp/x", "action",
                      "--match", "window_id:42", "first_window"]]
    assert not any("focus-window" in c for c in calls)


def test_app_id_is_the_kitty_bundle_id():
    """app_id → kitty.app's macOS bundle id (what lsappinfo reports when
    kitty is frontmost — the dashboard focus-bounce guard compares against
    it); the inert stub answers ""."""
    assert KittyFrontend(listen="unix:/tmp/x", kitten="/k").app_id() \
        == "net.kovidgoyal.kitty"
    assert Frontend().app_id() == ""


def test_close_tab_matches_containing_window(monkeypatch):
    """close_tab → `kitten @ close-tab --match window_id:<win>` (the tab
    CONTAINING that window). Truthy on rc 0."""
    calls = []
    monkeypatch.setattr(fk, "kitten_run", lambda *a: calls.append(list(a)) or 0)
    fe = KittyFrontend(listen="unix:/tmp/x", kitten="/k")
    assert fe.close_tab("55") is True
    assert calls == [["/k", "unix:/tmp/x", "close-tab",
                      "--match", "window_id:55"]]
    monkeypatch.setattr(fk, "kitten_run", lambda *a: 1)
    assert fe.close_tab("55") is False


def test_model_tail_scan_bytes():
    sys.path.insert(0, REPO)
    from plugins.claude_code import model as cm
    assert cm.TAIL_SCAN_BYTES == 262144
