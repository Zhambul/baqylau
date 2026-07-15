# L7 — the audit oracle itself.
#
# assert_clean (tests/oracle.py) is the meta-assertion every session-flow test
# ends with; these tests prove the oracle can't rot: each canned
# anomaly section must FIRE on a synthetically poisoned audit DB, and stay
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
            "from core import audit as A\n"
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
    ("bg/fg tailer outlived the park", [
        # keep-history park at ts=100, then a bg stream ending AFTER it with a
        # non-parked reason = the zombie that recreated the state DB.
        ("INSERT INTO state_files(ts,session_id,path,action,content,script,pid)"
         " VALUES(100,?,'/x.keep','keep-history','resume',NULL,1)", (SID,)),
        ("INSERT INTO streams(session_id,kind,src_path,pid,started_at,ended_at,"
         "end_reason) VALUES(?,'bg','/x.out',1,50,200,'writer-gone')", (SID,))]),
    ("SessionEnd fired but the stop-fold never ran", [
        # SessionEnd subscriber row, NO bump-otel, NO stop-fmt decision row.
        ("INSERT INTO hook_events(ts,session_id,hook,tool_name,agent_id,"
         "handler,decision,pid,payload)"
         " VALUES(1,?,'SessionEnd','','','subscriber','',1,'{}')", (SID,))]),
    ("cross-session contamination", [
        # The same task_id streaming under two sids (both ended cleanly, so
        # only the contamination section fires).
        ("INSERT INTO streams(session_id,kind,task_id,src_path,pid,started_at,"
         "ended_at,end_reason) VALUES(?,'bg','t9','/x.out',1,1,2,'writer-gone')",
         (SID,)),
        ("INSERT INTO streams(session_id,kind,task_id,src_path,pid,started_at,"
         "ended_at,end_reason) VALUES('other-sid','bg','t9','/x.out',1,1,2,"
         "'writer-gone')", ())]),
    ("duplicated mirror ops", [
        # The same long gut op painted twice 1s apart (the re-read tailer bug).
        ("INSERT INTO ops(ts,session_id,producer,pid,op) VALUES(1,?,'x.py',1,"
         "'{\"t\": \"gut\", \"s\": \"an identical long output line the tailer"
         " re-read and painted twice\", \"c\": [1,2,3]}')", (SID,)),
        ("INSERT INTO ops(ts,session_id,producer,pid,op) VALUES(2,?,'x.py',1,"
         "'{\"t\": \"gut\", \"s\": \"an identical long output line the tailer"
         " re-read and painted twice\", \"c\": [1,2,3]}')", (SID,))]),
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


def test_parked_tailer_end_is_not_a_zombie(test_env):
    """The zombie-tailer section must stay silent for the healthy shapes: a
    bg stream that ended WITH the park reason, and one that ended BEFORE the
    keep-history park."""
    sid = "parked-clean"
    poison(test_env,
           "INSERT INTO state_files(ts,session_id,path,action,content,script,pid)"
           " VALUES(100,?,'/x.keep','keep-history','other',NULL,1)", (sid,))
    poison(test_env,
           "INSERT INTO streams(session_id,kind,src_path,pid,started_at,ended_at,"
           "end_reason) VALUES(?,'bg','/x.out',1,50,200,"
           "'state-db-parked (session end)')", (sid,))
    poison(test_env,
           "INSERT INTO streams(session_id,kind,src_path,pid,started_at,ended_at,"
           "end_reason) VALUES(?,'bg','/y.out',1,10,50,'writer-gone')", (sid,))
    counts = oracle.anomaly_counts(test_env, sid)
    n = next(v for t, v in counts.items() if "outlived the park" in t)
    assert n == 0, oracle.anomalies(test_env, sid)


