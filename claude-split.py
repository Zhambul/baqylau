#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude-split.py open|close|toggle|grow|shrink|reset|setpct
#
# Manage a "command mirror" vertical split — by default the right 25% of the tab —
# that renders the Claude Code session's activity (see claude-mirror.py).
#
# PER SESSION. Everything is keyed by the Claude session_id so PARALLEL sessions
# never collide: each mirror pane carries var:claude_mirror=<sid>, each Claude pane
# carries var:claude_session=<sid> (tagged at SessionStart), and each session's
# content lives in its own /tmp/claude-mirror-<sid>.log. Toggling/resizing one
# session's mirror never touches another's.
#
#   open          SessionStart: set up this session's log (fresh for a new session,
#                 restored from *.keep on resume, left alone on compact/crash-resume),
#                 tag the Claude pane, open its mirror at ${CLAUDE_MIRROR_BIAS:-25}%.
#                 (sid from stdin payload)
#   close         SessionEnd: close this session's mirror + park its log/state DB as
#                 *.keep so a --resume/--continue replays the history. (sid from stdin)
#   toggle        close if present, else (re)open — WITHOUT truncating, so the
#                 session's history re-appears; while closed there is no process.
#   grow/shrink [N]  widen/narrow by N cells (default ${CLAUDE_MIRROR_STEP:-4}).
#   reset / setpct N set the width to BIAS% / N% of the tab.
#
# open/close get the sid from their hook payload (stdin); the keybindings have no
# payload, so they recover the sid from the currently focused kitty tab.
#
# Keybinding (background) launches have no KITTY_WINDOW_ID, so we only require
# KITTY_LISTEN_ON — it is inherited / self-resolved (see below).

import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import claude_audit as A   # noqa: E402
import claude_kitty as K   # noqa: E402
import claude_ops as O     # noqa: E402
import claude_paths as P   # noqa: E402

# Keymap-launched background processes can inherit a minimal PATH; guarantee the
# tools we shell out to (kitten, ps) resolve.
os.environ["PATH"] = ("/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:"
                      "/opt/homebrew/bin:" + os.environ.get("PATH", ""))

CMD = sys.argv[1] if len(sys.argv) > 1 else ""
CONFIG_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


# Mirror width (% of tab) and resize step (cells). SINGLE SOURCE OF TRUTH is the
# `env` block of Claude's settings.json — read from BOTH the user/global file and
# the project file, with the project overriding the global (Claude's own layering).
# That env reaches Claude's hook processes directly (already merged), but NOT the
# kitty keybindings (which launch this script from kitty's environment) — so when
# the var isn't already in our env, we read+merge the files ourselves. The
# keybindings pass `--cwd current`, so $PWD is the project here too.
# Precedence: inherited env (if any) → project settings → global settings → default.
def _settings_files():
    files = [os.path.join(CONFIG_DIR, "settings.json")]
    proj = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not proj:                             # walk up from cwd to the nearest .claude
        d = os.getcwd()
        home = os.path.expanduser("~")
        while d and d not in ("/", home):
            if os.path.isdir(os.path.join(d, ".claude")):
                proj = d
                break
            d = os.path.dirname(d)
    if proj:
        files += [os.path.join(proj, ".claude", "settings.json"),
                  os.path.join(proj, ".claude", "settings.local.json")]
    return files


def read_setting(key):
    """The env-var's merged value across the settings files (last wins), or ""."""
    val = ""
    for f in _settings_files():
        try:
            with open(f, encoding="utf-8") as fh:
                v = json.load(fh).get("env", {}).get(key)
            if v is not None:
                val = str(v)
        except Exception:
            pass
    return val


def _int_setting(env_key, default):
    v = os.environ.get(env_key) or read_setting(env_key)
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


BIAS = _int_setting("CLAUDE_MIRROR_BIAS", 25)
STEP = _int_setting("CLAUDE_MIRROR_STEP", 4)


# --- audit: pane operations (open/close/toggle/resize + failures) ---------------
# The kitten calls here are all silenced, so a mirror that failed to open — or a
# resize that did nothing — used to leave no evidence. Every pane op is recorded
# in the audit DB's pane_events (claude_audit's writers never raise; CLAUDE_AUDIT=0
# turns them off).

