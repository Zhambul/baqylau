# dashboard/screendrive.py — the GENERIC plumbing shared by the screen-verified
# dialog drivers (askdialog / plandialog / confirmdialog / rewindmenu). Those
# drivers are deliberately NOT unified — their ANATOMY (keys, region parsing,
# bail semantics) is per-dialog and must stay separate (docs/dashboard.md). Only
# the two tool-agnostic mechanical pieces live here:
#
#   - poll_until: the screen re-read poll loop every driver used to inline as a
#     private `_wait`, byte-identical across the three menu drivers (and the
#     same loop confirmdialog spelled out twice). Reads the window's screen,
#     polls until pred(screen) holds or `timeout` elapses, returns the last
#     screen either way plus whether pred held — the drivers key their bail on
#     the bool. `fe` is passed in (this module touches no frontend directly),
#     `sleep` is injectable so tests drive it without real waits, and the poll
#     beat defaults to the drivers' shared 0.15s.
#   - StepError: the common exception base. Every driver's error did the same
#     `super().__init__(step + ": detail")` + `self.step = step`; the four keep
#     DISTINCT names (post.py catches each by name) but share this base.
import time

POLL_S = 0.15   # shared screen re-read beat (each driver's own POLL_S == this)


def poll_until(fe, win, pred, timeout, sleep=time.sleep, poll=POLL_S):
    """Poll window `win`'s screen until pred(screen) or `timeout`; returns the
    last screen either way plus whether pred held (screen, bool)."""
    deadline = time.monotonic() + timeout
    screen = fe.get_text(win) or ""
    while not pred(screen):
        if time.monotonic() >= deadline:
            return screen, False
        sleep(poll)
        screen = fe.get_text(win) or ""
    return screen, True


class StepError(Exception):
    """A dialog step's expected screen state never appeared. `.step` names it
    for the audit row; `.screen` optionally carries the last capture the failing
    step saw (only askdialog populates it — the others leave it None). The four
    driver-specific subclasses keep their distinct names but share this base."""

    def __init__(self, step, detail="", screen=None):
        super().__init__(step + ((": " + detail) if detail else ""))
        self.step = step
        self.screen = screen