def test_steal_stale_counts_as_claim(seed, session, test_env):
    """steal-stale is an ACQUISITION: a healthy steal (dead holder displaced —
    slots.py synthesizes its release-stale — then the stealer releases) must
    balance, while a stealer that LEAKS its slot must stay flagged (the old
    accounting counted the steal as a release, so steal-then-leak escaped).
    Seeded via the product API: slots.claim in subprocesses whose pids are dead
    by the next claim."""
    s = session.make()
    # Fill all 5 bg slots from one (immediately dead) process: 5 leaked claims.
    seed.py("from core import slots\n"
            "for _ in range(5): slots.claim('bg', %r)\n" % s.log)
    # Steal one and release it properly — that group must come out balanced
    # (claim + steal vs release-stale + release), leaving the other 4 flagged.
    seed.py("import os\nfrom core import slots\n"
            "idx, tok = slots.claim('bg', %r)\n"
            "assert tok, 'expected a steal, got claim-denied'\n"
            "slots.release('bg', %r, idx, os.getpid())\n" % (s.log, s.log))
    def unbalanced():
        counts = oracle.anomaly_counts(test_env, s.sid)
        return next(v for t, v in counts.items()
                    if "without a matching release" in t)
    assert unbalanced() == 4, oracle.anomalies(test_env, s.sid)
    # steal-then-leak: steal another dead-holder slot and exit WITHOUT
    # releasing. Its group is claim + release-stale + steal = 2 claims vs 1
    # release -> still flagged (the old steal-as-release accounting would have
    # read it 1 vs 2 and let the leak escape).
    seed.py("from core import slots\n"
            "idx, tok = slots.claim('bg', %r)\n"
            "assert tok, 'expected a steal, got claim-denied'\n" % s.log)
    assert unbalanced() == 4, oracle.anomalies(test_env, s.sid)
    actions = [r[3] for r in oracle.slots(test_env, s.sid)]
    assert actions.count("steal-stale") == 2
    assert actions.count("release-stale") == 2   # one synthesized per steal


# ------------------------------------------------------------- CLI dispatch

def cli(env, *args):
    return subprocess.run([sys.executable, os.path.join(REPO, "bin", "claude-audit.py"),
                           *args], env=dict(env), cwd=REPO, capture_output=True,
                          text=True, timeout=15)


def test_unknown_command_prints_usage(test_env):
    """An unrecognized (or missing) command must print REAL usage (the module
    docstring + the COMMANDS-derived command list, so it can't go stale) and
    exit 0. The old `__doc__ or …` fallback was dead: the header was a comment
    block, so users only ever saw 'see module docstring for usage'."""
    from core import audit as A
    for args in (("definitely-not-a-command",), ()):
        p = cli(test_env, *args)
        assert p.returncode == 0, p.stderr
        assert "audit trail" in p.stdout          # docstring prose made it out
        for name in A.COMMANDS:                   # every command is listed
            assert name in p.stdout, name
        assert "see module docstring" not in p.stdout


def test_sql_command_is_read_only(test_env):
    """`sql` opens the DB mode=ro: a query works, a mutation is refused, and a
    run against a missing DB never creates the file. Deliberate fixups go
    through the explicit `sql-write` command."""
    db = oracle.audit_db(test_env)
    # missing DB: refuse without creating it
    p = cli(test_env, "sql", "SELECT 1")
    assert p.returncode == 0 and "audit db unavailable" in p.stdout
    assert not os.path.exists(db)
    # seed a row through the product write path, then query + attempt mutation
    poison(test_env, "INSERT INTO sessions(session_id,project_slug,started_at)"
                     " VALUES(?, 'slug', 1)", (SID,))
    p = cli(test_env, "sql", "SELECT session_id FROM sessions")
    assert SID in p.stdout
    p = cli(test_env, "sql", "DELETE FROM sessions")
    assert "sql error" in p.stdout                      # ro connection refused it
    p = cli(test_env, "sql", "SELECT count(*) FROM sessions")
    assert "1" in p.stdout                              # row survived
    # sql-write is the sanctioned mutation path
    p = cli(test_env, "sql-write", "DELETE FROM sessions")
    assert "sql error" not in p.stdout, p.stdout
    p = cli(test_env, "sql", "SELECT count(*) FROM sessions")
    assert "0" in p.stdout


def test_swallow_set_derived_from_command_table():
    """The shim's never-fail-loudly set is WRITE_COMMANDS, derived from the one
    command table — the hand-maintained copy it replaced had already drifted."""
    from core import audit as A
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


