# core/state.py — per-session runtime coordination state in SQLite.
# (Historical top-level name: claude_state.py — that compat shim is deleted.)
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
# markers live in its `live` table (core/slots.py; claude-tab-status.py queries
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
from contextlib import contextmanager

from core import paths as P

_CONNS = {}                     # path -> (connection, st_ino) (streamers are long-lived)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ops(id INTEGER PRIMARY KEY AUTOINCREMENT, op TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS counters(key TEXT PRIMARY KEY, val REAL NOT NULL);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, val TEXT);
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS messages(
  msg_id TEXT, read INTEGER, sender TEXT, recipient TEXT, summary TEXT,
  PRIMARY KEY(msg_id, recipient));
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
    return P.state_db(log)


def parked(log):
    """True when this session's state DB FILE no longer exists — SessionEnd parked
    it as *.keep (or it never existed), i.e. the session is over. This is THE
    session-alive probe every detached tailer/watcher completion loop polls
    (substream, bg/monitor stream, codex stream/watch, scorebar): once parked they
    must quit footer-less, because any further state write would either recreate
    the DB (whose file-existence IS the alive signal) or mutate the parked
    snapshot through a cached connection. Deliberately a bare os.path.exists —
    never a connect, which would CREATE the file it is probing for."""
    return not os.path.exists(db_path(log))


def pid_alive(pid):
    """The ONE pid-liveness probe (slot rows, claims, fg-live hand-offs, watcher
    locks all trust it). EPERM means the pid EXISTS but is owned by another user —
    that is alive; treating it as dead falsely reaps live foreign-owned streamers.
    Tolerates None / non-numeric pids from DB rows (-> dead)."""
    try:
        os.kill(int(pid), 0)
        return True
    except OSError as e:
        return e.errno == errno.EPERM
    except (TypeError, ValueError):
        return False


def _connect(path):
    # Revalidate the cached connection against the file's CURRENT inode. A
    # SessionEnd parks the DB (moving it out to the durable HISTORY_DIR) and a
    # fresh DB is created at the same path on resume — so a long-lived caller (the
    # OTLP receiver, the renderer) that cached a connection still holds an fd to
    # the OLD inode (now the parked file), and its writes silently land there,
    # invisible to everything reading the live path. No error is ever raised
    # (the stale fd points at a real, valid DB), so the swap can only be caught
    # proactively: one os.stat per call, cheap next to the reconnect it guards,
    # and the reopen branch runs ONLY after a park actually happened. This also
    # evicts+closes the stale fd, which otherwise leaks for the process lifetime.
    cached = _CONNS.get(path)
    if cached is not None:
        conn, ino = cached
        try:
            cur = os.stat(path).st_ino
        except OSError:
            cur = None
        # Only reconnect when a FRESH DB with a different inode sits at the path
        # (a park+resume swapped it out from under this long-lived caller — the
        # receiver-stranded-on-*.keep bug). When the path is simply GONE (cur is
        # None: parked, not yet recreated), keep returning the stale cached conn
        # — NEVER recreate the DB here, because its absence is the session-alive
        # exit signal watchers/streamers poll (recreating it wedged the substream
        # open past a park). A first connect (no cache) still creates below.
        if cur is None or cur == ino:
            return conn
        try:
            conn.close()
        except Exception:
            pass
        del _CONNS[path]
    try:
        conn = sqlite3.connect(path, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA)
        _migrate(conn)
        conn.commit()
        _CONNS[path] = (conn, os.stat(path).st_ino)
        return conn
    except Exception:
        return None


