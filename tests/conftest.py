# conftest.py — the e2e harness (see docs/testing.md).
#
# Philosophy: drive the REAL hook scripts as subprocesses with synthetic JSON
# payloads (exactly how Claude Code invokes them), then assert on the three
# observable state surfaces: the per-session state DB, the global tab DB, and
# the audit DB. The terminal is faked at the pre-existing $KITTY_KITTEN_BIN
# seam (a recorder script standing in for `kitten`); everything else is the
# shipped code, isolated per-test via the env knobs in docs/testing.md.
import json
import os
import signal
import sqlite3
import socket
import subprocess
import sys
import threading
import time
import uuid

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:          # make core/, plugins/, frontends/ importable in-process
    sys.path.insert(0, REPO)

# ---------------------------------------------------------------- wait_until

# Slow shared CI runners (macOS especially, under -n auto) blow the local
# 10s ceiling on legitimately-passing waits: two adjacent pushes each failed
# a DIFFERENT test on the same generic "wait_until timed out (10.0s)" while
# the code was fine. Scale every wait there — a passing wait returns as soon
# as the predicate holds, so the larger ceiling costs nothing when green.
# CLAUDE_TEST_WAIT_SCALE overrides; CI=true (GitHub Actions) defaults to 6x.
# The per-test pytest-timeout budget must scale in LOCKSTEP: with pytest.ini's
# local 30s left unscaled, a slow-but-passing wait dies at 30s as an opaque
# pytest-timeout thread dump before its scaled ceiling is ever reachable (the
# workflow sets PYTEST_TIMEOUT=180 = 30 * 6; pinned by
# test_pytest_timeout_budget_outlives_scaled_waits).
WAIT_SCALE = float(os.environ.get(
    "CLAUDE_TEST_WAIT_SCALE", "6" if os.environ.get("CI") else "1"))


def wait_until(pred, timeout=10.0, interval=0.05, desc=""):
    """The ONE wait primitive — poll an observable fact, never sleep blind."""
    timeout *= WAIT_SCALE
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(interval)
    raise AssertionError("wait_until timed out (%ss): %s" % (timeout, desc or pred))


@pytest.fixture(autouse=True)
def _fresh_audit_conn(tmp_path):
    """core.audit caches its connection (and its gave-up latch) per PROCESS,
    but xdist reuses one worker process across many tests, each with its own
    hermetic CLAUDE_AUDIT_DIR. An in-process test that audits (slots claim/
    release, park_db, ...) after an earlier test primed the cache writes its
    rows into THAT test's dir — the oracle then sees [] (f12's 'slot must be
    released exactly once: []' CI flake was exactly this, worker-order
    dependent). Reset the cache around every test; product processes are
    unaffected (they are per-test subprocesses).

    ALSO sandbox CLAUDE_AUDIT_DIR for in-process product calls: subprocess
    seams get the hermetic dir from test_env, but a unit test calling
    audit-writing product code directly (spawn_detached's script-missing
    degrade row) used to write to the REAL ~/.claude/baqylau-audit DB — and such
    rows are GLOBAL (no sid), so every LIVE session's ⚠ warning light surfaced
    the suite's own deliberate error rows (observed: '⚠ audit: global: -c:
    NoneType: None' in an unrelated session's mirror — '-c' is the xdist
    worker's argv[0]). Tests needing a specific dir still monkeypatch over
    this default.

    ALSO sandbox CLAUDE_CONFIG_DIR the same way: test_env only builds the
    SUBPROCESS env — the pytest process itself inherits the launching shell's
    value, which under the claude-subscription switcher is configs/<slug>,
    whose settings.json is a SYMLINK to the real ~/.claude/settings.json. An
    in-process test that wrote 'the hermetic config dir's settings.json'
    through that ambient value truncated the user's real settings (hooks,
    env, statusLine — everything) to one key. In-process settings reads/
    writes now land in a per-test dir by default."""
    import core.audit as A
    prev = os.environ.get("CLAUDE_AUDIT_DIR")
    prev_cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    os.environ["CLAUDE_AUDIT_DIR"] = str(tmp_path / "audit-inproc")
    cfg_dir = tmp_path / "config-inproc"
    cfg_dir.mkdir(exist_ok=True)
    os.environ["CLAUDE_CONFIG_DIR"] = str(cfg_dir)
    A._CONN, A._FAILED = None, False
    yield
    try:
        if A._CONN is not None:
            A._CONN.close()
    except Exception:
        pass
    A._CONN, A._FAILED = None, False
    if prev is None:
        os.environ.pop("CLAUDE_AUDIT_DIR", None)
    else:
        os.environ["CLAUDE_AUDIT_DIR"] = prev
    if prev_cfg is None:
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
    else:
        os.environ["CLAUDE_CONFIG_DIR"] = prev_cfg


