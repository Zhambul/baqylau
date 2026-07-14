# L7 — the audit oracle itself.
#
# assert_clean (tests/oracle.py) is the meta-assertion every session-flow test
# ends with; these tests prove the oracle can't rot: each of the 13 canned
# anomaly sections must FIRE on a synthetically poisoned audit DB, and stay
# silent on a healthy one. Plus the audit's own degradation paths (spool
# fallback, CLAUDE_AUDIT=0).
import json
import os
import subprocess
import sys

import pytest

import oracle
import payloads as P
from conftest import REPO


def poison(env, sql, args=()):
    """Insert a row through the product's own audit connection (so the schema
    exists and the write path matches production)."""
    code = ("import json, sys\n"
            "import claude_audit as A\n"
            "c = A._connect()\n"
            "assert c is not None, 'audit db unavailable'\n"
            "c.execute(sys.argv[1], json.loads(sys.argv[2]))\n"
            "c.commit()\n")
    p = subprocess.run([sys.executable, "-c", code, sql, json.dumps(list(args))],
                       env=dict(env), cwd=REPO, capture_output=True, text=True,
                       timeout=15)
    assert p.returncode == 0, p.stderr


SID = "poisoned-session"

# (section substring, [(sql, args), ...]) — one poison per canned anomaly query.
BUG_SHAPES = [
    ("swallowed errors", [
        ("INSERT INTO errors(ts,session_id,script,func,traceback,context,pid)"
         " VALUES(1,?,'x.py','boom','Traceback...','{}',1)", (SID,))]),
    ("streams that never ended", [
        ("INSERT INTO streams(session_id,kind,src_path,pid,started_at)"
         " VALUES(?,'bg','/x.out',1,1)", (SID,))]),
    ("slot claims without a matching release", [
        ("INSERT INTO slots(ts,session_id,kind,slot_n,agent_id,owner_pid,action)"
         " VALUES(1,?,'bg',0,'',1,'claim')", (SID,))]),
    ("tab left on a busy colour", [
        ("INSERT INTO tab_transitions(ts,session_id,window_id,dispatch,"
         "prev_state,new_state,applied,reason,pid)"
         " VALUES(1,?,'9','stop','','working',1,'x',1)", (SID,))]),
    ("duplicate SubagentStart", [
        ("INSERT INTO hook_events(ts,session_id,hook,tool_name,agent_id,"
         "handler,decision,pid,payload)"
         " VALUES(1,?,'SubagentStart','','a1','h','',1,'{}')", (SID,)),
        ("INSERT INTO hook_events(ts,session_id,hook,tool_name,agent_id,"
         "handler,decision,pid,payload)"
         " VALUES(2,?,'SubagentStart','','a1','h','',1,'{}')", (SID,)),
        ("INSERT INTO hook_events(ts,session_id,hook,tool_name,agent_id,"
         "handler,decision,pid,payload)"
         " VALUES(3,?,'SubagentStop','','a1','h','',1,'{}')", (SID,))]),
    ("SubagentStart without SubagentStop", [
        ("INSERT INTO hook_events(ts,session_id,hook,tool_name,agent_id,"
         "handler,decision,pid,payload)"
         " VALUES(1,?,'SubagentStart','','a2','h','',1,'{}')", (SID,))]),
    ("failed tools", [
        ("INSERT INTO hook_events(ts,session_id,hook,tool_name,agent_id,"
         "handler,decision,pid,payload)"
         " VALUES(1,?,'PostToolUseFailure','Bash','','h','',1,'{}')", (SID,))]),
    ("spawned processes that never registered a stream", [
        ("INSERT INTO spawns(ts,session_id,parent_script,child_pid,argv,purpose)"
         " VALUES(1,?,'x.py',999999,'[]','stream:bg tail')", (SID,))]),
    ("pane operations that failed", [
        ("INSERT INTO pane_events(ts,session_id,action,ok,detail,pid)"
         " VALUES(1,?,'open',0,'launch failed',1)", (SID,))]),
    ("tab colour applies where kitten @ failed", [
        ("INSERT INTO tab_transitions(ts,session_id,window_id,dispatch,"
         "prev_state,new_state,applied,reason,pid)"
         " VALUES(1,?,'9','stop','','awaiting-response',0,"
         "'stop — kitten @ failed rc=1 — state row unchanged',1)", (SID,))]),
    ("resume that lost its mirror history", [
        ("INSERT INTO hook_events(ts,session_id,hook,tool_name,agent_id,"
         "handler,decision,pid,payload)"
         " VALUES(1000,?,'SessionStart','','','h','',1,"
         "'{\"source\":\"resume\"}')", (SID,)),
        ("INSERT INTO state_files(ts,session_id,path,action,content,script,pid)"
         " VALUES(1001,?,'/x.log','fresh-db','',NULL,1)", (SID,))]),
    ("unattributed token/cost bumps", [
        ("INSERT INTO state_files(ts,session_id,path,action,content,script,pid)"
         " VALUES(1,?,'/x.log','bump','{\"deltas\":{\"tokens\":5}}',NULL,1)",
         (SID,))]),
    ("SessionEnd transcript fallback fired despite OTEL data", [
        # A SessionEnd fallback fold decision AND a bump-otel row for the same
        # session = the otel_seen gate broke and cost was double-counted.
        ("INSERT INTO hook_events(ts,session_id,hook,tool_name,agent_id,"
         "handler,decision,pid,payload)"
         " VALUES(1,?,'SessionEnd','','','claude-stop-fmt.py',"
         "'otel absent — folded transcript fallback; tokens=5 cost=0.1',1,'{}')", (SID,)),
        ("INSERT INTO state_files(ts,session_id,path,action,content,script,pid)"
         " VALUES(1,?,'/x.log','bump-otel','{\"deltas\":{\"cost\":0.1}}',NULL,1)",
         (SID,))]),
]