def audit_pane(sid, action, ok, detail):
    try:
        A.pane(sid, action, ok, detail)
    except Exception:
        pass


def audit_state(log, path, action, content):
    try:
        A.state_file(log, path, action, content)
    except Exception:
        pass


# Need socket remote control inside kitty, else no-op. A keymap-driven
# `launch --type=background` child does NOT inherit KITTY_LISTEN_ON, so when it
# is absent, resolve the controlling instance's socket ourselves: listen_on
# `unix:/tmp/kitty` yields `/tmp/kitty-<kitty-pid>`, and that kitty pid is an
# ancestor of this process. Fall back to the lone socket if just one instance.
def _is_socket(p):
    try:
        import stat
        return stat.S_ISSOCK(os.stat(p).st_mode)
    except OSError:
        return False


def resolve_listen_on():
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


LISTEN_ON = resolve_listen_on()
os.environ["KITTY_LISTEN_ON"] = LISTEN_ON


KITTEN = K.find_kitten()


def kitten_run(*args):
    """A silenced `kitten @ …` call; returns the exit code (1 on any failure)."""
    return K.kitten_run(KITTEN, LISTEN_ON, *args)


def kitten_ls():
    """Parsed `kitten @ ls` (the OS-window/tab/window tree), or [] on failure."""
    return K.kitten_ls(KITTEN, LISTEN_ON)


# --- session identity --------------------------------------------------------

def sid_from_stdin():
    """SessionStart/SessionEnd: (payload, session_id) from the hook's stdin."""
    try:
        payload = json.loads(sys.stdin.read() or "{}") or {}
    except Exception:
        payload = {}
    return payload, str(payload.get("session_id") or "")


def sid_from_focus():
    """Keybinding: the session of the currently focused kitty tab."""
    sess = mir = ""
    for osw, t, w in K.iter_windows(kitten_ls()):
        if not (osw.get("is_focused") and t.get("is_focused")):
            continue                          # frontmost OS window, active tab
        uv = w.get("user_vars", {})
        if uv.get("claude_session"):
            sess = uv["claude_session"]
        if uv.get("claude_mirror"):
            mir = uv["claude_mirror"]
    return sess or mir


def log_for(sid):
    """Canonical per-session log path — claude_ops.log_path, so it is byte-for-byte
    the same path the producers write to (sid primary, cwd slug fallback)."""
    try:
        return O.log_path({"session_id": sid, "cwd": os.getcwd()})
    except Exception:
        return ""


# --- per-project remembered size --------------------------------------------
# The width you set (grow/shrink/setpct/reset) is remembered PER PROJECT and
# restored on the next SessionStart. Keyed by the project cwd — $PWD is the project
# both at SessionStart (runs in it) and for the keybindings (they pass --cwd
# current). Stored in a small SQLite DB under the Claude config dir (was a
# directory of one-number files) so it survives restarts.
SIZEDB = os.path.join(CONFIG_DIR, "kitty-mirror.db")
SIZE_DIR = os.path.join(CONFIG_DIR, "kitty-mirror-sizes")   # legacy, imported+removed


def proj_slug():
    return P.cwd_slug()


def size_put(project, pct):
    try:
        conn = sqlite3.connect(SIZEDB, timeout=0.2)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS sizes"
                         "(project TEXT PRIMARY KEY, pct INTEGER)")
            conn.execute("INSERT INTO sizes(project, pct) VALUES(?, ?) "
                         "ON CONFLICT(project) DO UPDATE SET pct = excluded.pct",
                         (project, int(pct)))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def import_legacy_sizes():
    """One-time import of the legacy per-project size files, then the dir goes."""
    if not os.path.isdir(SIZE_DIR):
        return
    for f in glob.glob(os.path.join(SIZE_DIR, "*")):
        try:
            with open(f, encoding="utf-8") as fh:
                v = fh.read().strip()
            if v.isdigit():
                size_put(os.path.basename(f), int(v))
        except OSError:
            continue
    shutil.rmtree(SIZE_DIR, ignore_errors=True)


import_legacy_sizes()


def project_bias():
    """Remembered % for this project, or the configured default (BIAS)."""
    if os.path.isfile(SIZEDB):
        try:
            conn = sqlite3.connect(f"file:{SIZEDB}?mode=ro", uri=True, timeout=0.2)
            try:
                row = conn.execute("SELECT pct FROM sizes WHERE project=?",
                                   (proj_slug(),)).fetchone()
            finally:
                conn.close()
            if row and isinstance(row[0], int):
                return row[0]
        except Exception:
            pass
    return BIAS


