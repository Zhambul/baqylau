# frontends/kitty.py — the kitty terminal adapter.
#
# Talking to kitty happens over the socket in $KITTY_LISTEN_ON via the `kitten`
# client (hooks run with no controlling terminal, so never the TTY). The
# module-level helpers are the historical claude_kitty.py API (kept: the
# deleted claude_kitty compat shim re-exported this module); KittyFrontend wraps them in
# the Frontend interface (frontends/base.py) that tab-status / split / scorebar
# now speak. Everything is best-effort and silent: a failed call returns
# rc 1 / [] / None, never raises (callers audit what matters).
import glob
import json
import os
import stat
import subprocess
import shutil
import time

from frontends.base import Frontend, INACTIVE_FG, TAB_COLOR_NONE

# Timeout for mutating `kitten @` calls (kitten_run): kitten has its own
# client-side response timeout, but a hang on socket CONNECT is unbounded, and
# every split op (and every tab paint whose raw-socket attempt missed) runs
# through here from hook processes — which must never block.
KITTEN_TIMEOUT_S = 10
# Tighter timeout for read-only queries (get-text / ls): they run on hot paths
# (renderer reflow, geometry probes) where a stale answer is useless anyway.
KITTEN_QUERY_TIMEOUT_S = 5
# Timeout for a raw unix-socket remote-control exchange (_rc_raw): the whole
# point of the raw path is sub-millisecond latency, so give up fast and let
# the caller fall back to the kitten subprocess.
RC_SOCKET_TIMEOUT_S = 0.5
# Gap between send_text's message write and its Enter (CR) write. Delivered in
# the SAME write, Claude Code's chunk-based paste detection sometimes read
# text+CR as one pasted chunk, turning the CR into a draft newline instead of a
# submit (timing-dependent → intermittent). The gap makes the CR arrive as its
# own stdin read = an unambiguous Enter keypress.
SEND_ENTER_GAP_S = 0.15
# The remote-control protocol version stamped into every @kitty-cmd envelope
# (what a current kitten client sends; kitty accepts any version <= its own).
KITTY_RC_VERSION = [0, 26, 0]
# The @kitty-cmd wire framing: ESC P (DCS) + key + {json} + ESC \ (ST). The
# reply, when requested, is framed the same way — locate its payload by the
# key, not the DCS introducer (the reply may arrive mid-buffer).
RC_CMD_KEY = b"@kitty-cmd"
RC_CMD_DCS = b"\x1bP" + RC_CMD_KEY
RC_ST = b"\x1b\\"
# kitty.app's macOS bundle identifier — what LaunchServices (`lsappinfo`)
# reports when kitty is the frontmost app; the Frontend.app_id() answer.
KITTY_BUNDLE_ID = "net.kovidgoyal.kitty"


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
    Bounded by KITTEN_TIMEOUT_S (see its comment): hook processes must never
    block on a hung socket connect."""
    try:
        return subprocess.run([kitten, "@", "--to", listen, *args],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL,
                              timeout=KITTEN_TIMEOUT_S).returncode
    except Exception:
        return 1


def kitten_get_text(kitten, listen, win_id, extent="screen"):
    """`kitten @ get-text` for a window, or None on failure. extent="screen" is
    the VISIBLE viewport — verified live: a window scrolled up returns the
    scrolled-to rows, not the live screen's bottom — which is what lets the
    mirror renderer restore the exact scroll position across a reflow."""
    try:
        r = subprocess.run([kitten, "@", "--to", listen, "get-text",
                            "--match", f"id:{win_id}", "--extent", extent],
                           capture_output=True, timeout=KITTEN_QUERY_TIMEOUT_S)
        return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None
    except Exception:
        return None


def kitten_send_text(kitten, listen, win, text):
    """`kitten @ send-text --stdin` to window `win`: the text goes over STDIN
    precisely so it is never a shell argument NOR a kitten escape vector —
    `--stdin` sends the bytes verbatim, no `\\n`/`\\x1b` interpretation. The
    Enter (CR) is a SEPARATE second call after SEND_ENTER_GAP_S (see its
    comment: one write let paste detection swallow the CR into the draft).
    True only when both writes rc 0. Bounded by KITTEN_TIMEOUT_S like every
    other mutating call."""
    try:
        argv = [kitten, "@", "--to", listen, "send-text",
                "--match", f"id:{win}", "--stdin"]
        r = subprocess.run(argv, input=text.encode("utf-8"),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=KITTEN_TIMEOUT_S)
        if r.returncode != 0:
            return False
        time.sleep(SEND_ENTER_GAP_S)
        r = subprocess.run(argv, input=b"\r",
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=KITTEN_TIMEOUT_S)
        return r.returncode == 0
    except Exception:
        return False


def kitten_send_key(kitten, listen, win, *keys):
    """`kitten @ send-key --match id:<win> <keys…>` — real key EVENTS encoded
    for the window's current keyboard mode (send-text's raw bytes bypass the
    kitty keyboard protocol, so a TUI never sees \\x1b as Escape). Key names
    are kitty's ("escape", "ctrl+c"). rc 0 only says the call was accepted —
    kitty reports no per-window delivery errors for send-key."""
    return kitten_run(kitten, listen, "send-key",
                      "--match", f"id:{win}", *keys) == 0


def kitten_launch_tab(kitten, listen, cwd, argv):
    """`kitten @ launch --type=tab --cwd <cwd> <argv…>` — a new tab running
    argv (a list, never a shell string, so no interpolation). True on rc 0.
    Deliberately NOT `--keep-focus`: when kitty is a background app (the web
    dashboard's launch — the user is in a browser), kitty's keep-focus path
    "restores" focus to the previous window via focus_os_window(raise=True),
    which ACTIVATES the kitty app over the browser (verified against a
    plain-config kitty 0.45: plain launch leaves the browser frontmost,
    --keep-focus yanks kitty to the front). The dashboard compensates for
    the arrangement-dependent cases where a plain launch still activates
    kitty with its own macOS focus-bounce guard (dashboard/server.py)."""
    return kitten_run(kitten, listen, "launch", "--type=tab",
                      "--cwd", cwd, *argv) == 0


def kitten_close_tab(kitten, listen, win):
    """`kitten @ close-tab --match window_id:<win>` — the tab CONTAINING that
    window (kitty tab matching supports window_id). True on rc 0."""
    return kitten_run(kitten, listen, "close-tab",
                      "--match", f"window_id:{win}") == 0


def kitten_ls(kitten, listen):
    """Parsed `kitten @ ls` (the OS-window/tab/window tree), or [] on failure."""
    try:
        out = subprocess.run([kitten, "@", "--to", listen, "ls"],
                             capture_output=True, text=True,
                             timeout=KITTEN_QUERY_TIMEOUT_S).stdout
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
    (tagged by claude-split.py at SessionStart), or None. Kept only for the
    (deleted) claude_kitty compat shim — in-repo callers use Frontend.window_for_session;
    this delegates to that one scan implementation."""
    return KittyFrontend(listen=listen, kitten=kitten).window_for_session(sid)


def set_tab_color(kitten, listen, win, active_bg, active_fg, inactive_bg,
                  inactive_fg=INACTIVE_FG):
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


def _tab_color_int(v):
    """One set-tab-color VALUE, kitten-CLI grammar → RC-payload wire form:
    "NONE" → None (JSON null), "#rrggbb" → the 24-bit RGB integer — exactly
    what the kitten client itself puts in the @kitty-cmd payload (captured
    live: `active_bg=#ff00aa` travels as `"active_bg": 16711850`,
    `inactive_fg=NONE` as `"inactive_fg": null`)."""
    return None if v == TAB_COLOR_NONE else int(v.lstrip("#"), 16)


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

    def app_id(self):
        return KITTY_BUNDLE_ID

    def _run(self, *args):
        return kitten_run(self.kitten, self.listen, *args)

    # --- tab colour -----------------------------------------------------------
    def set_tab_color(self, win, active_bg, active_fg, inactive_bg,
                      inactive_fg=INACTIVE_FG):
        # Raw socket first (~0.1ms vs the ~20-100ms kitten subprocess — this
        # runs on the BLOCKING hook path several times per turn), kitten
        # subprocess as the no-socket fallback. The raw exchange REQUESTS the
        # response and maps it to the same exit-code contract the subprocess
        # gives (`ok` → 0, else 1): the tab DB row is persisted only on rc==0,
        # and a fire-and-forget "success" here would report paints that never
        # landed — the stranded-colour bug class set_tab_color's docstring
        # exists to prevent. Only a socket miss (None) falls back; a definitive
        # ok:false from kitty is the answer, not a reason to retry slower.
        try:
            colors = {"active_bg": _tab_color_int(active_bg),
                      "active_fg": _tab_color_int(active_fg),
                      "inactive_bg": _tab_color_int(inactive_bg),
                      "inactive_fg": _tab_color_int(inactive_fg)}
        except (ValueError, AttributeError):   # unparseable value: let kitten
            colors = None                      # produce its own rc
        if colors is not None:
            r = self._rc_raw("set-tab-color",
                             {"match": "window_id:%s" % win, "colors": colors},
                             want_response=True)
            if isinstance(r, dict):
                return 0 if r.get("ok") else 1
        return set_tab_color(self.kitten, self.listen, win, active_bg,
                             active_fg, inactive_bg, inactive_fg)

    def clear_tab_color(self, win):
        return self.set_tab_color(win, TAB_COLOR_NONE, TAB_COLOR_NONE,
                                  TAB_COLOR_NONE, inactive_fg=TAB_COLOR_NONE)

    # --- control plane (writes) -------------------------------------------------
    def send_text(self, win, text):
        return kitten_send_text(self.kitten, self.listen, win, text)

    def send_key(self, win, *keys):
        return kitten_send_key(self.kitten, self.listen, win, *keys)

    def launch_tab(self, cwd, argv):
        return kitten_launch_tab(self.kitten, self.listen, cwd, argv)

    def close_tab(self, win):
        return kitten_close_tab(self.kitten, self.listen, win)

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
                    next_to=None, in_tab_of=None, cwd="current",
                    keep_focus=True):
        args = ["launch"]
        if in_tab_of is not None:
            # `--next-to` alone CANNOT cross tabs: kitty resolves it only
            # within the ACTIVE tab, so an open anchored to a window in an
            # unfocused tab silently split whatever tab the user was looking
            # at instead (observed live 2026-07-11 — the two-mirrors bug).
            # `--match window_id:N` selects the TAB containing the anchor
            # first; --next-to then picks the right window inside it.
            args += ["--match", f"window_id:{in_tab_of}"]
        args += [f"--location={location}"]
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

    def scroll_window(self, win_id, lines_up):
        # `N-` = scroll up N lines (kitten @ scroll-window's amount grammar).
        return self._run("scroll-window", "--match", f"id:{win_id}",
                         f"{int(lines_up)}-")

    def _rc_raw(self, cmd, payload, want_response=False,
                timeout=RC_SOCKET_TIMEOUT_S):
        """A remote-control command over a RAW unix-socket write of the
        @kitty-cmd DCS — sub-millisecond vs the ~30-100ms kitten subprocess
        spawn. The wire bytes are exactly what the kitten client sends
        (captured live): ESC P @kitty-cmd {json} ESC \\, with the reply (when
        requested) framed the same way. Speed is load-bearing for the mirror
        renderer AND the hook path: get-text runs on every click-to-view
        toggle, the tab paint (set_tab_color) runs on the BLOCKING hook path
        several times per turn, and the scroll
        runs INSIDE its DEC 2026 freeze bracket, where a subprocess outlives
        kitty's render-freeze window and exposes the intermediate frame (the
        toggle flicker). Returns the parsed response dict, True (fire-and-
        forget success), or None on any failure — callers fall back to the
        subprocess path."""
        listen = self.listen or ""
        path = listen[5:] if listen.startswith("unix:") else listen
        if not path:
            return None
        import json as _json
        import socket as _socket
        obj = {"cmd": cmd, "version": KITTY_RC_VERSION,
               "no_response": not want_response, "payload": payload}
        try:
            s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            try:
                s.settimeout(timeout)
                s.connect(path)
                s.sendall(RC_CMD_DCS + _json.dumps(obj).encode("utf-8") + RC_ST)
                if not want_response:
                    return True
                buf = b""
                while RC_ST not in buf:
                    b = s.recv(65536)
                    if not b:
                        return None
                    buf += b
            finally:
                s.close()
            return _json.loads(buf[buf.index(RC_CMD_KEY) + len(RC_CMD_KEY):
                                   buf.index(RC_ST)])
        except Exception:
            return None

    def scroll_window_fast(self, win_id, lines_up):
        """scroll_window over the raw socket (amount [-N, "l"] = up N lines,
        fire-and-forget). False → caller falls back to scroll_window."""
        return self._rc_raw("scroll-window",
                            {"amount": [-float(lines_up), "l"],
                             "match": "id:%s" % win_id}) is True

    def scroll_window_end(self, win_id):
        """Scroll the window's viewport to the absolute BOTTOM (raw socket,
        subprocess fallback). The mirror's toggle restore issues this before
        its relative up-scroll: a repaint's clear-scrollback under a SCROLLED
        viewport leaves kitty's scroll state clamped somewhere undefined, so
        relative math needs this deterministic base first."""
        if self._rc_raw("scroll-window", {"amount": ["end", None],
                                          "match": "id:%s" % win_id}) is True:
            return True
        return self._run("scroll-window", "--match", f"id:{win_id}",
                         "end") == 0

    def get_text(self, win_id, extent="screen"):
        # Raw socket first (~0.4ms; it runs on every click-to-view toggle),
        # kitten subprocess as the fallback.
        r = self._rc_raw("get-text",
                         {"match": "id:%s" % win_id, "extent": extent},
                         want_response=True)
        if isinstance(r, dict) and r.get("ok") and isinstance(r.get("data"), str):
            return r["data"]
        return kitten_get_text(self.kitten, self.listen, win_id, extent)

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
        for osw in self.ls():
            for t in osw.get("tabs", []):
                wins, pane = self._geometry_windows(t, var, exclude_var)
                if not pane or not pane.get("columns"):
                    continue
                cur = pane.get("columns", 0)
                if "neighbors" not in pane:               # older kitty: old behavior
                    return cur, sum(w.get("columns", 0) for w in wins.values())
                resolve = self._resolve_groups(t, wins)
                return cur, self._neighbor_span(pane, resolve)
        return None

    @staticmethod
    def _geometry_windows(tab, var, exclude_var):
        """One tab's window map (exclude_var-tagged panes dropped — the
        scorebar shares the mirror's column, so counting it would double-count
        that column) plus the `var`-tagged pane within it, or None."""
        name, value = var
        wins = {w.get("id"): w for w in tab.get("windows", [])
                if not (exclude_var
                        and w.get("user_vars", {}).get(exclude_var))}
        pane = next((w for w in wins.values()
                     if w.get("user_vars", {}).get(name) == value), None)
        return wins, pane

    @staticmethod
    def _resolve_groups(tab, wins):
        """A resolver from a `neighbors` entry to a window dict. `neighbors`
        holds GROUP ids (confirmed live), which coincide with window ids only
        for never-regrouped windows — resolve through the tab's groups map
        first, then as a plain window id."""
        groups = {g.get("id"): (g.get("windows") or [])
                  for g in tab.get("groups", [])}

        def resolve(i):
            for wid in groups.get(i, [i]):
                if wid in wins:
                    return wins[wid]
            return None
        return resolve

    @staticmethod
    def _neighbor_span(pane, resolve):
        """The pane's row total: walk the `neighbors` chain left and right,
        summing ONE window per horizontal segment — NOT every window's
        columns: two windows hsplit-stacked in the same column each report the
        full column width, so the plain sum double-counted it, under-reported
        the pane's %, and drove reset/setpct (and the remembered size) far
        off."""
        total, seen = pane.get("columns", 0), {pane.get("id")}
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
        return total
