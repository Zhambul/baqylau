"""core/audit.py — always-on SQLite audit trail for the mirror tooling.
(Historical name: claude_audit.py; bin/claude-audit.py is also
the CLI entry point: `python3 bin/claude-audit.py sessions|anomalies|…`.)

The mirror is ~20 short-lived hook processes plus detached tailers/watchers
coordinating through /tmp marker files, sidecars, and sentinels — and almost every
failure is swallowed (`except Exception: pass`, `2>/dev/null`), so when a tab
sticks blue or a block never closes, the evidence evaporates with the processes.
This module records everything durable enough to chase a bug after the fact:

  sessions        one row per Claude session (the anchor everything joins to)
  hook_events     every hook invocation: full stdin payload + the handler's decision
  tab_transitions the tab-colour state machine (replaces the old CLAUDE_TAB_DEBUG logs)
  slots           every marker-file claim/release (the mechanism behind stuck colours)
  streams         lifecycle of every detached tailer/streamer/watcher
  ops             every paint op appended to the mirror log (full pane reconstruction)
  errors          every swallowed exception, with traceback + context
  spawns          every detached process launch
  state_files     writes/removals of coordination files (.done sentinels, .fg-live, …)

ON by default; set CLAUDE_AUDIT=0 to turn it off (every call becomes a no-op).
The DB lives OUTSIDE /tmp (session artifacts there are deleted at SessionEnd) in
$CLAUDE_AUDIT_DIR or ~/.claude/kitty-audit/audit.db — one global DB, all sessions,
WAL mode so the many concurrent short-lived writers never block each other. Audit
failures NEVER propagate to callers: a failed write degrades to an append-only
spool (spool.jsonl) that is re-ingested on the next successful open, so auditing
can neither lose evidence nor break a hook.

CLI (what the audit-debug skill drives):
  bin/claude-audit.py sessions [N]          recent sessions
  bin/claude-audit.py timeline <sid> [limit] [--ops] [--otel]
                                        merged chronological event timeline
                                        (--ops / --otel merge those high-volume
                                        tables in too; off by default)
  bin/claude-audit.py errors <sid>          swallowed exceptions for a session
  bin/claude-audit.py anomalies <sid>       canned queries for known bug signatures
  bin/claude-audit.py sql "<query>"         free-form read-only SQL (opens mode=ro)
  bin/claude-audit.py sql-write "<query>"   free-form read-write SQL for manual fixups
  bin/claude-audit.py prune [days]          drop sessions older than N days (default 30)
  bin/claude-audit.py session-start|session-end|hook <handler>|transition …
                                        write entry points for the shell scripts
"""
import json, os, re, sys, time, traceback

from core import paths as P    # the one owner of the mirror-log path format

_CONN = None            # cached per-process connection (streamers are long-lived)
_FAILED = False         # sqlite gave up this process -> spool only, don't retry each call

PRUNE_DAYS = 30

# Tables (plus their spool pseudo-tables) that carry their OWN time column
# (started_at/ended_at) instead of the generic `ts` — event() must not stamp
# `ts` onto their rows (sessions/streams have no ts column to insert into).
OWN_TS_TABLES = ("sessions", "streams", "stream_end", "session_end")


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
    # Drain spool.jsonl — and any orphaned claim files a hard-killed drainer left
    # behind — into the DB. Every drain claims its file first by an exclusive
    # rename to OUR pid suffix (spool.jsonl.<pid>), so two processes ingesting at
    # once can never double-insert: exactly one rename wins. A claimer that dies
    # between claim and remove leaves its file behind with a now-dead pid; each
    # pass ADOPTS those by the same claim-by-rename (dead-pid check via
    # core.state.pid_alive — EPERM = alive foreign-owned, left alone), so no rows
    # are ever permanently stranded. Canonical-spool-then-orphans ordering is
    # arbitrary: an orphan's rows are older, but audit chronology comes from each
    # row's own ts column, never from insert order.
    from core.state import pid_alive        # the ONE liveness probe
    p = spool_path()
    todo = [p] if os.path.exists(p) else []
    try:
        import glob
        me = os.getpid()
        for orphan in glob.glob(p + ".*"):
            pid = orphan[len(p) + 1:]
            if pid.isdigit() and int(pid) != me and not pid_alive(int(pid)):
                todo.append(orphan)
    except Exception:
        pass                                # orphan scan is best-effort
    claimed = p + f".{os.getpid()}"
    for src in todo:
        try:
            os.rename(src, claimed)
        except OSError:
            continue                        # another process claimed/adopted it
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
            # Leave the claim at our own pid suffix: once this process exits the
            # pid is dead and a later pass adopts it. (Renaming back to the
            # canonical spool could clobber rows freshly spooled there — POSIX
            # rename replaces — and `claimed` must be free before the next
            # rename above, so stop the pass here.)
            return


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
    # Interpolated identifiers only: `table`/`keys` come from this module's own
    # callers (the _SCHEMA vocabulary), never user input; values are bound.
    sql = (f"INSERT INTO {table}({','.join(keys)}) "
           f"VALUES({','.join('?' * len(keys))})")
    return conn.execute(sql, [cols[k] for k in keys])