# ------------------------------------------------------------------ test env

@pytest.fixture
def test_env(tmp_path):
    """Hermetic env for every subprocess: fresh dirs for the mirror tmp root,
    audit DB, HOME/TMPDIR, plus fast timing knobs. Host KITTY_*/CLAUDE_* vars
    are stripped so running the suite from inside a live Claude-in-kitty
    session can't leak its state in."""
    dirs = {"mirror": tmp_path / "mirror", "audit": tmp_path / "audit",
            "home": tmp_path / "home", "tmp": tmp_path / "tmp",
            "config": tmp_path / "home" / ".claude"}
    for p in dirs.values():
        p.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("KITTY_", "CLAUDE_"))}
    env.update({
        "HOME": str(dirs["home"]),
        "TMPDIR": str(dirs["tmp"]),
        "CLAUDE_CONFIG_DIR": str(dirs["config"]),
        "CLAUDE_MIRROR_TMPDIR": str(dirs["mirror"]),
        "CLAUDE_AUDIT_DIR": str(dirs["audit"]),
        "CLAUDE_AUDIT": "1",
        "CLAUDE_TAIL_POLL_S": "0.05",
        "CLAUDE_TAIL_BACKSTOP_S": "60",
        "CLAUDE_STREAM_GRACE_S": "0.3",
        "CLAUDE_STREAM_LSOF_S": "0.25",
        "CLAUDE_WATCH_POLL_S": "0.1",
        "CLAUDE_CODEX_GRACE_S": "0.3",
        "CLAUDE_CODEX_WATCH_POLL_S": "0.05",
        "CLAUDE_CODEX_RO_GRACE_S": "0.5",
        "CLAUDE_STREAM_PARENT_SCAN_S": "0.3",
        # Hermeticity: claude-stream.py/claude-cmd-fmt.py glob this root for
        # Claude Code's tasks/<id>.output files (default: the real product
        # location on shared host /private/tmp) — point it into the sandbox
        # so the task_dir fixture never touches host /tmp.
        "CLAUDE_TASKS_GLOB_ROOT": str(dirs["tmp"] / "tasks"),
        # OTLP receiver: a short idle-exit so a spawned receiver never lingers.
        # The port is picked per-test in test_l5_otel (this default is only a
        # backstop). The receiver only spawns when CLAUDE_CODE_ENABLE_TELEMETRY=1,
        # which the suite never sets — so it stays inert unless a test opts in.
        "CLAUDE_OTEL_GRACE_S": "0.5",
        # Hermeticity: claude-split.py's resolve_listen_on() otherwise goes
        # looking for a REAL kitty socket under /tmp/kitty-* (and found the
        # desktop session's when this suite ran on the dev machine). A dead
        # socket path makes every kitten call fail silently instead;
        # fake_kitten overrides this with its recorder.
        "KITTY_LISTEN_ON": "unix:" + str(tmp_path / "no-such-kitty.sock"),
    })
    return env


# ---------------------------------------------------------------- fake kitten

