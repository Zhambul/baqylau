# core/audit.py — always-on SQLite audit trail for the mirror tooling.
# (Importable as `claude_audit` via the top-level compat shim, which is also
# the CLI entry point: `python3 claude_audit.py sessions|anomalies|…`.)
#
# The mirror is ~20 short-lived hook processes plus detached tailers/watchers
# coordinating through /tmp marker files, sidecars, and sentinels — and almost every
# failure is swallowed (`except Exception: pass`, `2>/dev/null`), so when a tab
# sticks blue or a block never closes, the evidence evaporates with the processes.
# This module records everything durable enough to chase a bug after the fact:
#
#   sessions        one row per Claude session (the anchor everything joins to)
#   hook_events     every hook invocation: full stdin payload + the handler's decision
#   tab_transitions the tab-colour state machine (replaces the old CLAUDE_TAB_DEBUG logs)
#   slots           every marker-file claim/release (the mechanism behind stuck colours)
#   streams         lifecycle of every detached tailer/streamer/watcher
#   ops             every paint op appended to the mirror log (full pane reconstruction)
#   errors          every swallowed exception, with traceback + context
#   spawns          every detached process launch
#   state_files     writes/removals of coordination files (.done sentinels, .fg-live, …)
#
# ON by default; set CLAUDE_AUDIT=0 to turn it off (every call becomes a no-op).
# The DB lives OUTSIDE /tmp (session artifacts there are deleted at SessionEnd) in
# $CLAUDE_AUDIT_DIR or ~/.claude/kitty-audit/audit.db — one global DB, all sessions,
# WAL mode so the many concurrent short-lived writers never block each other. Audit
# failures NEVER propagate to callers: a failed write degrades to an append-only
# spool (spool.jsonl) that is re-ingested on the next successful open, so auditing
# can neither lose evidence nor break a hook.
#
# CLI (what the audit-debug skill drives):
#   claude_audit.py sessions [N]          recent sessions
#   claude_audit.py timeline <sid>        merged chronological event timeline
#   claude_audit.py errors <sid>          swallowed exceptions for a session
#   claude_audit.py anomalies <sid>       canned queries for known bug signatures
#   claude_audit.py sql "<query>"         free-form read-only SQL
#   claude_audit.py prune [days]          drop sessions older than N days (default 30)
#   claude_audit.py session-start|session-end|hook <handler>|transition …
#                                         write entry points for the shell scripts
import json, os, re, sys, time, traceback

from core import paths as P    # the one owner of the mirror-log path format

_CONN = None            # cached per-process connection (streamers are long-lived)
_FAILED = False         # sqlite gave up this process -> spool only, don't retry each call

PRUNE_DAYS = 30


def enabled():
    return (os.environ.get("CLAUDE_AUDIT", "1") or "1") != "0"


def audit_dir():
    d = (os.environ.get("CLAUDE_AUDIT_DIR") or "").strip()
    return d or os.path.expanduser("~/.claude/kitty-audit")


def db_path():
    return os.path.join(audit_dir(), "audit.db")


def spool_path():
    return os.path.join(audit_dir(), "spool.jsonl")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  session_id TEXT PRIMARY KEY, cwd TEXT, project_slug TEXT, transcript_path TEXT,
  mirror_log TEXT, kitty_window_id TEXT, started_at REAL, ended_at REAL,
  end_reason TEXT, env TEXT);
CREATE TABLE IF NOT EXISTS hook_events(
  id INTEGER PRIMARY KEY, ts REAL, session_id TEXT, hook TEXT, tool_name TEXT,
  agent_id TEXT, handler TEXT, decision TEXT, pid INTEGER, duration_ms REAL,
  payload TEXT);
CREATE TABLE IF NOT EXISTS tab_transitions(
  id INTEGER PRIMARY KEY, ts REAL, session_id TEXT, window_id TEXT, dispatch TEXT,
  prev_state TEXT, new_state TEXT, applied INTEGER, reason TEXT, pid INTEGER);
CREATE TABLE IF NOT EXISTS slots(
  id INTEGER PRIMARY KEY, ts REAL, session_id TEXT, kind TEXT, slot_n INTEGER,
  agent_id TEXT, owner_pid INTEGER, action TEXT, marker_path TEXT);
CREATE TABLE IF NOT EXISTS streams(
  id INTEGER PRIMARY KEY, session_id TEXT, kind TEXT, agent_id TEXT, task_id TEXT,
  src_path TEXT, pid INTEGER, started_at REAL, ended_at REAL, end_reason TEXT,
  lines_emitted INTEGER);
CREATE TABLE IF NOT EXISTS ops(
  id INTEGER PRIMARY KEY, ts REAL, session_id TEXT, producer TEXT, pid INTEGER,
  op TEXT);
CREATE TABLE IF NOT EXISTS errors(
  id INTEGER PRIMARY KEY, ts REAL, session_id TEXT, script TEXT, func TEXT,
  traceback TEXT, context TEXT, pid INTEGER);
CREATE TABLE IF NOT EXISTS spawns(
  id INTEGER PRIMARY KEY, ts REAL, session_id TEXT, parent_script TEXT,
  child_pid INTEGER, argv TEXT, purpose TEXT);
CREATE TABLE IF NOT EXISTS state_files(
  id INTEGER PRIMARY KEY, ts REAL, session_id TEXT, path TEXT, action TEXT,
  content TEXT, script TEXT, pid INTEGER);
CREATE TABLE IF NOT EXISTS pane_events(
  id INTEGER PRIMARY KEY, ts REAL, session_id TEXT, action TEXT, ok INTEGER,
  detail TEXT, pid INTEGER);
