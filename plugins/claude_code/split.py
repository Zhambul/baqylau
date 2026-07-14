# plugins/claude_code/split.py — the mirror-pane + session lifecycle manager.
# Entry point: claude-split.py (a thin shim — the entry FILENAME is the audit
# vocabulary and what the kitty keybindings invoke).
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
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time

from core.paths import ROOT  # the repo root, where the sibling ENTRY scripts live
import frontends                          # noqa: E402
from core.noaudit import load_audit       # noqa: E402

A = load_audit()   # audit trail (real module, or an inert stub if it can't import)
from core import hostpane as HP           # noqa: E402  (shared host pane lifecycle)
from core import paths as P               # noqa: E402
from core import tabs as T                # noqa: E402  (adopt_note — sid-fork registry)
from plugins.claude_code import hookkit as HK  # noqa: E402  (log_path + the injected-payload accessor)
from plugins.claude_code import model as M     # noqa: E402  (settings_env)

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
# The walk + layering live in model.settings_env (the shared settings-resolution
# home); nearest_only=True keeps this path's historical nearest-.claude-wins walk.
def _int_setting(env_key, default):
    v = os.environ.get(env_key) or M.settings_env(env_key, nearest_only=True)
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


# The terminal adapter. resolve=True lets it hunt for its control channel
# beyond the environment — a keymap-driven `launch --type=background` child
# does NOT inherit KITTY_LISTEN_ON, so the kitty frontend walks the ppid chain
# to the controlling instance's socket (frontends.kitty.resolve_listen_on).
# export_env() then stamps the resolved channel back into our env so detached
# children (streamers, the codex watcher) inherit it.
#
# FE is resolved LAZILY (memoized on first use) rather than at import, same as
# tabstatus.py: dispatch.py imports this module for every hook event, including
# ones that never touch a pane, and eagerly resolving the frontend (a ppid-walk
# socket hunt) there was per-invocation work paid by everything sharing the
# process. export_env() runs inside _fe() on first resolution — every detached
# spawn (open_mirror's streamers, the codex watcher via plugins.on_session_start)
# happens strictly after a pane call, i.e. after _fe() has stamped the env.
# None = not-yet-resolved; tests may pre-seed FE directly, which _fe() honours.
FE = None


def _fe():
    global FE
    if FE is None:
        FE = frontends.get(resolve=True)
        FE.export_env()
    return FE


# --- session identity --------------------------------------------------------

def sid_from_stdin():
    """SessionStart/SessionEnd: (payload, session_id) from the hook's stdin — or
    from the dispatcher-injected payload when driven in-process (dispatch.py;
    handle() below re-injects for direct callers). The lenient parse/cache is
    hookkit.payload_or_stdin() — {} on anything unparsable."""
    payload = HK.payload_or_stdin()
    return payload, str(payload.get("session_id") or "")


def sid_from_focus():
    """Keybinding: the session of the currently focused terminal tab."""
    sess = mir = ""
    for osw, t, w in _fe().iter_windows():
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
        return HK.log_path({"session_id": sid, "cwd": os.getcwd()})
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


_LEGACY_DONE = False


def import_legacy_sizes():
    """One-time import of the legacy per-project size files, then the dir goes.
    Called LAZILY (memoized) from the sizes-DB readers/writers, not at import —
    dispatch.py imports this module for every hook event, and the /tmp-era glob
    + isdir probe was per-invocation work paid by events that never touch the
    sizes DB."""
    global _LEGACY_DONE
    if _LEGACY_DONE:
        return
    _LEGACY_DONE = True
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


def project_bias():
    """Remembered % for this project, or the configured default (BIAS)."""
    import_legacy_sizes()                    # fold any legacy files in first
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
    into two near-identical copies). The walk itself (kitty's `neighbors`/
    `groups` semantics) lives in the frontend — see split_geometry in
    frontends/kitty.py."""
    return _fe().split_geometry(("claude_mirror", sid), exclude_var="claude_scorebar")


def current_pct(sid):
    """The mirror's current width as % of its tab, or None."""
    g = mirror_geometry(sid)
    if not g:
        return None
    cur, total = g
    return round(100 * cur / total) if total else None


