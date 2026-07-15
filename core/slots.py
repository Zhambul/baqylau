# core/slots.py — shared palette + slot allocation for the command mirror.
# (Historical top-level name: claude_slots.py — that compat shim is deleted.)
#
# Background and monitor streams each draw their "│ " gutter colour from their own
# palette so concurrent jobs are visually distinct. The LAUNCHER (claude-cmd-fmt
# for background, claude-monitor-fmt for monitor) claims a free slot and colours
# the block's header chip with it, then passes the slot index to claude-stream.py,
# which uses it for the gutter + finish chip — so a job's header, gutter, and
# finish all share ONE colour, and parallel jobs differ. Slots are rows in the
# per-session state DB's `live` table (core.state — were O_EXCL marker files
# under "<mirror-log>.slots/"), liveness-checked by pid and released when the
# streamer exits; >5 concurrent of a kind reuse colours.
#
# The `live` table doubles as the tab tracker's liveness signal: claude-tab-status.py
# queries it (sqlite3 CLI) for pids of live tailers — kinds bg/monitor/fg (numeric
# palette slots, key = the slot index) and sub.pid (a substream tailer, key = the
# agent_id). Colour-mapping rows (kind sub.id, no pid) are NOT liveness rows.
import os, time

from core import state as St

from core.noaudit import load_audit

A = load_audit()   # always-on audit trail (CLAUDE_AUDIT=0 disables); inert stub if it can't import

# Full-spectrum, well-separated hues (large min pairwise distance), avoiding the
# foreground status hues (red/orange). Slot order keeps slots 0/1/2 very distinct.
#   background: yellow · spring · blue · rose · green
#   monitor:    azure · magenta · chartreuse · cyan · violet
#   subagent:   indigo · magenta · teal · lime · orange  (≥80 from bg+monitor and
#               from the status/file-op colours; ≥130 within the set so PARALLEL
#               subagents are sharply distinct — the priority, since several can run
#               at once). No red/green so a subagent gutter is never read as fail/ok.
#   teammate:   rose · amber · lavender · mint — a LIGHTER, pastel family so an agent
#               team member reads differently from an ordinary (electric/dark) subagent
#               when both run at once. Teammates reuse the subagent slot machinery
#               (round-robin + the sub.* live rows); only the render colour differs, so
#               this is keyed as its own palette but never gets its own slot kind.
#   codex:      jade · sky · orchid · gold — a distinct family (jade slot-0 evokes the
#               OpenAI mark) for codex-plugin / codex-CLI streams, so a codex block
#               never reads as one of our own subagents/teammates. The codex watcher
#               round-robins these itself (passing the RGB to the streamer); rarely
#               >2 concurrent (a normal + an adversarial reviewer), so four suffice.
# All palettes are desaturated (blended ~70% toward gray) so the chip highlights read
# as muted, not neon — the chip text is near-black, so keep them light enough to stay
# legible. Each slot keeps a hint of its distinguishing hue.
BG_PALETTE    = [(211, 204, 173), (101, 138, 116), (150, 151, 188), (140, 103, 120), (159, 192, 153)]
MON_PALETTE   = [(106, 127, 143), (216, 178, 216), (134, 146, 110), (176, 215, 211), (123, 105, 141)]
SUB_PALETTE   = [(97, 97, 154), (170, 116, 170), (120, 178, 160), (170, 182, 122), (156, 127, 106)]
TEAM_PALETTE  = [(205, 175, 185), (197, 175, 143), (196, 184, 215), (164, 197, 188)]
CODEX_PALETTE = [(82, 142, 127), (97, 145, 173), (164, 131, 190), (183, 166, 106)]


def palette(kind):
    if kind == "bg":
        return BG_PALETTE
    if kind == "sub":
        return SUB_PALETTE
    if kind == "team":
        return TEAM_PALETTE
    if kind == "codex":
        return CODEX_PALETTE
    return MON_PALETTE


def color(kind, idx):
    p = palette(kind)
    return p[idx % len(p)]


_alive = St.pid_alive               # one canonical liveness probe (EPERM = alive)


def _token(log, kind, idx):
    """Opaque claim token (was the marker-file path): carried by the claimer to
    set_owner(), and shown verbatim in the audit's marker_path column."""
    return f"{log}::live:{kind}.{idx}"


def _untoken(token):
    """Inverse of _token(): (log, kind, key). The key is the raw string tail —
    a numeric slot index for palette slots, an agent_id for sub.pid rows —
    matching the `live` table's TEXT key column."""
    log, _, tail = token.partition("::live:")
    kind, _, key = tail.rpartition(".")
    return log, kind, key


def _next(log, kind, n):
    """Round-robin counter per kind (counters key 'slotnext:<kind>'): returns the
    next index and advances it atomically, so concurrent launches each get a
    different starting index (was an flock'd <kind>.next file)."""
    conn = St.connect(log)
    if conn is None:
        return 0
    key = "slotnext:" + kind
    try:
        with St.immediate(conn):
            cur = int(St.counter_get(conn, key))
            St.counter_set(conn, key, cur + 1)
        return cur % n
    except Exception:
        return 0