@pytest.mark.parametrize("section,rows", BUG_SHAPES,
                         ids=[b[0][:38].replace(" ", "-") for b in BUG_SHAPES])
def test_each_bug_shape_fires(test_env, section, rows):
    for sql, args in rows:
        poison(test_env, sql, args)
    counts = oracle.anomaly_counts(test_env, SID)
    hits = {t: n for t, n in counts.items() if section in t}
    assert hits and any(n > 0 for n in hits.values()), \
        "poisoned %r but its section stayed clean: %s" % (section, counts)
    with pytest.raises(AssertionError):
        oracle.assert_clean(test_env, SID)


def test_healthy_session_is_clean(run_hook, test_env, session):
    """A benign run of real handlers must come out of anomalies spotless."""
    s = session.make()
    s.add_assistant("msg_1")
    run_hook("claude-file-fmt.py", P.post_file(s, tool="Edit"))
    run_hook("claude-task-fmt.py", P.task_created(s))
    run_hook("claude-stop-fmt.py", P.stop(s))
    oracle.assert_clean(test_env, s.sid)


def test_assert_clean_allowlist(test_env):
    poison(test_env,
           "INSERT INTO errors(ts,session_id,script,func,traceback,context,pid)"
           " VALUES(1,?,'x.py','boom','tb','{}',1)", (SID,))
    with pytest.raises(AssertionError):
        oracle.assert_clean(test_env, SID)
    oracle.assert_clean(test_env, SID, allow=("swallowed errors",))


# ------------------------------------------------------------- CLI dispatch

def cli(env, *args):
    return subprocess.run([sys.executable, os.path.join(REPO, "claude_audit.py"),
                           *args], env=dict(env), cwd=REPO, capture_output=True,
                          text=True, timeout=15)