_KITTEN_SRC = r'''#!/usr/bin/env python3
# Fake `kitten` recorder: logs every invocation's argv, honours a control file
# for programmed rc / canned `@ ls` output, and keeps a minimal WINDOW MODEL so
# launch/close-window/set-user-vars round-trip through `ls` (what the product
# uses to find its panes). Stands in for the real binary via the product's own
# $KITTY_KITTEN_BIN override (frontends/kitty.py find_kitten).
import json, os, sys
root = os.path.dirname(os.path.abspath(__file__))
argv = sys.argv[1:]
with open(os.path.join(root, "kitten-calls.jsonl"), "a") as f:
    f.write(json.dumps(argv) + "\n")

def load(name, default):
    try:
        with open(os.path.join(root, name)) as f:
            return json.load(f)
    except Exception:
        return default

def save(name, obj):
    with open(os.path.join(root, name), "w") as f:
        json.dump(obj, f)

cfg = load("kitten-ctl.json", {})
wins = load("kitten-windows.json", None)
if wins is None:
    wins = [{"id": int(cfg.get("base_win", 1)), "user_vars": {},
             "is_focused": True}]

# argv shape (frontends/kitty.py kitten_run/kitten_ls): @ --to <listen> <subcmd> ...
sub, toks = "", list(argv)
if toks and toks[0] == "@":
    toks = toks[1:]
while toks:
    if toks[0] == "--to":
        toks = toks[2:]
        continue
    sub = toks[0]
    toks = toks[1:]
    break

def matches(w, m):
    kind, _, val = m.partition(":")
    if kind in ("id", "window_id"):
        return str(w["id"]) == val
    if kind == "var":
        k, _, v = val.partition("=")
        return w.get("user_vars", {}).get(k) == v
    return False

def opt(flag):
    return toks[toks.index(flag) + 1] if flag in toks else None

if sub == "launch":
    uv = {}
    for i, a in enumerate(toks):
        if a == "--var" and i + 1 < len(toks):
            k, _, v = toks[i + 1].partition("=")
            uv[k] = v
    wid = max([w["id"] for w in wins] + [999]) + 1
    wins.append({"id": wid, "user_vars": uv, "is_focused": False})
    save("kitten-windows.json", wins)
    print(wid)
elif sub == "close-window":
    m = opt("--match")
    wins = [w for w in wins if not (m and matches(w, m))]
    save("kitten-windows.json", wins)
elif sub == "set-user-vars":
    m = opt("--match")
    for w in wins:
        if m and matches(w, m):
            for a in toks[toks.index(m) + 1:]:
                if "=" in a and not a.startswith("--"):
                    k, _, v = a.partition("=")
                    w["user_vars"][k] = v
    save("kitten-windows.json", wins)
elif sub == "ls":
    tree = cfg.get("ls") or [{"id": 1, "is_focused": True,
                              "tabs": [{"id": 1, "is_focused": True,
                                        "windows": wins}]}]
    print(json.dumps(tree))
sys.exit(int(cfg.get("rc", {}).get(sub, cfg.get("rc_default", 0))))
'''

_WIN_COUNTER = [100]


class FakeKitten:
    def __init__(self, root):
        os.makedirs(str(root), exist_ok=True)
        self.root = str(root)
        self.bin = os.path.join(self.root, "kitten")
        self.calls_path = os.path.join(self.root, "kitten-calls.jsonl")
        self.ctl_path = os.path.join(self.root, "kitten-ctl.json")
        with open(self.bin, "w") as f:
            f.write(_KITTEN_SRC)
        os.chmod(self.bin, 0o755)
        _WIN_COUNTER[0] += 1
        self.window_id = str(_WIN_COUNTER[0])
        self.listen = "unix:" + os.path.join(self.root, "fake-kitty.sock")
        self._ctl = {"base_win": int(self.window_id)}
        self._write_ctl()

    def calls(self, sub=None):
        """Recorded invocations, each as the argv list; optionally only those
        whose subcommand (first token after `@ --to <listen>`) matches."""
        out = []
        try:
            with open(self.calls_path) as f:
                for line in f:
                    argv = json.loads(line)
                    if sub is None or self._sub(argv) == sub:
                        out.append(argv)
        except OSError:
            pass
        return out

    @staticmethod
    def _sub(argv):
        toks = argv[1:] if argv[:1] == ["@"] else list(argv)
        while toks:
            if toks[0] == "--to":
                toks = toks[2:]
                continue
            return toks[0]
        return ""

    def clear(self):
        try:
            os.remove(self.calls_path)
        except OSError:
            pass

    def windows(self):
        """The fake window model (what `@ ls` reflects after launches/closes)."""
        try:
            with open(os.path.join(self.root, "kitten-windows.json")) as f:
                return json.load(f)
        except OSError:
            return []

    def _write_ctl(self):
        with open(self.ctl_path, "w") as f:
            json.dump(self._ctl, f)

    def set_rc(self, sub, rc):
        self._ctl.setdefault("rc", {})[sub] = rc
        self._write_ctl()

    def set_ls(self, tree):
        self._ctl["ls"] = tree
        self._write_ctl()

    def set_ls_for_session(self, sid, win_id=None, extra_windows=()):
        """Canned `kitten @ ls` tree: one OS window / tab holding a window
        tagged with user_vars.claude_session=<sid> (how the product finds the
        Claude pane), plus any extra window dicts."""
        win = {"id": int(win_id or self.window_id),
               "user_vars": {"claude_session": sid}, "is_focused": True}
        self.set_ls([{"id": 1, "is_focused": True,
                      "tabs": [{"id": 1, "is_focused": True,
                                "windows": [win, *extra_windows]}]}])