def test_timeline_ops_and_otel_flags(test_env):
    """`timeline` excludes the high-volume ops/otel tables by default; --ops and
    --otel merge them in (each independently)."""
    poison(test_env, "INSERT INTO ops(ts,session_id,producer,pid,op)"
           " VALUES(1,?,'prod.py',1,'{\"t\": \"line\"}')", (SID,))
    poison(test_env, "INSERT INTO otel(ts,session_id,metric,query_source,model,"
           "type,value,pid) VALUES(2,?,'token','main','m','input',3.0,1)", (SID,))
    out = cli(test_env, "timeline", SID).stdout
    assert "prod.py" not in out and "token main" not in out
    out = cli(test_env, "timeline", SID, "--ops").stdout
    assert "prod.py" in out and "token main" not in out
    out = cli(test_env, "timeline", SID, "--ops", "--otel").stdout
    assert "prod.py" in out and "token main input=3.0" in out


def test_anomalies_registry_smoke(test_env):
    """Every registry entry is well-formed and its SQL executes against the
    real schema (a bad query would blind the whole triage path)."""
    import sqlite3
    from core import audit as A
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


def _fake_orphan(test_env, suffix, sid, decisions):
    """Write a claimed-spool file (spool.jsonl.<suffix>) with valid hook_events
    rows — what a drainer hard-killed between claim and remove leaves behind."""
    path = os.path.join(test_env["CLAUDE_AUDIT_DIR"], "spool.jsonl.%s" % suffix)
    os.makedirs(test_env["CLAUDE_AUDIT_DIR"], exist_ok=True)
    with open(path, "w") as f:
        for d in decisions:
            f.write(json.dumps({"table": "hook_events", "cols": {
                "ts": 1.0, "session_id": sid, "hook": "PostToolUse",
                "tool_name": "Bash", "agent_id": "", "handler": "orphan-test",
                "decision": d, "pid": 1, "payload": "{}"}}) + "\n")
    return path


def _dead_pid(start=None):
    """A pid that is certainly not a live process."""
    pid = start or (os.getpid() + 40000)
    while pid < 2 ** 22:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return pid
        except OSError:
            pass
        pid += 1
    raise RuntimeError("no dead pid found")


def test_orphaned_spool_claim_is_adopted(test_env):
    """A spool.jsonl.<dead-pid> orphan (drainer hard-killed mid-claim) must be
    adopted and drained on the next ingest pass; a claim held by a LIVE pid must
    be left alone; a double ingest must not duplicate the adopted rows."""
    dead = _dead_pid()
    _fake_orphan(test_env, dead, "orphan-sid", ["o1"])
    _fake_orphan(test_env, _dead_pid(dead + 1), "orphan-sid", ["o2"])
    live = _fake_orphan(test_env, os.getpid(), "live-sid", ["still-mine"])
    # Two ingest passes (concurrent-ish): claim-by-rename must yield each row once.
    _audit_calls(test_env, "assert A._connect() is not None\nA._ingest_spool(A._CONN)")
    _audit_calls(test_env, "assert A._connect() is not None")
    rows = oracle.q(test_env, "SELECT decision FROM hook_events"
                    " WHERE session_id='orphan-sid' ORDER BY decision")
    assert rows == [("o1",), ("o2",)], rows
    leftovers = [f for f in os.listdir(test_env["CLAUDE_AUDIT_DIR"])
                 if f.startswith("spool.jsonl.")]
    assert leftovers == [os.path.basename(live)], leftovers
    assert oracle.q(test_env, "SELECT COUNT(*) FROM hook_events"
                    " WHERE session_id='live-sid'") == [(0,)], \
        "a claim file with a LIVE pid was drained"


def test_oracle_sees_spooled_stream_end(test_env):
    """A streamer whose audit connect latched to spool-only writes its
    stream_end as a spool pseudo-row; if it is the LAST audit writer of the
    session nothing ingests it, and the row's ended_at stays NULL to any plain
    reader. The test oracle must drain the spool before answering, or every
    streams_all_ended wait times out on a session that in fact shut down
    cleanly (the f10b CI flake)."""
    _audit_calls(test_env, "sid_row = A.stream_start('/tmp/claude-mirror-spoolsid.log',"
                           " 'codex-watcher', src_path='x')")
    (stream_id,) = oracle.q(test_env, "SELECT id FROM streams"
                            " WHERE session_id='spoolsid'")[0]
    with open(os.path.join(test_env["CLAUDE_AUDIT_DIR"], "spool.jsonl"), "a") as f:
        f.write(json.dumps({"table": "stream_end", "cols": {
            "id": stream_id, "ended_at": 2.0,
            "end_reason": "state-db-parked (session end)"}}) + "\n")
    rows = oracle.q(test_env, "SELECT ended_at, end_reason FROM streams"
                    " WHERE session_id='spoolsid'")
    assert rows == [(2.0, "state-db-parked (session end)")], rows