def mirror_geometry(sid):
    """(mirror_columns, tab_total_columns) for the tab holding this session's
    mirror, or None. Scorebar windows are excluded from the width math — the bar
    shares the mirror's column, so counting it would double-count that column.
    The one geometry walk behind current_pct AND target_delta (they had drifted
    into two near-identical copies).

    The tab total is computed by walking the mirror's `neighbors` chain left and
    right, summing ONE window per horizontal segment — NOT by summing every
    window's columns: two windows hsplit-stacked in the same column each report
    the full column width, so the plain sum double-counted it, under-reported
    the mirror's %, and drove reset/setpct (and the remembered size) far off.
    Falls back to the plain sum on a kitty too old to report `neighbors`."""
    for osw in kitten_ls():
        for t in osw.get("tabs", []):
            wins = {w.get("id"): w for w in t.get("windows", [])
                    if not w.get("user_vars", {}).get("claude_scorebar")}
            mirror = next((w for w in wins.values()
                           if w.get("user_vars", {}).get("claude_mirror") == sid), None)
            if not mirror or not mirror.get("columns"):
                continue
            cur = mirror.get("columns", 0)
            if "neighbors" not in mirror:                 # older kitty: old behavior
                return cur, sum(w.get("columns", 0) for w in wins.values())
            # `neighbors` holds GROUP ids (confirmed live), which coincide with
            # window ids only for never-regrouped windows — resolve through the
            # tab's groups map first, then as a plain window id.
            groups = {g.get("id"): (g.get("windows") or []) for g in t.get("groups", [])}

            def resolve(i):
                for wid in groups.get(i, [i]):
                    if wid in wins:
                        return wins[wid]
                return None

            total, seen = cur, {mirror.get("id")}
            for side in ("left", "right"):
                w = mirror
                while True:
                    cands = ((w.get("neighbors") or {}).get(side)) or []
                    nxt = next((ww for ww in map(resolve, cands)
                                if ww is not None and ww.get("id") not in seen), None)
                    if nxt is None:
                        break
                    seen.add(nxt.get("id"))
                    total += nxt.get("columns", 0)
                    w = nxt
            return cur, total
    return None


def current_pct(sid):
    """The mirror's current width as % of its tab, or None."""
    g = mirror_geometry(sid)
    if not g:
        return None
    cur, total = g
    return round(100 * cur / total) if total else None


def save_size(sid):
    """Remember the mirror's current % for this project."""
    pct = current_pct(sid)
    if isinstance(pct, int):
        size_put(proj_slug(), pct)


# --- pane ops, all scoped to ONE session's mirror (var:claude_mirror=<sid>) ----
# The mirror pane carries a small companion: the SCOREBOARD BAR (claude-scorebar.py),
# a small (BAR_ROWS-tall) window hsplit under it (var:claude_scorebar=<sid>). Its own window — not
# lines pinned inside the mirror — so scrolling the mirror's history can't scroll it
# away. Opened/closed with the mirror; excluded from the width math above (it shares
# the mirror's column, so counting its columns would double-count that column).

def window_exists(var, sid):
    return any(w.get("user_vars", {}).get(var) == sid
               for _o, _t, w in K.iter_windows(kitten_ls()))


def mirror_exists(sid):
    return window_exists("claude_mirror", sid)


def close_stale_mirrors(keep):
    """Close any STALE mirror/scoreboard in the tab whose sid differs from `keep`.
    A session's id changes on --resume/--continue (and often /clear): SessionStart
    then re-tags the Claude pane and opens a mirror keyed by the NEW sid, while the
    OLD-sid mirror lingers in the same tab — tailing a log nothing writes to anymore
    (frozen) and doubling the pane. One tab holds exactly one Claude session, so a
    mirror there with a different sid is always stale. Anchored to KITTY_WINDOW_ID
    (the hook's Claude pane) when present, else the focused tab (keybinding).
    No-op when there's nothing stale to close."""
    anchor = os.environ.get("KITTY_WINDOW_ID", "")
    stale = []
    for osw in kitten_ls():
        for t in osw.get("tabs", []):
            if anchor:
                if not any(str(w.get("id")) == anchor for w in t.get("windows", [])):
                    continue
            elif not (osw.get("is_focused") and t.get("is_focused")):
                continue
            for w in t.get("windows", []):
                uv = w.get("user_vars", {})
                sid = uv.get("claude_mirror") or uv.get("claude_scorebar")
                if sid and sid != keep:
                    stale.append(w.get("id"))
            break
    for wid in stale:
        kitten_run("close-window", "--match", f"id:{wid}")


