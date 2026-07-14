# core/panescript.py — the shared skeleton of the two pane-renderer ENTRY
# scripts (claude-mirror.py and claude-scorebar.py, the processes running
# INSIDE kitty panes). Both grew the same boilerplate nearly verbatim: the
# `MIRROR_LOG [WIDTH]` argv contract, the live-width probe, the SIGWINCH
# flag-setter shape, the `fit` alias, and the crash-audit main wrapper. That
# shape lives HERE, once — the per-script behavior (what the WINCH handler
# actually flips, what the main loop does) stays at the call site.
#
# Deliberately NOT shared: the mirror's wakeup-pipe/set_wakeup_fd plumbing
# (scorebar has none) and each script's loop body.
import signal
import sys

from core import render as R

# The renderers' truncate-to-width — re-exported so pane scripts take their
# whole width vocabulary from one import.
fit = R.fit


def parse_argv(argv=None):
    """The pane-renderer argv contract: `script MIRROR_LOG [WIDTH]`.
    Returns (log, fixed_width) — fixed_width is an int only when argv[2] is
    all digits (a literal WIDTH is accepted for non-tty testing), else None
    (width is read live from the pane)."""
    argv = sys.argv if argv is None else argv
    log = argv[1] if len(argv) > 1 else ""
    fixed = int(argv[2]) if len(argv) > 2 and argv[2].isdigit() else None
    return log, fixed


def make_width(fixed):
    """A zero-arg `width()` for this pane: the fixed argv width when given,
    else the pane's live terminal size (R.term_width)."""
    def width():
        return R.term_width(fixed)
    return width


def install_winch(on_winch):
    """Install the SIGWINCH handler shape both renderers share: the handler
    body is just `on_winch()` — a zero-arg flag-setter supplied by the
    caller (the mirror sets L.resized; the scorebar sets its repaint flag).
    Anything heavier belongs in the loop, not the handler."""
    signal.signal(signal.SIGWINCH, lambda signum, frame: on_winch())


def run_renderer(main_fn, log, audit):
    """The pane script's `__main__` crash wrapper: run the loop, exit quietly
    on Ctrl-C, and audit any crash before re-raising (the detail string is
    audit vocabulary — keep it stable). `audit` is the caller's loaded audit
    module/stub (core.noaudit.load_audit())."""
    try:
        main_fn()
    except KeyboardInterrupt:
        pass
    except Exception:
        try:
            audit.error(log, "main (renderer crashed)")
        except Exception:
            pass
        raise
