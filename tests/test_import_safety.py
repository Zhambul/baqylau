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


def test_substream_imports_clean():
    """substream must not parse argv or read meta.json at import — the run
    identity is bound by _init(), called only from entry()."""
    _import_fresh("plugins.claude_code.substream")


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