CREATE TABLE IF NOT EXISTS otel(
  id INTEGER PRIMARY KEY, ts REAL, session_id TEXT, metric TEXT, query_source TEXT,
  model TEXT, type TEXT, value REAL, pid INTEGER);
CREATE INDEX IF NOT EXISTS ix_hook_sid   ON hook_events(session_id, ts);
CREATE INDEX IF NOT EXISTS ix_tab_sid    ON tab_transitions(session_id, ts);
CREATE INDEX IF NOT EXISTS ix_slot_sid   ON slots(session_id, ts);
CREATE INDEX IF NOT EXISTS ix_stream_sid ON streams(session_id, started_at);
CREATE INDEX IF NOT EXISTS ix_ops_sid    ON ops(session_id, ts);
CREATE INDEX IF NOT EXISTS ix_err_sid    ON errors(session_id, ts);
CREATE INDEX IF NOT EXISTS ix_spawn_sid  ON spawns(session_id, ts);
CREATE INDEX IF NOT EXISTS ix_state_sid  ON state_files(session_id, ts);
CREATE INDEX IF NOT EXISTS ix_pane_sid   ON pane_events(session_id, ts);
CREATE INDEX IF NOT EXISTS ix_otel_sid   ON otel(session_id, ts);
"""


def _connect():
    """Open (and cache) the audit DB, creating the schema on first use. Returns None
    when auditing is off or sqlite is unusable (callers then spool)."""
    global _CONN, _FAILED
    if not enabled() or _FAILED:
        return None
    if _CONN is not None:
        return _CONN
    try:
        import sqlite3
        os.makedirs(audit_dir(), exist_ok=True)
        conn = sqlite3.connect(db_path(), timeout=3.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=3000")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA)
        conn.commit()
        _CONN = conn
        _ingest_spool(conn)
        return conn
    except Exception:
        _FAILED = True
        # Record why the auditor itself couldn't open the DB — lands in `errors`
        # when the spool is next ingested, so audit outages are visible too.
        _spool("errors", {"ts": time.time(), "session_id": "", "script": _script(),
                          "func": "_connect", "traceback": traceback.format_exc(),
                          "context": "", "pid": os.getpid()})
        return None


def _spool(table, cols):
    """Fallback when sqlite is unavailable: append the row to a plain JSONL spool,
    re-ingested by the next process that opens the DB successfully."""
    try:
        os.makedirs(audit_dir(), exist_ok=True)
        with open(spool_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps({"table": table, "cols": cols}, ensure_ascii=False,
                               default=str) + "\n")
    except Exception:
        pass


def _ingest_spool(conn):
    # Drain spool.jsonl into the DB under an exclusive rename so two processes
    # opening at once don't double-ingest.
    p = spool_path()
    if not os.path.exists(p):
        return
    claimed = p + f".{os.getpid()}"
    try:
        os.rename(p, claimed)
    except OSError:
        return                              # another process claimed it
    try:
        with open(claimed, encoding="utf-8") as f:
            for ln in f:
                try:
                    o = json.loads(ln)
                    _insert(conn, o["table"], o["cols"])
                except Exception:
                    continue
        conn.commit()
        os.remove(claimed)
    except Exception:
        try:
            os.rename(claimed, p)           # put it back for a later attempt
        except Exception:
            pass


def _insert(conn, table, cols):
    # "stream_end" is a pseudo-table used by the spool: a streamer that couldn't
    # reach the DB at exit spools its end as this record, and ingest applies it as
    # the UPDATE it stands for — otherwise the stream would look "never ended"
    # forever (a false positive in `anomalies`).
    if table == "stream_end":
        return conn.execute(
            "UPDATE streams SET ended_at=?, end_reason=?, lines_emitted=? WHERE id=?",
            (cols.get("ended_at") or time.time(), cols.get("end_reason"),
             cols.get("lines_emitted"), cols.get("id")))
    # "session_end" is the same idea for the sessions row (a locked DB at
    # SessionEnd otherwise leaves the session "(open)" forever).
    if table == "session_end":
        return conn.execute(
            "UPDATE sessions SET ended_at=?, end_reason=? WHERE session_id=?",
            (cols.get("ended_at") or time.time(), cols.get("end_reason"),
             cols.get("session_id")))
    keys = list(cols.keys())
    sql = (f"INSERT INTO {table}({','.join(keys)}) "
           f"VALUES({','.join('?' * len(keys))})")
    return conn.execute(sql, [cols[k] for k in keys])


def event(table, **cols):
    """Insert one row; never raises. Returns the new rowid (or None). Falls back to
    the spool when sqlite can't be written."""
    if not enabled():
        return None
    cols.setdefault("ts", time.time())
    conn = _connect()
    if conn is None:
        cols.pop("ts", None) if table in ("sessions", "streams") else None
        _spool(table, cols)
        return None
    try:
        if table in ("sessions", "streams"):
            cols.pop("ts", None)            # these tables carry their own time columns
        cur = _insert(conn, table, cols)
        conn.commit()
        return cur.lastrowid
    except Exception:
        _spool(table, cols)
        return None


_HANDLER = None   # in-process override for the handler/script name (see set_handler)


def set_handler(name):
    """Override the name _script() reports, for the duration of one subsystem's
    in-process run. claude-hook.py (the single per-event dispatcher) calls each
    subsystem's body in ONE process, so argv[0] is always 'claude-hook.py' — the
    audit's handler/script vocabulary (hook_events.handler, errors.script) would
    collapse to that one name. The dispatcher stamps the subsystem's ENTRY
    filename here before the call and clears it (set_handler(None)) after, so the
    rows keep attributing to claude-cmd-fmt.py / claude-tab-status.py / … exactly
    as when each ran as its own process."""
    global _HANDLER
    _HANDLER = name


