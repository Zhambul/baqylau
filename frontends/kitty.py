# frontends/kitty.py — the kitty terminal adapter.
#
# Talking to kitty happens over the socket in $KITTY_LISTEN_ON via the `kitten`
# client (hooks run with no controlling terminal, so never the TTY). The
# module-level helpers are the historical claude_kitty.py API (kept: the
# claude_kitty compat shim re-exports this module); KittyFrontend wraps them in
# the Frontend interface (frontends/base.py) that tab-status / split / scorebar
# now speak. Everything is best-effort and silent: a failed call returns
# rc 1 / [] / None, never raises (callers audit what matters).
import glob
import json
import os
import stat
import subprocess
import shutil

from frontends.base import Frontend


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
    """A silenced `kitten @ …` call; returns the exit code (1 on any failure).
    The timeout matches kitten_ls's: kitten has its own client-side response
    timeout, but a hang on socket CONNECT is unbounded, and every tab paint and
    split op runs through here from hook processes — which must never block."""
    try:
        return subprocess.run([kitten, "@", "--to", listen, *args],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=10).returncode
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


def _is_socket(p):
    try:
        return stat.S_ISSOCK(os.stat(p).st_mode)
    except OSError:
        return False


def resolve_listen_on():
    """The controlling kitty instance's socket when $KITTY_LISTEN_ON is absent
    (a keymap-driven `launch --type=background` child does NOT inherit it):
    listen_on `unix:/tmp/kitty` yields `/tmp/kitty-<kitty-pid>`, and that kitty
    pid is an ancestor of this process. Falls back to the lone socket if just
    one instance. (Moved here from claude-split.py — it is pure kitty
    knowledge.)"""
    if os.environ.get("KITTY_LISTEN_ON"):
        return os.environ["KITTY_LISTEN_ON"]
    pid = os.getppid()
    while pid and pid > 1:
        if _is_socket(f"/tmp/kitty-{pid}"):
            return f"unix:/tmp/kitty-{pid}"
        try:
            out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)],
                                 capture_output=True, text=True).stdout.strip()
            pid = int(out)
        except (ValueError, OSError):
            break
    socks = [s for s in glob.glob("/tmp/kitty-*") if _is_socket(s)]
    if len(socks) == 1:
        return "unix:" + socks[0]
    return ""


class KittyFrontend(Frontend):
    name = "kitty"

    def __init__(self, listen=None, kitten=None, resolve=False):
        self.listen = listen if listen is not None else (
            resolve_listen_on() if resolve
            else os.environ.get("KITTY_LISTEN_ON", ""))
        self.kitten = kitten if kitten is not None else find_kitten()

    # --- presence -------------------------------------------------------------
    def available(self):
        return bool(self.listen)

    def usable(self):
        return bool(self.listen and self.kitten)

    def current_window(self):
        return os.environ.get("KITTY_WINDOW_ID", "")

    def export_env(self):
        os.environ["KITTY_LISTEN_ON"] = self.listen or ""

    def _run(self, *args):
        return kitten_run(self.kitten, self.listen, *args)

    # --- tab colour -----------------------------------------------------------
    def set_tab_color(self, win, active_bg, active_fg, inactive_bg,
                      inactive_fg="#c0c4cc"):
        return set_tab_color(self.kitten, self.listen, win, active_bg,
                             active_fg, inactive_bg, inactive_fg)

    def clear_tab_color(self, win):
        return set_tab_color(self.kitten, self.listen, win,
                             "NONE", "NONE", "NONE", inactive_fg="NONE")

    # --- window enumeration -----------------------------------------------------
    def ls(self):
        return kitten_ls(self.kitten, self.listen)

    def iter_windows(self, tree=None):
        return iter_windows(self.ls() if tree is None else tree)

    # --- pane management --------------------------------------------------------
    def goto_splits_layout(self, win=None):
        # vsplit sizing only works in the splits layout. `--match window_id:`
        # targets the tab holding `win` — a hook without focus (daemon-origin
        # SessionStart) must not re-layout whatever tab the user is looking at.
        if win:
            return self._run("goto-layout", "--match", f"window_id:{win}",
                             "splits")
        return self._run("goto-layout", "splits")

    def launch_pane(self, argv, location, bias=None, var=None, title=None,
                    next_to=None, cwd="current", keep_focus=True):
        args = ["launch", f"--location={location}"]
        if next_to is not None:
            args += ["--next-to", next_to]
        if bias is not None:
            args += ["--bias", str(bias)]
        if keep_focus:
            args += ["--keep-focus"]
        args += ["--cwd", cwd]
        for k, v in (var or {}).items():
            args += ["--var", f"{k}={v}"]
        if title is not None:
            args += ["--title", title]
        return self._run(*args, *argv)

    def close_pane(self, var=None, win_id=None):
        m = f"var:{var[0]}={var[1]}" if var else f"id:{win_id}"
        return self._run("close-window", "--match", m)

    def set_user_vars(self, win_id, uv):
        args = [f"{k}={v}" for k, v in uv.items()]
        return self._run("set-user-vars", "--match", f"id:{win_id}", *args)

    def resize_pane(self, var, axis, increment):
        return self._run("resize-window", "--match", f"var:{var[0]}={var[1]}",
                         "--axis", axis, "--increment", str(increment))

    def split_geometry(self, var, exclude_var=None):
        """(pane_columns, row_total_columns) for the pane tagged `var`
        (a (name, value) pair), excluding `exclude_var`-tagged panes from the
        width math — the scorebar shares the mirror's column, so counting it
        would double-count that column. None when the pane can't be found.

        The row total is computed by walking the pane's `neighbors` chain left
        and right, summing ONE window per horizontal segment — NOT by summing
        every window's columns: two windows hsplit-stacked in the same column
        each report the full column width, so the plain sum double-counted it,
        under-reported the pane's %, and drove reset/setpct (and the remembered
        size) far off. Falls back to the plain sum on a kitty too old to report
        `neighbors`. `neighbors` holds GROUP ids (confirmed live), which
        coincide with window ids only for never-regrouped windows — resolve
        through the tab's groups map first, then as a plain window id."""
        name, value = var
        for osw in self.ls():
            for t in osw.get("tabs", []):
                wins = {w.get("id"): w for w in t.get("windows", [])
                        if not (exclude_var
                                and w.get("user_vars", {}).get(exclude_var))}
                pane = next((w for w in wins.values()
                             if w.get("user_vars", {}).get(name) == value), None)
                if not pane or not pane.get("columns"):
                    continue
                cur = pane.get("columns", 0)
                if "neighbors" not in pane:               # older kitty: old behavior
                    return cur, sum(w.get("columns", 0) for w in wins.values())
                groups = {g.get("id"): (g.get("windows") or [])
                          for g in t.get("groups", [])}

                def resolve(i):
                    for wid in groups.get(i, [i]):
                        if wid in wins:
                            return wins[wid]
                    return None

                total, seen = cur, {pane.get("id")}
                for side in ("left", "right"):
                    w = pane
                    while True:
                        cands = ((w.get("neighbors") or {}).get(side)) or []
                        nxt = next((ww for ww in map(resolve, cands)
                                    if ww is not None and ww.get("id") not in seen),
                                   None)
                        if nxt is None:
                            break
                        seen.add(nxt.get("id"))
                        total += nxt.get("columns", 0)
                        w = nxt
                return cur, total
        return None