# kitty bias is approximate ("you cannot use this method to create windows of fixed
# sizes"), so after launching the bar, iterate relative resizes until it is exactly
# BAR_ROWS tall (or kitty's minimum stops shrinking it).
BAR_ROWS = 5   # ⬡ session id + ✉ census + ▪ summary + Σ token breakdown + tools


def bar_delta(sid):
    """(BAR_ROWS - current bar rows), or None if the bar can't be measured."""
    for _o, _t, w in K.iter_windows(kitten_ls()):
        if w.get("user_vars", {}).get("claude_scorebar") == sid:
            return BAR_ROWS - int(w.get("lines") or 0)
    return None


def size_bar(sid):
    for _ in range(3):
        d = bar_delta(sid)
        if not d:
            return
        kitten_run("resize-window", "--match", f"var:claude_scorebar={sid}",
                   "--axis", "vertical", "--increment", str(d))
        time.sleep(0.08)                     # let kitty apply before re-measuring


def open_mirror(sid, log, bias):
    """Does NOT truncate — the caller decides the log's fate."""
    if not mirror_exists(sid):
        # vsplit sizing only works in the splits layout; switch the active tab to it.
        kitten_run("goto-layout", "splits")
        kitten_run("launch",
                   "--location=vsplit", "--bias", str(bias or BIAS),
                   "--keep-focus", "--cwd", "current",
                   "--var", f"claude_mirror={sid}", "--title", "◧ cmd mirror",
                   os.path.join(HERE, "claude-mirror.py"), log)
    if not window_exists("claude_scorebar", sid):   # checked separately so a crashed/
        kitten_run("launch",                        # closed bar comes back on toggle
                   "--location=hsplit", "--next-to", f"var:claude_mirror={sid}",
                   "--bias", "5", "--keep-focus", "--cwd", "current",
                   "--var", f"claude_scorebar={sid}", "--title", "▪ session",
                   os.path.join(HERE, "claude-scorebar.py"), log)
        size_bar(sid)


def close_mirror(sid):
    """The bar rides along with the mirror."""
    kitten_run("close-window", "--match", f"var:claude_scorebar={sid}")
    kitten_run("close-window", "--match", f"var:claude_mirror={sid}")


def tag_window(sid):
    """Tag THIS hook's own Claude pane so a keybinding can find it."""
    win = os.environ.get("KITTY_WINDOW_ID", "")
    if win:
        kitten_run("set-user-vars", "--match", f"id:{win}", f"claude_session={sid}")


def resize_mirror(inc, sid):
    """Positive increment widens."""
    kitten_run("resize-window", "--match", f"var:claude_mirror={sid}",
               "--axis", "horizontal", "--increment", str(inc))


# Resize a session's mirror to an ABSOLUTE width of PCT% of the tab. kitty only
# resizes by a relative increment (and its `--axis reset` snaps to 50/50); worse,
# in the splits layout one increment unit isn't exactly one column, so a single
# delta over/undershoots. So read the live geometry, resize toward the target, and
# ITERATE — re-measuring — until within a cell.
def target_delta(pct, sid):
    """(target_cols - current_mirror_cols), or None if the mirror can't be found."""
    g = mirror_geometry(sid)
    if not g:
        return None
    cur, total = g
    return round(total * pct / 100.0) - cur if total else None


def size_to(pct, sid):
    for _ in range(6):
        inc = target_delta(pct, sid)
        if inc is None:                      # no mirror / unreadable -> stop
            return
        if inc == 0:                         # on target
            return
        resize_mirror(inc, sid)
        if inc in (1, -1):                   # within a cell -> avoid oscillation
            return
        time.sleep(0.08)                     # let kitty apply before re-measuring


# --- commands -------------------------------------------------------------------