def _script():
    if _HANDLER:
        return _HANDLER
    try:
        return os.path.basename(sys.argv[0]) or "?"
    except Exception:
        return "?"


def sid_from_log(log):
    """Recover the session id from a mirror-log path (/tmp/claude-mirror-<sid>.log —
    or any derived path). Returns the key verbatim (the cwd-slug fallback included)."""
    return P.sid_from_log(log)


def sid_of(d):
    """Session id from a hook payload dict, falling back to the mirror-log key."""
    if isinstance(d, dict):
        s = (d.get("session_id") or "").strip()
        if s:
            return s
    return ""


# --- high-level writers -----------------------------------------------------------

def hook_event(d, handler=None, decision="", duration_ms=None):
    """Record one hook invocation with its FULL payload + what the handler decided."""
    d = d if isinstance(d, dict) else {}
    try:
        payload = json.dumps(d, ensure_ascii=False, default=str)
    except Exception:
        payload = str(d)
    event("hook_events", session_id=sid_of(d), hook=d.get("hook_event_name") or "",
          tool_name=d.get("tool_name") or "", agent_id=d.get("agent_id") or "",
          handler=handler or _script(), decision=decision, pid=os.getpid(),
          duration_ms=duration_ms, payload=payload)


def transition(session_id, window_id, dispatch, prev, new, applied, reason=""):
    event("tab_transitions", session_id=session_id or "", window_id=window_id or "",
          dispatch=dispatch or "", prev_state=prev or "", new_state=new or "",
          applied=1 if applied else 0, reason=reason, pid=os.getpid())


def slot(log, kind, action, slot_n=None, agent_id="", owner_pid=None, marker_path=""):
    event("slots", session_id=sid_from_log(log), kind=kind, slot_n=slot_n,
          agent_id=agent_id or "", owner_pid=owner_pid, action=action,
          marker_path=marker_path or "")


def stream_start(log, kind, agent_id="", task_id="", src_path=""):
    """Register a tailer/streamer/watcher start; returns the stream row id to pass
    to stream_end (or None)."""
    return event("streams", session_id=sid_from_log(log), kind=kind,
                 agent_id=agent_id or "", task_id=task_id or "",
                 src_path=src_path or "", pid=os.getpid(), started_at=time.time())


def stream_end(stream_id, end_reason, lines_emitted=None):
    if stream_id is None or not enabled():
        return
    row = {"id": stream_id, "ended_at": time.time(), "end_reason": end_reason,
           "lines_emitted": lines_emitted}
    conn = _connect()
    if conn is None:
        _spool("stream_end", row)
        return
    try:
        conn.execute("UPDATE streams SET ended_at=?, end_reason=?, lines_emitted=? "
                     "WHERE id=?", (row["ended_at"], end_reason, lines_emitted, stream_id))
        conn.commit()
    except Exception:
        _spool("stream_end", row)


def ops(log, op_list, producer=None):
    """Mirror a batch of paint ops into the DB (called from claude_ops.emit — one
    chokepoint covers every producer). One transaction per batch."""
    if not enabled() or not op_list:
        return
    conn = _connect()
    sid, now, prod, pid = sid_from_log(log), time.time(), producer or _script(), os.getpid()
    rows = []
    for o in op_list:
        try:
            rows.append(json.dumps(o, ensure_ascii=False, default=str))
        except Exception:
            rows.append(str(o))
    if conn is None:
        for r in rows:
            _spool("ops", {"ts": now, "session_id": sid, "producer": prod,
                           "pid": pid, "op": r})
        return
    try:
        conn.executemany(
            "INSERT INTO ops(ts, session_id, producer, pid, op) VALUES(?,?,?,?,?)",
            [(now, sid, prod, pid, r) for r in rows])
        conn.commit()
    except Exception:
        for r in rows:
            _spool("ops", {"ts": now, "session_id": sid, "producer": prod,
                           "pid": pid, "op": r})


def error(session_or_log="", func="", context=None):
    """Record the CURRENTLY HANDLED exception (call from an `except` block) with its
    full traceback. `session_or_log` accepts a session id or a mirror-log path."""
    if not enabled():
        return
    sid = session_or_log
    if "/" in (session_or_log or ""):
        sid = sid_from_log(session_or_log)
    try:
        tb = traceback.format_exc()
    except Exception:
        tb = "?"
    if context is not None and not isinstance(context, str):
        try:
            context = json.dumps(context, ensure_ascii=False, default=str)
        except Exception:
            context = str(context)
    event("errors", session_id=sid or "", script=_script(), func=func or "",
          traceback=tb, context=context or "", pid=os.getpid())


def spawn(log, child_pid, argv, purpose=""):
    try:
        argv_s = json.dumps([str(a) for a in argv], ensure_ascii=False)
    except Exception:
        argv_s = str(argv)
    event("spawns", session_id=sid_from_log(log), parent_script=_script(),
          child_pid=child_pid, argv=argv_s, purpose=purpose)


def state_file(log, path, action, content=""):
    """Record a coordination-file transition: action = write/remove/read-stale/…"""
    if content and not isinstance(content, str):
        try:
            content = json.dumps(content, ensure_ascii=False, default=str)
        except Exception:
            content = str(content)
    event("state_files", session_id=sid_from_log(log), path=path, action=action,
          content=(content or "")[:2000], script=_script(), pid=os.getpid())