def test_unknown_command_prints_usage(test_env):
    """An unrecognized (or missing) command must no-op with usage, exit 0."""
    for args in (("definitely-not-a-command",), ()):
        p = cli(test_env, *args)
        assert p.returncode == 0, p.stderr
        assert "usage" in p.stdout or "docstring" in p.stdout


def test_swallow_set_derived_from_command_table():
    """The shim's never-fail-loudly set is WRITE_COMMANDS, derived from the one
    command table — the hand-maintained copy it replaced had already drifted."""
    import claude_audit as A
    assert A.WRITE_COMMANDS == {n for n, (_, w) in A.COMMANDS.items() if w}
    for name, (fn, write) in A.COMMANDS.items():
        assert callable(fn), name
        assert isinstance(write, bool), name
    # the known hook-fired write entry points are all swallowed …
    assert {"session-start", "session-end", "hook", "transition",
            "error", "pane", "state-file"} <= A.WRITE_COMMANDS
    # … and the interactive read/query commands are NOT
    assert not ({"sessions", "timeline", "errors", "anomalies", "otel",
                 "sql", "prune"} & A.WRITE_COMMANDS)
    # stream-start/stream-end were removed: streamers audit in-process only
    assert "stream-start" not in A.COMMANDS and "stream-end" not in A.COMMANDS


def test_write_command_swallows_argv_garbage(test_env):
    """A write entry point with broken argv must exit 0 (hooks fire these)."""
    p = cli(test_env, "transition")          # every positional arg missing
    assert p.returncode == 0, p.stderr


def test_anomalies_registry_smoke(test_env):
    """Every registry entry is well-formed and its SQL executes against the
    real schema (a bad query would blind the whole triage path)."""
    import sqlite3
    import claude_audit as A
    poison(test_env,                # force schema creation via the product path
           "INSERT INTO errors(ts,session_id,script,func,traceback,context,pid)"
           " VALUES(1,?,'x.py','f','tb','{}',1)", (SID,))
    conn = sqlite3.connect(oracle.audit_db(test_env))
    try:
        for entry in A.ANOMALY_SECTIONS:
            if callable(entry):
                continue
            title, sql, nparams = entry
            assert isinstance(title, str) and title, entry
            assert nparams == sql.count("?"), title
            conn.execute(sql, (SID,) * nparams).fetchall()   # must not raise
    finally:
        conn.close()


# ------------------------------------------------------- degradation paths

def test_spool_fallback_and_ingest(run_hook, test_env, session):
    """DB unusable -> rows land in spool.jsonl; next usable open ingests them."""
    s = session.make()
    db = oracle.audit_db(test_env)
    with open(db, "wb") as f:                       # corrupt: not a sqlite file
        f.write(b"this is not a sqlite database at all........")
    run_hook("claude-task-fmt.py", P.task_created(s, "9", "Spooled"))
    spool = os.path.join(test_env["CLAUDE_AUDIT_DIR"], "spool.jsonl")
    assert os.path.exists(spool), "no spool file while the DB was unusable"
    with open(spool) as f:
        assert any(json.loads(l).get("table") == "hook_events" for l in f)
    os.remove(db)                                    # DB becomes creatable again
    run_hook("claude-task-fmt.py", P.task_created(s, "10", "Direct"))
    assert not os.path.exists(spool) or os.path.getsize(spool) == 0, \
        "spool was not ingested on the next successful open"
    hooks = [r[3] for r in oracle.hook_events(test_env, s.sid)]
    assert len(hooks) >= 2, "spooled row lost during ingest: %s" % hooks


def test_audit_disabled_still_works(run_hook, test_env, session):
    env = dict(test_env, CLAUDE_AUDIT="0")
    s = session.make()
    run_hook("claude-task-fmt.py", P.task_created(s), env=env)
    assert not os.path.exists(oracle.audit_db(test_env)), \
        "CLAUDE_AUDIT=0 must not create the audit DB"
    assert s.ops(), "handler must still work with auditing off"