def claim(kind, log):
    """Claim a palette slot round-robin. Returns (index, token|None). Starts at the
    next counter value (so a just-freed colour isn't immediately reused) and walks
    forward to the first slot not held by a live streamer — all in ONE transaction,
    so two concurrent claimers can't take the same slot."""
    n = len(palette(kind))
    mypid = os.getpid()
    start = _next(log, kind, n)
    conn = St.connect(log)
    if conn is None:
        return start, None
    got, action = None, "claim"
    try:
        with St.immediate(conn):
            for k in range(n):
                idx = (start + k) % n
                row = conn.execute("SELECT pid FROM live WHERE kind=? AND key=?",
                                   (kind, str(idx))).fetchone()
                if row is None:
                    conn.execute("INSERT INTO live(kind, key, pid, idx, start_ts) "
                                 "VALUES(?,?,?,?,?)",
                                 (kind, str(idx), mypid, idx, time.time()))
                    got = idx
                    break
                holder = int(row[0] or 0)
                if not holder or not _alive(holder):    # stale holder -> steal the slot
                    conn.execute("UPDATE live SET pid=?, start_ts=? WHERE kind=? AND key=?",
                                 (mypid, time.time(), kind, str(idx)))
                    got, action = idx, "steal-stale"
                    break
    except Exception:
        A.error(log, "claim", {"kind": kind, "start": start})
        return start, None
    if got is None:                                 # all live -> reuse start, no token
        A.slot(log, kind, "claim-denied", slot_n=start, owner_pid=mypid)
        return start, None
    A.slot(log, kind, action, slot_n=got, owner_pid=mypid,
           marker_path=_token(log, kind, got))
    return got, _token(log, kind, got)


def set_owner(token, pid):
    """Re-point a freshly claimed slot at the long-lived streamer pid."""
    if not token:
        return
    try:
        log, kind, idx = _untoken(token)
        conn = St.connect(log)
        if conn is None:
            return
        conn.execute("UPDATE live SET pid=? WHERE kind=? AND key=?", (pid, kind, idx))
        conn.commit()
        # A.slot (not raw A.event) WITH slot_n: the "claims without a release"
        # anomaly groups by (kind, slot_n, agent) — a slot_n-less set-owner row
        # lands in its own group instead of alongside its claim/release rows.
        A.slot(log, kind, "set-owner", slot_n=int(idx) if idx.isdigit() else None,
               owner_pid=pid, marker_path=token)
    except Exception:
        A.error(token, "set_owner", {"pid": pid})


def release(kind, log, idx, pid):
    """Release a numeric slot — only if `pid` still owns it (same guard the old
    marker-file content check gave)."""
    conn = St.connect(log)
    if conn is None:
        return
    try:
        cur = conn.execute("DELETE FROM live WHERE kind=? AND key=? AND pid=?",
                           (kind, str(idx), pid))
        conn.commit()
        if cur.rowcount:
            A.slot(log, kind, "release", slot_n=idx, owner_pid=pid,
                   marker_path=_token(log, kind, idx))
    except Exception:
        # Audited like the claim side: a silently failed release leaves a live
        # row lingering (tab stays blue, bg-recheck refuses) with a claim row but
        # no release row and — without this — no errors row saying why.
        A.error(log, "release", {"kind": kind, "idx": idx})


# --- id-keyed slots (subagents) -------------------------------------------------
# A background/monitor stream is one detached process that holds its slot from
# claim to release. A subagent is different: its lifetime spans MANY separate hook
# invocations (SubagentStart, each inner PreToolUse/PostToolUse, SubagentStop),
# each a fresh short-lived process. So its colour is keyed by the stable agent_id
# in a `live` row (kind "<kind>.id", key = agent_id, no pid — was a small map file
# "<kind>.id.<agent_id>"), claimed on SubagentStart and released on SubagentStop;
# every event in between just looks it up. The slot index itself is still
# round-robin so parallel subagents differ.

def claim_id(kind, log, ident, prefer=None):
    """Map `ident` to a round-robin slot (stamping the start time), or return the
    existing mapping if already claimed. `prefer` pins a specific slot for a NEW
    mapping (a resumed teammate keeps its original colour). Returns
    (slot_index, is_new)."""
    got = lookup_id(kind, log, ident)
    if got is not None:
        return got[0], False
    idx = prefer if prefer is not None else _next(log, kind, len(palette(kind)))
    conn = St.connect(log)
    if conn is None:
        return idx, True
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO live(kind, key, pid, idx, start_ts) "
            "VALUES(?,?,NULL,?,?)", (kind + ".id", ident, idx, time.time()))
        conn.commit()
        if not cur.rowcount:                        # raced: another hook claimed it
            got = lookup_id(kind, log, ident)
            if got is not None:
                return got[0], False
        A.slot(log, kind, "claim-id", slot_n=idx, agent_id=ident,
               owner_pid=os.getpid(), marker_path=_token(log, kind + ".id", idx))
    except Exception:
        A.error(log, "claim_id", {"kind": kind, "ident": ident})
    return idx, True


