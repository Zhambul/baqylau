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
#   - clip_screen: the audit-context bound every driver's bail applies to the
#     screen it gave up on. It sat in dashboard/config.py, which is a knob
#     registry — a FUNCTION about captured screens belongs with the capture
#     plumbing, next to the callers that clip a StepError's `.screen`.
#   - StepError: the common exception base. Every driver's error did the same
#     `super().__init__(step + ": detail")` + `self.step = step`; the four keep
#     DISTINCT names (post.py catches each by name) but share this base.
import time

POLL_S = 0.15   # shared screen re-read beat (each driver's own POLL_S == this)

SCREEN_CLIP = 2000   # cap on a bail's captured screen in an audit errors row


def clip_screen(scr, cap=SCREEN_CLIP):
    """Bound a captured `get_text` screen for the audit `errors` context while
    keeping BOTH diagnostic ends. A plain `scr[-cap:]` kept only the TAIL, but a
    `step:open` bail's discriminator — is the ☐/☒ header-chip bar present at the
    TOP? — lives at the HEAD (dialog-too-tall vs footer-drift vs blank capture,
    docs/dashboard.md *Web ask*): a WIDE window whose visible screen exceeds
    `cap` would have an on-screen chip bar truncated away and read as
    'off-screen'. Keep the head and the tail with a marker between."""
    if not scr or len(scr) <= cap:
        return scr
    half = cap // 2
    return scr[:half] + "\n…[%d chars elided]…\n" % (len(scr) - cap) + scr[-half:]


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