def save_size(sid):
    """Remember the mirror's current % for this project."""
    import_legacy_sizes()                    # a fresh save must not be shadowed later
    pct = current_pct(sid)
    if isinstance(pct, int):
        size_put(proj_slug(), pct)


# --- pane ops, all scoped to ONE session's mirror (var:claude_mirror=<sid>) ----
# The mirror pane + scoreboard bar lifecycle is tool-AGNOSTIC — a second host
# (standalone codex) drives the identical machinery — so it lives in core:
# core.hostpane, frontend-INJECTED (core imports no frontend, so we pass FE/ROOT
# in). These thin wrappers bind THIS host's FE/ROOT/BIAS so the call sites below
# read unchanged. The scoreboard bar (claude-scorebar.py) is a small hsplit under
# the mirror (var:claude_scorebar=<sid>) — its own window so scrolling the
# mirror's history can't scroll it away; excluded from the width math above (it
# shares the mirror's column, so counting its columns would double-count it).
BAR_ROWS = HP.BAR_ROWS


def window_exists(var, sid):
    return HP.window_exists(_fe(), var, sid)


def mirror_exists(sid):
    return HP.mirror_exists(_fe(), sid)


def close_stale_mirrors(keep, anchor=None):
    HP.close_stale_mirrors(_fe(), keep, anchor)


def open_mirror(sid, log, bias, anchor=None):
    """Does NOT truncate — the caller decides the state DB's fate."""
    HP.open_mirror(_fe(), ROOT, sid, log, bias, BIAS, anchor)


def close_mirror(sid):
    HP.close_mirror(_fe(), sid)


def tag_window(sid):
    """Tag THIS hook's own Claude pane so a keybinding can find it."""
    win = _fe().current_window()
    if win:
        _fe().set_user_vars(win, {"claude_session": sid})