def lookup_id(kind, log, ident):
    """Return (slot_index, start_ts) for a claimed `ident`, or None."""
    conn = St.connect(log)
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT idx, start_ts FROM live WHERE kind=? AND key=?",
                           (kind + ".id", ident)).fetchone()
        return (int(row[0]), float(row[1] or 0.0)) if row else None
    except Exception:
        return None


def release_id(kind, log, ident):
    """Release an id-keyed slot. Returns True only when THIS call deleted the
    row — the caller's licence for once-only follow-up work (the safety-net
    footer in claude-subagent-fmt.py: duplicate SubagentStops can overlap, and
    both used to pass a lookup_id check and both paint the footer)."""
    conn = St.connect(log)
    if conn is None:
        return False
    try:
        # Fetch idx before the delete so the audit row carries slot_n: the
        # anomalies query pairs claims with releases grouped by (kind, slot_n,
        # agent), so a slot_n-less release lands in a different group and every
        # normally-finished subagent false-flagged "claim without release".
        row = conn.execute("SELECT idx FROM live WHERE kind=? AND key=?",
                           (kind + ".id", ident)).fetchone()
        cur = conn.execute("DELETE FROM live WHERE kind=? AND key=?",
                           (kind + ".id", ident))
        conn.commit()
        if cur.rowcount:
            A.slot(log, kind, "release-id", slot_n=row[0] if row else None,
                   agent_id=ident, owner_pid=os.getpid())
            return True
        return False
    except Exception:
        A.error(log, "release_id", {"kind": kind, "ident": ident})
        return False


# --- per-agent tailer pid (the tab tracker's liveness signal) --------------------
# A substream tailer registers its pid under kind "sub.pid" (key = agent_id — was
# the <log>.slots/sub.pid.<agent_id> file). claude-tab-status.py counts these rows
# (with bg/monitor/fg) as "something is still running"; claude-subagent-fmt's stop
# handler liveness-checks it to decide whether the safety-net footer is needed.

def pid_set(log, ident, pid):
    conn = St.connect(log)
    if conn is None:
        return
    try:
        with St.immediate(conn):
            # Read the incumbent INSIDE the claim txn: the upsert silently
            # REPLACES a live pid (a resumed agent's new tailer displacing the
            # old one) — without a paired release-pid for the displaced holder,
            # the claim/release pairing anomaly false-flags every healthy
            # resume as an unbalanced slot.
            row = conn.execute("SELECT pid FROM live WHERE kind='sub.pid' "
                               "AND key=?", (ident,)).fetchone()
            old = int(row[0] or 0) if row else 0
            conn.execute("INSERT INTO live(kind, key, pid, idx, start_ts) "
                         "VALUES('sub.pid', ?, ?, NULL, ?) "
                         "ON CONFLICT(kind, key) DO UPDATE SET pid = excluded.pid",
                         (ident, pid, time.time()))
        if old and old != pid:
            A.slot(log, "sub", "release-pid", agent_id=ident, owner_pid=old)
        A.slot(log, "sub", "claim-pid", agent_id=ident, owner_pid=pid,
               marker_path=_token(log, "sub.pid", ident))
    except Exception:
        A.error(log, "pid_set", {"ident": ident, "pid": pid})


def pid_get(log, ident):
    """The registered tailer pid for `ident`, or 0."""
    conn = St.connect(log)
    if conn is None:
        return 0
    try:
        row = conn.execute("SELECT pid FROM live WHERE kind='sub.pid' AND key=?",
                           (ident,)).fetchone()
        return int(row[0] or 0) if row else 0
    except Exception:
        return 0


def pid_del(log, ident):
    conn = St.connect(log)
    if conn is None:
        return
    try:
        cur = conn.execute("DELETE FROM live WHERE kind='sub.pid' AND key=?", (ident,))
        conn.commit()
        if cur.rowcount:
            A.slot(log, "sub", "release-pid", agent_id=ident, owner_pid=os.getpid())
    except Exception:
        # Same rationale as release(): a lingering sub.pid row keeps the tab blue.
        A.error(log, "pid_del", {"ident": ident})


# --- subagent description hand-off ---------------------------------------------
# A subagent's description is only in the PreToolUse(Agent) payload (which has no
# agent_id); SubagentStart has the agent_id but no description, and the on-disk
# meta.json with the description isn't written until the subagent FINISHES. So we
# bridge them with a tiny FIFO: PreToolUse(Agent) pushes the description, the next
# SubagentStart pops it. Order matches spawn order, so this is exact for sequential
# subagents; for several SAME-TYPE subagents launched in one message the only risk
# is two descriptions being swapped (cosmetic) if SubagentStart order reverses the
# launch order — agent_type + colour still identify each correctly.
# The queue lives in the per-session state DB (core.state.queue — was an flock'd
# desc.queue file); the signatures are kept here so callers don't change.
def desc_push(log, text):
    St.desc_push(log, text)


def desc_pop(log):
    return St.desc_pop(log)