def connect_existing(path):
    """Open the state DB ONLY if its file already exists — sqlite's mode=rw
    makes the exists-probe and the open one atomic operation (no TOCTOU), so
    this can never resurrect a parked DB, whose file-ABSENCE is the
    session-alive exit signal (see parked()). Returns None when the file is
    missing (or unopenable). The connection is cached in _CONNS like
    _connect's, so a later _connect on the same path in this process reuses it
    (writing to the old inode after a park, never recreating the file)."""
    cached = _CONNS.get(path)
    if cached is not None:
        return _connect(path)          # inode revalidation lives in _connect
    try:
        conn = sqlite3.connect("file:%s?mode=rw" % path, uri=True, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        _CONNS[path] = (conn, os.stat(path).st_ino)
        return conn
    except Exception:
        return None


def _migrate(conn):
    """In-place schema upgrades for a DB created by an older build (a resumed
    session restores its parked *.keep). messages: PK msg_id -> (msg_id,
    recipient) — a broadcast (the same msg_id in N inboxes) collapsed to one row
    under the old PK, so per-recipient read tracking miscounted. Best-effort,
    same swallow-with-default style as the rest of this module."""
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='messages'").fetchone()
        if row and "PRIMARY KEY(msg_id, recipient)" not in (row[0] or ""):
            conn.executescript(
                "ALTER TABLE messages RENAME TO messages_old;"
                "CREATE TABLE messages(msg_id TEXT, read INTEGER, sender TEXT,"
                " recipient TEXT, summary TEXT, PRIMARY KEY(msg_id, recipient));"
                "INSERT OR IGNORE INTO messages"
                " SELECT msg_id, read, sender, COALESCE(recipient, '?'), summary"
                " FROM messages_old;"
                "DROP TABLE messages_old;")
    except Exception:
        pass


def connect(log):
    return _connect(db_path(log))


def evict(log):
    """Close and drop this session's cached state-DB connection, if any. Returns
    True when a connection was actually closed. For LONG-LIVED MULTI-SESSION
    processes ONLY (the OTLP receiver): each session that ends (DB parked away)
    otherwise pins its cached connection + WAL/SHM fds for the process lifetime,
    because _connect only swaps a cached conn on an inode CHANGE at the path.
    PER-SESSION processes (streamers, renderers, hooks) must NOT call this —
    their stale conn after a park is DELIBERATE (_connect's path-gone branch:
    reconnecting would recreate the DB whose absence is the session-alive exit
    signal). Never raises."""
    cached = _CONNS.pop(db_path(log), None)
    if cached is None:
        return False
    try:
        cached[0].close()
    except Exception:
        pass
    return True


@contextmanager
def immediate(conn):
    """BEGIN IMMEDIATE / commit; on any exception roll back (best-effort) and
    re-raise — each call site keeps its own swallow-with-default. Replaces the
    try/commit/except/rollback/except/pass block that was copy-pasted at every
    read-modify-write site in this module, core.slots, and core.ops.

    The commit is INSIDE the protected region: with it outside, a commit-time
    failure (I/O error, SQLITE_FULL) left the cached connection stuck in an open
    transaction — the next plain autocommit write on that connection then
    durably committed the "failed" transaction's writes, e.g. a hand_take that
    returned None ("nothing taken") whose DELETE landed later anyway, silently
    breaking take-once semantics."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise


# --- counter primitives (used inside an open transaction) ---------------------------

# Counter typing/visibility, declared HERE where the counters physically live
# (ops.py imports this module, so the vocabulary can't sit next to bump() without
# an import cycle). The `counters` table stores every value as REAL
# (counter_add/counter_set coerce to float), so stats() re-types generically on
# the way out — int when the stored value is integral, float otherwise — EXCEPT
# the declared floats below, which stay float even at whole numbers (cost must
# not flip type at an integral dollar figure). New counters therefore get the
# right public type automatically; only add here when a counter must STAY float
# or stay private.
FLOAT_COUNTERS = frozenset({"cost", "paused"})
# Internal accounting cursors — real counters (readable via counter_get, and raw
# in the tests' counters() dump) but NOT part of the public stats() dict: 'v' is
# the change counter behind version(), 'txpos' the transcript byte cursor owned
# by transcript_fold, 'block_seq' the ⧉ copy-group sequence behind next_group().
INTERNAL_COUNTERS = frozenset({"v", "txpos", "block_seq"})


def counter_add(conn, key, v=1):
    conn.execute("INSERT INTO counters(key, val) VALUES(?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET val = val + excluded.val",
                 (key, float(v)))


def counter_set(conn, key, v):
    conn.execute("INSERT INTO counters(key, val) VALUES(?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET val = excluded.val",
                 (key, float(v)))


def counter_get(conn, key, default=0):
    row = conn.execute("SELECT val FROM counters WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def next_group(log):
    """A session-unique monotonic block id, for ⧉ copy groups on blocks that lack a
    natural tool_use_id (messages, prompts, results, file ops, headers). Atomic across
    the separate producer processes via the `counters` table. Returns an int, or 0 on
    failure — callers treat a falsy id as 'no copy group' and skip the affordance."""
    conn = connect(log)
    if conn is None:
        return 0
    try:
        with immediate(conn):
            counter_add(conn, "block_seq", 1)
            return int(counter_get(conn, "block_seq"))
    except Exception:
        return 0


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
        with immediate(conn):
            conn.executemany("INSERT INTO ops(op) VALUES(?)",
                             [(json.dumps(o, ensure_ascii=False),) for o in ops])
        return True
    except Exception:
        return False


def ops_after(log, last_id, check_reset=True):
    """(new_last_id, [op, ...]) — every op with id > last_id, in insertion order.
    A max id BELOW last_id means the DB was recreated (fresh session reusing the
    key): the caller should reset and re-read from 0. Returns (last_id, []) on
    failure so a transient error never looks like a reset.

    check_reset=False skips the MAX(id) recreated-DB probe on the empty path —
    one query per idle poll instead of two — for a caller that detects DB
    recreation ITSELF (the renderer's sync_inode stat catches every recreate,
    since a park/restore or fresh session always swaps the file's inode; ops
    rows are never deleted in place). Every other caller keeps the -1 reset
    contract unchanged."""
    conn = connect(log)
    if conn is None:
        return last_id, []
    try:
        rows = conn.execute("SELECT id, op FROM ops WHERE id > ? ORDER BY id",
                            (last_id,)).fetchall()
        if not rows:
            if not check_reset:
                return last_id, []
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
        with immediate(conn):
            for k, v in deltas.items():
                counter_add(conn, k, v)
            if tool:
                counter_add(conn, "tool:" + tool)
            if file:
                conn.execute("INSERT OR IGNORE INTO files(path) VALUES(?)", (file,))
            conn.execute("INSERT OR IGNORE INTO counters(key, val) VALUES('start', ?)",
                         (int(time.time()),))
            counter_add(conn, "v")
        return stats(log)
    except Exception:
        return {}


def stats(log):
    """The scoreboard state as a dict in the OLD .stats.json shape — counters,
    tools{}, files (unique count), txlast{} — so core.ops.scoreboard_parts and the
    audit's "resulting totals" snapshots work unchanged. {} on failure.

    Counter values come back typed generically (integral REAL -> int, else float;
    FLOAT_COUNTERS always float) and INTERNAL_COUNTERS ('v', 'txpos', 'block_seq')
    are excluded — they're accounting cursors, not scoreboard state; internal
    readers get them via counter_get inside their own transaction."""
    conn = connect(log)
    if conn is None:
        return {}
    try:
        st, tools = {}, {}
        for k, v in conn.execute("SELECT key, val FROM counters"):
            if k.startswith("tool:"):
                tools[k[5:]] = int(v)
            elif k in INTERNAL_COUNTERS:
                continue
            elif k in FLOAT_COUNTERS:
                st[k] = float(v)
            else:
                fv = float(v)
                st[k] = int(fv) if fv.is_integer() else fv
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


def transcript_fold(log, fold):
    """One atomic read-modify-write of the main session's transcript cursor — the
    transaction plugins/claude_code/accounting.bump_transcript used to hand-roll against this module's
    private tables. `fold(pos, prev)` is called INSIDE the transaction with the
    current byte cursor ('txpos') and dedup carry record ('txlast', a dict or
    None); it returns None to leave everything unchanged, or
    (new_pos, new_prev, tok, usd[, comps]) — tok/usd are added to the
    'tokens'/'cost' counters and each key of the optional `comps` dict (the
    per-category token split tk_in/tk_out/tk_read/tk_create that feeds the
    scoreboard's Σ breakdown row) to its own counter, the cursor advances, 'start'
    is stamped and the change counter 'v' bumped, all in the same transaction (so
    concurrent hooks never double-count). Returns the updated stats dict, {} when
    the DB is unreachable. Exceptions from `fold` propagate after rollback — the
    caller audits them."""
    conn = connect(log)
    if conn is None:
        return {}
    with immediate(conn):
        pos = int(counter_get(conn, "txpos"))
        row = conn.execute("SELECT val FROM kv WHERE key='txlast'").fetchone()
        try:
            prev = json.loads(row[0]) if row else None
        except Exception:
            prev = None
        if not isinstance(prev, dict):
            prev = None
        r = fold(pos, prev)
        if r is not None:
            new_pos, new_prev, tok, usd = r[:4]
            comps = r[4] if len(r) > 4 else None
            if tok:
                counter_add(conn, "tokens", tok)
            if usd:
                counter_add(conn, "cost", usd)
            if comps:                    # per-category split (tk_in/out/read/create)
                for k, v in comps.items():
                    if v:
                        counter_add(conn, k, v)
            if new_prev is not None:
                conn.execute("INSERT INTO kv(key, val) VALUES('txlast', ?) "
                             "ON CONFLICT(key) DO UPDATE SET val = excluded.val",
                             (json.dumps(new_prev, ensure_ascii=False),))
            counter_set(conn, "txpos", new_pos)
            conn.execute("INSERT OR IGNORE INTO counters(key, val) VALUES('start', ?)",
                         (int(time.time()),))
            counter_add(conn, "v")
    return stats(log)


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
    """(delivered, read, live) — live is {(recipient, msg_id): [read, from,
    recipient, summary]}. Keyed per RECIPIENT COPY, not per msg_id: a broadcast
    puts the same msg_id in several inboxes, and collapsing those to one entry
    made the read flag depend on inbox scan order (reads double-counted or lost)."""
    conn = connect(log)
    if conn is None:
        return 0, 0, {}
    try:
        d = conn.execute("SELECT val FROM counters WHERE key='msg_delivered'").fetchone()
        r = conn.execute("SELECT val FROM counters WHERE key='msg_read'").fetchone()
        live = {(rc or "?", mid): [bool(rd), s or "?", rc or "?", su or ""]
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
        with immediate(conn):
            conn.execute("DELETE FROM messages")
            conn.executemany(
                "INSERT INTO messages(msg_id, read, sender, recipient, summary) "
                "VALUES(?,?,?,?,?)",
                [(mid, 1 if ent[0] else 0, ent[1], ent[2], ent[3])
                 for (_rc, mid), ent in live.items()])
            counter_set(conn, "msg_delivered", delivered)
            counter_set(conn, "msg_read", read)
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
        # Interpolated identifiers only: every column name is filtered through the
        # `allowed` whitelist above, never user input; values are bound.
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
        with immediate(conn):
            row = conn.execute("SELECT id, text FROM queue ORDER BY id LIMIT 1").fetchone()
            if row:
                conn.execute("DELETE FROM queue WHERE id=?", (row[0],))
        return row[1] if row else ""
    except Exception:
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


def hand_take(log, key, match=None):
    """Atomically consume a hand-off record: returns the object and deletes it, or
    None if absent — the take-once the old poll/read/remove sentinel dance meant.
    With `match` ({field: value, ...}) the record is consumed ONLY if every given
    field equals the record's value; a mismatch leaves the record in place and
    returns None. A record MISSING a matched field still matches (records written
    by an older producer). This is what keys the fg-live hand-off to its own tool
    call: without it, a cancelled command's surviving record was consumed by the
    NEXT Bash call's PostToolUse, cross-wiring the two commands' outcomes."""
    conn = connect(log)
    if conn is None:
        return None
    try:
        with immediate(conn):
            row = conn.execute("SELECT val FROM handoffs WHERE key=?", (key,)).fetchone()
            obj = json.loads(row[0]) if row else None
            if obj is not None and match:
                for f, v in match.items():
                    if f in obj and obj[f] != v:
                        return None            # someone else's record — leave it
            if row:
                conn.execute("DELETE FROM handoffs WHERE key=?", (key,))
        return obj
    except Exception:
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


# --- global tab DB (OWNED by claude-tab-status.py) -----------------------------------

def tab_state(win):
    """The tab colour-state claude-tab-status.py last applied for a kitty window,
    '' when absent/unreadable. mode=ro so a probe can never create the DB. The DB
    and its schema belong to claude-tab-status.py; this accessor is the ONE
    sanctioned cross-module reader (the scorebar's pause-accounting) — before it,
    the scorebar hardcoded the path and schema and a change there broke it
    silently. Delegates to tabs.tab_get — tabs.py owns the tab-DB schema, and
    its sq() gives the same guarantees this used to hand-roll (mode=ro URI +
    isfile guard so a probe can never create the DB, timeout=0.2, silent on
    missing/locked DB)."""
    from core import tabs
    return tabs.tab_get(str(win)) or ""


# The pid-liveness locks (lock_acquire/lock_holder/lock_release, the claims
# table over an arbitrary caller-supplied DB path) moved to core/locks.py --
# they were never per-session state, just borrowers of this module's _connect/
# immediate/pid_alive machinery.