def pane(session_id, action, ok, detail=""):
    """Record a mirror/scoreboard pane operation (open/close/toggle/resize) and
    whether it verifiably succeeded — claude-split.py's kitten calls were silent."""
    event("pane_events", session_id=session_id or "", action=action,
          ok=1 if ok else 0, detail=detail or "", pid=os.getpid())


def otel(session_id, rows):
    """Record the RAW OTLP metric datapoints the OTLP receiver (plugins/otel/) folds
    into the scoreboard — one row per claude_code.token.usage / cost.usage datapoint,
    so every OTEL cost/token input is captured verbatim and the aggregated counters
    (and the bump-otel deltas) are fully reconstructible: SUM(value) GROUP BY
    session/type == the counter. `rows` = [{metric,query_source,model,type,value}, …].
    Batched (executemany) with a per-row spool fallback; never raises into the
    receiver's request handler."""
    if not enabled() or not rows:
        return
    ts = time.time()
    pid = os.getpid()
    packed = [(ts, session_id or "", r.get("metric") or "", r.get("query_source") or "",
               r.get("model") or "", r.get("type") or "", float(r.get("value") or 0), pid)
              for r in rows]
    conn = _connect()
    if conn is not None:
        try:
            conn.executemany(
                "INSERT INTO otel(ts,session_id,metric,query_source,model,type,value,pid)"
                " VALUES(?,?,?,?,?,?,?,?)", packed)
            conn.commit()
            return
        except Exception:
            pass
    for t, sid, metric, qs, model, typ, val, p in packed:
        _spool("otel", dict(ts=t, session_id=sid, metric=metric, query_source=qs,
                            model=model, type=typ, value=val, pid=p))


def session_start(d):
    """Upsert the session row from a SessionStart payload."""
    if not enabled():
        return
    conn = _connect()
    sid = sid_of(d)
    if not sid:
        return
    envkeys = {k: v for k, v in os.environ.items()
               if k.startswith(("CLAUDE_MIRROR", "CLAUDE_TAB", "CLAUDE_CODE_EFFORT",
                                "CLAUDE_AUDIT",
                                # test-suite seams (README § Testing): a session
                                # run with altered timing/paths must say so here
                                "CLAUDE_TAIL_", "CLAUDE_STREAM_", "CLAUDE_WATCH_",
                                "CLAUDE_CODEX_", "CLAUDE_OTEL_"))
               or k in ("KITTY_WINDOW_ID",)}
    cols = dict(session_id=sid, cwd=d.get("cwd") or os.getcwd(),
                project_slug=os.path.basename((d.get("cwd") or os.getcwd()).rstrip("/")),
                transcript_path=d.get("transcript_path") or "",
                mirror_log=P.mirror_log(sid),
                kitty_window_id=os.environ.get("KITTY_WINDOW_ID") or "",
                started_at=time.time(), env=json.dumps(envkeys, ensure_ascii=False))
    if conn is None:
        _spool("sessions", cols)
        return
    try:
        conn.execute(
            "INSERT INTO sessions(session_id, cwd, project_slug, transcript_path,"
            " mirror_log, kitty_window_id, started_at, env)"
            " VALUES(:session_id, :cwd, :project_slug, :transcript_path, :mirror_log,"
            " :kitty_window_id, :started_at, :env)"
            " ON CONFLICT(session_id) DO UPDATE SET started_at=excluded.started_at,"
            " transcript_path=excluded.transcript_path, env=excluded.env",
            cols)
        conn.commit()
    except Exception:
        _spool("sessions", cols)


def session_end(d, reason=""):
    if not enabled():
        return
    sid = sid_of(d)
    if not sid:
        return
    # Same spool degradation as stream_end (the "session_end" pseudo-table in
    # _insert): a locked/unreachable DB at SessionEnd used to drop the row
    # silently, leaving the session "(open)" in cli_sessions forever.
    row = {"session_id": sid, "ended_at": time.time(),
           "end_reason": reason or (d.get("reason") or "")}
    conn = _connect()
    if conn is None:
        _spool("session_end", row)
        return
    try:
        conn.execute("UPDATE sessions SET ended_at=?, end_reason=? WHERE session_id=?",
                     (row["ended_at"], row["end_reason"], sid))
        conn.commit()
    except Exception:
        _spool("session_end", row)


def prune(days=PRUNE_DAYS):
    """Delete every table's rows for sessions that ENDED more than `days` ago (or
    started that long ago and never ended — a crashed session)."""
    conn = _connect()
    if conn is None:
        return 0
    cutoff = time.time() - days * 86400
    try:
        rows = conn.execute(
            "SELECT session_id FROM sessions WHERE COALESCE(ended_at, started_at) < ?",
            (cutoff,)).fetchall()
        sids = [r[0] for r in rows]
        for sid in sids:
            for t in ("hook_events", "tab_transitions", "slots", "streams", "ops",
                      "errors", "spawns", "state_files", "pane_events", "otel"):
                conn.execute(f"DELETE FROM {t} WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM sessions WHERE session_id=?", (sid,))
        # Orphan rows whose session row never existed (pre-session writes) age out too.
        for t in ("hook_events", "tab_transitions", "slots", "ops", "errors",
                  "spawns", "state_files", "pane_events", "otel"):
            conn.execute(f"DELETE FROM {t} WHERE ts < ? AND session_id NOT IN "
                         "(SELECT session_id FROM sessions)", (cutoff,))
        conn.execute("DELETE FROM streams WHERE started_at < ? AND session_id NOT IN "
                     "(SELECT session_id FROM sessions)", (cutoff,))
        conn.commit()
        return len(sids)
    except Exception:
        return 0


