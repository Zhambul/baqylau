# plugins/codex/watch.py — argv: MIRROR_LOG CWD [SESSION_ID] [HOST_PID]
# Entry point: claude-codex-watch.py (a thin shim — the entry FILENAME is the
# audit vocabulary and what claude-codex-launch.py spawns).
#
# TWO roles, selected by whether a HOST_PID (argv[4]) is passed:
#   secondary source (no HOST_PID) — ONE per Claude Code session; streams EVERY
#     codex run in the repo into that Claude session's mirror (sources A + B below).
#   standalone host manager (HOST_PID set) — spawned by plugins/codex/session.py
#     for a codex running on its OWN (no Claude host). Streams exactly this codex
#     session's rollout (uuid == SID) and, because codex has no SessionEnd hook,
#     owns teardown: parks the DB + closes the panes when the codex host pid dies
#     (see standalone_scan / teardown). The rest of this file is the secondary role.
#
# ONE per Claude session (launched DETACHED by claude-codex-launch.py at SessionStart
# — see that file for why it must be Popen(start_new_session=True), never a bash `&`).
# It makes the mirror show codex activity GLOBALLY: every codex run shows, however it
# was launched — a `/codex:review`, adversarial-review, `task`, the stop-gate, or a
# raw `codex` / `codex exec`; from the main agent, a subagent, a teammate, a fg/bg
# command, or a slash subcommand. Rather than detect the codex command at every launch
# site, it tails the TWO directories every codex run funnels through and spawns
# claude-codex-stream.py per run.
#
#   Source A — companion jobs: `$CLAUDE_PLUGIN_DATA/state/<slug>/jobs/<jobId>.{log,json}`
#              (labelled by job title, matched to this Claude session by sessionId).
#   Source B — native rollouts: `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`
#              (matched to this repo by the session_meta cwd; catches raw `codex exec`).
#              Dedup: the rollout <uuid> IS the companion sidecar threadId, so a run
#              already handled by source A is skipped here.
#
# Cross-session isolation: a companion job is matched to its Claude session by
# sessionId, but a raw rollout (and a job with no sessionId) has no session identity —
# so those are claimed atomically in a per-repo shared claims DB (see claim()), keeping each
# such run in exactly ONE same-repo session's mirror instead of replaying in all.
#
# The <slug> is basename(git-root) + sha256(realpath(git-root))[:16] — byte-for-byte
# what codex's state.mjs computes. Colours round-robin claude_slots.CODEX_PALETTE and
# are passed to the streamer as "r,g,b". The watcher exits on its own when the
# session's mirror log is removed at SessionEnd; a pid lock guards against a duplicate.
import glob, hashlib, json, os, re, subprocess, sys, tempfile, time
from datetime import datetime, timedelta

from core.slots import CODEX_PALETTE
from core import state as S

try:
    from core import audit as A         # always-on audit trail (CLAUDE_AUDIT=0 disables)
except Exception:
    class _NoAudit:
        def __getattr__(self, _):
            return lambda *a, **k: None
    A = _NoAudit()

# The repo root, where the sibling ENTRY scripts live (this module is two
# package levels below it) — the per-run streamer is spawned by entry filename.
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STREAM = os.path.join(REPO, "claude-codex-stream.py")
LOG = sys.argv[1] if len(sys.argv) > 1 else ""
CWD = sys.argv[2] if len(sys.argv) > 2 else os.getcwd()
SID = sys.argv[3] if len(sys.argv) > 3 else ""
# argv[4] = the codex HOST pid, present ONLY when this watcher is the session
# manager for a STANDALONE codex (plugins/codex/session.py). Its presence flips
# the watcher into standalone mode: stream exactly THIS session's own rollout
# (uuid == SID, adopting the codex-tui originator we otherwise skip) and, since
# codex has no SessionEnd hook, own teardown — park the DB + close the panes when
# the codex host pid dies. Empty/"0" = the classic secondary-source mode inside a
# Claude Code host (backward-compatible 3-arg launch).
HOST_PID = sys.argv[4] if len(sys.argv) > 4 else ""
STANDALONE = bool(HOST_PID) and HOST_PID != "0"