@pytest.fixture
def fake_kitten(test_env, tmp_path):
    fk = FakeKitten(tmp_path / "kitten-root")
    test_env["KITTY_KITTEN_BIN"] = fk.bin
    test_env["KITTY_LISTEN_ON"] = fk.listen
    test_env["KITTY_WINDOW_ID"] = fk.window_id
    return fk


# ------------------------------------------------------------- fake rc socket

class FakeRCServer:
    """A live AF_UNIX socket speaking kitty's @kitty-cmd framing — the seam for
    the RAW remote-control path (frontends/kitty.py _rc_raw), which bypasses
    the kitten subprocess entirely so the fake-kitten recorder never sees it.
    Records every decoded command envelope and replies with a programmable
    response ({"ok": True} by default) whenever the client requests one."""

    ST = b"\x1b\\"
    KEY = b"@kitty-cmd"

    def __init__(self, path):
        # Caller supplies a SHORT path (unix socket paths cap at ~104 bytes;
        # pytest tmp dirs blow past that — same constraint as test_l8_kitty).
        self.path = path
        self.frames = []
        self.response = {"ok": True}
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(path)
        self._srv.listen(8)
        self._srv.settimeout(0.2)
        self._stop = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while not self._stop:
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                conn.settimeout(2)
                buf = b""
                while self.ST not in buf:
                    b = conn.recv(65536)
                    if not b:
                        break
                    buf += b
                if self.KEY in buf and self.ST in buf:
                    obj = json.loads(buf[buf.index(self.KEY) + len(self.KEY):
                                         buf.index(self.ST)])
                    self.frames.append(obj)
                    if not obj.get("no_response"):
                        conn.sendall(b"\x1bP" + self.KEY +
                                     json.dumps(self.response).encode("utf-8") +
                                     self.ST)
            except Exception:
                pass
            finally:
                conn.close()

    def commands(self, cmd=None):
        """Decoded @kitty-cmd envelopes, optionally filtered by cmd name."""
        return [f for f in self.frames if cmd is None or f.get("cmd") == cmd]

    def close(self):
        self._stop = True
        try:
            self._srv.close()
        except OSError:
            pass
        self._thread.join(2)
        try:
            os.remove(self.path)
        except OSError:
            pass


@pytest.fixture
def fake_rc_socket(test_env):
    """A FakeRCServer wired into the test env as $KITTY_LISTEN_ON, so both
    in-process KittyFrontend calls and hook subprocesses hit the raw path.
    Compose AFTER fake_kitten when both are wanted: this overwrites the
    recorder's dead listen path with the live socket (the kitten binary
    override stays, so subprocess fallbacks still hit the recorder)."""
    srv = FakeRCServer("/tmp/claude-rc-%d-%d.sock" % (os.getpid(), _WIN_COUNTER[0]))
    _WIN_COUNTER[0] += 1
    test_env["KITTY_LISTEN_ON"] = "unix:" + srv.path
    yield srv
    srv.close()


# ------------------------------------------------------------------- session

