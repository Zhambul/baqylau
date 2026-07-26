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
$CLAUDE_AUDIT_DIR or ~/.claude/baqylau-audit/audit.db — one global DB, all sessions,
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
    return d or os.path.expanduser("~/.claude/baqylau-audit")


def db_path():
    return os.path.join(audit_dir(), "audit.db")


def spool_path():
    return os.path.join(audit_dir(), "spool.jsonl")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  session_id TEXT PRIMARY KEY, cwd TEXT, project_slug TEXT, transcript_path TEXT,
  mirror_log TEXT, kitty_window_id TEXT, started_at REAL, ended_at REAL,
  end_reason TEXT, env TEXT,
  -- the session's ORIGINAL cwd, frozen at SessionStart. Unlike `cwd` (which
  -- session_paths re-stamps on every event as the session relocates), this
  -- never changes, so the dashboard groups a session under where it STARTED
  -- even after the agent cd's away (docs/dashboard.md "Grouping and titles").
  -- Added by _migrate() on DBs that predate it.
  start_cwd TEXT);
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
-- sessionapi.sid_chain() resolves the adopt map on nearly every read; without
-- this the WHERE action='adopt' scan walks the whole table (~19ms warm, ~700ms
-- cold at 1GB) and the dashboard pays it ~16x per /api/session request.
CREATE INDEX IF NOT EXISTS ix_state_act  ON state_files(action);
CREATE INDEX IF NOT EXISTS ix_pane_sid   ON pane_events(session_id, ts);
CREATE INDEX IF NOT EXISTS ix_otel_sid   ON otel(session_id, ts);
"""


def _migrate(conn):
    """Additive COLUMN migrations for DBs created before a column existed. The
    base _SCHEMA is CREATE TABLE IF NOT EXISTS, so adding a whole new TABLE is
    free — but a new COLUMN on an existing table needs an explicit ALTER. Each
    step is guarded by PRAGMA table_info, so it's idempotent (every open
    re-checks; re-running is a no-op). Writers only — the read-only consumers
    (dashboard / core.sessionapi) can't ALTER, so they tolerate the column still
    being absent right after an upgrade (see core.sessionapi.sessions)."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    if "start_cwd" not in have:
        conn.execute("ALTER TABLE sessions ADD COLUMN start_cwd TEXT")
        # Backfill: the TRUE original cwd of a pre-existing session lives only
        # in its SessionStart hook_events.payload; its last-known `cwd` is the
        # best cheap approximation. Sessions started after the upgrade capture
        # start_cwd exactly (session_start below).
        conn.execute("UPDATE sessions SET start_cwd=cwd WHERE start_cwd IS NULL")


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
        _migrate(conn)
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
    # "session_paths" refreshes the sessions row's location columns (see
    # session_paths() below). Spool-safe like the two above: a locked DB at the
    # relocation moment replays the UPDATE at ingest (idempotent — re-applying
    # the same values is a no-op).
    if table == "session_paths":
        return conn.execute(
            "UPDATE sessions SET cwd=?, project_slug=?, transcript_path=?"
            " WHERE session_id=?",
            (cols.get("cwd"), cols.get("project_slug"),
             cols.get("transcript_path"), cols.get("session_id")))
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


def connect():
    """The audit DB connection every write goes through — schema ensured, spool
    ingested, None when sqlite gave up. PUBLIC because the read/report tier is a
    separate module now (core/auditcli.py) and must not reach for `_connect`;
    inside this module the private name stays the one that's called."""
    return _connect()


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
                start_cwd=d.get("cwd") or os.getcwd(),
                project_slug=_project_slug(d.get("cwd") or os.getcwd()),
                transcript_path=d.get("transcript_path") or "",
                mirror_log=P.mirror_log(sid),
                kitty_window_id=os.environ.get("KITTY_WINDOW_ID") or "",
                started_at=time.time(), env=json.dumps(envkeys, ensure_ascii=False))
    if conn is None:
        _spool("sessions", cols)
        return
    try:
        conn.execute(
            "INSERT INTO sessions(session_id, cwd, start_cwd, project_slug,"
            " transcript_path, mirror_log, kitty_window_id, started_at, env)"
            " VALUES(:session_id, :cwd, :start_cwd, :project_slug, :transcript_path,"
            " :mirror_log, :kitty_window_id, :started_at, :env)"
            # start_cwd is deliberately absent from the UPDATE: it's the frozen
            # ORIGINAL cwd, so a resume (same sid, new SessionStart) keeps the
            # first value — session_paths never touches it either.
            " ON CONFLICT(session_id) DO UPDATE SET started_at=excluded.started_at,"
            " transcript_path=excluded.transcript_path, env=excluded.env,"
            # the session is alive again (a resume restarts it under the SAME
            # sid) — a lingering ended_at/end_reason would say otherwise
            " ended_at=NULL, end_reason=NULL,"
            # a resumed session reopens in a NEW kitty window; without this the
            # dashboard's win->session map (toasts) points at the dead window
            # forever. Guarded: a daemon-origin SessionStart has a SCRUBBED env
            # (no KITTY_WINDOW_ID) and must not erase a valid window id.
            " kitty_window_id=CASE WHEN excluded.kitty_window_id<>''"
            " THEN excluded.kitty_window_id ELSE kitty_window_id END",
            cols)
        conn.commit()
    except Exception:
        _spool("sessions", cols)


def _project_slug(cwd):
    return os.path.basename((cwd or "").rstrip("/"))


def session_paths(d):
    """Keep the sessions row's location columns (cwd/project_slug/transcript_path)
    in step with what the hooks report. Claude Code RELOCATES a session's
    transcript when its cwd moves to another project directory — empirically
    confirmed 2026-07-18 via EnterWorktree: the transcript moves to the worktree
    cwd's projects/ slug dir and every later hook payload carries the new path —
    so the start-time row goes stale and every consumer of it (the dashboard's
    title/ctx-probe/git chips, web rename, sessionapi) points at a file that no
    longer exists. Called by the dispatcher once per event; skips agent_id events
    (an isolated subagent's cwd is its OWN worktree, not the session's — the
    main-session-only invariant) and no-ops when nothing changed. On an actual
    change it also leaves a `session-paths` state_files row (old -> new), so the
    relocation moment is visible evidence, not just a silent UPDATE."""
    if not enabled() or not isinstance(d, dict) or d.get("agent_id"):
        return
    sid, tpath, cwd = sid_of(d), d.get("transcript_path") or "", d.get("cwd") or ""
    if not sid or not (tpath or cwd):
        return
    conn = _connect()
    if conn is None:
        return          # next event retries — the payload keeps carrying the paths
    try:
        row = conn.execute("SELECT cwd, transcript_path FROM sessions"
                           " WHERE session_id=?", (sid,)).fetchone()
    except Exception:
        return          # no schema yet / unreadable — nothing to refresh
    if row is None:     # pre-SessionStart event (or un-adopted fork): no row yet
        return
    old_cwd, old_tpath = row[0] or "", row[1] or ""
    new_cwd, new_tpath = cwd or old_cwd, tpath or old_tpath
    if (new_cwd, new_tpath) == (old_cwd, old_tpath):
        return
    event("session_paths", session_id=sid, cwd=new_cwd,
          project_slug=_project_slug(new_cwd), transcript_path=new_tpath)
    state_file(P.mirror_log(sid), new_tpath, "session-paths",
               {"cwd": new_cwd, "cwd_old": old_cwd,
                "transcript_path": new_tpath, "transcript_path_old": old_tpath})


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