# --- CLI ---------------------------------------------------------------------------

def _read_stdin_json():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def _fmt_ts(ts):
    if not ts:
        return "?"
    try:
        return time.strftime("%m-%d %H:%M:%S", time.localtime(ts)) + f".{int(ts * 1000) % 1000:03d}"
    except Exception:
        return str(ts)


def _print_rows(rows, headers):
    if not rows:
        print("(no rows)")
        return
    print(" | ".join(headers))
    for r in rows:
        print(" | ".join("" if v is None else str(v) for v in r))


def cli_timeline(sid, limit=2000):
    """Merged chronological view across all event tables for one session."""
    conn = _connect()
    if conn is None:
        print("audit db unavailable"); return
    q = """
    SELECT ts, src, detail FROM (
      SELECT ts, 'hook' AS src,
             hook || ' ' || tool_name ||
             CASE WHEN agent_id != '' THEN ' agent=' || substr(agent_id, 1, 8) ELSE '' END ||
             ' [' || handler || '] ' || decision AS detail, session_id
        FROM hook_events
      UNION ALL
      SELECT ts, 'tab', dispatch || ': ' || prev_state || ' -> ' || new_state ||
             CASE WHEN applied = 1 THEN '' ELSE ' (skipped)' END ||
             CASE WHEN reason != '' THEN ' — ' || reason ELSE '' END, session_id
        FROM tab_transitions
      UNION ALL
      SELECT ts, 'slot', action || ' ' || kind ||
             CASE WHEN slot_n IS NOT NULL THEN '.' || slot_n ELSE '' END ||
             CASE WHEN agent_id != '' THEN ' agent=' || substr(agent_id, 1, 8) ELSE '' END ||
             ' pid=' || COALESCE(owner_pid, '?'), session_id
        FROM slots
      UNION ALL
      SELECT started_at, 'stream', 'start ' || kind || ' pid=' || pid ||
             CASE WHEN task_id != '' THEN ' task=' || task_id ELSE '' END ||
             CASE WHEN agent_id != '' THEN ' agent=' || substr(agent_id, 1, 8) ELSE '' END, session_id
        FROM streams
      UNION ALL
      SELECT ended_at, 'stream', 'end ' || kind || ' pid=' || pid || ' reason=' ||
             COALESCE(end_reason, '?') || ' lines=' || COALESCE(lines_emitted, '?'), session_id
        FROM streams WHERE ended_at IS NOT NULL
      UNION ALL
      SELECT ts, 'ERROR', script || ' ' || func || ': ' ||
             replace(substr(traceback, 1, 400), char(10), ' ⏎ '), session_id
        FROM errors
      UNION ALL
      SELECT ts, 'spawn', parent_script || ' -> pid=' || child_pid || ' ' || purpose, session_id
        FROM spawns
      UNION ALL
      SELECT ts, 'file', action || ' ' || path ||
             CASE WHEN content != '' THEN ' :: ' || substr(content, 1, 120) ELSE '' END, session_id
        FROM state_files
      UNION ALL
      SELECT ts, 'pane', action || CASE WHEN ok = 1 THEN '' ELSE ' FAILED' END ||
             CASE WHEN detail != '' THEN ' — ' || detail ELSE '' END, session_id
        FROM pane_events
    ) WHERE session_id = ? ORDER BY ts LIMIT ?"""
    for ts, src, detail in conn.execute(q, (sid, limit)):
        print(f"{_fmt_ts(ts)}  {src:<7} {detail}")


def cli_errors(sid):
    conn = _connect()
    if conn is None:
        print("audit db unavailable"); return
    rows = conn.execute("SELECT ts, script, func, traceback, context FROM errors "
                        "WHERE session_id=? ORDER BY ts", (sid,)).fetchall()
    if not rows:
        print("(no recorded errors)")
    for ts, script, func, tb, ctx in rows:
        print(f"--- {_fmt_ts(ts)}  {script} {func}")
        if ctx:
            print(f"context: {ctx[:500]}")
        print(tb)