class Session:
    """A synthetic Claude Code session: unique sid, its own cwd + transcript,
    and helpers for the paths/DBs every assertion needs. The path arithmetic
    here deliberately re-states the core/paths.py format — pinning it."""

    def __init__(self, env, root, sid=None):
        self.env = env
        self.sid = sid or str(uuid.uuid4())
        self.cwd = os.path.join(str(root), "project")
        os.makedirs(self.cwd, exist_ok=True)
        tdir = os.path.join(str(root), "transcripts")
        os.makedirs(tdir, exist_ok=True)
        self.transcript = os.path.join(tdir, self.sid + ".jsonl")
        open(self.transcript, "a").close()
        self.log = env["CLAUDE_MIRROR_TMPDIR"] + "/claude-mirror-" + self.sid + ".log"
        self.state_db = self.log + ".state.db"
        # The DURABLE park (core/paths.parked_db) — under HISTORY_DIR, which the
        # CLAUDE_MIRROR_TMPDIR seam relocates into the hermetic tmpdir. Re-stated
        # here (like the paths above) to pin the format.
        self.parked_db = (env["CLAUDE_MIRROR_TMPDIR"]
                          + "/baqylau-mirror-history/" + self.sid + ".state.db")

    # ---- transcript writers (shapes per plugins/claude_code/accounting.py bump_transcript) ----
    def add_line(self, obj):
        with open(self.transcript, "a") as f:
            f.write(json.dumps(obj) + "\n")

    def add_assistant(self, msg_id, model="claude-opus-4-8", usage=None,
                      text="hello", sidechain=False):
        o = {"type": "assistant",
             "message": {"id": msg_id, "model": model, "role": "assistant",
                         "content": [{"type": "text", "text": text}],
                         "usage": usage or {"input_tokens": 10, "output_tokens": 5,
                                            "cache_creation_input_tokens": 0,
                                            "cache_read_input_tokens": 0}}}
        if sidechain:
            o["isSidechain"] = True
        self.add_line(o)

    def add_user(self, text="do the thing"):
        self.add_line({"type": "user", "message": {"role": "user", "content": text}})

    def add_interrupted(self):
        self.add_line({"type": "user", "message": {
            "role": "user", "content": "[Request interrupted by user]"}})

    # ---- subagent sidecars (paths per claude-substream.py) ----
    @property
    def subagent_dir(self):
        return self.transcript[:-len(".jsonl")] + "/subagents"

    def subagent_jsonl(self, agent_id):
        return os.path.join(self.subagent_dir, "agent-%s.jsonl" % agent_id)

    def write_subagent_jsonl(self, agent_id, events):
        os.makedirs(self.subagent_dir, exist_ok=True)
        with open(self.subagent_jsonl(agent_id), "a") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")

    def write_meta(self, agent_id, **fields):
        os.makedirs(self.subagent_dir, exist_ok=True)
        path = os.path.join(self.subagent_dir, "agent-%s.meta.json" % agent_id)
        cur = {}
        if os.path.exists(path):
            with open(path) as f:
                cur = json.load(f)
        cur.update(fields)
        with open(path, "w") as f:
            json.dump(cur, f)
        return path

    # ---- state-DB readers ----
    def query_state(self, sql, args=()):
        if not os.path.exists(self.state_db):
            return []
        conn = sqlite3.connect("file:%s?mode=ro" % self.state_db, uri=True, timeout=5)
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def ops(self):
        """All mirror paint ops, as parsed JSON rows (core/state.py ops table)."""
        return [json.loads(r[0]) for r in
                self.query_state("SELECT op FROM ops ORDER BY id")]

    def ops_text(self):
        """Every string anywhere in the ops stream, joined — coarse 'does the
        mirror show X' assertions."""
        def strings(v):
            if isinstance(v, str):
                yield v
            elif isinstance(v, (list, tuple)):
                for x in v:
                    yield from strings(x)
            elif isinstance(v, dict):
                for x in v.values():
                    yield from strings(x)
        return "\n".join(s for op in self.ops() for s in strings(op))

    def counters(self):
        return dict(self.query_state("SELECT key, val FROM counters"))

    def live(self, kind=None):
        rows = self.query_state("SELECT kind, key, pid, idx FROM live")
        return [r for r in rows if kind is None or r[0] == kind]

    def agents(self):
        return self.query_state(
            "SELECT agent_id, slot, desc, pos, done FROM agents")