POLL = 0.4
SKEW = 5.0          # accept a run created up to this many seconds before we started
RO_GRACE = 8.0      # rollout: wait before deciding a thread has no companion job
RO_UUID = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
HOME = os.path.expanduser("~")


def git_root(cwd):
    try:
        r = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        r = ""
    return r or cwd


def workspace_slug():
    root = git_root(CWD)
    try:
        rp = os.path.realpath(root)
    except Exception:
        rp = root
    base = os.path.basename(root.rstrip("/")) or "workspace"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-") or "workspace"
    return f"{slug}-{hashlib.sha256(rp.encode()).hexdigest()[:16]}"


SLUGDIR = workspace_slug()
try:
    REPO_ROOT = os.path.realpath(git_root(CWD))
except Exception:
    REPO_ROOT = git_root(CWD)


def claims_db():
    # Shared ACROSS every Claude session in this repo (keyed by the repo slug), so
    # concurrent sessions coordinate: a codex run that can't be attributed to one
    # session by id is claimed by the FIRST watcher to see it, and the others skip it —
    # otherwise every same-repo session's mirror would replay the same run. The claims
    # live in a shared SQLite table (claude_state.claim — was a dir of O_EXCL pid
    # files); stale holders (dead pid) are taken over the same way.
    d = os.path.join(tempfile.gettempdir(), "codex-companion", SLUGDIR)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return os.path.join(d, "mirror-claims.db")


def claim(key):
    # Every outcome is audited (slots table, kind=codex-claim) — "why did session A
    # (not) show that codex run" is a cross-session question only evidence can answer.
    db = claims_db()
    got = S.lock_acquire(db, key)
    if got in ("claim", "steal-stale"):
        A.slot(LOG, "codex-claim", got, agent_id=key,
               owner_pid=os.getpid(), marker_path=db)
        return True
    holder = got.split(":", 1)[1] if ":" in got else ""
    A.slot(LOG, "codex-claim", "claim-denied", agent_id=key,
           owner_pid=int(holder) if holder.isdigit() else None, marker_path=db)
    return False


def jobs_dirs():
    # Recomputed each poll (the codex state dir is created lazily on the first run, and
    # CLAUDE_PLUGIN_DATA may be unset in our env, so glob the plugin-data dirs + tmp).
    dirs = set(glob.glob(os.path.join(HOME, ".claude", "plugins", "data", "*",
                                      "state", SLUGDIR, "jobs")))
    pd = os.environ.get("CLAUDE_PLUGIN_DATA")
    if pd:
        dirs.add(os.path.join(pd, "state", SLUGDIR, "jobs"))
    dirs.add(os.path.join(tempfile.gettempdir(), "codex-companion", SLUGDIR, "jobs"))
    return [d for d in dirs if os.path.isdir(d)]


