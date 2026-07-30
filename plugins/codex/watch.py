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
# what codex's state.mjs computes. Colours round-robin core.slots.CODEX_PALETTE and
# are passed to the streamer as "r,g,b". The watcher exits on its own when the
# session's mirror log is removed at SessionEnd; a pid lock guards against a duplicate.
import glob, hashlib, json, os, re, subprocess, sys, tempfile, time
from datetime import datetime, timedelta

from core.slots import CODEX_PALETTE, SUB_PALETTE
from core import paths as P
from core import env as EV
from core import locks as LK
from core import state as S
from core.spawn import spawn_detached

from core.noaudit import load_audit

A = load_audit()   # always-on audit trail (CLAUDE_AUDIT=0 disables); inert stub if it can't import

from core.paths import BIN  # bin/, where the sibling ENTRY scripts live
STREAM = os.path.join(BIN, "claude-codex-stream.py")

# --- run identity (argv contract) ---------------------------------------------------
# All of this used to be bound at module top level — importing the module read
# argv and even FORKED a `git rev-parse` (workspace_slug -> git_root). It now
# lives in _init(), called from entry(), so IMPORTING this module (tests,
# tooling) reads no argv and runs no subprocess — only running it does. The
# placeholders below just name the module globals every function reads at call
# time.
LOG = CWD = SID = ""
HOST_PID = ""
STANDALONE = False
SLUGDIR = ""
REPO_ROOT = ""


def _init(argv):
    """Bind this run's identity from the shim's argv:
      claude-codex-watch.py MIRROR_LOG CWD [SESSION_ID] [HOST_PID]
    plus the derived workspace slug + repo root (a `git rev-parse` fork)."""
    global LOG, CWD, SID, HOST_PID, STANDALONE, SLUGDIR, REPO_ROOT
    LOG = argv[1] if len(argv) > 1 else ""
    CWD = argv[2] if len(argv) > 2 else os.getcwd()
    SID = argv[3] if len(argv) > 3 else ""
    # argv[4] = the codex HOST pid, present ONLY when this watcher is the session
    # manager for a STANDALONE codex (plugins/codex/session.py). Its presence flips
    # the watcher into standalone mode: stream exactly THIS session's own rollout
    # (uuid == SID, adopting the codex-tui originator we otherwise skip) and, since
    # codex has no SessionEnd hook, own teardown — park the DB + close the panes when
    # the codex host pid dies. Empty/"0" = the classic secondary-source mode inside a
    # Claude Code host (backward-compatible 3-arg launch).
    HOST_PID = argv[4] if len(argv) > 4 else ""
    STANDALONE = bool(HOST_PID) and HOST_PID != "0"
    SLUGDIR = workspace_slug()
    try:
        REPO_ROOT = os.path.realpath(git_root(CWD))
    except Exception:
        REPO_ROOT = git_root(CWD)

POLL = EV.env_float("CLAUDE_CODEX_WATCH_POLL_S", 0.4)
SKEW = 5.0          # accept a run created up to this many seconds before we started
# rollout: wait before deciding a thread has no companion job (env knob is
# test-only — see docs/testing.md; unset, behavior is unchanged)
RO_GRACE = EV.env_float("CLAUDE_CODEX_RO_GRACE_S", 8.0)
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


def claims_db():
    # Shared ACROSS every Claude session in this repo (keyed by the repo slug), so
    # concurrent sessions coordinate: a codex run that can't be attributed to one
    # session by id is claimed by the FIRST watcher to see it, and the others skip it —
    # otherwise every same-repo session's mirror would replay the same run. The claims
    # live in a shared SQLite table (core.state.claim — was a dir of O_EXCL pid
    # files); stale holders (dead pid) are taken over the same way.
    d = os.path.join(tempfile.gettempdir(), "codex-companion", SLUGDIR)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        # Degrade (lock_acquire will surface the unusable path) — but leave
        # evidence: "why did no session claim that codex run" starts here.
        A.error(LOG, "codex claims_db makedirs", {"path": d})
    return os.path.join(d, "mirror-claims.db")


def claim(key):
    # Every outcome is audited (slots table, kind=codex-claim) — "why did session A
    # (not) show that codex run" is a cross-session question only evidence can answer.
    db = claims_db()
    got = LK.lock_acquire(db, key)
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


