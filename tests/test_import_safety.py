# test_import_safety.py — importing hook/streamer modules must be side-effect
# free. dispatch.py imports plugins.claude_code.tabstatus for EVERY hook event,
# so an import-time frontend/current-window resolution (or argv read) is work
# paid by everything sharing the process — and substream's old import-time argv
# parse + meta.json read made the module un-importable from tests/tooling.
# These tests run a FRESH interpreter (no cached modules), with empty argv and
# a scrubbed env, and a frontends.get that raises: the import must still
# succeed, proving nothing at import time reads argv or resolves a frontend.
import os
import subprocess
import sys

from conftest import REPO

_PROG = """
import sys, os
sys.argv = ["import-safety-test"]          # no argv contract available
import frontends
def _boom(*a, **k):
    raise AssertionError("frontends.get() called at import time")
frontends.get = _boom
import importlib
importlib.import_module(sys.argv_module)
print("OK")
"""


def _import_fresh(module):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("KITTY_", "CLAUDE_"))}
    prog = _PROG.replace("sys.argv_module", repr(module))
    r = subprocess.run([sys.executable, "-c", prog], cwd=REPO, env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "OK" in r.stdout, (
        f"import of {module} had side effects:\n{r.stderr}")


def test_tabstatus_imports_clean():
    """tabstatus must not resolve the frontend / current window / argv at
    import — FE/WIN are lazy accessors and argv parsing lives in entry()."""
    _import_fresh("plugins.claude_code.tabstatus")


def test_subagent_fmt_imports_clean():
    """subagent_fmt must not read argv at import — dispatch.py imports it for
    every hook event; PHASE defaults to "start" and is set by entry() (argv)
    or run_phase() (the in-process dispatcher path)."""
    _import_fresh("plugins.claude_code.subagent_fmt")


def test_subagent_fmt_run_phase_sets_phase(monkeypatch):
    """The in-process dispatcher path (run_phase) must override the module
    default — no argv involved."""
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    from plugins.claude_code import subagent_fmt as SF
    monkeypatch.setattr(SF, "main", lambda: None)   # phase set is the unit under test
    monkeypatch.setattr(SF, "PHASE", SF.PHASE)      # restore the default at teardown
    SF.run_phase("push")
    assert SF.PHASE == "push"


def test_substream_imports_clean():
    """substream must not parse argv or read meta.json at import — the run
    identity is bound by _init(), called only from entry()."""
    _import_fresh("plugins.claude_code.substream")


def test_transcript_imports_clean():
    """transcript.py (the parse half of the substream split) is a pure parser
    at import — no argv, no I/O, no DB; its only I/O (timeline/activity) is
    call-time, and activity's sessionapi import is deferred."""
    _import_fresh("plugins.claude_code.transcript")


def test_sessionapi_imports_clean():
    """core/sessionapi.py must open no DB at import — it's a read-side leaf
    consumed by long-lived renderers and (transitively, via plugins.activity)
    tooling; all its connects are per-call mode=ro."""
    _import_fresh("core.sessionapi")


def test_dashboard_imports_clean():
    """dashboard/server.py must not bind a port, take the singleton lock, or
    open a DB at import (serve() owns all three); opshtml is a pure presenter."""
    _import_fresh("dashboard.server")
    _import_fresh("dashboard.opshtml")


_STREAM_PROG = """
import sys, os
sys.argv = ["import-safety-test"]          # no argv contract available
# Importing stream must claim no palette slot and write no state DB — the old
# top-level `SLOT, _MARKER = claude_slots.claim(KIND, LOG)` did both.
from core import slots as claude_slots
from core import state as S
def _boom(*a, **k):
    raise AssertionError("slot claim / state-DB write at import time")
claude_slots.claim = _boom
S.connect = _boom
import plugins.claude_code.stream as ST
assert ST.SLOT == 0 and ST._MARKER is None, "slot bound at import"
print("OK")
"""


def test_stream_imports_clean():
    """stream must not parse argv, read the CLAUDE_STREAM_* env contract, or
    — worst of all — claim a palette slot (a state-DB WRITE) at import; the
    run identity is bound by _init(), called only from entry()."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("KITTY_", "CLAUDE_"))}
    r = subprocess.run([sys.executable, "-c", _STREAM_PROG], cwd=REPO, env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "OK" in r.stdout, (
        f"import of plugins.claude_code.stream had side effects:\n{r.stderr}")


_SPLIT_PROG = """
import sys, os
sys.argv = ["import-safety-test"]          # no argv contract available
import frontends, glob
def _boom(*a, **k):
    raise AssertionError("frontends.get() called at import time")
frontends.get = _boom
def _globboom(*a, **k):
    raise AssertionError("glob.glob() called at import time (legacy-size scan)")
glob.glob = _globboom
import plugins.claude_code.split as S
assert S.FE is None, "frontend resolved at import"
print("OK")
"""


def test_split_imports_clean():
    """split must not resolve the frontend (ppid-walk socket hunt +
    export_env) or scan the legacy size dir at import — dispatch.py imports it
    for EVERY hook event; FE is a lazy _fe() accessor and import_legacy_sizes()
    runs memoized from the sizes-DB readers/writers."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("KITTY_", "CLAUDE_"))}
    r = subprocess.run([sys.executable, "-c", _SPLIT_PROG], cwd=REPO, env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "OK" in r.stdout, (
        f"import of plugins.claude_code.split had side effects:\n{r.stderr}")


_CODEX_WATCH_PROG = """
import sys, os, subprocess
sys.argv = ["import-safety-test"]          # no argv contract available
# Importing watch must fork no subprocess — the old top-level
# `SLUGDIR = workspace_slug()` ran `git rev-parse` at import time.
def _boom(*a, **k):
    raise AssertionError("subprocess forked at import time (git rev-parse)")
subprocess.run = _boom
subprocess.Popen = _boom
import plugins.codex.watch as W
assert W.LOG == "" and W.SLUGDIR == "" and W.REPO_ROOT == "", "argv/slug bound at import"
print("OK")
"""


def test_codex_watch_imports_clean():
    """codex watch must not read argv or fork `git rev-parse` (workspace_slug
    -> git_root) at import — the run identity, slug and repo root are bound by
    _init(), called only from entry()."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("KITTY_", "CLAUDE_"))}
    r = subprocess.run([sys.executable, "-c", _CODEX_WATCH_PROG], cwd=REPO, env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "OK" in r.stdout, (
        f"import of plugins.codex.watch had side effects:\n{r.stderr}")


_CODEX_STREAM_PROG = """
import sys, os, subprocess
sys.argv = ["import-safety-test"]          # no argv contract available
def _boom(*a, **k):
    raise AssertionError("subprocess forked at import time")
subprocess.run = _boom
subprocess.Popen = _boom
import plugins.codex.stream as CS
assert CS.LOG == "" and CS.LOGFILE == "" and not CS.ROLLOUT, "argv bound at import"
print("OK")
"""


def test_codex_stream_imports_clean():
    """codex stream must not read argv at import — LOG/SLOT_RGB/LOGFILE/JSONF/
    LABEL/ROLLOUT are bound by _init(), called only from entry()."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("KITTY_", "CLAUDE_"))}
    r = subprocess.run([sys.executable, "-c", _CODEX_STREAM_PROG], cwd=REPO, env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "OK" in r.stdout, (
        f"import of plugins.codex.stream had side effects:\n{r.stderr}")


def test_split_lazy_fe_memoizes(monkeypatch):
    """split._fe() resolves + export_env()s once on first use and honours a
    pre-seeded FE (the test seam, matching tabstatus)."""
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    import frontends
    from plugins.claude_code import split as S
    calls = []

    class _FE:
        def export_env(self):
            calls.append("env")

    monkeypatch.setattr(frontends, "get", lambda *a, **k: calls.append("fe") or _FE())
    monkeypatch.setattr(S, "FE", None)
    fe = S._fe()
    assert S._fe() is fe
    assert calls == ["fe", "env"]          # one resolution + one env stamp
    seeded = object()
    monkeypatch.setattr(S, "FE", seeded)   # pre-seeded value is honoured
    assert S._fe() is seeded


def test_tabstatus_lazy_accessors_memoize(monkeypatch):
    """_fe()/_win() resolve once on first use and honour pre-seeded values
    (the daemon-env fallback and the test seams both assign FE/WIN)."""
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    import frontends
    from plugins.claude_code import tabstatus as T
    calls = []

    class _FE:
        def current_window(self):
            calls.append("win")
            return "7"

    monkeypatch.setattr(frontends, "get", lambda *a, **k: calls.append("fe") or _FE())
    monkeypatch.setattr(T, "FE", None)
    monkeypatch.setattr(T, "WIN", None)
    assert T._win() == "7" and T._win() == "7"
    assert calls == ["fe", "win"]          # one resolution, memoized
    monkeypatch.setattr(T, "WIN", "")      # resolved-but-absent stays absent
    assert T._win() == ""