def parse_iso(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def companion_threadids():
    ids = set()
    for d in jobs_dirs():
        for jf in glob.glob(os.path.join(d, "*.json")):
            try:
                with open(jf, encoding="utf-8") as fh:
                    tid = (json.load(fh).get("threadId") or "").strip()
                if tid:
                    ids.add(tid)
            except Exception:
                pass
    return ids


def rollout_files():
    # Only today's + yesterday's session dirs (bounded; handles midnight rollover).
    base = os.path.join(HOME, ".codex", "sessions")
    out = []
    for dd in (datetime.now(), datetime.now() - timedelta(days=1)):
        out += glob.glob(os.path.join(base, f"{dd.year:04d}", f"{dd.month:02d}",
                                     f"{dd.day:02d}", "rollout-*.jsonl"))
    return out


RO_TS = re.compile(r"rollout-(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})-")


def rollout_created(path):
    """Creation time of a rollout — the filename timestamp when parseable (local
    time, e.g. rollout-2026-07-04T10-30-05-<uuid>.jsonl), else inode birth time
    (macOS), else mtime as a last resort. Deliberately NOT plain mtime: a rollout
    still being WRITTEN refreshes its mtime forever, so a long `codex exec` run
    started before this session passed the predates-this-session filter — its
    dead previous claim was stolen and its entire history replayed into the new
    session's mirror."""
    m = RO_TS.search(os.path.basename(path))
    if m:
        try:
            return datetime(*map(int, m.groups())).timestamp()
        except ValueError:
            pass
    try:
        st = os.stat(path)
        return getattr(st, "st_birthtime", 0) or st.st_mtime
    except OSError:
        return None


def rollout_meta(path):
    # -> (cwd, originator). originator tells us WHO launched the run: "Claude Code"
    # (companion, deduped via source A), "codex_exec" (a programmatic raw exec), or
    # "codex-tui" (a human driving the interactive TUI in a terminal — belongs to no
    # Claude session, so the mirror must not adopt it into any session).
    try:
        with open(path, encoding="utf-8") as fh:
            for _ in range(5):
                line = fh.readline()
                if not line:
                    break
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") == "session_meta":
                    p = o.get("payload") or {}
                    return ((p.get("cwd") or "").strip(),
                            (p.get("originator") or "").strip())
    except Exception:
        pass
    return "", ""


_n = 0


def spawn(srcfile, jsonfile, label):
    global _n
    rgb = ",".join(str(x) for x in CODEX_PALETTE[_n % len(CODEX_PALETTE)])
    _n += 1
    try:
        proc = subprocess.Popen(
            [sys.executable, STREAM, LOG, rgb, srcfile, jsonfile, label],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        A.spawn(LOG, proc.pid, [STREAM, srcfile, label], purpose=f"stream:codex {label}")
    except Exception:
        A.error(LOG, "spawn codex stream", {"src": srcfile, "label": label})


def label_for(data):
    label = (data.get("title") or "").strip()
    if label.lower().startswith("codex "):
        label = label[6:]
    return label or (data.get("kindLabel") or "task")


def acquire_lock():
    """Per-session single-watcher lock (was <log>.slots/codex.watch.pid) — a claim
    row in the SESSION state DB, pid-liveness-checked so a stale lock is stolen."""
    got = S.lock_acquire(S.db_path(LOG), "codex-watch")
    return got in ("claim", "steal-stale")


# --- standalone mode: this session's own codex run + its teardown ------------

def standalone_scan(seen):
    """STANDALONE poll: stream exactly this codex session's own rollout. We know
    our session_id, and the rollout filename's uuid IS that session_id, so we
    target `rollout-*-<SID>.jsonl` precisely — no cwd heuristics, no claim races,
    and (unlike the secondary-source path) we adopt it even though its originator
    is `codex-tui`, because here that human-driven TUI IS our session."""
    if SID in seen:
        return
    for rf in rollout_files():
        m = RO_UUID.search(os.path.basename(rf))
        if not m or m.group(1) != SID:
            continue
        cw, _origin = rollout_meta(rf)
        if not cw:
            return                    # session_meta not written yet — retry next poll
        seen.add(SID)
        spawn(rf, "-", "cli")         # our standalone codex session
        return


def teardown():
    """STANDALONE SessionEnd surrogate. Codex fires no SessionEnd hook, so when
    the codex host pid dies (exit OR a hard Ctrl-C, which fires nothing) this is
    how the mirror closes: park the state DB (-> *.keep, so a codex `resume`
    replays history; renaming makes the DB path vanish, which also stops the
    scoreboard bar) and close the panes. The watcher's cached DB connection means
    the finally's lock_release writes to the parked inode, never recreating the
    file."""
    from core import hostpane as HP
    action = HP.park_db(SID, LOG)
    A.state_file(LOG, S.db_path(LOG) + ".keep", action, "codex host pid gone")
    try:
        import frontends
        fe = frontends.get(resolve=True)
        if fe.usable():
            HP.close_mirror(fe, SID)
    except Exception:
        A.error(LOG, "codex standalone teardown (close panes)")
    A.pane(SID, "close", 1, "standalone codex host exited")


def main():
    if not LOG:
        return
    if not acquire_lock():
        A.event("streams", session_id=A.sid_from_log(LOG), kind="codex-watcher",
                pid=os.getpid(), started_at=time.time(), ended_at=time.time(),
                end_reason="duplicate (pid lock held)")
        return
    global _WATCH_ID
    _WATCH_ID = A.stream_start(LOG, "codex-watcher",
                               src_path=("standalone:" if STANDALONE else "") + SLUGDIR)
    start = time.time()
    seen = set()             # companion job ids + rollout uuids already handled
    pending_ro = {}          # rollout uuid -> first-seen wall time (grace before deciding)
    try:
        # Session-alive signal: the per-session state DB (parked as *.keep at
        # SessionEnd, so the path vanishes — S.parked, the shared probe).
        while not S.parked(LOG):
            # --- standalone codex host: own run + pid-liveness teardown -------------
            if STANDALONE:
                if not S.pid_alive(HOST_PID):
                    teardown()
                    break             # DB parked -> loop condition now false anyway
                standalone_scan(seen)
                time.sleep(POLL)
                continue
            # --- source A: companion jobs (labelled, Claude-session matched) ---------
            for d in jobs_dirs():
                for jf in glob.glob(os.path.join(d, "*.json")):
                    jid = os.path.basename(jf)[:-5]
                    if jid in seen:
                        continue
                    try:
                        with open(jf, encoding="utf-8") as fh:
                            data = json.load(fh)
                    except Exception:
                        continue              # partial write — retry next poll
                    seen.add(jid)
                    js = (data.get("sessionId") or "").strip()
                    if SID and js and js != SID:
                        continue              # another session's codex job
                    created = parse_iso(data.get("createdAt"))
                    if created and created < start - SKEW:
                        continue              # predates this session — don't replay
                    # A job with a matching sessionId is uniquely ours; one WITHOUT a
                    # sessionId can't be attributed, so claim it to keep it in a single
                    # session's mirror rather than every same-repo session's.
                    if not (SID and js == SID) and not claim("job-" + jid):
                        continue
                    logfile = data.get("logFile") or os.path.join(d, jid + ".log")
                    spawn(logfile, jf, label_for(data))

            # --- source B: native rollouts (any codex run, incl. raw `codex exec`) ---
            now = time.time()
            cthreads = None
            for rf in rollout_files():
                m = RO_UUID.search(os.path.basename(rf))
                if not m:
                    continue
                u = m.group(1)
                if u in seen:
                    continue
                created = rollout_created(rf)
                if created is None:
                    continue
                if created < start - SKEW:
                    seen.add(u); continue     # predates this session (creation
                                              # time, NOT mtime — see rollout_created)
                cw, origin = rollout_meta(rf)
                if not cw:
                    continue                  # session_meta not written yet — retry
                if origin == "codex-tui":
                    seen.add(u); continue     # a human-driven interactive codex TUI —
                                              # not this (or any) Claude session's run
                try:
                    cwr = os.path.realpath(cw)
                except Exception:
                    cwr = cw
                if not (cwr == REPO_ROOT or cwr.startswith(REPO_ROOT + os.sep)):
                    seen.add(u); continue     # a codex run in a different repo
                # Defer so a companion sidecar can reveal its threadId — a companion-
                # owned thread is streamed by source A with a nicer label.
                if u not in pending_ro:
                    pending_ro[u] = now; continue
                if now - pending_ro[u] < RO_GRACE:
                    continue
                seen.add(u); pending_ro.pop(u, None)
                if cthreads is None:
                    cthreads = companion_threadids()
                if u in cthreads:
                    continue                  # companion owns it — already streamed
                # A raw run has no session identity; claim so exactly ONE same-repo
                # session's mirror shows it instead of all of them.
                if not claim("ro-" + u):
                    continue
                spawn(rf, "-", "cli")         # a raw `codex` / `codex exec` run
            time.sleep(POLL)
    finally:
        S.lock_release(S.db_path(LOG), "codex-watch")


_WATCH_ID = None

def entry():
    try:
        os.setsid()          # redundant when launched via start_new_session, harmless
    except Exception:
        pass
    try:
        main()
        A.stream_end(_WATCH_ID, "state-db-parked (session end)")
    except Exception:
        A.error(LOG, "main")
        A.stream_end(_WATCH_ID, "crash")