def rollout_subagent(path):
    """If this rollout is a codex SUBAGENT run (spawned by `collaboration.spawn_agent`,
    cli 0.146+), return (parent_thread_id, label); else (None, None). A subagent
    writes its OWN rollout whose first `session_meta` links back to the spawning
    session — the tell is `thread_source == "subagent"` (or a
    `source.subagent.thread_spawn` block), and the parent is that block's
    `parent_thread_id`. The label is the agent's display nickname, else its
    `agent_path` basename, else "agent". Returns (None, None) for a plain run AND
    for a rollout whose `session_meta` isn't written yet, so the caller retries a
    mid-write file rather than skipping it forever."""
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
                if o.get("type") != "session_meta":
                    continue
                p = o.get("payload") or {}
                spawn = (((p.get("source") or {}).get("subagent") or {})
                         .get("thread_spawn") or {})
                if p.get("thread_source") != "subagent" and not spawn:
                    return None, None          # a plain (non-subagent) rollout
                parent = (spawn.get("parent_thread_id")
                          or p.get("parent_thread_id") or "").strip()
                label = (spawn.get("agent_nickname") or p.get("agent_nickname")
                         or "").strip()
                if not label:
                    ap = (spawn.get("agent_path") or p.get("agent_path") or "").strip()
                    label = os.path.basename(ap.rstrip("/")) if ap else ""
                return (parent or None), (label or "agent")
    except Exception:
        pass
    return None, None


_n = 0
# A SUBAGENT's colour comes from the SUBAGENT palette, round-robin on its OWN
# counter: a codex subagent IS a child agent, so it wears the same family a
# Claude subagent does and the two read alike in a shared pane (and every
# palette-gated stage downstream — actclass's prose/recolour gates — covers it
# with no codex special-casing). Its own counter so a session's codex sidecars
# and its subagents don't advance each other's hue.
_sub_n = 0


def spawn(srcfile, jsonfile, label, subagent=False):
    global _n, _sub_n
    if subagent:
        rgb = ",".join(str(x) for x in SUB_PALETTE[_sub_n % len(SUB_PALETTE)])
        _sub_n += 1
    else:
        rgb = ",".join(str(x) for x in CODEX_PALETTE[_n % len(CODEX_PALETTE)])
        _n += 1
    # The detach mechanics + spawn/error audit live in core.spawn (the one
    # owner); audit_argv drops the rgb/jsonfile noise the spawns row never
    # recorded here. WHO the run is decides the register:
    #   * A STANDALONE codex host's OWN rollout stays unstamped: there codex IS
    #     the main agent, so its exec blocks are painted in Claude's own semantic
    #     command colours + block shape (uniform, no codex palette — docs/codex.md
    #     *Standalone command parity*). The CLAUDE_CODEX_STANDALONE flag rides the
    #     env so the stream paints the main-agent register.
    #   * A codex SUBAGENT (`subagent=True`) is a CHILD AGENT, so it is stamped
    #     `sub:<codex_aid>` — the very prefix a Claude subagent uses. The prefix is
    #     the REGISTER: `codex:` now means exactly "a sidecar codex run inside a
    #     Claude host" (the thing the web counts as `ran N codex runs`), while a
    #     native subagent classifies, scopes and folds as an AGENT with no
    #     per-tool special-casing. Safe to re-point because nothing keys on the
    #     `codex:` prefix to FIND a run: read/mirror.agent_scope accepts all three
    #     prefixes for one id, and sessionapi.codex_runs reads the audit `streams`
    #     rows, not the op stamp.
    #   * A SECONDARY-source run inside a Claude host keeps `codex:<codex_aid>`.
    #     Either way the stamp is the run's synthesized AGENT ID (paths.codex_aid,
    #     the same id sessionapi.codex_runs mints the card with), NOT the display
    #     `label`: making the stamp EQUAL the agent id is what lets
    #     read/mirror.agent_scope match it directly, no per-tool label lookup.
    #     Both are NOT the host session's main agent, so the web dashboard's
    #     main-agent-only mirror drops their ops and the agent-scope view shows them.
    env = dict(os.environ)
    if subagent:
        # …and the stream paints the SUBAGENT register off this flag (an explicit
        # env flag, like the STANDALONE precedent — the rollout itself cannot say
        # which of the two roles this watcher spawned it for).
        env["CLAUDE_CODEX_SUBAGENT"] = "1"
        env["CLAUDE_OPS_SRC"] = "sub:" + P.codex_aid(srcfile)
    elif STANDALONE:
        env["CLAUDE_CODEX_STANDALONE"] = "1"
    else:
        env["CLAUDE_OPS_SRC"] = "codex:" + P.codex_aid(srcfile)
    purpose = ("stream:codex-subagent " if subagent else "stream:codex ") + label
    spawn_detached(STREAM, [LOG, rgb, srcfile, jsonfile, label], LOG, env=env,
                   purpose=purpose, audit_argv=[srcfile, label])


def label_for(data):
    label = (data.get("title") or "").strip()
    if label.lower().startswith("codex "):
        label = label[6:]
    return label or (data.get("kindLabel") or "task")