@pytest.fixture
def session(test_env, tmp_path):
    """Factory: session.make() → a fresh synthetic Session in this test's env."""
    class _Factory:
        def make(self, sid=None):
            return Session(test_env, tmp_path, sid=sid)
    return _Factory()


# ------------------------------------------------------------------ run_hook

@pytest.fixture
def run_hook(test_env):
    """Invoke a hook script exactly as Claude Code does: a subprocess with the
    JSON payload on stdin. Asserts the never-fail contract (rc 0) by default."""
    def _run(script, payload=None, argv=(), env=None, raw_stdin=None,
             check=True, timeout=15):
        e = dict(env if env is not None else test_env)
        stdin = raw_stdin if raw_stdin is not None else json.dumps(payload or {})
        p = subprocess.run(
            [sys.executable, os.path.join(REPO, "bin", script), *map(str, argv)],
            input=stdin, text=True, capture_output=True, env=e, timeout=timeout,
            cwd=REPO)
        if check:
            assert p.returncode == 0, \
                "%s %s exited %s\nstdout: %s\nstderr: %s" % (
                    script, argv, p.returncode, p.stdout, p.stderr)
        return p
    return _run


# ---------------------------------------------------------------- state seed

@pytest.fixture
def seed(test_env, reaper):
    """Seed runtime state exactly like the product would — via core.state in
    a subprocess running under the test env (so CLAUDE_MIRROR_TMPDIR applies)."""
    class _Seed:
        def py(self, code, timeout=15):
            p = subprocess.run([sys.executable, "-c", code], env=dict(test_env),
                               cwd=REPO, capture_output=True, text=True,
                               timeout=timeout)
            assert p.returncode == 0, "seed code failed:\n%s\n%s" % (code, p.stderr)
            return p.stdout

        def live_row(self, log, kind, pid):
            """A live slot row — the tab tracker's liveness signal. Seeded via
            the product's own writer (core.slots.claim + set_owner, exactly the
            launcher→streamer hand-off) so a `live` schema change breaks here
            loudly at the API, not silently in an interpolated SQL string. Stays
            a subprocess so CLAUDE_MIRROR_TMPDIR (and the rest of the test env)
            governs where core.paths puts the state DB; the row's OWNER pid is
            the caller-supplied one (live_pid()/dead_pid()), not the seeder's."""
            self.py(
                "from core import slots\n"
                "idx, token = slots.claim(%r, %r)\n"
                "assert token is not None, 'seed: slot claim failed'\n"
                "slots.set_owner(token, %d)\n" % (kind, log, int(pid)))

        def live_pid(self):
            """A genuinely-alive pid to own a live row (reaped at teardown)."""
            p = subprocess.Popen(["sleep", "300"], start_new_session=True)
            reaper.append(p)
            return p.pid

        def dead_pid(self):
            p = subprocess.Popen(["true"])
            p.wait()
            return p.pid
    return _Seed()


# -------------------------------------------------------------------- reaper

@pytest.fixture(autouse=True)
def reaper(test_env):
    """Kill every process a test leaves behind: Popens registered by the test
    plus every hook-spawned detached child recorded in the audit spawns table
    (each is its own process group via start_new_session=True)."""
    procs = []
    yield procs
    pids = set()
    for p in procs:
        pids.add(p.pid if hasattr(p, "pid") else int(p))
    db = os.path.join(test_env["CLAUDE_AUDIT_DIR"], "audit.db")
    if os.path.exists(db):
        try:
            conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
            pids.update(r[0] for r in
                        conn.execute("SELECT child_pid FROM spawns").fetchall())
            conn.close()
        except sqlite3.Error:
            pass
    # tab-status watchers register in the tab DB, not the audit spawns table
    tab_db = os.path.join(test_env["CLAUDE_MIRROR_TMPDIR"], "claude-kitty-tab.db")
    if os.path.exists(tab_db):
        try:
            conn = sqlite3.connect("file:%s?mode=ro" % tab_db, uri=True, timeout=5)
            pids.update(r[0] for r in
                        conn.execute("SELECT pid FROM watchers").fetchall())
            conn.close()
        except sqlite3.Error:
            pass
    for pid in pids:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(int(pid), sig)
            except (ProcessLookupError, PermissionError, OSError):
                break
            time.sleep(0.02)
