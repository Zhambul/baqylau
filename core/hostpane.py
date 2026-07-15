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
# `here` is the repo's bin/ (where the entry scripts — claude-mirror.py,
# claude-scorebar.py — live), likewise passed by the caller.
import os
import shutil
import sqlite3
import time

from core.noaudit import load_audit

A = load_audit()   # audit trail (real module, or an inert stub if it can't import)
from core import paths as P
from core import state as S

# kitty bias is approximate ("you cannot use this method to create windows of
# fixed sizes"), so after launching the bar we iterate relative resizes until it
# is exactly BAR_ROWS tall (or kitty's minimum stops shrinking it).
BAR_ROWS = 5   # ⬡ session id + ✉ census + ▪ summary + Σ token breakdown + tools
BAR_SETTLE_S = 0.08   # pause between resize_pane and re-measure — kitty applies the resize async

# The mirror-pane width default: % of the tab when no CLAUDE_MIRROR_BIAS (env or
# settings.json) says otherwise. Owned HERE because both hosts (claude_code's
# split.py and codex's session.py) fall back to it — each used to hardcode 25.
DEFAULT_BIAS = 25

# How long the pre-park WAL checkpoint waits on SQLite's busy handler before
# giving up. Short by design: park_db runs inside SessionEnd (a hook), and a
# busy-failed checkpoint degrades gracefully — the -wal file is then moved
# alongside the main DB, frames intact.
CHECKPOINT_TIMEOUT_S = 2.0


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
        time.sleep(BAR_SETTLE_S)             # let kitty apply before re-measuring


# --- open / close ------------------------------------------------------------

def open_mirror(fe, here, sid, log, bias, default_bias=DEFAULT_BIAS, anchor=None):
    """Open this session's mirror pane (+ scoreboard bar). Does NOT truncate —
    the caller decides the state DB's fate (decide_log_fate). `bias` is the
    mirror width %, `default_bias` the fallback when it is 0/None. `anchor` is
    the host pane's window id: the vsplit lands next to IT, not next to
    whatever window happens to be focused — a hook can fire while the user is
    looking at a different tab (daemon-origin SessionStart, keybinding)."""
    if not mirror_exists(fe, sid):
        # vsplit sizing only works in the splits layout; switch the anchor's
        # tab (or, unanchored, the active tab) to it. `in_tab_of` is
        # load-bearing alongside next_to: next_to alone cannot cross tabs, so
        # an anchored open while the user looks at a DIFFERENT tab split that
        # tab instead (see frontends/kitty.py launch_pane).
        fe.goto_splits_layout(anchor)
        fe.launch_pane([os.path.join(here, "claude-mirror.py"), log],
                       "vsplit", bias=(bias or default_bias),
                       next_to=(f"id:{anchor}" if anchor else None),
                       in_tab_of=anchor,
                       var={"claude_mirror": sid}, title="◧ cmd mirror")
    if not window_exists(fe, "claude_scorebar", sid):   # checked separately so a
        fe.launch_pane([os.path.join(here, "claude-scorebar.py"), log],  # crashed/
                       "hsplit", bias=BAR_ROWS, next_to=f"var:claude_mirror={sid}",
                       in_tab_of=anchor,
                       var={"claude_scorebar": sid}, title="▪ session")
        size_bar(fe, sid)                          # closed bar comes back on toggle


def close_mirror(fe, sid):
    """The bar rides along with the mirror."""
    fe.close_pane(var=("claude_scorebar", sid))
    fe.close_pane(var=("claude_mirror", sid))


def _anchored_tab_windows(fe, anchor):
    """Yield the window lists of anchor-selected tabs — the ONE traversal behind
    the daemon-origin/pane-hijack anchoring invariant (docs/mirror-pane.md
    § Anchoring): with an `anchor` window id, a tab qualifies only if it
    CONTAINS that window; without one, only the focused tab of the focused
    os-window qualifies (keybinding case). At most the first qualifying tab per
    os-window is yielded, as a list of its windows. Callers own what they read
    from the windows' user_vars and whether they stop after the first tab."""
    for osw in fe.ls():
        for t in osw.get("tabs", []):
            if anchor:
                if not any(str(w.get("id")) == anchor for w in t.get("windows", [])):
                    continue
            elif not (osw.get("is_focused") and t.get("is_focused")):
                continue
            yield t.get("windows", [])
            break


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
    for windows in _anchored_tab_windows(fe, anchor):
        for w in windows:
            uv = w.get("user_vars", {})
            sid = uv.get("claude_mirror") or uv.get("claude_scorebar")
            if sid and sid != keep:
                stale.append((w.get("id"), sid))
    for wid, sid in stale:
        rc = fe.close_pane(win_id=wid)
        try:
            # ok carries the REAL close result (pane_events contract: ok is
            # verified, not asserted) — a failed sweep (ok=0, the pane
            # lingered) surfaces in the "pane operations that failed" anomaly;
            # the pane-hijack anomaly keys on the close-stale ATTEMPT (the
            # detail's sid), so it reads correctly for both values.
            A.pane(keep, "close-stale", 0 if rc else 1,
                   "closed sid=%s win=%s" % (sid, wid))
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
    for windows in _anchored_tab_windows(fe, fe.current_window()):
        for w in windows:
            uv = w.get("user_vars", {})
            sid = uv.get("claude_mirror") or uv.get("claude_session")
            if sid and sid != exclude_sid and not S.parked(_log_for_sid(sid)):
                return sid
        return ""       # one tab is THE tab — a later os-window's is a different host
    return ""


