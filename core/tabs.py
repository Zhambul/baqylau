# core/tabs.py — the tab-state vocabulary + the global window-keyed tab DB.
#
# The SEMANTIC tab states (what colour means what), the hex colour table every
# frontend paints from, and the /tmp tab DB (shown-state per window + watcher
# pid locks) are core: any host-tool plugin drives them and any terminal
# frontend renders them. The DISPATCH logic that maps one tool's signals onto
# these states lives with that tool's plugin (plugins/claude_code/tabstatus.py
# for Claude Code hooks); claude_state.tab_state() is the one sanctioned
# cross-module READER of the DB this module owns.
import os
import sqlite3
import time

from core import paths as P

# The literal tab states (also the vocabulary of the tab DB's `state` column and
# the hooks' argv) — constants so an internal typo is a NameError, not a silently
# never-matching transition. The mapping to colours is COLORS below.
IDLE       = "idle"
THINKING   = "thinking"
WORKING    = "working"
EXECUTING  = "executing"
AWAITING_BG       = "awaiting-bg"
AWAITING_COMMAND  = "awaiting-command"      # red — Claude is asking YOU
AWAITING_RESPONSE = "awaiting-response"     # green — done, your turn


# --- read-only sqlite (never creates a DB whose absence is a liveness signal) ----

def sq(db, sql, params=()):
    """Query a DB read-only; first column of every row. Silent on any failure
    (missing db, lock). mode=ro so a probe can never create the state DB — its
    file-existence is the session-alive signal watchers poll."""
    if not db or not os.path.isfile(db):
        return []
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.2)
        try:
            return [r[0] for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


# --- global tab DB -------------------------------------------------------------
# The per-window shown-state + the per-window watcher pid locks live in ONE global
# runtime DB. Window-keyed — a kitty window id is unique per kitty instance and
# outlives any one session — so this is deliberately NOT the per-session state DB.
# In /tmp so it self-clears on reboot.
TABDB = P.TAB_DB
TABDB_SCHEMA = """
CREATE TABLE IF NOT EXISTS tab(win TEXT PRIMARY KEY, state TEXT);
CREATE TABLE IF NOT EXISTS watchers(kind TEXT, win TEXT, pid INTEGER, PRIMARY KEY(kind, win));
CREATE TABLE IF NOT EXISTS sids(sid TEXT PRIMARY KEY, ts REAL);
CREATE TABLE IF NOT EXISTS adopt_pending(cwd TEXT PRIMARY KEY, sid TEXT, ts REAL);
"""


def tw(sql, params=()):
    """Write against the tab DB (creates it + schema on first use); silent."""
    twc(sql, params)


def twc(sql, params=()):
    """Like tw() but returns the statement's rowcount (-1 on any failure) — the
    take-once primitive adopt_take needs it (DELETE … → did WE delete it?)."""
    try:
        conn = sqlite3.connect(TABDB, timeout=0.2)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(TABDB_SCHEMA)
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()
    except Exception:
        return -1


def tab_get(win):
    rows = sq(TABDB, "SELECT state FROM tab WHERE win=?", (win,))
    return rows[0] if rows else ""


def tab_set(win, state):
    tw("INSERT INTO tab(win, state) VALUES(?, ?) "
       "ON CONFLICT(win) DO UPDATE SET state=excluded.state", (win, state))


def tab_clear(win):
    tw("DELETE FROM tab WHERE win=?", (win,))


def watcher_pid(kind, win):
    rows = sq(TABDB, "SELECT pid FROM watchers WHERE kind=? AND win=?", (kind, win))
    return rows[0] if rows else None


def watcher_set(kind, win, pid):
    tw("INSERT INTO watchers(kind, win, pid) VALUES(?, ?, ?) "
       "ON CONFLICT(kind, win) DO UPDATE SET pid=excluded.pid", (kind, win, pid))


def watcher_del(kind, win):
    tw("DELETE FROM watchers WHERE kind=? AND win=?", (kind, win))


# --- session registry (sid-fork adoption — plugins/claude_code/adopt.py) ---------
# Claude Code can FORK a session id mid-flight: on --resume (SessionStart fires
# with the OLD sid while every subsequent event carries a NEW sid that never
# gets its own SessionStart) and on BACKGROUNDING a session (the conversation
# continues under the background-job id, again with no SessionStart). `sids`
# records every sid whose SessionStart we actually saw (so an unknown-sid event
# is distinguishable from a headless/agents-view session that legitimately
# started); `adopt_pending` is the take-once note every HOSTED SessionStart
# leaves (written by split.cmd_open once the pane+DB really exist, keyed by
# cwd) that the fork's first event consumes to find its predecessor. Both live
# here because this is the one GLOBAL runtime DB every hook process can reach.

def sid_mark(sid):
    """Record that this sid had a real SessionStart (prunes 30-day-old rows)."""
    if not sid:
        return
    tw("INSERT OR REPLACE INTO sids(sid, ts) VALUES(?, ?)", (sid, time.time()))
    tw("DELETE FROM sids WHERE ts < ?", (time.time() - 30 * 86400,))


def sid_seen(sid):
    return bool(sq(TABDB, "SELECT 1 FROM sids WHERE sid=?", (sid,)))


def adopt_note(cwd, sid):
    """A HOSTED session (pane + state DB) started for `sid` in `cwd` — the
    candidate predecessor if an unknown sid shows up there next (keyed by cwd:
    a newer hosted SessionStart in the same project supersedes the note)."""
    tw("INSERT OR REPLACE INTO adopt_pending(cwd, sid, ts) VALUES(?, ?, ?)",
       (cwd, sid, time.time()))


def adopt_peek(cwd):
    rows = sq(TABDB, "SELECT sid FROM adopt_pending WHERE cwd=?", (cwd,))
    return rows[0] if rows else ""


def adopt_take(cwd, sid):
    """Consume the note — take-once, so concurrent hook processes racing to
    adopt the same predecessor see exactly one winner."""
    return twc("DELETE FROM adopt_pending WHERE cwd=? AND sid=?",
               (cwd, sid)) == 1


def adopt_drop(cwd, sid):
    adopt_take(cwd, sid)



COLORS = {
    IDLE:              ("#5c6370", "#e6e9ef", "#33373f"),  # grey  — ready, nothing running
    # thinking + working are merged: there's no signal to tell reasoning apart
    # from non-shell tool use / reply-writing, so both are one "busy" colour.
    THINKING:          ("#c678dd", "#1a0620", "#4a2b52"),  # magenta — Claude busy
    WORKING:           ("#c678dd", "#1a0620", "#4a2b52"),
    # blue — a command is running: a foreground shell command (executing), or a
    # background command / monitor Claude is awaiting (awaiting-bg). Same colour.
    EXECUTING:         ("#61afef", "#06121f", "#2c4a63"),
    AWAITING_BG:       ("#61afef", "#06121f", "#2c4a63"),
    AWAITING_COMMAND:  ("#e06c75", "#2a0608", "#5e2d31"),  # red — Claude is asking you
    AWAITING_RESPONSE: ("#98c379", "#07180a", "#445733"),  # green — done, your turn
}