def cli_anomalies(sid):
    """Canned queries for known bug signatures. Each prints a section; empty = clean."""
    conn = _connect()
    if conn is None:
        print("audit db unavailable"); return

    def section(title, q, params=()):
        rows = conn.execute(q, params).fetchall()
        print(f"== {title}: {len(rows)}")
        for r in rows:
            print("   " + " | ".join("" if v is None else str(v) for v in r))

    section("swallowed errors",
            "SELECT ts, script, func FROM errors WHERE session_id=? ORDER BY ts", (sid,))
    section("streams that never ended (crashed/stuck tailer)",
            "SELECT id, kind, pid, task_id, agent_id, started_at FROM streams "
            "WHERE session_id=? AND ended_at IS NULL", (sid,))
    # kind='codex-claim' is EXCLUDED: those rows are permanent cross-session
    # OWNERSHIP records (which session shows a codex run), not slot lifecycles
    # — no release ever follows, so counting them false-fired on every adopted
    # rollout. 'claim-denied' is likewise not an acquisition (nothing was
    # taken, so nothing will be released).
    section("slot claims without a matching release",
            "SELECT kind, slot_n, agent_id, COUNT(*) FROM slots WHERE session_id=? "
            "AND kind != 'codex-claim' "
            "GROUP BY kind, COALESCE(slot_n, -1), agent_id "
            "HAVING SUM(CASE WHEN action LIKE 'claim%' AND action != 'claim-denied' "
            "               THEN 1 ELSE 0 END) > "
            "       SUM(CASE WHEN action LIKE 'release%' OR action LIKE 'steal%' THEN 1 ELSE 0 END)",
            (sid,))
    section("tab left on a busy colour (last transition not green/idle/clear)",
            "SELECT ts, dispatch, prev_state, new_state, reason FROM tab_transitions "
            "WHERE session_id=? AND applied=1 AND ts = (SELECT MAX(ts) FROM "
            "tab_transitions WHERE session_id=? AND applied=1) AND new_state NOT IN "
            "('awaiting-response', 'idle', 'clear', '')", (sid, sid))
    # handler != 'subscriber': the universal async subscriber records EVERY hook
    # event alongside the handler's own decision row, so counting both made every
    # normally-started agent look started-twice (a false positive on all sessions
    # since the subscriber landed).
    section("duplicate SubagentStart (same agent started twice)",
            "SELECT agent_id, COUNT(*) FROM hook_events WHERE session_id=? AND "
            "hook='SubagentStart' AND agent_id != '' AND handler != 'subscriber' "
            "GROUP BY agent_id HAVING COUNT(*) > 1",
            (sid,))
    section("SubagentStart without SubagentStop",
            "SELECT DISTINCT h.agent_id FROM hook_events h WHERE h.session_id=? AND "
            "h.hook='SubagentStart' AND h.agent_id != '' AND h.agent_id NOT IN "
            "(SELECT agent_id FROM hook_events WHERE session_id=? AND hook='SubagentStop')",
            (sid, sid))
    # The inverse is the scoreboard-under-/cost signature: Claude Code runs hidden
    # summarizer-style agents that fire ONLY SubagentStop — no SubagentStart, no
    # substream, and (usually) no transcript file, so their billed spend never
    # reaches the scoreboard. Since the OTEL cost pipeline, a hidden agent's spend
    # IS captured (the OTLP receiver folds query_source=auxiliary/subagent live), so
    # this is now informational, not a spend gap. The stop handler's decision row
    # still says whether a transcript existed to cross-check ("never started …").
    section("SubagentStop without SubagentStart (hidden agent — spend now captured via OTEL)",
            "SELECT DISTINCT h.agent_id FROM hook_events h WHERE h.session_id=? AND "
            "h.hook='SubagentStop' AND h.agent_id != '' AND h.agent_id NOT IN "
            "(SELECT agent_id FROM hook_events WHERE session_id=? AND hook='SubagentStart')",
            (sid, sid))
    # A subagent turn that dies on an API error (e.g. 529 Overloaded) fires
    # StopFailure carrying its agent_id and NO SubagentStop — the agent's only stop
    # signal. claude-stop-fmt.py must hand it to the subagent finaliser (a
    # 'stopfail: …' decision); the pre-fix behaviour ('ignored: agent_id …') left the
    # streamer's slot claimed forever and wedged the tab blue. This flags only the
    # UNrecovered case — a StopFailure+agent_id whose decision is NOT 'stopfail:' — so
    # a healthy recovered session stays clean and a non-empty row IS the regression.
    section("StopFailure carrying an agent_id NOT handed to the finaliser (stuck-blue regression)",
            "SELECT ts, agent_id, decision FROM hook_events WHERE session_id=? AND "
            "hook='StopFailure' AND agent_id != '' AND handler != 'subscriber' "
            "AND decision NOT LIKE 'stopfail:%' ORDER BY ts", (sid,))
    # An ASYNC (background) agent's Task resolves IMMEDIATELY in the parent
    # transcript with a synthetic "Async agent launched successfully" tool_result
    # (is_error absent) meaning launched-not-finished. parent_tool_result() must
    # ignore that ack; treating it as a resolution ended the substream ~2s in with
    # 0 lines rendered, so the agent's whole transcript never reached the mirror.
    # Tell: a subagent/teammate stream ending 'parent-task-resolved' (NOT rejected)
    # with lines_emitted=0 while a real SubagentStop later fired for that agent.
    section("async launch-ack ended the substream early (0 lines rendered)",
            "SELECT s.agent_id, s.ended_at, s.end_reason FROM streams s WHERE "
            "s.session_id=? AND s.kind IN ('subagent','teammate') AND "
            "s.end_reason='parent-task-resolved' AND COALESCE(s.lines_emitted,0)=0 "
            "AND s.agent_id IN (SELECT agent_id FROM hook_events WHERE session_id=? "
            "AND hook='SubagentStop')", (sid, sid))
    # Since the single-dispatcher refactor every event runs through claude-hook.py
    # -> dispatch.py. A crash in the DISPATCHER itself (not a subsystem) records
    # script='dispatch' — that means route() threw before/around fanning out, so a
    # whole event may have produced no tab change / no block. A subsystem crash keeps
    # its own entry-filename script (surfaced by "swallowed errors" above); this
    # isolates the dispatcher-level failure, which should essentially never fire.
    section("dispatcher-level crash (route() threw — whole event may be lost)",
            "SELECT ts, func, substr(traceback,1,120) FROM errors WHERE session_id=? "
            "AND script='dispatch' ORDER BY ts", (sid,))
    section("failed tools (PostToolUseFailure)",
            "SELECT ts, tool_name, decision FROM hook_events WHERE session_id=? AND "
            "hook LIKE '%Failure%' ORDER BY ts", (sid,))
    # A content-render stream (claude-stream.py MD/JSON mode: cat/head/tail of a .md,
    # cat of a .json; decision '[md-render]'/'[json-render]' in hook_events) records a
    # 'done' state_file row (path render:<taskid>) with the block count it emitted.
    # Zero blocks from a stream that ran means the renderer produced nothing — a
    # wenmode/json parse failure or an empty fallback. The paired 'start' row records
    # the kind (md/json). See core/mdrender.py / core/jsonrender.py.
    section("content-render streams that emitted zero blocks (render failure)",
            "SELECT ts, path, content FROM state_files WHERE session_id=? AND "
            "path LIKE 'render:%' AND action='done' AND content LIKE '%\"blocks\": 0%' "
            "ORDER BY ts", (sid,))
    section("spawned processes that never registered a stream",
            "SELECT s.ts, s.child_pid, s.purpose FROM spawns s WHERE s.session_id=? "
            "AND s.purpose LIKE 'stream%' AND s.child_pid NOT IN "
            "(SELECT pid FROM streams WHERE session_id=?)", (sid, sid))
    section("pane operations that failed",
            "SELECT ts, action, detail FROM pane_events WHERE session_id=? AND ok=0 "
            "ORDER BY ts", (sid,))
    # close_stale_mirrors audits every window it sweeps (action=close-stale,
    # detail "closed sid=<sid> win=<id>"). Sweeping a mirror whose session is
    # still OPEN is the cross-session pane-hijack shape (a daemon-origin
    # SessionStart anchored to the wrong tab — the agents-view bug); the benign
    # exception is a predecessor that crashed without SessionEnd in the same tab.
    section("stale-mirror sweep closed a LIVE session's mirror (pane hijack)",
            "SELECT p.ts, p.session_id, p.detail FROM pane_events p JOIN sessions s "
            "ON p.detail LIKE ('closed sid=' || s.session_id || ' %') "
            "WHERE p.action='close-stale' AND s.ended_at IS NULL "
            "AND s.session_id != p.session_id "
            "AND (p.session_id=? OR s.session_id=?) ORDER BY p.ts", (sid, sid))
    section("tab colour applies where kitten @ failed",
            "SELECT ts, dispatch, new_state, reason FROM tab_transitions "
            "WHERE session_id=? AND reason LIKE '%kitten @ failed%' ORDER BY ts", (sid,))
    # Token/cost spend must arrive as an ATTRIBUTED action: 'bump-otel' (the OTLP
    # receiver, keyed by session.id + query_source) or 'bump-agent' (codex's own
    # rollout fold — codex runs in a separate process OTEL can't see). A plain 'bump'
    # carrying a tokens/cost delta means some producer bypassed attribution — the
    # scoreboard number it fed can only be traced by timestamp correlation.
    # A --resume/--continue SessionStart should find the parked *.keep state DB and
    # log a `restore-history` (or, after a crash with no SessionEnd, find the DB
    # still live: `reuse-live-db`). A `fresh-db` row on a source=resume start
    # means the history was lost — the mirror came back empty.
    section("resume that lost its mirror history (fresh-db on source=resume)",
            "SELECT h.ts FROM hook_events h WHERE h.session_id=? AND "
            "h.hook='SessionStart' AND json_extract(h.payload,'$.source')='resume' "
            "AND EXISTS (SELECT 1 FROM state_files f WHERE f.session_id=h.session_id "
            "AND f.action='fresh-db' AND abs(f.ts - h.ts) < 30)", (sid,))
    section("unattributed token/cost bumps (should be bump-agent with meta)",
            "SELECT ts, content FROM state_files WHERE session_id=? AND action='bump' "
            "AND (json_extract(content, '$.deltas.tokens') IS NOT NULL "
            "OR json_extract(content, '$.deltas.cost') IS NOT NULL) ORDER BY ts", (sid,))
    # Cost is OTEL-authoritative; the transcript fold survives ONLY as a SessionEnd
    # fallback that must fire ONLY when the receiver wrote nothing (otel_seen==0). If a
    # session has BOTH a 'folded transcript fallback' SessionEnd decision AND bump-otel
    # rows, the fallback fired despite OTEL data — a double-count regression (the
    # otel_seen gate in stop_fmt broke). A healthy session has exactly one source.
    section("SessionEnd transcript fallback fired despite OTEL data (double-count regression)",
            "SELECT ts, decision FROM hook_events WHERE session_id=? AND "
            "handler='claude-stop-fmt.py' AND decision LIKE 'otel absent%' AND EXISTS "
            "(SELECT 1 FROM state_files f WHERE f.session_id=? AND f.action='bump-otel') "
            "ORDER BY ts", (sid, sid))
    # The OTLP receiver is a long-lived singleton that caches its state-DB
    # connection. A park (os.replace db -> db.keep) + resume swaps the inode under
    # the path, so a receiver that didn't revalidate kept writing token counters to
    # the ORPHANED *.keep inode while the scorebar read the fresh live DB — a silent
    # divergence (no error; both files are valid DBs). Tell: bump-otel rows exist for
    # the session (OTEL landed) yet the LIVE state DB has no tk_read/tokens counter.
    # core/state._connect now revalidates by st_ino, so a non-empty row here is that
    # regression (or the receiver holding an fd on a *.keep path — check `lsof`).
    _section_otel_stranded(conn, section, sid)