def _log_for_sid(sid):
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
      restore-history — SessionEnd parked this sid's state DB DURABLY (under
                        HISTORY_DIR); a resume keeps the sid, so move it back to
                        the live path and the renderer replays the whole prior
                        session. A legacy in-place *.keep (older builds / a
                        park that predates this durable location) is also
                        honoured, so a resume across the upgrade still restores.
      reuse-live-db   — the DB already exists (compact fires SessionStart
                        mid-session; a crash skips SessionEnd). Leave it alone.
      fresh-db        — brand-new session. With no sid (the shared cwd-slug
                        fallback) any leftover DB may be another session's —
                        remove it.
      restore-failed (park kept) — the MAIN park file could not be moved back
                        (audited); the park stays intact for a later resume and
                        the caller's ensure_db starts this session fresh."""
    db = P.state_db(log)
    pk = P.parked_db(log)
    if sid and (os.path.isfile(pk) or os.path.isfile(db + ".keep")):
        for suf in ("", "-wal", "-shm"):
            dst = db + suf
            # durable park first, then a legacy in-place *.keep (transition).
            src = next((s for s in (pk + suf, dst + ".keep")
                        if os.path.isfile(s)), None)
            if src is None:
                # No parked sidecar (the park checkpoints the WAL away). A
                # STALE live sidecar left next to the restored main file would
                # corrupt it — SQLite would replay a foreign WAL — so drop it.
                if suf:
                    try:
                        os.remove(dst)
                    except OSError:
                        pass
                continue
            try:
                shutil.move(src, dst)
            except OSError:
                A.error(log, "decide_log_fate (restore move %s)" % (suf or "main"))
                if not suf:
                    # Without the main file there is nothing to restore onto;
                    # leave the park (and its sidecars) intact for a later try.
                    return "restore-failed (park kept)"
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


def _checkpoint_wal(db, log):
    """Best-effort `wal_checkpoint(TRUNCATE)` through a short-lived connection,
    run just before the park moves files: with the WAL flushed into the main
    file (and truncated to nothing), the main-file move is the only one that
    matters — a sidecar move can no longer tear uncheckpointed frames away from
    a parked DB. Connecting here is fine (the DB still exists; this IS the park
    path), but other writers may still be live at SessionEnd, so a busy-failed
    TRUNCATE is a graceful no-op — the -wal file is then moved alongside the
    main DB, frames intact. Only an exception is audited."""
    try:
        conn = sqlite3.connect(db, timeout=CHECKPOINT_TIMEOUT_S)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
    except Exception:
        A.error(log, "park_db (wal checkpoint)")


def park_db(sid, log):
    """Tear down the state DB at session end. MOVING the DB (+ -wal/-shm) out to
    the durable park (HISTORY_DIR) makes the live DB PATH vanish — the exit
    signal the codex watcher and the scoreboard bar poll for — while preserving
    the session's history DURABLY so a same-sid resume replays it even across a
    machine reboot (decide_log_fate → restore-history). The park lives under
    ~/.claude, not /tmp, precisely because macOS wipes /tmp on reboot. Only the
    anonymous cwd-slug fallback (no sid) is deleted outright. Returns the action
    string the caller audits:
      keep-history — parked (a sidecar-only move failure is audited but still
                     parks: the WAL was checkpointed first, so the main file
                     already holds every frame).
      park-failed (kept live) — the MAIN DB could not be moved (ENOSPC, EPERM,
                     blocked destination — audited). The live path persists, so
                     parked() never fires: the scorebar and codex watcher keep
                     polling as orphans and a same-sid resume sees reuse-live-db.
                     Returning a distinct, audited fate makes that VISIBLE (the
                     errors row also lights the errwatch ⚠ chip); a poller
                     backstop for this state is deliberately out of scope.
      discard      — the sid-less cwd-slug fallback, deleted outright."""
    db = P.state_db(log)
    if sid:
        pk = P.parked_db(log)
        try:
            os.makedirs(os.path.dirname(pk), exist_ok=True)
        except OSError:
            A.error(log, "park_db (mkdir park dir)")
        if os.path.isfile(db):
            _checkpoint_wal(db, log)
        # MAIN file first: if IT can't move, stop before touching the sidecars
        # — the DB stays live and intact (not torn), and the caller audits the
        # distinct failure fate instead of a false keep-history.
        if os.path.isfile(db):
            _remove_blocking_park(pk, log)
            try:
                shutil.move(db, pk)
            except OSError:
                A.error(log, "park_db (main move — DB kept live)")
                return "park-failed (kept live)"
        for suf in ("-wal", "-shm"):
            src = db + suf
            if os.path.isfile(src):
                dst = pk + suf
                _remove_blocking_park(dst, log)
                try:
                    shutil.move(src, dst)
                except OSError:
                    A.error(log, "park_db (sidecar move %s)" % suf)
                    try:
                        # The main file is already parked; a stale sidecar left
                        # at the live path would corrupt the NEXT restore, and
                        # the checkpoint above already folded its frames in.
                        os.remove(src)
                    except OSError:
                        pass
        return "keep-history"
    for f in (db, db + "-wal", db + "-shm"):
        try:
            os.remove(f)
        except OSError:
            pass
    return "discard"


def _remove_blocking_park(dst, log):
    try:            # a stale prior park would block a cross-device move
        if os.path.exists(dst):
            os.remove(dst)
    except OSError:
        A.error(log, "park_db (remove blocking park file)")
