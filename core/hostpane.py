# core/hostpane.py — the tool-AGNOSTIC host mirror lifecycle.
#
# Standing up (and tearing down) the command-mirror pane + scoreboard bar + the
# per-session state DB for ONE *host* session, keyed by an opaque session key.
# This is the machinery a host tool's SessionStart/SessionEnd drives; it used to
# live inside plugins/claude_code/split.py when Claude Code was the only host.
#
# It is shared because there are now TWO hosts:
#   - claude_code  — the original host (SessionStart/SessionEnd hooks, split.py)
#   - codex        — a standalone codex session (its native SessionStart hook +
#                    a pid-liveness teardown, plugins/codex/session.py + watch.py)
# The plugin-layering rule forbids one plugin importing another, so the pane
# lifecycle both hosts need lives here in core instead.
#
# FRONTEND INJECTION. core must import only core (CLAUDE.md dependency rule), yet
# opening a pane needs a frontend. So every function that touches the terminal
# takes the caller's `fe` (a frontends.Frontend) as its FIRST argument — core
# never imports frontends; the plugin that already holds an `fe` passes it in.
# `here` is the repo root (where the entry scripts — claude-mirror.py,
# claude-scorebar.py — live), likewise passed by the caller.
import os
import time

from core import audit as A
from core import state as S

# kitty bias is approximate ("you cannot use this method to create windows of
# fixed sizes"), so after launching the bar we iterate relative resizes until it
# is exactly BAR_ROWS tall (or kitty's minimum stops shrinking it).
BAR_ROWS = 5   # ⬡ session id + ✉ census + ▪ summary + Σ token breakdown + tools


# --- window lookups ----------------------------------------------------------

def window_exists(fe, var, sid):
    return fe.find_window(var, sid) is not None


def mirror_exists(fe, sid):
    return window_exists(fe, "claude_mirror", sid)


# --- scoreboard bar sizing ---------------------------------------------------

def bar_delta(fe, sid):
    """(BAR_ROWS - current bar rows), or None if the bar can't be measured."""
    w = fe.find_window("claude_scorebar", sid)
    return BAR_ROWS - int(w.get("lines") or 0) if w else None


def size_bar(fe, sid):
    for _ in range(3):
        d = bar_delta(fe, sid)
        if not d:
            return
        fe.resize_pane(("claude_scorebar", sid), "vertical", d)
        time.sleep(0.08)                     # let kitty apply before re-measuring


# --- open / close ------------------------------------------------------------

def open_mirror(fe, here, sid, log, bias, default_bias=25, anchor=None):
    """Open this session's mirror pane (+ scoreboard bar). Does NOT truncate —
    the caller decides the state DB's fate (decide_log_fate). `bias` is the
    mirror width %, `default_bias` the fallback when it is 0/None. `anchor` is
    the host pane's window id: the vsplit lands next to IT, not next to
    whatever window happens to be focused — a hook can fire while the user is
    looking at a different tab (daemon-origin SessionStart, keybinding)."""
    if not mirror_exists(fe, sid):
        # vsplit sizing only works in the splits layout; switch the anchor's
        # tab (or, unanchored, the active tab) to it.
        fe.goto_splits_layout(anchor)
        fe.launch_pane([os.path.join(here, "claude-mirror.py"), log],
                       "vsplit", bias=(bias or default_bias),
                       next_to=(f"id:{anchor}" if anchor else None),
                       var={"claude_mirror": sid}, title="◧ cmd mirror")
    if not window_exists(fe, "claude_scorebar", sid):   # checked separately so a
        fe.launch_pane([os.path.join(here, "claude-scorebar.py"), log],  # crashed/
                       "hsplit", bias=BAR_ROWS, next_to=f"var:claude_mirror={sid}",
                       var={"claude_scorebar": sid}, title="▪ session")
        size_bar(fe, sid)                          # closed bar comes back on toggle


def close_mirror(fe, sid):
    """The bar rides along with the mirror."""
    fe.close_pane(var=("claude_scorebar", sid))
    fe.close_pane(var=("claude_mirror", sid))


