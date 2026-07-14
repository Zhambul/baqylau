# L0 — core/panescript.py: the shared skeleton of the two pane-renderer
# ENTRY scripts (claude-mirror.py / claude-scorebar.py). Pins the argv
# contract, the width closure, the SIGWINCH flag-setter shape, and the
# crash-audit main wrapper (its detail string is audit vocabulary).
import os
import signal
import sys

import pytest

from conftest import REPO

sys.path.insert(0, REPO)

from core import panescript as PS            # noqa: E402
from core import render as R                 # noqa: E402


# ---- parse_argv: `script MIRROR_LOG [WIDTH]` --------------------------------

def test_parse_argv_log_and_width():
    assert PS.parse_argv(["x.py", "/tmp/m.log", "80"]) == ("/tmp/m.log", 80)


def test_parse_argv_log_only():
    assert PS.parse_argv(["x.py", "/tmp/m.log"]) == ("/tmp/m.log", None)


def test_parse_argv_empty():
    assert PS.parse_argv(["x.py"]) == ("", None)


def test_parse_argv_non_digit_width_ignored():
    # A stray non-numeric argv[2] must not crash the renderer at import.
    assert PS.parse_argv(["x.py", "/tmp/m.log", "wide"]) == ("/tmp/m.log", None)


# ---- make_width: fixed argv width vs live probe -----------------------------

def test_make_width_fixed():
    assert PS.make_width(72)() == 72


def test_make_width_live_matches_term_width():
    # No fixed width -> defer to R.term_width(None) (live pane size, or its
    # non-tty fallback under pytest) — the two must agree by construction.
    assert PS.make_width(None)() == R.term_width(None)


def test_fit_is_render_fit():
    assert PS.fit is R.fit


# ---- install_winch: the handler body is just the caller's flag-setter -------

def test_install_winch_sets_callers_flag():
    hits = []
    old = signal.getsignal(signal.SIGWINCH)
    try:
        PS.install_winch(lambda: hits.append(1))
        os.kill(os.getpid(), signal.SIGWINCH)
        assert hits == [1]
    finally:
        signal.signal(signal.SIGWINCH, old)


# ---- run_renderer: the __main__ crash wrapper --------------------------------

class _Audit:
    def __init__(self, boom=False):
        self.rows, self.boom = [], boom

    def error(self, log, where, detail=None):
        if self.boom:
            raise RuntimeError("audit down")
        self.rows.append((log, where))


def test_run_renderer_clean_exit():
    a = _Audit()
    PS.run_renderer(lambda: None, "/tmp/m.log", a)
    assert a.rows == []


def test_run_renderer_swallows_keyboard_interrupt():
    a = _Audit()
    PS.run_renderer(lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
                    "/tmp/m.log", a)
    assert a.rows == []                       # Ctrl-C is not a crash


def test_run_renderer_audits_then_reraises():
    a = _Audit()
    with pytest.raises(ValueError):
        PS.run_renderer(lambda: (_ for _ in ()).throw(ValueError("x")),
                        "/tmp/m.log", a)
    # The detail string is audit vocabulary — byte-identical to the old
    # per-script copies.
    assert a.rows == [("/tmp/m.log", "main (renderer crashed)")]


def test_run_renderer_audit_failure_still_reraises():
    with pytest.raises(ValueError):
        PS.run_renderer(lambda: (_ for _ in ()).throw(ValueError("x")),
                        "/tmp/m.log", _Audit(boom=True))