def decide_log_fate(sid, log):
    """What happens to this sid's state DB at SessionStart — the session's entire
    mirror history lives in it ("log" is only the KEY the DB path derives from —
    no log file exists anymore): the ops table is what the renderer replays, the
    rest is scoreboard/coordination state. Keyed on file existence, NOT the
    payload's `source` field, so a resume-after-crash (no SessionEnd, DB still
    live) is covered too. Returns the fate the caller audits ("mirror came back
    empty after a resume" is diagnosable as a `fresh-db` row on a source=resume
    start):
      restore-history — SessionEnd parked this sid's state DB at *.keep;
                        --resume/--continue keeps the sid, so move it back and
                        the renderer replays the whole prior session.
      reuse-live-db   — the DB already exists: compact fires SessionStart
                        mid-session, and a crash skips SessionEnd. Leave it alone.
      fresh-db        — brand-new session (the DB is created lazily by the first
                        writer). With no sid (the shared cwd-slug fallback) any
                        leftover DB may be another session's — remove it."""
    db = log + ".state.db"
    if sid and os.path.isfile(db + ".keep"):
        for f in (db, db + "-wal", db + "-shm"):
            if os.path.isfile(f + ".keep"):
                try:
                    os.replace(f + ".keep", f)
                except OSError:
                    pass
        return "restore-history"
    if os.path.isfile(db):
        if sid:
            return "reuse-live-db"
        for f in (db, db + "-wal", db + "-shm"):
            try:
                os.remove(f)
            except OSError:
                pass
    return "fresh-db"


def cmd_open():                              # SessionStart (payload on stdin)
    payload, sid = sid_from_stdin()
    # Register the session in the audit DB (always on; CLAUDE_AUDIT=0 disables).
    try:
        A.session_start(payload)
    except Exception:
        pass
    log = log_for(sid)
    if not log:
        return
    log_fate = decide_log_fate(sid, log)
    audit_state(log, log + ".state.db.keep", log_fate,
                f"source={payload.get('source') or ''}")
    # The DB file's EXISTENCE is the session-alive signal the scoreboard bar and
    # the codex watcher poll — create it (with schema) BEFORE launching them, or
    # on a fresh session they'd start, find no DB, and exit instantly (the old
    # design's `: > "$log"` provided this same guarantee for the log file).
    try:
        import claude_state
        claude_state.connect(log)
    except Exception:
        pass
    tag_window(sid)
    close_stale_mirrors(sid)   # drop a prior-sid mirror (resume/clear) so it can't double up
    bias = project_bias()
    open_mirror(sid, log, bias)              # restore this project's remembered size
    # Verify the panes actually exist now — open_mirror's kitten calls are silent.
    if mirror_exists(sid):
        if window_exists("claude_scorebar", sid):
            audit_pane(sid, "open", 1, f"bias={bias}% {log_fate}")
        else:
            audit_pane(sid, "open", 0, "mirror opened but scoreboard bar absent")
    else:
        audit_pane(sid, "open", 0, "mirror window absent after launch")
    # Stream any codex run (companion job OR raw `codex`/`codex exec`) into this
    # session's mirror. The launcher Popens the watcher DETACHED (start_new_session)
    # and exits in a few ms, so it can never hang SessionStart — the long-lived
    # watcher must never sit in the hook's process group. The watcher exits on its
    # own when this log is removed at SessionEnd.
    launcher = os.path.join(HERE, "claude-codex-launch.py")
    if os.path.isfile(launcher):
        try:
            subprocess.run([sys.executable or "python3", launcher,
                            log, os.getcwd(), sid],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


def cmd_close():                             # SessionEnd (payload on stdin)
    payload, sid = sid_from_stdin()
    # Stamp the session's end in the audit DB (also prunes sessions > 30 days old).
    try:
        A.session_end(payload)
    except Exception:
        pass
    if sid:
        close_mirror(sid)
    audit_pane(sid, "close", 1, "session end")
    log = log_for(sid)
    # Park (don't delete) the per-session state DB (claude_state: the ops table —
    # the mirror's entire visible history — plus scoreboard counters, message
    # tracker, agent records, hand-offs) as *.keep: --resume/--continue keeps the
    # session_id, so the next SessionStart moves it back and the mirror replays
    # the session (scoreboard included). Renaming makes the DB PATH vanish, which
    # is the exit signal the codex watcher and the scoreboard bar poll for —
    # leaving it in place would leak them. Only the anonymous cwd-slug fallback
    # (no sid) is deleted outright, and parked sessions older than 7 days are
    # pruned on every close ("log" itself is a pre-migration leftover: removed).
    if not log:
        return
    db = log + ".state.db"
    if sid:
        for f in (db, db + "-wal", db + "-shm"):
            if os.path.isfile(f):
                try:
                    os.replace(f, f + ".keep")
                except OSError:
                    pass
        audit_state(log, db + ".keep", "keep-history", "parked for resume")
    else:
        for f in (db, db + "-wal", db + "-shm"):
            try:
                os.remove(f)
            except OSError:
                pass
    for f in (log, log + ".keep"):           # legacy JSONL log, if any
        try:
            os.remove(f)
        except OSError:
            pass
    sweep_tmp_debris()


def sweep_tmp_debris():
    """Global /tmp janitor, run at every SessionEnd: pre-migration leftovers
    (marker dirs, tab-state/watcher-pid files — nothing reads or writes them
    anymore) and anything session-scoped older than 7 days (parked *.keep never
    resumed, state DBs of crashed sessions, orphaned fg .out/.done side files —
    a LIVE session's files are always younger)."""
    for p in glob.glob("/tmp/claude-mirror-*.log.slots"):
        shutil.rmtree(p, ignore_errors=True)
    for pat in ("/tmp/claude-tab-state-*", "/tmp/claude-tab-bgwatch-*",
                "/tmp/claude-tab-interruptwatch-*"):
        for p in glob.glob(pat):
            try:
                os.remove(p)
            except OSError:
                pass
    cutoff = time.time() - 7 * 86400
    for p in glob.glob("/tmp/claude-mirror-*"):
        try:
            if os.lstat(p).st_mtime < cutoff:
                shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) \
                    else os.remove(p)
        except OSError:
            continue


