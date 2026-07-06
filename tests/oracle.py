# oracle.py — assertion helpers over the audit DB (and the tab DB).
#
# The audit trail is the project's own always-on flight recorder; these helpers
# make it the test suite's oracle too. `assert_clean` runs the SAME 13 canned
# anomaly queries the audit-debug skill starts from — a healthy synthetic
# session must come out clean, exactly like a healthy real one.
import os
import re
import sqlite3
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def audit_db(env):
    return os.path.join(env["CLAUDE_AUDIT_DIR"], "audit.db")


def q(env, sql, args=()):
    """Read-only query against this test's audit DB ([] if it doesn't exist)."""
    path = audit_db(env)
    if not os.path.exists(path):
        return []
    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=5)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def hook_events(env, sid, handler=None):
    sql = "SELECT hook, tool_name, handler, decision FROM hook_events WHERE session_id=?"
    rows = q(env, sql, (sid,))
    return [r for r in rows if handler is None or r[2] == handler]


def decisions(env, sid, handler=None):
    return [r[3] or "" for r in hook_events(env, sid, handler)]


def errors(env, sid=None):
    if sid is None:
        return q(env, "SELECT session_id, script, func, context FROM errors")
    return q(env, "SELECT session_id, script, func, context FROM errors"
                  " WHERE session_id=?", (sid,))


def transitions(env, sid):
    return q(env, "SELECT dispatch, prev_state, new_state, applied, reason"
                  " FROM tab_transitions WHERE session_id=? ORDER BY id", (sid,))


def streams(env, sid):
    return q(env, "SELECT kind, end_reason, ended_at, src_path FROM streams"
                  " WHERE session_id=? ORDER BY id", (sid,))


def spawns(env, sid=None):
    sql = "SELECT parent_script, child_pid, purpose FROM spawns"
    return q(env, sql + " WHERE session_id=?", (sid,)) if sid else q(env, sql)


def state_files(env, sid):
    return q(env, "SELECT path, action, content FROM state_files"
                  " WHERE session_id=? ORDER BY id", (sid,))


def slots(env, sid):
    return q(env, "SELECT kind, slot_n, agent_id, action FROM slots"
                  " WHERE session_id=? ORDER BY id", (sid,))


# ------------------------------------------------------------- anomalies CLI

def anomalies(env, sid):
    """Run the product's own canned invariant queries; returns the raw text."""
    p = subprocess.run(
        [sys.executable, os.path.join(REPO, "claude_audit.py"), "anomalies", sid],
        capture_output=True, text=True, env=dict(env), timeout=30, cwd=REPO)
    assert p.returncode == 0, p.stderr
    return p.stdout


def anomaly_counts(env, sid):
    """{section title: row count} parsed from the anomalies output."""
    out = anomalies(env, sid)
    return {m.group(1).strip(): int(m.group(2))
            for m in re.finditer(r"^== (.+?): (\d+)", out, re.M)}


def assert_clean(env, sid, allow=()):
    """Every anomaly section must be 0 except the explicitly allowed ones.
    `allow` entries are substring-matched against section titles."""
    counts = anomaly_counts(env, sid)
    assert counts, "anomalies produced no sections — audit DB missing?"
    dirty = {t: n for t, n in counts.items()
             if n and not any(a in t for a in allow)}
    assert not dirty, "anomalies not clean for %s: %s\n\n%s" % (
        sid, dirty, anomalies(env, sid))


# ----------------------------------------------------------------- tab DB

def tab_state(env, win):
    """Current colour-state row for a window in this test's tab DB."""
    path = os.path.join(env["CLAUDE_MIRROR_TMPDIR"], "claude-kitty-tab.db")
    if not os.path.exists(path):
        return None
    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=5)
    try:
        row = conn.execute("SELECT state FROM tab WHERE win=?", (str(win),)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()
