#!/usr/bin/env python3
# claude-codex-watch.py MIRROR_LOG CWD [SESSION_ID]
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
# The <slug> is basename(git-root) + sha256(realpath(git-root))[:16] — byte-for-byte
# what codex's state.mjs computes. Colours round-robin claude_slots.CODEX_PALETTE and
# are passed to the streamer as "r,g,b". The watcher exits on its own when the
# session's mirror log is removed at SessionEnd; a pid lock guards against a duplicate.
import glob, hashlib, json, os, re, subprocess, sys, tempfile, time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from claude_slots import CODEX_PALETTE

HERE = os.path.dirname(os.path.abspath(__file__))
STREAM = os.path.join(HERE, "claude-codex-stream.py")
LOG = sys.argv[1] if len(sys.argv) > 1 else ""
CWD = sys.argv[2] if len(sys.argv) > 2 else os.getcwd()
SID = sys.argv[3] if len(sys.argv) > 3 else ""

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


def rollout_cwd(path):
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
                    return ((o.get("payload") or {}).get("cwd") or "").strip()
    except Exception:
        pass
    return ""


_n = 0


def spawn(srcfile, jsonfile, label):
    global _n
    rgb = ",".join(str(x) for x in CODEX_PALETTE[_n % len(CODEX_PALETTE)])
    _n += 1
    try:
        subprocess.Popen(
            [sys.executable, STREAM, LOG, rgb, srcfile, jsonfile, label],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception:
        pass


def label_for(data):
    label = (data.get("title") or "").strip()
    if label.lower().startswith("codex "):
        label = label[6:]
    return label or (data.get("kindLabel") or "task")


def acquire_lock():
    d = LOG + ".slots"
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    p = os.path.join(d, "codex.watch.pid")
    try:
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.write(fd, str(os.getpid()).encode()); os.close(fd)
        return p
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
                import errno
                alive = (e.errno == errno.EPERM)
        if alive:
            return None
        try:
            os.remove(p)
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.write(fd, str(os.getpid()).encode()); os.close(fd)
            return p
        except Exception:
            return None
    except Exception:
        return None


def main():
    if not LOG:
        return
    lock = acquire_lock()
    if lock is None:
        return
    start = time.time()
    seen = set()             # companion job ids + rollout uuids already handled
    pending_ro = {}          # rollout uuid -> first-seen wall time (grace before deciding)
    try:
        while os.path.exists(LOG):
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
                try:
                    mtime = os.path.getmtime(rf)
                except OSError:
                    continue
                if mtime < start - SKEW:
                    seen.add(u); continue     # predates this session
                cw = rollout_cwd(rf)
                if not cw:
                    continue                  # session_meta not written yet — retry
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
                spawn(rf, "-", "cli")         # a raw `codex` / `codex exec` run
            time.sleep(POLL)
    finally:
        try:
            if (open(lock).read().strip() or "0") == str(os.getpid()):
                os.remove(lock)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        os.setsid()          # redundant when launched via start_new_session, harmless
    except Exception:
        pass
    try:
        main()
    except Exception:
        pass