def event(table, **cols):
    """Write one row (INSERT, or the UPDATE a spool pseudo-table stands for);
    never raises. Returns the new rowid (or None). Falls back to the spool when
    sqlite can't be written."""
    if not enabled():
        return None
    if table not in OWN_TS_TABLES:
        cols.setdefault("ts", time.time())
    conn = _connect()
    if conn is None:
        _spool(table, cols)
        return None
    try:
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
    # Routed through event() via the "stream_end" pseudo-table: _insert applies it
    # as the UPDATE it stands for (live write and spool replay share one shape).
    if stream_id is None:
        return
    event("stream_end", id=stream_id, ended_at=time.time(),
          end_reason=end_reason, lines_emitted=lines_emitted)


def _event_many(table, sql, packed, spool_rows):
    """Shared degradation shape for the BATCHED writers (ops, otel). They can't
    route through event() — that is one INSERT + commit per row, and these are
    hot paths writing whole batches in a single transaction — so the
    connect→try→spool fallback lives once here instead. Never raises: an
    unreachable or failing DB degrades to spooling each row individually."""
    conn = _connect()
    if conn is not None:
        try:
            conn.executemany(sql, packed)
            conn.commit()
            return
        except Exception:
            pass
    for r in spool_rows:
        _spool(table, r)


def ops(log, op_list, producer=None):
    """Mirror a batch of paint ops into the DB (called from core.ops.emit — one
    chokepoint covers every producer). One transaction per batch."""
    if not enabled() or not op_list:
        return
    sid, now, prod, pid = sid_from_log(log), time.time(), producer or _script(), os.getpid()
    rows = []
    for o in op_list:
        try:
            rows.append(json.dumps(o, ensure_ascii=False, default=str))
        except Exception:
            rows.append(str(o))
    _event_many(
        "ops",
        "INSERT INTO ops(ts, session_id, producer, pid, op) VALUES(?,?,?,?,?)",
        [(now, sid, prod, pid, r) for r in rows],
        [{"ts": now, "session_id": sid, "producer": prod, "pid": pid, "op": r}
         for r in rows])


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
    _event_many(
        "otel",
        "INSERT INTO otel(ts,session_id,metric,query_source,model,type,value,pid)"
        " VALUES(?,?,?,?,?,?,?,?)", packed,
        [dict(ts=t, session_id=sid, metric=metric, query_source=qs, model=model,
              type=typ, value=val, pid=p)
         for t, sid, metric, qs, model, typ, val, p in packed])


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
                                # test-suite seams (docs/testing.md): a session
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
    # Routed through event() via the "session_end" pseudo-table (like stream_end):
    # _insert applies it as the UPDATE it stands for. A locked/unreachable DB at
    # SessionEnd used to drop the row silently, leaving the session "(open)" in
    # cli_sessions forever — the shared spool degradation covers that.
    event("session_end", session_id=sid, ended_at=time.time(),
          end_reason=reason or (d.get("reason") or ""))