def test_audit_disabled_still_works(run_hook, test_env, session):
    env = dict(test_env, CLAUDE_AUDIT="0")
    s = session.make()
    run_hook("claude-task-fmt.py", P.task_created(s), env=env)
    assert not os.path.exists(oracle.audit_db(test_env)), \
        "CLAUDE_AUDIT=0 must not create the audit DB"
    assert s.ops(), "handler must still work with auditing off"


# ------------------------------------- spool-row equivalence for the refactor

def _audit_calls(env, code):
    """Run audit-writer calls in a subprocess (matches production: one short-lived
    process per writer) against this test's audit dir."""
    p = subprocess.run([sys.executable, "-c",
                        "from core import audit as A\n" + code],
                       env=dict(env), cwd=REPO, capture_output=True, text=True,
                       timeout=15)
    assert p.returncode == 0, p.stderr


WRITER_SCRIPT = """
sid = A.stream_start('/tmp/claude-mirror-eqv-sid.log', 'bg', task_id='t1')
# stream_start returns None on a degraded DB; the spooled streams row is
# ingested as id 1 into the fresh DB, so target that.
A.stream_end(sid or 1, 'writer-exit', lines_emitted=7)
A.session_start({'session_id': 'eqv-sid', 'cwd': '/x',
                 'hook_event_name': 'SessionStart'})
A.session_end({'session_id': 'eqv-sid'}, 'clear')
A.ops('/tmp/claude-mirror-eqv-sid.log', [{'t': 'line', 'text': 'hello'}],
      producer='eqv-test')
A.otel('eqv-sid', [{'metric': 'claude_code.token.usage', 'query_source': 'main',
                    'model': 'm', 'type': 'input', 'value': 3.0}])
"""


def _rows(env):
    return {
        "streams": oracle.q(env, "SELECT kind, task_id, end_reason, lines_emitted,"
                            " started_at IS NOT NULL, ended_at IS NOT NULL"
                            " FROM streams WHERE session_id='eqv-sid'"),
        "sessions": oracle.q(env, "SELECT cwd, end_reason, started_at IS NOT NULL,"
                             " ended_at IS NOT NULL FROM sessions"
                             " WHERE session_id='eqv-sid'"),
        "ops": oracle.q(env, "SELECT producer, op, ts IS NOT NULL FROM ops"
                        " WHERE session_id='eqv-sid'"),
        "otel": oracle.q(env, "SELECT metric, query_source, model, type, value,"
                         " ts IS NOT NULL FROM otel WHERE session_id='eqv-sid'"),
    }


def test_writers_direct_row_shapes(test_env):
    """The rerouted writers (stream_end/session_end via event(), ops/otel via
    _event_many) still land the exact row shapes on a healthy DB."""
    _audit_calls(test_env, WRITER_SCRIPT)
    r = _rows(test_env)
    assert r["streams"] == [("bg", "t1", "writer-exit", 7, 1, 1)]
    assert r["sessions"] == [("/x", "clear", 1, 1)]
    assert r["ops"] == [("eqv-test", '{"t": "line", "text": "hello"}', 1)]
    assert r["otel"] == [("claude_code.token.usage", "main", "m", "input", 3.0, 1)]


