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
#   - SessionStart (and the earlier-firing InstructionsLoaded, which a fork never
#     emits) registers its sid in the global registry (core.tabs `sids` — "this
#     sid had a real start"). split.cmd_open additionally leaves a
#     take-once note keyed by cwd (core.tabs `adopt_pending`) for every HOSTED
#     session — written only once the pane + state DB really exist, so an
#     agents-view agent session or a headless `claude -p` (whose lifecycle is
#     skipped) can never shadow the real predecessor.
#   - Any LATER event whose sid has NO state DB, NO prior SessionStart, and a
#     matching note ADOPTS the predecessor: its state DB is moved to the new
#     sid's path — a HARDLINK first (the inode keeps both names, so the running
#     renderer / scorebar / OTLP-receiver connections keep working), then a
#     symlink is atomically renamed over the old path. The old path therefore
#     exists at EVERY instant: the earlier replace-then-symlink pair had a
#     window where it was absent, and an old-key poller sampling parked() (a
#     bare exists) in that window concluded SessionEnd and quit — frozen
#     scoreboard — while a straggler old-key writer's _connect created a fresh
#     orphan DB there, losing its writes and failing the symlink with EEXIST.
#     Symlinks are left at all the old paths so old-key pollers (the
#     scorebar's liveness stat, the
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
#   - tabs.sid_seen(sid) → the sid had its OWN start (SessionStart, or the
#     earlier InstructionsLoaded — a headless `claude -p`, an agents-view agent
#     session, or ANY real new session whose first pre-SessionStart event raced
#     ahead of its own start; all skip/precede the pane lifecycle and so have no
#     state DB yet) — a genuinely new session, not a fork.
#   - the note's predecessor DB must still be LIVE (not parked/deleted) — a
#     stale note from a long-ended session can never capture anything.
#   - adopt_take is take-once — concurrent hook processes race to adopt;
#     exactly one wins, the losers see the note gone and fall through.
import os

from core.noaudit import load_audit

A = load_audit()   # audit trail (real module, or an inert stub if it can't import)
from core import paths as P
from core import tabs as T


def on_event(d):
    ev = d.get("hook_event_name") or ""
    sid = str(d.get("session_id") or "")
    cwd = str(d.get("cwd") or "")
    if not sid:
        return
    if ev in ("SessionStart", "InstructionsLoaded"):
        # InstructionsLoaded fires BEFORE SessionStart for a real new session and
        # is NOT emitted by a fork (a resumed/backgrounded continuation already has
        # its instructions) — so it is the earliest reliable "this sid had a real
        # start" signal. Marking here closes a TOCTOU: without it, a new session's
        # pre-SessionStart InstructionsLoaded reaches _maybe_adopt() with sid_seen
        # still false and could adopt a CONCURRENT same-cwd session's note (live
        # bug: 507fc4c8's InstructionsLoaded adopted the unrelated live db081e65,
        # stealing its panes). The note stays split.cmd_open's job — SessionStart
        # only, hosted sessions (see header).
        T.sid_mark(sid)
        return
    if ev == "SessionEnd":
        T.adopt_drop(cwd, sid)              # a clean end retires its own note
        return
    _maybe_adopt(d, sid, cwd)


def _maybe_adopt(d, sid, cwd):
    log = P.mirror_log(sid)
    db = P.state_db(log)
    if (os.path.exists(db) or os.path.exists(P.parked_db(log))
            or os.path.exists(db + ".keep") or not cwd):
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
        src, dst = old_db + suf, db + suf
        linked = False
        try:
            # Hardlink, not os.replace: the inode gains the NEW name while the
            # OLD one stays resolvable — a replace-then-symlink pair left a
            # window where the old path was ABSENT, which an old-key poller
            # (parked() is a bare exists) read as SessionEnd and quit on, and a
            # straggler old-key _connect filled with a fresh orphan DB.
            os.link(src, dst)
            linked = True
            moved.append(suf or "db")
        except FileNotFoundError:
            if not suf:                     # -wal/-shm may legitimately not exist
                A.error(sid, "adopt: move state db",
                        {"src": src, "dst": dst, "old": old})
        except OSError:
            A.error(sid, "adopt: move state db",
                    {"src": src, "dst": dst, "old": old})
        if not linked and os.path.exists(src):
            # The link failed with the original still in place — renaming a
            # symlink over it would DESTROY the un-adopted data, so leave it
            # (same shape the pre-atomic sequence's EEXIST swallow had).
            A.error(sid, "adopt: symlink old path",
                    {"target": dst, "link": src, "old": old})
            continue
        try:
            # Even where nothing moved (-wal/-shm may not exist), a symlink at
            # the old path routes any future write/create through to the
            # adopted DB — SQLite derives sidecar paths from the path a
            # connection was OPENED with, so an old-path connection needs all
            # three names to resolve to the new file set. The swap goes via a
            # tmp name + rename so the old path is never absent mid-flip.
            _swap_in_symlink(sid, src, dst)
        except OSError:
            A.error(sid, "adopt: symlink old path",
                    {"target": dst, "link": src, "old": old})
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


_TMP_SYMLINK_SUF = ".adopt-tmp"    # scratch name for the rename-over swap


def _swap_in_symlink(sid, src, dst):
    """Leave a symlink src → dst by creating it under a tmp name and renaming
    it over src — rename atomically replaces whatever entry is at src (the
    hardlinked original, or nothing for a missing sidecar), so at no instant is
    src absent. Removes the tmp name on failure (audited) and re-raises for
    the caller's audit."""
    tmp = src + _TMP_SYMLINK_SUF
    try:
        os.remove(tmp)                      # a crashed prior attempt's leftover
    except OSError:
        pass
    try:
        os.symlink(dst, tmp)
        os.rename(tmp, src)
    except OSError:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass
        except OSError:
            A.error(sid, "adopt: tmp symlink cleanup", {"tmp": tmp, "dst": dst})
        raise


def _retag_windows(old, sid):
    """Re-point the predecessor's pane tags at the new sid, best-effort (a
    headless fork has no windows; a dead socket just yields nothing)."""
    try:
        import frontends
        fe = frontends.get(resolve=True)
        if not fe.usable():
            return []
    except Exception:
        A.error(sid, "adopt: frontend unavailable", {"old": old})
        return []
    out = []
    for var in ("claude_session", "claude_mirror", "claude_scorebar"):
        try:
            w = fe.find_window(var, old)
            if w and fe.set_user_vars(str(w.get("id")), {var: sid}) == 0:
                out.append(var)
        except Exception:
            A.error(sid, "adopt: retag window",
                    {"var": var, "old": old, "sid": sid})
    return out