def schema_tables():
    """Every table _SCHEMA creates, in declaration order."""
    return re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)\(", _SCHEMA)


# Tables prune() must NOT sweep by session_id + ts like the rest. `sessions` is the
# driver of pruning (its COALESCE(ended_at, started_at) picks the sids; its own row is
# deleted per-sid, last). `streams` has no `ts` column — its orphan age-out keys on
# `started_at` instead, handled explicitly below. A NEW audit table is prunable by
# default: give it session_id + ts columns, or classify it here (and in the unit test).
_PRUNE_SPECIAL = ("sessions", "streams")


def prunable_tables():
    """Tables swept by prune()'s generic session_id/ts loops, derived from _SCHEMA so
    a new audit table can't silently escape pruning."""
    return [t for t in schema_tables() if t not in _PRUNE_SPECIAL]


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
            # Interpolated identifier only: `t` comes from the _SCHEMA-derived
            # prunable_tables() list, never user input; values are bound.
            for t in prunable_tables() + ["streams"]:
                conn.execute(f"DELETE FROM {t} WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM sessions WHERE session_id=?", (sid,))
        # Orphan rows whose session row never existed (pre-session writes) age out too.
        # (Same trusted-identifier note: `t` is _SCHEMA-derived, values bound.)
        for t in prunable_tables():
            conn.execute(f"DELETE FROM {t} WHERE ts < ? AND session_id NOT IN "
                         "(SELECT session_id FROM sessions)", (cutoff,))
        # streams has no ts column — its orphans age out by started_at.
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


def cli_timeline(sid, limit=2000, with_ops=False, with_otel=False):
    """Merged chronological view across all event tables for one session.
    `ops` and `otel` are opt-in (--ops / --otel): they dwarf the event tables
    (one row per paint op / per metric datapoint) and would drown the story."""
    conn = _connect()
    if conn is None:
        print("audit db unavailable"); return
    extra = ""
    if with_ops:
        extra += """
      UNION ALL
      SELECT ts, 'op', producer || ': ' || substr(op, 1, 160), session_id
        FROM ops"""
    if with_otel:
        extra += """
      UNION ALL
      SELECT ts, 'otel', metric || ' ' || query_source ||
             CASE WHEN type != '' THEN ' ' || type ELSE '' END || '=' || value, session_id
        FROM otel"""
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
        FROM pane_events""" + extra + """
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


# The anomalies registry — the canned queries `cli_anomalies` runs, IN ORDER.
# Each entry is either
#   (title, sql, nparams)   — sql takes the sid repeated nparams times, printed
#                             as a counted section (empty = clean), or
#   a callable(conn, section, sid) — a special-case section that needs more than
#                             one query (e.g. a cross-DB check); it must print its
#                             own `== title: N` section via the passed `section`
#                             helper or an equivalent print.
# CLAUDE.md tells contributors to extend this list when a feature has a known
# failure signature — add the entry here (with the why-comment above it) and
# poison-test it in tests/test_l7_audit.py.

# Window for the duplicated-ops signature: the fixed-2026-07-04 tailer bug
# (unbounded read() + pos=size) re-emitted the same chunk on the NEXT poll, so
# genuine duplicates land seconds apart; identical long lines hours apart are
# just a command printing the same thing twice.
DUP_OPS_WINDOW_S = 5.0

ANOMALY_SECTIONS = [
    ("swallowed errors",
     "SELECT ts, script, func FROM errors WHERE session_id=? ORDER BY ts", 1),
    ("streams that never ended (crashed/stuck tailer)",
     "SELECT id, kind, pid, task_id, agent_id, started_at FROM streams "
     "WHERE session_id=? AND ended_at IS NULL", 1),
    # kind='codex-claim' is EXCLUDED: those rows are permanent cross-session
    # OWNERSHIP records (which session shows a codex run), not slot lifecycles
    # — no release ever follows, so counting them false-fired on every adopted
    # rollout. 'claim-denied' is likewise not an acquisition (nothing was
    # taken, so nothing will be released). 'steal-stale' IS an acquisition (the
    # new holder takes the slot) — it was once counted on the release side,
    # which balanced out a stealer that then leaked its slot (steal-then-leak
    # escaped). The displaced dead holder's missing release is synthesized at
    # steal time (core/slots.py 'release-stale'), so a healthy steal still
    # balances; pre-2026-07-15 sessions have steal rows without release-stale
    # and may flag here — historical data, not a live bug.
    ("slot claims without a matching release",
     "SELECT kind, slot_n, agent_id, COUNT(*) FROM slots WHERE session_id=? "
     "AND kind != 'codex-claim' "
     "GROUP BY kind, COALESCE(slot_n, -1), agent_id "
     "HAVING SUM(CASE WHEN (action LIKE 'claim%' AND action != 'claim-denied') "
     "               OR action LIKE 'steal%' THEN 1 ELSE 0 END) > "
     "       SUM(CASE WHEN action LIKE 'release%' THEN 1 ELSE 0 END)",
     1),
    # 'awaiting-command' (red — the permission prompt) is a RESTING user-blocked
    # state, exactly like green: a session can legitimately sit on it for hours,
    # so it's excluded alongside the green/idle/clear set. NB pre-2026-07
    # sessions wrote their literal-state dispatches (SessionStart idle /
    # SessionEnd clear) under session_id='' — this per-sid query misses that
    # final clear, so old sessions can false-flag here.
    ("tab left on a busy colour (last transition not green/idle/clear)",
     "SELECT ts, dispatch, prev_state, new_state, reason FROM tab_transitions "
     "WHERE session_id=? AND applied=1 AND ts = (SELECT MAX(ts) FROM "
     "tab_transitions WHERE session_id=? AND applied=1) AND new_state NOT IN "
     "('awaiting-response', 'awaiting-command', 'idle', 'clear', '')", 2),
    # handler != 'subscriber': the universal async subscriber records EVERY hook
    # event alongside the handler's own decision row, so counting both made every
    # normally-started agent look started-twice (a false positive on all sessions
    # since the subscriber landed).
    ("duplicate SubagentStart (same agent started twice)",
     "SELECT agent_id, COUNT(*) FROM hook_events WHERE session_id=? AND "
     "hook='SubagentStart' AND agent_id != '' AND handler != 'subscriber' "
     "GROUP BY agent_id HAVING COUNT(*) > 1", 1),
    ("SubagentStart without SubagentStop",
     "SELECT DISTINCT h.agent_id FROM hook_events h WHERE h.session_id=? AND "
     "h.hook='SubagentStart' AND h.agent_id != '' AND h.agent_id NOT IN "
     "(SELECT agent_id FROM hook_events WHERE session_id=? AND hook='SubagentStop')",
     2),
    # The inverse is the scoreboard-under-/cost signature: Claude Code runs hidden
    # summarizer-style agents that fire ONLY SubagentStop — no SubagentStart, no
    # substream, and (usually) no transcript file, so their billed spend never
    # reaches the scoreboard. Since the OTEL cost pipeline, a hidden agent's spend
    # IS captured (the OTLP receiver folds query_source=auxiliary/subagent live), so
    # this is now informational, not a spend gap. The stop handler's decision row
    # still says whether a transcript existed to cross-check ("never started …").
    ("SubagentStop without SubagentStart (hidden agent — spend now captured via OTEL)",
     "SELECT DISTINCT h.agent_id FROM hook_events h WHERE h.session_id=? AND "
     "h.hook='SubagentStop' AND h.agent_id != '' AND h.agent_id NOT IN "
     "(SELECT agent_id FROM hook_events WHERE session_id=? AND hook='SubagentStart')",
     2),
    # A subagent turn that dies on an API error (e.g. 529 Overloaded) fires
    # StopFailure carrying its agent_id and NO SubagentStop — the agent's only stop
    # signal. claude-stop-fmt.py must hand it to the subagent finaliser (a
    # 'stopfail: …' decision); the pre-fix behaviour ('ignored: agent_id …') left the
    # streamer's slot claimed forever and wedged the tab blue. This flags only the
    # UNrecovered case — a StopFailure+agent_id whose decision is NOT 'stopfail:' — so
    # a healthy recovered session stays clean and a non-empty row IS the regression.
    ("StopFailure carrying an agent_id NOT handed to the finaliser (stuck-blue regression)",
     "SELECT ts, agent_id, decision FROM hook_events WHERE session_id=? AND "
     "hook='StopFailure' AND agent_id != '' AND handler != 'subscriber' "
     "AND decision NOT LIKE 'stopfail:%' ORDER BY ts", 1),
    # An ASYNC (background) agent's Task resolves IMMEDIATELY in the parent
    # transcript with a synthetic "Async agent launched successfully" tool_result
    # (is_error absent) meaning launched-not-finished. parent_tool_result() must
    # ignore that ack; treating it as a resolution ended the substream ~2s in with
    # 0 lines rendered, so the agent's whole transcript never reached the mirror.
    # Tell: a subagent/teammate stream ending 'parent-task-resolved' (NOT rejected)
    # with lines_emitted=0 while a real SubagentStop later fired for that agent.
    ("async launch-ack ended the substream early (0 lines rendered)",
     "SELECT s.agent_id, s.ended_at, s.end_reason FROM streams s WHERE "
     "s.session_id=? AND s.kind IN ('subagent','teammate') AND "
     "s.end_reason='parent-task-resolved' AND COALESCE(s.lines_emitted,0)=0 "
     "AND s.agent_id IN (SELECT agent_id FROM hook_events WHERE session_id=? "
     "AND hook='SubagentStop')", 2),
    # Claude Code creates tasks/<id>.output LAZILY, on the monitor's first output
    # byte — a quiet persistent monitor has no file for minutes or hours. The
    # monitor tailer waits for it keyed on the monitor PROCESS's liveness
    # (stream.py monitor_wait_file); a monitor stream ending plain
    # 'output-file-not-found' is the pre-fix bounded 12s give-up — the block closed
    # "■ output not found" and the tab cleared to green while the monitor ran on.
    # Post-fix the only legitimate not-found end carries the
    # '(monitor process never found)' suffix (nothing to key liveness on), so a
    # bare match here IS the regression.
    ("monitor gave up on a lazily-created output file (tab wrongly cleared)",
     "SELECT id, task_id, started_at, ended_at FROM streams WHERE session_id=? "
     "AND kind='monitor' AND end_reason='output-file-not-found'", 1),
    # Since the single-dispatcher refactor every event runs through claude-hook.py
    # -> dispatch.py. A crash in the DISPATCHER itself (not a subsystem) records
    # script='dispatch' — that means route() threw before/around fanning out, so a
    # whole event may have produced no tab change / no block. A subsystem crash keeps
    # its own entry-filename script (surfaced by "swallowed errors" above); this
    # isolates the dispatcher-level failure, which should essentially never fire.
    ("dispatcher-level crash (route() threw — whole event may be lost)",
     "SELECT ts, func, substr(traceback,1,120) FROM errors WHERE session_id=? "
     "AND script='dispatch' ORDER BY ts", 1),
    ("failed tools (PostToolUseFailure)",
     "SELECT ts, tool_name, decision FROM hook_events WHERE session_id=? AND "
     "hook LIKE '%Failure%' ORDER BY ts", 1),
    # A content-render stream (claude-stream.py MD/JSON mode: cat/head/tail of a .md,
    # cat of a .json; decision '[md-render]'/'[json-render]' in hook_events) records a
    # 'done' state_file row (path render:<taskid>) with the block count it emitted.
    # Zero blocks from a stream that ran means the renderer produced nothing — a
    # wenmode/json parse failure or an empty fallback. The paired 'start' row records
    # the kind (md/json). See core/mdrender.py / core/jsonrender.py.
    ("content-render streams that emitted zero blocks (render failure)",
     "SELECT ts, path, content FROM state_files WHERE session_id=? AND "
     "path LIKE 'render:%' AND action='done' AND content LIKE '%\"blocks\": 0%' "
     "ORDER BY ts", 1),
    ("spawned processes that never registered a stream",
     "SELECT s.ts, s.child_pid, s.purpose FROM spawns s WHERE s.session_id=? "
     "AND s.purpose LIKE 'stream%' AND s.child_pid NOT IN "
     "(SELECT pid FROM streams WHERE session_id=?)", 2),
    ("pane operations that failed",
     "SELECT ts, action, detail FROM pane_events WHERE session_id=? AND ok=0 "
     "ORDER BY ts", 1),
    # close_stale_mirrors audits every window it sweeps (action=close-stale,
    # detail "closed sid=<sid> win=<id>"). Sweeping a mirror whose session is
    # still OPEN is the cross-session pane-hijack shape (a daemon-origin
    # SessionStart anchored to the wrong tab — the agents-view bug); the benign
    # exception is a predecessor that crashed without SessionEnd in the same tab.
    # The LIKE join parses the swept sid out of `detail` and is deliberately
    # non-sargable: pane_events is per-session-pruned and close-stale rows are
    # rare, so the scan is tiny — a dedicated swept_sid column (the audit DB's
    # first ALTER-style migration) was judged not worth it. If close-stale
    # volume ever grows, add the column at the write site
    # (core/hostpane.py close_stale_mirrors) instead of tuning this query.
    ("stale-mirror sweep closed a LIVE session's mirror (pane hijack)",
     "SELECT p.ts, p.session_id, p.detail FROM pane_events p JOIN sessions s "
     "ON p.detail LIKE ('closed sid=' || s.session_id || ' %') "
     "WHERE p.action='close-stale' AND s.ended_at IS NULL "
     "AND s.session_id != p.session_id "
     "AND (p.session_id=? OR s.session_id=?) ORDER BY p.ts", 2),
    ("tab colour applies where kitten @ failed",
     "SELECT ts, dispatch, new_state, reason FROM tab_transitions "
     "WHERE session_id=? AND reason LIKE '%kitten @ failed%' ORDER BY ts", 1),
    # A --resume/--continue SessionStart should find the parked *.keep state DB and
    # log a `restore-history` (or, after a crash with no SessionEnd, find the DB
    # still live: `reuse-live-db`). A `fresh-db` row on a source=resume start
    # means the history was lost — the mirror came back empty.
    # park_db/decide_log_fate audit their move failures as DISTINCT fates now
    # (2026-07-15): 'park-failed (kept live)' = SessionEnd could not move the
    # state DB out (ENOSPC/EPERM/blocked destination — the paired errors row has
    # the traceback), so the live path persists, parked() never fires, and the
    # scorebar/codex-watcher pollers keep running as orphans; 'restore-failed
    # (park kept)' = the resume's move-back failed, the park stays for a later
    # try and the session started fresh. Either row is a real filesystem problem.
    ("state-DB park/restore move failed (orphaned pollers / history not restored)",
     "SELECT ts, action, content FROM state_files WHERE session_id=? AND "
     "action IN ('park-failed (kept live)', 'restore-failed (park kept)') "
     "ORDER BY ts", 1),
    # The reuse-live-db zombie shape (docs/mirror-pane.md): a bg/fg tailer that
    # outlived SessionEnd's park kept pumping, and its first post-park emit
    # RECREATED an empty state DB at the live path — the next resume then saw
    # 'reuse-live-db' and trusted the empty DB while the real history sat in the
    # park. Current builds exit 'state-db-parked (session end)' before pumping,
    # so a bg/fg stream that ended AFTER the session's keep-history park with any
    # OTHER reason is the regression. (No keep-history row -> subquery NULL ->
    # comparison false -> clean, by construction.)
    ("bg/fg tailer outlived the park (zombie recreated the state DB)",
     "SELECT s.id, s.kind, s.end_reason, s.ended_at FROM streams s WHERE "
     "s.session_id=? AND s.kind IN ('bg','fg') AND s.ended_at IS NOT NULL "
     "AND COALESCE(s.end_reason,'') != 'state-db-parked (session end)' "
     "AND s.ended_at > (SELECT MAX(ts) FROM state_files WHERE session_id=? "
     "AND action='keep-history')", 2),
    ("resume that lost its mirror history (fresh-db on source=resume)",
     "SELECT h.ts FROM hook_events h WHERE h.session_id=? AND "
     "h.hook='SessionStart' AND json_extract(h.payload,'$.source')='resume' "
     "AND EXISTS (SELECT 1 FROM state_files f WHERE f.session_id=h.session_id "
     "AND f.action='fresh-db' AND abs(f.ts - h.ts) < 30)", 1),
    # Claude Code can FORK the sid on --resume: SessionStart fires under the OLD
    # sid while every later event carries a NEW sid that never gets its own
    # SessionStart (see plugins/claude_code/adopt.py). On a current build the
    # fork's first event ADOPTS the predecessor — renaming its state DB,
    # retagging the panes, and writing the sessions row the fork never got.
    # Functional hook traffic under a sid with NO sessions row means the fork
    # was never adopted: its events fed a state DB nothing renders while the
    # old sid's mirror/scorebar/tab froze (the 19a42746→ebcecfcc shape). Every
    # legitimate session — interactive, headless, agents-view — gets a sessions
    # row from its own SessionStart (A.session_start runs before the pane-skip
    # check), so this only fires on an unadopted fork.
    ("hook traffic under a sid with no sessions row (resume fork never adopted)",
     "SELECT MIN(ts), MAX(ts), COUNT(*) FROM hook_events WHERE session_id=? "
     "AND handler='subscriber' AND NOT EXISTS "
     "(SELECT 1 FROM sessions WHERE session_id=?) HAVING COUNT(*) > 0", 2),
    # A genuine sid-fork NEVER gets its own SessionStart — that is the whole basis
    # for adoption. So a sid that ADOPTED a predecessor yet ALSO has its own
    # SessionStart is a MIS-adoption: an independent new session wrongly consumed a
    # concurrent same-cwd session's adopt_pending note and stole its panes (live
    # bug: 507fc4c8's pre-SessionStart InstructionsLoaded adopted the unrelated live
    # db081e65 — toggling 507's mirror then toggled db081e65's). Fixed by marking the
    # sid on InstructionsLoaded (adopt.py); a non-empty row here is the regression.
    ("adopted a predecessor despite having its OWN SessionStart (mis-adoption — pane theft)",
     "SELECT a.ts, a.decision FROM hook_events a WHERE a.session_id=? "
     "AND a.decision LIKE 'adopt:%' AND EXISTS (SELECT 1 FROM hook_events s "
     "WHERE s.session_id=a.session_id AND s.hook='SessionStart') ORDER BY a.ts", 1),
    # Token/cost spend must arrive as an ATTRIBUTED action: 'bump-otel' (the OTLP
    # receiver, keyed by session.id + query_source) or 'bump-agent' (codex's own
    # rollout fold — codex runs in a separate process OTEL can't see). A plain 'bump'
    # carrying a tokens/cost delta means some producer bypassed attribution — the
    # scoreboard number it fed can only be traced by timestamp correlation.
    ("unattributed token/cost bumps (should be bump-agent with meta)",
     "SELECT ts, content FROM state_files WHERE session_id=? AND action='bump' "
     "AND (json_extract(content, '$.deltas.tokens') IS NOT NULL "
     "OR json_extract(content, '$.deltas.cost') IS NOT NULL) ORDER BY ts", 1),
    # Cost is OTEL-authoritative; the transcript fold survives ONLY as a SessionEnd
    # fallback that must fire ONLY when the receiver wrote nothing (otel_seen==0). If a
    # session has BOTH a 'folded transcript fallback' SessionEnd decision AND bump-otel
    # rows, the fallback fired despite OTEL data — a double-count regression (the
    # otel_seen gate in stop_fmt broke). A healthy session has exactly one source.
    ("SessionEnd transcript fallback fired despite OTEL data (double-count regression)",
     "SELECT ts, decision FROM hook_events WHERE session_id=? AND "
     "handler='claude-stop-fmt.py' AND decision LIKE 'otel absent%' AND EXISTS "
     "(SELECT 1 FROM state_files f WHERE f.session_id=? AND f.action='bump-otel') "
     "ORDER BY ts", 2),
    # The inverse wiring failure: SessionEnd fired (the subscriber row proves the
    # dispatcher ran) but claude-stop-fmt.py never wrote its SessionEnd decision —
    # the stop-fold step was dropped from the dispatch plan. stop-fmt ALWAYS
    # decides on SessionEnd ('otel authoritative … fold skipped' or 'otel absent —
    # folded transcript fallback'), so its absence is the tell. Scoped to sessions
    # with NO bump-otel rows: with OTEL data the cost is intact anyway; without it
    # the missing fallback fold means the session's cost was silently lost.
    ("SessionEnd fired but the stop-fold never ran (fallback dropped — cost lost)",
     "SELECT h.ts FROM hook_events h WHERE h.session_id=? AND "
     "h.hook='SessionEnd' AND h.handler='subscriber' AND NOT EXISTS "
     "(SELECT 1 FROM state_files f WHERE f.session_id=? AND f.action='bump-otel') "
     "AND NOT EXISTS (SELECT 1 FROM hook_events e WHERE e.session_id=? AND "
     "e.hook='SessionEnd' AND e.handler='claude-stop-fmt.py')", 3),
    # Cross-session contamination: everything is keyed by session_id, EXCEPT
    # background-job detection, which is per-project (cwd slug) — two sessions in
    # one directory can cross-talk (CLAUDE.md). A task_id (streams) or slot claim
    # token (slots.marker_path — it embeds the mirror-log path, so a foreign sid
    # can only appear via a mis-keyed write) under >1 session_id is that shape.
    # Scoped to groups involving THIS sid so `anomalies <sid>` stays per-session.
    # Benign exception: a codex run taken over from a DEAD session
    # (codex-claim steal-stale) legitimately streams under the new sid.
    ("cross-session contamination (task_id/slot token under more than one sid)",
     "SELECT src, key, sids FROM ("
     "  SELECT 'stream-task' AS src, task_id AS key, "
     "         GROUP_CONCAT(DISTINCT session_id) AS sids, "
     "         SUM(session_id=?) AS mine FROM streams WHERE task_id != '' "
     "  GROUP BY task_id HAVING COUNT(DISTINCT session_id) > 1 "
     "  UNION ALL "
     "  SELECT 'slot-token', marker_path, GROUP_CONCAT(DISTINCT session_id), "
     "         SUM(session_id=?) FROM slots WHERE marker_path != '' "
     "  GROUP BY marker_path HAVING COUNT(DISTINCT session_id) > 1"
     ") WHERE mine > 0", 2),
    # The fixed-2026-07-04 duplicated-block shape: a tailer's unbounded read()
    # with pos=size re-read bytes appended mid-read, painting the same chunk
    # twice on the NEXT poll — identical ops seconds apart. Scoped to gut ops
    # (block body lines) long enough (>60 chars) that an identical repeat within
    # DUP_OPS_WINDOW_S is a re-read, not a command printing a short line twice.
    ("duplicated mirror ops (identical block lines painted twice within %gs)"
     % DUP_OPS_WINDOW_S,
     "SELECT substr(op,1,80), COUNT(*) FROM ops WHERE session_id=? "
     "AND op LIKE '%\"gut\"%' AND length(op) > 60 "
     "GROUP BY op HAVING COUNT(*) > 1 AND MAX(ts) - MIN(ts) < "
     + str(DUP_OPS_WINDOW_S), 1),
    # The OTLP receiver is a long-lived singleton that caches its state-DB
    # connection. A park (os.replace db -> db.keep) + resume swaps the inode under
    # the path, so a receiver that didn't revalidate kept writing token counters to
    # the ORPHANED *.keep inode while the scorebar read the fresh live DB — a silent
    # divergence (no error; both files are valid DBs). Tell: bump-otel rows exist for
    # the session (OTEL landed) yet the LIVE state DB has no tk_read/tokens counter.
    # core/state._connect now revalidates by st_ino, so a non-empty row here is that
    # regression (or the receiver holding an fd on a *.keep path — check `lsof`).
    lambda conn, section, sid: _section_otel_stranded(conn, section, sid),
]


def cli_anomalies(sid):
    """Canned queries for known bug signatures (the ANOMALY_SECTIONS registry
    above). Each prints a section; empty = clean."""
    conn = _connect()
    if conn is None:
        print("audit db unavailable"); return

    def section(title, q, params=()):
        rows = conn.execute(q, params).fetchall()
        print(f"== {title}: {len(rows)}")
        for r in rows:
            print("   " + " | ".join("" if v is None else str(v) for v in r))

    for entry in ANOMALY_SECTIONS:
        if callable(entry):
            entry(conn, section, sid)
        else:
            title, q, nparams = entry
            section(title, q, (sid,) * nparams)


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
        db = P.state_db(P.mirror_log(sid))
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


def cli_sql(argv):
    """`sql` — free-form READ-ONLY SQL. Opens the DB `mode=ro` so a debugging
    query can never mutate the evidence (or create the file); ad-hoc fixups go
    through the explicit `sql-write` command instead."""
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True, timeout=3.0)
    except Exception:
        print("audit db unavailable"); return
    q = argv[2] if len(argv) > 2 else ""
    try:
        cur = conn.execute(q)
        headers = [c[0] for c in cur.description] if cur.description else []
        _print_rows(cur.fetchall(), headers)
    except Exception as e:
        print(f"sql error: {e}")
    finally:
        conn.close()


