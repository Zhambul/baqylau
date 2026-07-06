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

def sq(db, sql):
    """Query a DB read-only; first column of every row. Silent on any failure
    (missing db, lock). mode=ro so a probe can never create the state DB — its
    file-existence is the session-alive signal watchers poll."""
    if not db or not os.path.isfile(db):
        return []
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.2)
        try:
            return [r[0] for r in conn.execute(sql).fetchall()]
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
"""


def tw(sql, params=()):
    """Write against the tab DB (creates it + schema on first use); silent."""
    try:
        conn = sqlite3.connect(TABDB, timeout=0.2)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(TABDB_SCHEMA)
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def tab_get(win):
    rows = sq(TABDB, f"SELECT state FROM tab WHERE win='{win}'")
    return rows[0] if rows else ""


def tab_set(win, state):
    tw("INSERT INTO tab(win, state) VALUES(?, ?) "
       "ON CONFLICT(win) DO UPDATE SET state=excluded.state", (win, state))


def tab_clear(win):
    tw("DELETE FROM tab WHERE win=?", (win,))


def watcher_pid(kind, win):
    rows = sq(TABDB, f"SELECT pid FROM watchers WHERE kind='{kind}' AND win='{win}'")
    return rows[0] if rows else None


def watcher_set(kind, win, pid):
    tw("INSERT INTO watchers(kind, win, pid) VALUES(?, ?, ?) "
       "ON CONFLICT(kind, win) DO UPDATE SET pid=excluded.pid", (kind, win, pid))


def watcher_del(kind, win):
    tw("DELETE FROM watchers WHERE kind=? AND win=?", (kind, win))



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