def acquire_lock():
    """Per-session single-watcher lock (was <log>.slots/codex.watch.pid) — a claim
    row in the SESSION state DB, pid-liveness-checked so a stale lock is stolen.
    create=False: this is the watcher's FIRST state-DB touch, and on a loaded
    machine the spawn can lose a race with a fast SessionEnd — a creating open
    would resurrect the just-parked DB, and since file-existence IS the
    session-alive signal the loop's parked() probe would then never fire (the
    watcher spun as an immortal orphan; CI's f10b timeout). Returns the
    lock_acquire vocabulary string."""
    return LK.lock_acquire(S.db_path(LOG), "codex-watch", create=False)


# --- standalone mode: this session's own codex run + its teardown ------------

def standalone_scan(seen, start):
    """STANDALONE poll: stream this codex session's own rollout AND every SUBAGENT
    it spawns. The OWN run is targeted precisely by the rollout filename uuid ==
    our session_id (no cwd heuristics, no claim races, and — unlike the
    secondary-source path — adopted even though its originator is `codex-tui`,
    because here that human-driven TUI IS our session). A SUBAGENT
    (`collaboration.spawn_agent`, cli 0.146+) writes its OWN rollout whose
    `session_meta.parent_thread_id` is our SID; it must read like a Claude
    subagent, so it is streamed STAMPED (`spawn(subagent=True)` -> the ops drop
    from the main mirror and surface in its agent scope). Subagents are gated on
    creation time so a RESUME doesn't replay a prior run's subagents; the own run
    has no such gate (it IS the session)."""
    for rf in rollout_files():
        m = RO_UUID.search(os.path.basename(rf))
        if not m:
            continue
        u = m.group(1)
        if u in seen:
            continue
        if u == SID:
            cw, _origin = rollout_meta(rf)
            if not cw:
                continue              # session_meta not written yet — retry next poll
            seen.add(SID)
            spawn(rf, "-", "cli")     # our standalone codex session (main agent)
            continue
        parent, label = rollout_subagent(rf)
        if parent != SID:
            continue                  # not our subagent (or session_meta not yet written)
        if (rollout_created(rf) or 0) < start - SKEW:
            seen.add(u); continue     # a PRIOR run's subagent (resume) — don't replay
        seen.add(u)
        spawn(rf, "-", label, subagent=True)


def teardown():
    """STANDALONE SessionEnd surrogate. Codex fires no SessionEnd hook, so when
    the codex host pid dies (exit OR a hard Ctrl-C, which fires nothing) this is
    how the session closes: route through the ONE host-teardown owner
    core.hostpane.host_end — session-end audit -> close panes -> park the state DB
    (-> *.keep, so a codex `resume` replays history; renaming makes the DB path
    vanish, which stops the scoreboard bar) -> drop the tab DB row. The `win` is
    passed so the tab is cleared too: the old park-only teardown wrote NO
    session_end and cleared NO tab, so a codex session's `ended_at` stayed NULL
    (an anomaly signal) and its tab lingered red/green forever. host_end drops the
    tab DB row; the visible tab COLOUR is repainted to the theme default here (a
    frontend paint host_end deliberately leaves to the caller), and the
    standalone-host registry row is cleared last. The watcher's cached DB
    connection means the finally's lock_release writes to the parked inode."""
    from core import hostpane as HP
    from core import tabpaint
    from core import tabs
    try:
        import frontends
        fe = frontends.get(resolve=True)
    except Exception:
        from frontends.base import Frontend
        fe = Frontend()
        A.error(LOG, "codex standalone teardown (resolve frontend)")
    win = tabs.codex_host_win(SID) or ""
    if fe.usable() and win:
        try:
            tabpaint.paint(fe, win, "clear", "codex host pid gone",
                           sid=SID, dispatch="codex-clear")
        except Exception:
            A.error(LOG, "codex standalone teardown (tab clear)")
    HP.host_end(fe, SID, LOG, "codex host pid gone", win=win)
    tabs.codex_host_clear(SID)


def main():
    if not LOG:
        return
    got = acquire_lock()
    if got not in ("claim", "steal-stale"):
        # no-db: the session parked before this watcher's first write (slow
        # spawn vs fast SessionEnd) — exit without ever touching the state DB.
        reason = ("parked-before-start (no state DB)"
                  if got == "claim-denied:no-db" else "duplicate (pid lock held)")
        A.event("streams", session_id=A.sid_from_log(LOG), kind="codex-watcher",
                pid=os.getpid(), started_at=time.time(), ended_at=time.time(),
                end_reason=reason)
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
                standalone_scan(seen, start)
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
        LK.lock_release(S.db_path(LOG), "codex-watch")


_WATCH_ID = None

def entry():
    _init(sys.argv)
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