def cli_sql_write(argv):
    """`sql-write` — free-form READ-WRITE SQL for deliberate manual fixups
    (e.g. closing a stuck "(open)" session row). Separate from `sql` so a
    routine debugging query can never mutate the audit trail by accident."""
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


# --------------------------------------------------------------- CLI dispatch
# Each handler owns its own argv parsing (argv is the FULL argv; argv[1] is the
# command name). write=True marks the fire-and-forget entry points hooks/shell
# invoke — the bin/claude-audit.py CLI derives its never-fail-loudly swallow set
# from WRITE_COMMANDS, so the two can't drift apart again.

def _cmd_session_start(argv):
    session_start(_read_stdin_json())


def _cmd_session_end(argv):
    session_end(_read_stdin_json())
    prune()


def _cmd_hook(argv):
    # hook <handler> [<decision>], payload on stdin
    hook_event(_read_stdin_json(), handler=(argv[2] if len(argv) > 2 else None),
               decision=(argv[3] if len(argv) > 3 else ""))


def _cmd_transition(argv):
    # transition <sid> <win> <dispatch> <prev> <new> <applied> [reason]
    a = argv[2:] + [""] * 7
    transition(a[0], a[1], a[2], a[3], a[4], a[5] == "1", a[6])


def _cmd_error(argv):
    # error <sid> <script> <message>
    a = argv[2:] + [""] * 3
    # getppid, unlike every other writer's getpid: this runs in a short-lived
    # `bin/claude-audit.py error …` CLI subprocess invoked FROM a shell script — the
    # diagnostic identity is the invoking shell process, not this throwaway
    # python pid (which is gone before anyone could correlate it).
    event("errors", session_id=a[0], script=a[1] or "shell", func="",
          traceback=a[2], context="", pid=os.getppid())


