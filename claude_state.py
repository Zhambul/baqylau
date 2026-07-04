#!/usr/bin/env python3
# claude_state.py — per-session runtime coordination state in SQLite.
#
# The mirror's Python-side coordination state used to be a zoo of small files next
# to the mirror log: a flock'd read-modify-write JSON for the scoreboard
# (.stats.json), a diffed-snapshot JSON for the team-message tracker (.msgs.json),
# five per-agent map files (sub.slot/.desc/.pos/.done + desc.queue), and
# write/poll/remove hand-off markers (.fg-live, <src>.done). Each was individually
# racy in its own way (torn JSON reads, swapped queue entries, stale markers). This
# module replaces them with ONE per-session SQLite DB at
#
#     /tmp/claude-mirror-<sid>.log.state.db
#
# WAL mode + BEGIN IMMEDIATE transactions give the atomic increments and take-once
# semantics those dances approximated. The "<sid>.log" path is a historical KEY —
# no log file exists: the mirror's paint-op stream itself lives in this DB's `ops`
# table (claude-mirror.py polls it by rowid), and the palette/liveness slot
# markers live in its `live` table (claude_slots.py; claude-tab-status.py queries
# it via the sqlite3 CLI). The DB FILE's existence is the session-alive signal —
# parked as *.keep at SessionEnd (claude-split.py), restored on resume, so the
# scorebar/codex-watcher exit when the path vanishes and a resumed session
# replays its history. Living in /tmp keeps it self-clearing on reboot. This DB
# is RUNTIME state — load-bearing for behavior — and is deliberately SEPARATE
# from the audit DB (~/.claude/kitty-audit), which must stay fire-and-forget.
#
# Related DBs: window-keyed tab state + watcher pid locks live in the GLOBAL
# /tmp/claude-kitty-tab.db (a window outlives any one session — see
# claude-tab-status.py); remembered pane sizes in ~/.claude/kitty-mirror.db
# (must survive reboots). What stays as plain files is only what physics
# demands: the fg tee'd .out streams + their .done sentinels (written by the
# rewritten command itself) and kitty's own sockets.
#
# The codex cross-session claims (previously O_EXCL files in
# $TMPDIR/codex-companion/<slug>/mirror-claims/) use the same table machinery in a
# SHARED per-repo DB — see claims_db() / claim().
#
# Every function swallows exceptions: runtime state is best-effort and must never
# break a hook or a streamer. Callers audit their own transitions.
import errno, json, os, sqlite3, time

