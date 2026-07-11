# plugins/claude_code/adopt.py — sid-fork session adoption.
#
# Claude Code can FORK a session id mid-flight — the session continues under a
# NEW sid that never gets a SessionStart of its own, while the mirror,
# scorebar, state DB, and pane tags all stay keyed to the OLD sid. Two
# triggers observed live 2026-07-11:
#   - --resume (19a42746 → ebcecfcc): the SessionStart hook fires with the OLD
#     sid (source=resume); every subsequent hook event and OTEL datapoint
#     carries the new one. The old sid received nothing but ConfigChange after
#     the resume; the new sid accrued 1,100+ events into a state DB nothing
#     rendered; the scorebar cost froze; the tab never repainted.
#   - BACKGROUNDING a session (12e32815 → 0ed3231c): the conversation resumes
#     under the background-job id, same shape, no SessionStart at all.
# (The forked events also arrive with a SCRUBBED env — no KITTY_WINDOW_ID —
# which tabstatus handles separately via the claude_session-var fallback; see
# tabstatus._ensure_win.)
#
# The fix: dispatch.route() calls on_event() before anything else touches the
# payload.
#   - SessionStart registers its sid in the global registry (core.tabs `sids` —
#     "this sid had a real start"). split.cmd_open additionally leaves a
#     take-once note keyed by cwd (core.tabs `adopt_pending`) for every HOSTED
#     session — written only once the pane + state DB really exist, so an
#     agents-view agent session or a headless `claude -p` (whose lifecycle is
#     skipped) can never shadow the real predecessor.
#   - Any LATER event whose sid has NO state DB, NO prior SessionStart, and a
#     matching note ADOPTS the predecessor: its state DB is renamed to the new
#     sid's path — os.replace preserves the inode, so the running renderer /
#     scorebar / OTLP-receiver connections keep working — with symlinks left at
#     the old paths so old-key pollers (the scorebar's liveness stat, the
#     renderer's reopen, a straggler OTEL datapoint under the old sid) land on
#     the adopted DB. The kitty windows are retagged (claude_session /
#     claude_mirror / claude_scorebar → new sid) so pane toggles, tab painting,
#     and close_stale_mirrors all find them under the sid every future event
#     carries. The new sid's SessionEnd then parks the DB under ITS key, so a
#     later resume restores normally (the old-key symlinks dangle, which is the
#     exit signal old-key pollers need anyway).
#
# Guards — each closes a real mis-adoption path:
#   - a state DB (or a parked *.keep) at the sid's path → a known session;
#     never touch it. This is also the fast path every normal event takes
#     (one os.path.exists).
#   - tabs.sid_seen(sid) → the sid had its OWN SessionStart (a headless
#     `claude -p`, an agents-view agent session — both skip the pane lifecycle
#     and so have no state DB) — a genuinely new session, not a fork.
#   - the note's predecessor DB must still be LIVE (not parked/deleted) — a
#     stale note from a long-ended session can never capture anything.
#   - adopt_take is take-once — concurrent hook processes race to adopt;
#     exactly one wins, the losers see the note gone and fall through.
import os

from core import audit as A
from core import paths as P
from core import tabs as T


def on_event(d):
    ev = d.get("hook_event_name") or ""
    sid = str(d.get("session_id") or "")
    cwd = str(d.get("cwd") or "")
    if not sid:
        return
    if ev == "SessionStart":
        T.sid_mark(sid)                     # the note is split.cmd_open's job —
        return                              # hosted sessions only (see header)
    if ev == "SessionEnd":
        T.adopt_drop(cwd, sid)              # a clean end retires its own note
        return
    _maybe_adopt(d, sid, cwd)


def _maybe_adopt(d, sid, cwd):
    db = P.state_db(P.mirror_log(sid))
    if os.path.exists(db) or os.path.exists(db + ".keep") or not cwd:
        return                              # known session — the normal fast path
    old = T.adopt_peek(cwd)
    if not old or old == sid or T.sid_seen(sid):
        return
    old_db = P.state_db(P.mirror_log(old))
    if not os.path.isfile(old_db):
        return                              # predecessor not live — stale note
    if not T.adopt_take(cwd, old):
        return                              # another hook process won the race
    T.sid_mark(sid)
    moved = []
    for suf in ("", "-wal", "-shm"):
        try:
            os.replace(old_db + suf, db + suf)
            moved.append(suf or "db")
        except OSError:
            pass
        try:
            # Even where nothing moved (-wal/-shm may not exist), a symlink at
            # the old path routes any future write/create through to the
            # adopted DB — SQLite derives sidecar paths from the path a
            # connection was OPENED with, so an old-path connection needs all
            # three names to resolve to the new file set.
            os.symlink(db + suf, old_db + suf)
        except OSError:
            pass
    retag = _retag_windows(old, sid)
    try:
        A.session_start(d)                  # the sessions row the fork never got
    except Exception:
        pass
    try:
        A.state_file(P.mirror_log(sid), db, "adopt",
                     {"from": old, "moved": moved, "retagged": retag})
    except Exception:
        pass
    try:
        A.hook_event(d, handler="claude-hook.py",
                     decision="adopt: sid forked — adopted %s" % old)
    except Exception:
        pass


def _retag_windows(old, sid):
    """Re-point the predecessor's pane tags at the new sid, best-effort (a
    headless fork has no windows; a dead socket just yields nothing)."""
    try:
        import frontends
        fe = frontends.get(resolve=True)
        if not fe.usable():
            return []
    except Exception:
        return []
    out = []
    for var in ("claude_session", "claude_mirror", "claude_scorebar"):
        try:
            w = fe.find_window(var, old)
            if w and fe.set_user_vars(str(w.get("id")), {var: sid}) == 0:
                out.append(var)
        except Exception:
            pass
    return out