def _cmd_pane(argv):
    # pane <sid> <action> <ok 0|1> [detail]
    a = argv[2:] + [""] * 4
    pane(a[0], a[1], a[2] == "1", a[3])


def _cmd_state_file(argv):
    # state-file <log> <path> <action> [content]
    a = argv[2:] + [""] * 4
    state_file(a[0], a[1], a[2], a[3])


def _cmd_sessions(argv):
    cli_sessions(int(argv[2]) if len(argv) > 2 else 20)


def _cmd_timeline(argv):
    # timeline <sid> [limit] [--ops] [--otel]
    flags = {a for a in argv[2:] if a.startswith("--")}
    args = [a for a in argv[2:] if not a.startswith("--")]
    cli_timeline(args[0] if args else "",
                 int(args[1]) if len(args) > 1 else 2000,
                 with_ops="--ops" in flags, with_otel="--otel" in flags)


def _cmd_errors(argv):
    cli_errors(argv[2] if len(argv) > 2 else "")


def _cmd_anomalies(argv):
    cli_anomalies(argv[2] if len(argv) > 2 else "")


def _cmd_otel(argv):
    cli_otel(argv[2] if len(argv) > 2 else "")


def _cmd_prune(argv):
    n = prune(int(argv[2]) if len(argv) > 2 else PRUNE_DAYS)
    print(f"pruned {n} session(s)")