def _section_otel_stranded(audit_conn, section, sid):
    """Cross-check the audit's bump-otel trail against the LIVE state DB counters —
    the only decisive signal for a receiver whose writes were diverted to a parked
    *.keep inode (see the caller's note). Reads the state DB read-only; degrades to
    a clean section if it isn't present (parked/ended session — nothing to check)."""
    import sqlite3
    n_otel = audit_conn.execute(
        "SELECT COUNT(*) FROM state_files WHERE session_id=? AND action='bump-otel'",
        (sid,)).fetchone()[0]
    hits = []
    if n_otel:
        db = P.mirror_log(sid) + ".state.db"
        if os.path.exists(db):
            try:
                c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.5)
                have = c.execute(
                    "SELECT COUNT(*) FROM counters WHERE key IN ('tokens','tk_read')"
                ).fetchone()[0]
                c.close()
                if have == 0:
                    hits.append((n_otel, db))
            except Exception:
                pass
    print(f"== OTLP writes stranded on a parked inode "
          f"(bump-otel rows but live DB has no token counters): {len(hits)}")
    for h in hits:
        print(f"   bump-otel={h[0]} but no tokens/tk_read in {h[1]}")


def cli_otel(sid):
    """The OTEL cost/token breakdown for one session, straight from the raw `otel`
    datapoints the receiver captured — grouped by query_source × type (so the hidden
    `auxiliary` agents' share is explicit), plus total cost per query_source. This IS
    the ground truth the scoreboard counters aggregate; SUM here == the counter."""
    conn = _connect()
    if conn is None:
        print("audit db unavailable"); return
    print("== token datapoints (SUM value) by query_source × type ==")
    rows = conn.execute(
        "SELECT query_source, type, COUNT(*), SUM(value) FROM otel WHERE session_id=? "
        "AND metric='token' GROUP BY query_source, type ORDER BY query_source, type",
        (sid,)).fetchall()
    _print_rows(rows, ["query_source", "type", "n", "tokens"])
    print("\n== cost (USD) by query_source ==")
    rows = conn.execute(
        "SELECT query_source, COUNT(*), SUM(value) FROM otel WHERE session_id=? "
        "AND metric='cost' GROUP BY query_source ORDER BY query_source", (sid,)).fetchall()
    _print_rows(rows, ["query_source", "n", "cost_usd"])
    tot = conn.execute("SELECT SUM(value) FROM otel WHERE session_id=? AND metric='cost'",
                       (sid,)).fetchone()
    print(f"\ntotal cost = ${(tot and tot[0]) or 0:.4f}")