def resize_mirror(inc, sid):
    """Positive increment widens."""
    _fe().resize_pane(("claude_mirror", sid), "horizontal", inc)


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
    """Delegates to core.hostpane (shared with the codex host): restore a parked
    *.keep DB on resume, reuse a live DB (compact / crash-resume), or start
    fresh. Returns the fate the caller audits."""
    return HP.decide_log_fate(sid, log)


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
    # Where does this session's pane live? The hook's own window when the env
    # says so (KITTY_WINDOW_ID — the normal interactive case), else the window
    # already tagged claude_session=<sid> (a daemon-origin SessionStart: the
    # agents view spawns `claude daemon run`, whose hook processes carry a
    # SCRUBBED env — re-entering a chat fires a `source=resume` SessionStart
    # with no kitty vars at all). Neither exists for a session that has no pane
    # anywhere (the agents view's own agent sessions — their SessionStart
    # carries `agent_type` — or a headless `claude -p` reaching the socket via
    # the lone-socket fallback): the old focused-tab fallback made such a
    # session hijack whatever tab the user was looking at — closing ITS mirror
    # as "stale" and splitting in an empty one — so skip the whole lifecycle
    # instead: no pane, no state DB, no watchers.
    anchor = _fe().current_window() or _fe().window_for_session(sid)
    if not anchor:
        audit_pane(sid, "open", 1, "skipped: no host pane (daemon/headless session)")
        return
    log_fate = decide_log_fate(sid, log)
    audit_state(log, log + ".state.db.keep", log_fate,
                f"source={payload.get('source') or ''}")
    # The DB file's EXISTENCE is the session-alive signal the scoreboard bar and
    # the codex watcher poll — create it (with schema) BEFORE launching them, or
    # on a fresh session they'd start, find no DB, and exit instantly (the old
    # design's `: > "$log"` provided this same guarantee for the log file).
    HP.ensure_db(log)
    tag_window(sid)
    # Register this as the last HOSTED session in this cwd — the predecessor
    # candidate adopt.py consumes if the sid forks (on --resume AND on
    # backgrounding a session — see plugins/claude_code/adopt.py). Written here,
    # not at every SessionStart: only a session whose pane + state DB really
    # exist can be adopted, so a skipped daemon/headless start (the early return
    # above) must never shadow the real predecessor.
    if sid:
        T.adopt_note(payload.get("cwd") or os.getcwd(), sid)
    close_stale_mirrors(sid, anchor)   # drop a prior-sid mirror (resume/clear) so it can't double up
    bias = project_bias()
    open_mirror(sid, log, bias, anchor)      # restore this project's remembered size
    # Verify the panes actually exist now — open_mirror's kitten calls are silent.
    if mirror_exists(sid):
        if window_exists("claude_scorebar", sid):
            audit_pane(sid, "open", 1, f"bias={bias}% {log_fate}")
        else:
            audit_pane(sid, "open", 0, "mirror opened but scoreboard bar absent")
    else:
        audit_pane(sid, "open", 0, "mirror window absent after launch")
    # Attach every secondary-source plugin to this session (plugins registry —
    # codex streams any companion job or raw `codex`/`codex exec` run into this
    # session's mirror; its launcher Popens the watcher DETACHED so SessionStart
    # can never hang, and the watcher exits on its own when this session's
    # state DB is parked at SessionEnd). Plugin failures are audited inside the
    # registry and never block SessionStart.
    import plugins
    plugins.on_session_start(log, os.getcwd(), sid)


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
    action = HP.park_db(sid, log)            # rename->*.keep (resume) or delete
    if action == "keep-history":
        audit_state(log, log + ".state.db.keep", "keep-history", "parked for resume")
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
        # Keybinding launches carry no KITTY_WINDOW_ID; anchor to the session's
        # own tagged pane (it is in the focused tab — sid_from_focus found it).
        anchor = _fe().current_window() or _fe().window_for_session(sid)
        close_stale_mirrors(sid, anchor)     # clear any prior-sid pane in this tab first
        bias = project_bias()
        open_mirror(sid, log_for(sid), bias, anchor)  # remembered size, keep history
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
    # SessionEnd close must ALWAYS run: parking the state DB (→ *.keep) is core
    # session-lifecycle, not frontend work — a --resume replays that history, and
    # the codex watcher / scorebar poll for the DB path vanishing as their exit
    # signal. It runs headless (no kitty / no kitten binary → FE.usable() False)
    # too; the pane-close calls inside self-no-op (kitten_run swallows). Gating it
    # behind usable() skipped the park on any host without kitten (e.g. CI), losing
    # the parked history and leaking the watchers.
    if CMD == "close":
        cmd_close()
        return
    if not _fe().usable():
        return
    if CMD == "open":
        cmd_open()
    elif CMD == "toggle":
        cmd_toggle()
    elif CMD in ("grow", "shrink", "reset", "setpct"):
        cmd_resize(CMD)


def handle(cmd, payload):
    """In-process entry for the single per-event dispatcher (dispatch.py): run the
    SessionStart(open) / SessionEnd(close) pane lifecycle against the injected
    payload. Keybinding subcommands (toggle/grow/shrink/reset/setpct) keep the
    argv path via the module-global CMD."""
    global CMD
    CMD = cmd
    prev = HK.injected()                   # under dispatch.py route() this is `payload`
    HK.set_payload(payload)                # already, but a direct caller needs the inject
    try:
        main()
    finally:
        HK.set_payload(prev)


def entry():
    try:
        main()
    except Exception:
        try:
            A.error("", "main")              # audit the swallow, then stay silent
        except Exception:
            pass
    sys.exit(0)
