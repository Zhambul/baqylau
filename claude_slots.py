#!/usr/bin/env python3
# claude_slots.py — shared palette + slot allocation for the kitty command mirror.
#
# Background and monitor streams each draw their "│ " gutter colour from their own
# palette so concurrent jobs are visually distinct. The LAUNCHER (claude-cmd-fmt
# for background, claude-monitor-fmt for monitor) claims a free slot and colours
# the block's header chip with it, then passes the slot index to claude-stream.py,
# which uses it for the gutter + finish chip — so a job's header, gutter, and
# finish all share ONE colour, and parallel jobs differ. Slots are atomic marker
# files under "<mirror-log>.slots/", liveness-checked by pid and released when the
# streamer exits; >5 concurrent of a kind reuse colours.
import errno, fcntl, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import claude_audit as A            # always-on audit trail (CLAUDE_AUDIT=0 disables)
except Exception:
    class _NoAudit:
        def __getattr__(self, _):
            return lambda *a, **k: None
    A = _NoAudit()

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
#               (round-robin + sub.* markers); only the render colour differs, so this
#               is keyed as its own palette but never gets its own slot kind.
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


def _dir(log):
    d = log + ".slots"
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _next(d, kind, n):
    """Round-robin counter per kind: returns the next index and advances it. A
    file lock makes concurrent launches each get a different starting index."""
    p = os.path.join(d, f"{kind}.next")
    try:
        fd = os.open(p, os.O_CREAT | os.O_RDWR, 0o644)
    except Exception:
        return 0
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except Exception:
            pass
        try:
            cur = int((os.read(fd, 64) or b"0").decode().strip() or "0")
        except Exception:
            cur = 0
        try:
            os.lseek(fd, 0, 0); os.ftruncate(fd, 0); os.write(fd, str(cur + 1).encode())
        except Exception:
            pass
        return cur % n
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except Exception:
            pass
        os.close(fd)


def claim(kind, log):
    """Claim a palette slot round-robin. Returns (index, marker_path|None). Starts
    at the next counter value (so a just-freed colour isn't immediately reused) and
    walks forward to the first slot not held by a live streamer."""
    d = _dir(log)
    n = len(palette(kind))
    mypid = str(os.getpid())
    start = _next(d, kind, n)
    for k in range(n):
        idx = (start + k) % n
        p = os.path.join(d, f"{kind}.{idx}")
        try:
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.write(fd, mypid.encode()); os.close(fd)
            A.slot(log, kind, "claim", slot_n=idx, owner_pid=os.getpid(), marker_path=p)
            return idx, p
        except FileExistsError:
            try:
                holder = int(open(p).read().strip() or "0")
            except Exception:
                holder = 0
            alive = False
            if holder:
                try:
                    os.kill(holder, 0); alive = True
                except OSError as e:
                    alive = (e.errno == errno.EPERM)
            if not alive:                       # stale holder -> steal the slot
                try:
                    os.remove(p)
                    fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                    os.write(fd, mypid.encode()); os.close(fd)
                    A.slot(log, kind, "steal-stale", slot_n=idx,
                           owner_pid=os.getpid(), marker_path=p)
                    return idx, p
                except Exception:
                    A.error(log, "claim", {"kind": kind, "idx": idx})
        except Exception:
            A.error(log, "claim", {"kind": kind, "idx": idx})
            break
    A.slot(log, kind, "claim-denied", slot_n=start, owner_pid=os.getpid())
    return start, None                          # all live -> reuse start, no marker


def set_owner(marker_path, pid):
    """Re-point a freshly claimed marker at the long-lived streamer pid."""
    if not marker_path:
        return
    try:
        with open(marker_path, "w") as f:
            f.write(str(pid))
        A.event("slots", session_id=A.sid_from_log(marker_path), kind="",
                action="set-owner", owner_pid=pid, marker_path=marker_path)
    except Exception:
        A.error(marker_path, "set_owner", {"pid": pid})


def release(kind, log, idx, pid):
    try:
        p = os.path.join(log + ".slots", f"{kind}.{idx}")
        if (open(p).read().strip() or "0") == str(pid):
            os.remove(p)
            A.slot(log, kind, "release", slot_n=idx, owner_pid=pid, marker_path=p)
    except Exception:
        pass


# --- id-keyed slots (subagents) -------------------------------------------------
# A background/monitor stream is one detached process that holds its slot from
# claim to release. A subagent is different: its lifetime spans MANY separate hook
# invocations (SubagentStart, each inner PreToolUse/PostToolUse, SubagentStop),
# each a fresh short-lived process. So its colour is keyed by the stable agent_id
# in a small map file "<kind>.id.<agent_id>" -> "<slot> <start_ts>", claimed on
# SubagentStart and released on SubagentStop; every event in between just looks it
# up. The slot index itself is still round-robin so parallel subagents differ.
def _id_path(log, kind, ident):
    return os.path.join(_dir(log), f"{kind}.id.{ident}")


def _read_id(p):
    try:
        parts = open(p).read().split()
        return int(parts[0]), (float(parts[1]) if len(parts) > 1 else 0.0)
    except Exception:
        return None


def claim_id(kind, log, ident, prefer=None):
    """Map `ident` to a round-robin slot (stamping the start time), or return the
    existing mapping if already claimed. `prefer` pins a specific slot for a NEW
    mapping (a resumed teammate keeps its original colour). Returns
    (slot_index, is_new)."""
    p = _id_path(log, kind, ident)
    got = _read_id(p)
    if got is not None:
        return got[0], False
    idx = prefer if prefer is not None else _next(_dir(log), kind, len(palette(kind)))
    try:
        with open(p, "w") as f:
            f.write(f"{idx} {time.time()}")
        A.slot(log, kind, "claim-id", slot_n=idx, agent_id=ident,
               owner_pid=os.getpid(), marker_path=p)
    except Exception:
        A.error(log, "claim_id", {"kind": kind, "ident": ident})
    return idx, True


def lookup_id(kind, log, ident):
    """Return (slot_index, start_ts) for a claimed `ident`, or None."""
    return _read_id(_id_path(log, kind, ident))


def release_id(kind, log, ident):
    try:
        os.remove(_id_path(log, kind, ident))
        A.slot(log, kind, "release-id", agent_id=ident, owner_pid=os.getpid())
    except Exception:
        pass


# --- subagent description hand-off ---------------------------------------------
# A subagent's description is only in the PreToolUse(Agent) payload (which has no
# agent_id); SubagentStart has the agent_id but no description, and the on-disk
# meta.json with the description isn't written until the subagent FINISHES. So we
# bridge them with a tiny FIFO: PreToolUse(Agent) pushes the description, the next
# SubagentStart pops it. Order matches spawn order, so this is exact for sequential
# subagents; for several SAME-TYPE subagents launched in one message the only risk
# is two descriptions being swapped (cosmetic) if SubagentStart order reverses the
# launch order — agent_type + colour still identify each correctly.
# The queue lives in the per-session state DB (claude_state.queue — was an flock'd
# desc.queue file); the signatures are kept here so callers don't change.
def desc_push(log, text):
    import claude_state
    claude_state.desc_push(log, text)


def desc_pop(log):
    import claude_state
    return claude_state.desc_pop(log)
