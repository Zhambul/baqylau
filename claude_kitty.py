#!/usr/bin/env python3
# claude_kitty.py — shared kitty remote-control plumbing.
#
# Talking to kitty happens over the socket in $KITTY_LISTEN_ON (hooks run with no
# controlling terminal, so never the TTY). The binary lookup, the silenced
# `kitten @ …` call shapes, the parsed `kitten @ ls` tree walk, and the
# set-tab-color invocation were each duplicated across claude-tab-status.py,
# claude-split.py, and claude-scorebar.py — this module is the one copy.
# Everything is best-effort and silent: a failed call returns rc 1 / [] / None,
# never raises (callers audit what matters).
import json
import os
import shutil
import subprocess


def find_kitten():
    """Locate the kitten binary: $KITTY_KITTEN_BIN override, PATH, then the macOS
    app bundle. None when kitty isn't installed (callers no-op)."""
    k = os.environ.get("KITTY_KITTEN_BIN")
    if k:
        return k
    k = shutil.which("kitten")
    if k:
        return k
    bundle = "/Applications/kitty.app/Contents/MacOS/kitten"
    return bundle if os.access(bundle, os.X_OK) else None


def kitten_run(kitten, listen, *args):
    """A silenced `kitten @ …` call; returns the exit code (1 on any failure)."""
    try:
        return subprocess.run([kitten, "@", "--to", listen, *args],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode
    except Exception:
        return 1


def kitten_ls(kitten, listen):
    """Parsed `kitten @ ls` (the OS-window/tab/window tree), or [] on failure."""
    try:
        out = subprocess.run([kitten, "@", "--to", listen, "ls"],
                             capture_output=True, text=True, timeout=5).stdout
        return json.loads(out)
    except Exception:
        return []


def iter_windows(ls):
    """Flatten a kitten_ls() tree into (os_window, tab, window) triples — the
    hand-rolled triple for-loop this replaces existed at six call sites."""
    for osw in ls or []:
        for t in osw.get("tabs", []):
            for w in t.get("windows", []):
                yield osw, t, w


def window_for_session(kitten, listen, sid):
    """Kitty window id (str) of the Claude pane carrying claude_session=<sid>
    (tagged by claude-split.py at SessionStart), or None."""
    for _osw, _t, w in iter_windows(kitten_ls(kitten, listen)):
        if (w.get("user_vars") or {}).get("claude_session") == sid:
            return str(w.get("id"))
    return None


def set_tab_color(kitten, listen, win, active_bg, active_fg, inactive_bg,
                  inactive_fg="#c0c4cc"):
    """Set (or with all-"NONE", clear) the colour of the tab containing `win`.
    Colour goes on BOTH active and inactive tabs so background sessions stay
    visible; callers pass a darkened inactive_bg of the same hue so the focused
    tab still stands out. Returns the `kitten @` exit code — the output is
    discarded (a hook must stay silent) but the caller's audit gets the truth:
    a row claiming a colour was applied while the socket call failed is exactly
    the trusted-but-wrong evidence that hides a stuck-colour bug."""
    return kitten_run(kitten, listen, "set-tab-color",
                      "--match", f"window_id:{win}",
                      f"active_bg={active_bg}", f"active_fg={active_fg}",
                      f"inactive_bg={inactive_bg}", f"inactive_fg={inactive_fg}")