# NB: the old `stream-start`/`stream-end` CLI branches were removed — every
# tailer records its lifecycle in-process via stream_start()/stream_end()
# (core/tail.py stream_lifecycle); no repo script, ~/.claude/settings.json
# entry, or open-actions.conf action invoked them.
COMMANDS = {
    # write entry points (fired from hooks/shell — must never fail loudly)
    "session-start": (_cmd_session_start, True),
    "session-end":   (_cmd_session_end,   True),
    "hook":          (_cmd_hook,          True),
    "transition":    (_cmd_transition,    True),
    "error":         (_cmd_error,         True),
    "pane":          (_cmd_pane,          True),
    "state-file":    (_cmd_state_file,    True),
    # read/query commands (interactive — errors should surface)
    "sessions":      (_cmd_sessions,      False),
    "timeline":      (_cmd_timeline,      False),
    "errors":        (_cmd_errors,        False),
    "anomalies":     (_cmd_anomalies,     False),
    "otel":          (_cmd_otel,          False),
    "sql":           (cli_sql,            False),
    "sql-write":     (cli_sql_write,      False),
    "prune":         (_cmd_prune,         False),
}

WRITE_COMMANDS = frozenset(name for name, (_, write) in COMMANDS.items() if write)


def _usage():
    # Derived from COMMANDS so the list can never go stale; the docstring above
    # carries the prose + per-command arg synopsis.
    reads = sorted(n for n, (_, w) in COMMANDS.items() if not w)
    writes = sorted(n for n, (_, w) in COMMANDS.items() if w)
    return ((__doc__ or "").rstrip()
            + "\n\nquery commands:  " + " ".join(reads)
            + "\nwrite commands:  " + " ".join(writes))


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    entry = COMMANDS.get(cmd)
    if entry is None:
        print(_usage())
        return
    entry[0](argv)


# The CLI entry point lives in bin/claude-audit.py (main() above
# is what it calls) — a package module can't be executed directly.