def cli_sessions(limit=20):
    conn = _connect()
    if conn is None:
        print("audit db unavailable"); return
    rows = conn.execute(
        "SELECT session_id, project_slug, datetime(started_at, 'unixepoch', 'localtime'),"
        " CASE WHEN ended_at IS NULL THEN '(open)' ELSE"
        " datetime(ended_at, 'unixepoch', 'localtime') END, end_reason"
        " FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
    _print_rows(rows, ["session_id", "project", "started", "ended", "reason"])


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "session-start":
        session_start(_read_stdin_json())
    elif cmd == "session-end":
        d = _read_stdin_json()
        session_end(d)
        prune()
    elif cmd == "hook":                     # hook <handler> [<decision>], payload on stdin
        hook_event(_read_stdin_json(), handler=(argv[2] if len(argv) > 2 else None),
                   decision=(argv[3] if len(argv) > 3 else ""))
    elif cmd == "transition":               # transition <sid> <win> <dispatch> <prev> <new> <applied> [reason]
        a = argv[2:] + [""] * 7
        transition(a[0], a[1], a[2], a[3], a[4], a[5] == "1", a[6])
    elif cmd == "error":                    # error <sid> <script> <message>
        a = argv[2:] + [""] * 3
        event("errors", session_id=a[0], script=a[1] or "shell", func="",
              traceback=a[2], context="", pid=os.getppid())
    elif cmd == "pane":                     # pane <sid> <action> <ok 0|1> [detail]
        a = argv[2:] + [""] * 4
        pane(a[0], a[1], a[2] == "1", a[3])
    elif cmd == "state-file":               # state-file <log> <path> <action> [content]
        a = argv[2:] + [""] * 4
        state_file(a[0], a[1], a[2], a[3])
    elif cmd == "stream-start":             # stream-start <sid> <kind> [src] -> prints row id
        a = argv[2:] + [""] * 3
        rid = event("streams", session_id=a[0], kind=a[1] or "watcher",
                    agent_id="", task_id="", src_path=a[2], pid=os.getppid(),
                    started_at=time.time())
        print(rid if rid is not None else "")
    elif cmd == "stream-end":               # stream-end <id> <reason> [lines]
        a = argv[2:] + [""] * 3
        try:
            rid = int(a[0])
        except Exception:
            rid = None
        stream_end(rid, a[1] or "?", int(a[2]) if a[2].isdigit() else None)
    elif cmd == "sessions":
        cli_sessions(int(argv[2]) if len(argv) > 2 else 20)
    elif cmd == "timeline":
        cli_timeline(argv[2] if len(argv) > 2 else "",
                     int(argv[3]) if len(argv) > 3 else 2000)
    elif cmd == "errors":
        cli_errors(argv[2] if len(argv) > 2 else "")
    elif cmd == "anomalies":
        cli_anomalies(argv[2] if len(argv) > 2 else "")
    elif cmd == "otel":
        cli_otel(argv[2] if len(argv) > 2 else "")
    elif cmd == "sql":
        conn = _connect()
        if conn is None:
            print("audit db unavailable"); return
        q = argv[2] if len(argv) > 2 else ""
        try:
            cur = conn.execute(q)
            headers = [c[0] for c in cur.description] if cur.description else []
            _print_rows(cur.fetchall(), headers)
            conn.commit()
        except Exception as e:
            print(f"sql error: {e}")
    elif cmd == "prune":
        n = prune(int(argv[2]) if len(argv) > 2 else PRUNE_DAYS)
        print(f"pruned {n} session(s)")
    else:
        print(__doc__ or "see module docstring for usage")


# The CLI entry point lives in the top-level claude_audit.py shim (main() above
# is what it calls) — a package module can't be executed directly.