def close_stale_mirrors(fe, keep, anchor=None):
    """Close any STALE mirror/scoreboard in the tab whose sid differs from `keep`.
    A session's id changes on --resume/--continue (and often /clear): SessionStart
    then re-tags the host pane and opens a mirror keyed by the NEW sid, while the
    OLD-sid mirror lingers in the same tab — tailing a DB nothing writes to anymore
    (frozen) and doubling the pane. One tab holds exactly one host session, so a
    mirror there with a different sid is always stale. Anchored to the caller's
    `anchor` window id when given, else the hook's own pane (KITTY_WINDOW_ID),
    else the focused tab (keybinding). Every close is audited (pane_events
    action `close-stale` naming the closed sid) — an unaudited sweep is exactly
    how a cross-session pane hijack stays invisible.
    No-op when there's nothing stale to close."""
    anchor = anchor or fe.current_window()
    stale = []
    for osw in fe.ls():
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
                    stale.append((w.get("id"), sid))
            break
    for wid, sid in stale:
        fe.close_pane(win_id=wid)
        try:
            A.pane(keep, "close-stale", 1, "closed sid=%s win=%s" % (sid, wid))
        except Exception:
            pass


def tab_host_sid(fe, exclude_sid=""):
    """The sid of a LIVE host mirror already present in this hook's tab, or "".
    Used by a second host (standalone codex) to detect it is NESTED inside a
    running first host (Claude Code) — codex launched as a Claude subagent runs
    inside Claude's pane, whose tab already carries a claude_mirror window; that
    session's watcher already streams the codex run, so the nested host must not
    open a second mirror. A mirror keyed by `exclude_sid` (our own, e.g. on a
    resume) does not count. 'Live' = its state DB still exists."""
    anchor = fe.current_window()
    for osw in fe.ls():
        for t in osw.get("tabs", []):
            if anchor:
                if not any(str(w.get("id")) == anchor for w in t.get("windows", [])):
                    continue
            elif not (osw.get("is_focused") and t.get("is_focused")):
                continue
            for w in t.get("windows", []):
                uv = w.get("user_vars", {})
                sid = uv.get("claude_mirror") or uv.get("claude_session")
                if sid and sid != exclude_sid and os.path.exists(
                        S.db_path(_log_for_sid(sid))):
                    return sid
            return ""
    return ""


def _log_for_sid(sid):
    from core import paths as P
    return P.mirror_log(sid)


# --- state DB fate: create / restore / park ---------------------------------

def ensure_db(log):
    """Create the per-session state DB (with schema) if absent. Its EXISTENCE is
    the session-alive signal the scoreboard bar and the codex watcher poll — it
    must exist BEFORE they launch, or on a fresh session they would start, find
    no DB, and exit instantly."""
    try:
        S.connect(log)
    except Exception:
        pass


def decide_log_fate(sid, log):
    """What happens to this sid's state DB at SessionStart — the session's entire
    mirror history lives in it ("log" is only the KEY the DB path derives from —
    no log file exists anymore): the ops table is what the renderer replays, the
    rest is scoreboard/coordination state. Keyed on file existence, NOT the
    payload's `source` field, so a resume-after-crash (no SessionEnd, DB still
    live) is covered too. Returns the fate the caller audits:
      restore-history — SessionEnd parked this sid's state DB at *.keep; a
                        resume keeps the sid, so move it back and the renderer
                        replays the whole prior session.
      reuse-live-db   — the DB already exists (compact fires SessionStart
                        mid-session; a crash skips SessionEnd). Leave it alone.
      fresh-db        — brand-new session. With no sid (the shared cwd-slug
                        fallback) any leftover DB may be another session's —
                        remove it."""
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


def park_db(sid, log):
    """Tear down the state DB at session end. Renaming the DB (+ -wal/-shm) to
    *.keep makes the DB PATH vanish — the exit signal the codex watcher and the
    scoreboard bar poll for — while preserving the session's history so a
    same-sid resume replays it (decide_log_fate → restore-history). Only the
    anonymous cwd-slug fallback (no sid) is deleted outright. Returns the action
    string the caller audits."""
    db = log + ".state.db"
    if sid:
        for f in (db, db + "-wal", db + "-shm"):
            if os.path.isfile(f):
                try:
                    os.replace(f, f + ".keep")
                except OSError:
                    pass
        return "keep-history"
    for f in (db, db + "-wal", db + "-shm"):
        try:
            os.remove(f)
        except OSError:
            pass
    return "discard"