def cmd_toggle():                            # keybinding
    sid = sid_from_focus()
    if not sid:
        return
    if mirror_exists(sid):
        close_mirror(sid)                    # keep the log -> history preserved
        audit_pane(sid, "toggle-off", 1, "")
    else:
        close_stale_mirrors(sid)             # clear any prior-sid pane in this tab first
        bias = project_bias()
        open_mirror(sid, log_for(sid), bias)     # remembered size, keep history
        if mirror_exists(sid):
            audit_pane(sid, "toggle-on", 1, f"bias={bias}%")
        else:
            audit_pane(sid, "toggle-on", 0, "mirror window absent after launch")


def cmd_resize(action):
    """grow/shrink/reset/setpct: resize, then remember the resulting % for this
    project. grow/shrink settle a moment after the async resize, so pause briefly
    before measuring. The audited detail carries the RESULTING width — a resize
    that changed nothing is visible."""
    sid = sid_from_focus()
    if not sid:
        return
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    if action == "grow":
        step = arg or STEP
        resize_mirror(step, sid)
        time.sleep(0.2)
        save_size(sid)
        audit_pane(sid, "grow", 1, f"+{step} cells -> {current_pct(sid)}%")
    elif action == "shrink":
        step = arg or STEP
        resize_mirror(f"-{step}", sid)
        time.sleep(0.2)
        save_size(sid)
        audit_pane(sid, "shrink", 1, f"-{step} cells -> {current_pct(sid)}%")
    elif action == "reset":
        size_to(BIAS, sid)
        save_size(sid)
        audit_pane(sid, "reset", 1, f"target={BIAS}% -> {current_pct(sid)}%")
    elif action == "setpct":
        try:
            pct = int(arg)
        except (TypeError, ValueError):
            pct = BIAS
        size_to(pct, sid)
        save_size(sid)
        audit_pane(sid, "setpct", 1, f"target={pct}% -> {current_pct(sid)}%")


def main():
    if not LISTEN_ON or not KITTEN:
        return
    if CMD == "open":
        cmd_open()
    elif CMD == "close":
        cmd_close()
    elif CMD == "toggle":
        cmd_toggle()
    elif CMD in ("grow", "shrink", "reset", "setpct"):
        cmd_resize(CMD)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        try:
            A.error("", "main")              # audit the swallow, then stay silent
        except Exception:
            pass
    sys.exit(0)