def test_spool_replay_produces_identical_rows(test_env):
    """DB unusable -> every writer spools; ingest must yield the same rows as the
    direct path — in particular sessions/streams rows must NOT grow a stray `ts`
    (they carry their own time columns; OWN_TS_TABLES)."""
    db = oracle.audit_db(test_env)
    # Create then corrupt the DB so writers spool.
    _audit_calls(test_env, "A._connect()")
    with open(db, "wb") as f:
        f.write(b"this is not a sqlite database at all........")
    _audit_calls(test_env, WRITER_SCRIPT)
    spool = os.path.join(test_env["CLAUDE_AUDIT_DIR"], "spool.jsonl")
    assert os.path.exists(spool)
    with open(spool) as f:
        recs = [json.loads(l) for l in f]
    by_table = {}
    for r in recs:
        by_table.setdefault(r["table"], []).append(r["cols"])
    for t in ("streams", "stream_end", "sessions", "session_end", "ops", "otel"):
        assert t in by_table, "writer did not spool a %s record: %s" % (t, by_table)
    for t in ("streams", "stream_end", "sessions", "session_end"):
        for cols in by_table[t]:
            assert "ts" not in cols, "%s spool row grew a ts column: %s" % (t, cols)
    # DB becomes creatable again -> next open ingests the spool.
    os.remove(db)
    _audit_calls(test_env, "assert A._connect() is not None")
    assert not os.path.exists(spool) or os.path.getsize(spool) == 0
    r = _rows(test_env)
    assert r["streams"] == [("bg", "t1", "writer-exit", 7, 1, 1)]
    assert r["sessions"] == [("/x", "clear", 1, 1)]
    assert r["ops"] == [("eqv-test", '{"t": "line", "text": "hello"}', 1)]
    assert r["otel"] == [("claude_code.token.usage", "main", "m", "input", 3.0, 1)]


# ------------------------------------------------------------- prune coverage

def test_prunable_tables_cover_schema():
    """prune()'s table list is derived from _SCHEMA; the only tables allowed to
    escape the generic session_id/ts sweep are the documented specials. Adding
    an audit table without classifying it must fail here."""
    import re
    from core import audit as A
    tables = A.schema_tables()
    assert set(A._PRUNE_SPECIAL) == {"sessions", "streams"}
    assert set(A.prunable_tables()) == set(tables) - set(A._PRUNE_SPECIAL)
    # Every prunable table must actually have the columns the sweep keys on.
    for t in A.prunable_tables():
        body = re.search(r"CREATE TABLE IF NOT EXISTS %s\((.*?)\);" % t,
                         A._SCHEMA, re.S).group(1)
        assert "session_id" in body and "ts REAL" in body, \
            "%s lacks the session_id/ts pruning keys" % t
    # And the specials are special for the reason documented.
    streams = re.search(r"CREATE TABLE IF NOT EXISTS streams\((.*?)\);",
                        A._SCHEMA, re.S).group(1)
    assert "ts" not in streams.replace("started_at", "").replace("ended_at", "")


def test_prune_round_trip(test_env):
    """Seed every audit table for an old ended session, an old orphan sid (no
    sessions row), and a fresh session; prune() must delete exactly the old
    rows from every table and leave the fresh ones."""
    from core import audit as A
    old, orphan, fresh = "prune-old", "prune-orphan", "prune-fresh"
    poison(test_env, "INSERT INTO sessions(session_id, started_at, ended_at)"
           " VALUES(?, 1, 2)", (old,))
    poison(test_env, "INSERT INTO sessions(session_id, started_at)"
           " VALUES(?, 9e12)", (fresh,))
    for sid, ts in ((old, 1), (orphan, 1), (fresh, 9e12)):
        for t in A.prunable_tables():
            if t == "spawns":
                # child_pid must be a real-typed (nonexistent) pid: the reaper
                # fixture kills every pid it finds in this table.
                poison(test_env, "INSERT INTO spawns(ts, session_id, child_pid)"
                       " VALUES(?, ?, 999999999)", (ts, sid))
            else:
                poison(test_env, "INSERT INTO %s(ts, session_id) VALUES(?, ?)"
                       % t, (ts, sid))
        poison(test_env, "INSERT INTO streams(session_id, started_at)"
               " VALUES(?, ?)", (sid, ts))
    _audit_calls(test_env, "assert A.prune(30) == 1")
    for t in A.prunable_tables() + ["streams", "sessions"]:
        left = [r[0] for r in oracle.q(
            test_env, "SELECT session_id FROM %s" % t)]
        assert left == [fresh], "%s after prune: %s" % (t, left)