_CONNS = {}                     # path -> connection (streamers are long-lived)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ops(id INTEGER PRIMARY KEY AUTOINCREMENT, op TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS counters(key TEXT PRIMARY KEY, val REAL NOT NULL);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, val TEXT);
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS messages(
  msg_id TEXT PRIMARY KEY, read INTEGER, sender TEXT, recipient TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS agents(
  agent_id TEXT PRIMARY KEY, slot INTEGER, desc TEXT, pos INTEGER,
  done INTEGER DEFAULT 0, start_ts REAL);
CREATE TABLE IF NOT EXISTS queue(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT);
CREATE TABLE IF NOT EXISTS handoffs(key TEXT PRIMARY KEY, val TEXT);
CREATE TABLE IF NOT EXISTS claims(key TEXT PRIMARY KEY, pid INTEGER);
CREATE TABLE IF NOT EXISTS live(
  kind TEXT, key TEXT, pid INTEGER, idx INTEGER, start_ts REAL,
  PRIMARY KEY(kind, key));
"""


def db_path(log):
    return log + ".state.db"


def _connect(path):
    conn = _CONNS.get(path)
    if conn is not None:
        return conn
    try:
        conn = sqlite3.connect(path, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA)
        conn.commit()
        _CONNS[path] = conn
        return conn
    except Exception:
        return None


def connect(log):
    return _connect(db_path(log))


# --- mirror paint ops (was the append-only JSONL mirror log) ------------------------
# The mirror's paint-op stream lives in the `ops` table: producers (hooks, tailers)
# INSERT rows, the renderer polls `id > last_seen`. One transaction per emit() keeps a
# block of ops contiguous relative to concurrent producers — the same atomicity the
# single O_APPEND write() gave the old file. The DB file's existence doubles as the
# session-alive signal the old log path provided (scorebar/codex-watcher exit when it
# vanishes at SessionEnd — the DB is parked as *.keep, so the path does disappear).

def ops_append(log, ops):
    """Append paint ops (dicts) as one atomic block. Returns True on success."""
    if not ops:
        return True
    conn = connect(log)
    if conn is None:
        return False
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany("INSERT INTO ops(op) VALUES(?)",
                         [(json.dumps(o, ensure_ascii=False),) for o in ops])
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def ops_after(log, last_id):
    """(new_last_id, [op, ...]) — every op with id > last_id, in insertion order.
    A max id BELOW last_id means the DB was recreated (fresh session reusing the
    key): the caller should reset and re-read from 0. Returns (last_id, []) on
    failure so a transient error never looks like a reset."""
    conn = connect(log)
    if conn is None:
        return last_id, []
    try:
        rows = conn.execute("SELECT id, op FROM ops WHERE id > ? ORDER BY id",
                            (last_id,)).fetchall()
        if not rows:
            top = conn.execute("SELECT COALESCE(MAX(id), 0) FROM ops").fetchone()[0]
            if top < last_id:
                return -1, []               # recreated DB -> signal a reset
            return last_id, []
        out = []
        for _id, s in rows:
            try:
                out.append(json.loads(s))
            except Exception:
                continue
        return rows[-1][0], out
    except Exception:
        return last_id, []


# --- scoreboard counters (was .stats.json under flock) -----------------------------

def incr(log, tool=None, file=None, **deltas):
    """Atomically add each numeric delta to its counter, bump tools[tool], record a
    touched file, stamp 'start' on first write, and advance the change counter 'v'
    (what the scorebar polls instead of a file mtime). Returns the updated stats
    dict ({} on failure) — same shape the old sidecar had."""
    conn = connect(log)
    if conn is None:
        return {}
    try:
        conn.execute("BEGIN IMMEDIATE")
        for k, v in deltas.items():
            conn.execute("INSERT INTO counters(key, val) VALUES(?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET val = val + excluded.val",
                         (k, float(v)))
        if tool:
            conn.execute("INSERT INTO counters(key, val) VALUES(?, 1) "
                         "ON CONFLICT(key) DO UPDATE SET val = val + 1",
                         ("tool:" + tool,))
        if file:
            conn.execute("INSERT OR IGNORE INTO files(path) VALUES(?)", (file,))
        conn.execute("INSERT OR IGNORE INTO counters(key, val) VALUES('start', ?)",
                     (int(time.time()),))
        conn.execute("INSERT INTO counters(key, val) VALUES('v', 1) "
                     "ON CONFLICT(key) DO UPDATE SET val = val + 1")
        conn.commit()
        return stats(log)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return {}


def stats(log):
    """The scoreboard state as a dict in the OLD .stats.json shape — counters,
    tools{}, files (unique count), txlast{} — so claude_ops.scoreboard_parts and the
    audit's "resulting totals" snapshots work unchanged. {} on failure."""
    conn = connect(log)
    if conn is None:
        return {}
    try:
        st, tools = {}, {}
        for k, v in conn.execute("SELECT key, val FROM counters"):
            if k.startswith("tool:"):
                tools[k[5:]] = int(v)
            elif k == "v":
                continue
            elif k in ("start", "txpos", "commands", "failed", "added", "removed"):
                st[k] = int(v)
            else:
                st[k] = v
        if tools:
            st["tools"] = tools
        n = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        if n:
            st["files"] = n
        tx = kv_get(log, "txlast")
        if isinstance(tx, dict):
            st["txlast"] = tx
        return st
    except Exception:
        return {}


def version(log):
    """Monotonic change counter — bumps on every incr(); the scorebar's repaint
    signal (WAL commits don't reliably touch the db file's mtime). 0 when absent."""
    conn = connect(log)
    if conn is None:
        return 0
    try:
        row = conn.execute("SELECT val FROM counters WHERE key='v'").fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


# --- generic kv (txlast, watcher pid locks, …) --------------------------------------

def kv_get(log, key):
    conn = connect(log)
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT val FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None
    except Exception:
        return None


def kv_set(log, key, obj):
    conn = connect(log)
    if conn is None:
        return False
    try:
        conn.execute("INSERT INTO kv(key, val) VALUES(?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET val = excluded.val",
                     (key, json.dumps(obj, ensure_ascii=False)))
        conn.commit()
        return True
    except Exception:
        return False


def kv_del(log, key):
    conn = connect(log)
    if conn is None:
        return
    try:
        conn.execute("DELETE FROM kv WHERE key=?", (key,))
        conn.commit()
    except Exception:
        pass


# --- team-message tracker (was .msgs.json) ------------------------------------------

def msgs_state(log):
    """(delivered, read, live) — live is {msg_id: [read, from, recipient, summary]},
    the same shape update_messages tracked in the old sidecar."""
    conn = connect(log)
    if conn is None:
        return 0, 0, {}
    try:
        d = conn.execute("SELECT val FROM counters WHERE key='msg_delivered'").fetchone()
        r = conn.execute("SELECT val FROM counters WHERE key='msg_read'").fetchone()
        live = {mid: [bool(rd), s or "?", rc or "?", su or ""]
                for mid, rd, s, rc, su in
                conn.execute("SELECT msg_id, read, sender, recipient, summary FROM messages")}
        return int(d[0]) if d else 0, int(r[0]) if r else 0, live
    except Exception:
        return 0, 0, {}


def msgs_write(log, delivered, read, live):
    """Replace the live set + cumulative counters in one transaction (single writer —
    the lone scorebar — same as the old sidecar)."""
    conn = connect(log)
    if conn is None:
        return
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM messages")
        conn.executemany(
            "INSERT INTO messages(msg_id, read, sender, recipient, summary) "
            "VALUES(?,?,?,?,?)",
            [(mid, 1 if ent[0] else 0, ent[1], ent[2], ent[3])
             for mid, ent in live.items()])
        for k, v in (("msg_delivered", delivered), ("msg_read", read)):
            conn.execute("INSERT INTO counters(key, val) VALUES(?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET val = excluded.val", (k, v))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


# --- per-agent record (was sub.slot.* / sub.desc.* / sub.pos.* / sub.done.*) --------

def agent_get(log, agent_id):
    """The agent's row as a dict (slot/desc/pos/done/start_ts), or {}."""
    conn = connect(log)
    if conn is None:
        return {}
    try:
        row = conn.execute("SELECT slot, desc, pos, done, start_ts FROM agents "
                           "WHERE agent_id=?", (agent_id,)).fetchone()
        if not row:
            return {}
        return {"slot": row[0], "desc": row[1], "pos": row[2],
                "done": bool(row[3]), "start_ts": row[4]}
    except Exception:
        return {}


def agent_set(log, agent_id, **fields):
    """Upsert individual fields of the agent's record (slot, desc, pos, done,
    start_ts). Fields not passed are left as they are."""
    allowed = {k: v for k, v in fields.items()
               if k in ("slot", "desc", "pos", "done", "start_ts")}
    if not allowed:
        return
    conn = connect(log)
    if conn is None:
        return
    try:
        sets = ", ".join(f"{k} = excluded.{k}" for k in allowed)
        cols = ["agent_id"] + list(allowed)
        conn.execute(
            f"INSERT INTO agents({', '.join(cols)}) "
            f"VALUES({', '.join('?' * len(cols))}) "
            f"ON CONFLICT(agent_id) DO UPDATE SET {sets}",
            [agent_id] + [int(v) if k == "done" else v for k, v in allowed.items()])
        conn.commit()
    except Exception:
        pass


# --- description hand-off queue (was desc.queue, an flock'd FIFO file) ---------------

def desc_push(log, text):
    text = " ".join((text or "").split())
    if not text:
        return
    conn = connect(log)
    if conn is None:
        return
    try:
        conn.execute("INSERT INTO queue(text) VALUES(?)", (text,))
        conn.commit()
    except Exception:
        pass


def desc_pop(log):
    conn = connect(log)
    if conn is None:
        return ""
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT id, text FROM queue ORDER BY id LIMIT 1").fetchone()
        if row:
            conn.execute("DELETE FROM queue WHERE id=?", (row[0],))
        conn.commit()
        return row[1] if row else ""
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return ""


# --- hand-off records (was the .fg-live marker + <src>.done sentinel files) ---------

def hand_put(log, key, obj):
    """Publish a hand-off record (overwrites). Returns True on success."""
    conn = connect(log)
    if conn is None:
        return False
    try:
        conn.execute("INSERT INTO handoffs(key, val) VALUES(?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET val = excluded.val",
                     (key, json.dumps(obj, ensure_ascii=False)))
        conn.commit()
        return True
    except Exception:
        return False


def hand_peek(log, key):
    conn = connect(log)
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT val FROM handoffs WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None
    except Exception:
        return None


def hand_take(log, key):
    """Atomically consume a hand-off record: returns the object and deletes it, or
    None if absent — the take-once the old poll/read/remove sentinel dance meant."""
    conn = connect(log)
    if conn is None:
        return None
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT val FROM handoffs WHERE key=?", (key,)).fetchone()
        if row:
            conn.execute("DELETE FROM handoffs WHERE key=?", (key,))
        conn.commit()
        return json.loads(row[0]) if row else None
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return None


def hand_del(log, key):
    conn = connect(log)
    if conn is None:
        return
    try:
        conn.execute("DELETE FROM handoffs WHERE key=?", (key,))
        conn.commit()
    except Exception:
        pass


# --- pid-liveness claims (was O_EXCL pid files: codex mirror-claims + watch lock) ----

def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError as e:
        return e.errno == errno.EPERM


def claim(db, key, pid=None):
    """Claim `key` for `pid` (default: this process) in the claims table of the DB at
    `db` (a full path — the codex claims DB is shared per-repo, not per-session).
    Returns 'claim', 'steal-stale', or 'claim-denied:<holder-pid>'. A holder whose
    pid is dead is taken over, same as the old O_EXCL marker files."""
    pid = pid or os.getpid()
    conn = _connect(db)
    if conn is None:
        return "claim-denied:no-db"
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT pid FROM claims WHERE key=?", (key,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO claims(key, pid) VALUES(?, ?)", (key, pid))
            conn.commit()
            return "claim"
        holder = int(row[0] or 0)
        if holder and holder != pid and _alive(holder):
            conn.commit()
            return f"claim-denied:{holder}"
        conn.execute("UPDATE claims SET pid=? WHERE key=?", (pid, key))
        conn.commit()
        return "claim" if holder == pid else "steal-stale"
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return "claim-denied:error"


def release_claim(db, key, pid=None):
    pid = pid or os.getpid()
    conn = _connect(db)
    if conn is None:
        return
    try:
        conn.execute("DELETE FROM claims WHERE key=? AND pid=?", (key, pid))
        conn.commit()
    except Exception:
        pass
