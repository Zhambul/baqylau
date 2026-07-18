# dashboard/confirmdialog.py — auto-answer the switch-confirm menu that
# /model and /effort can open (docs/dashboard.md, *Web quick commands*).
# Sibling of rewindmenu.py/askdialog.py in philosophy — every key press is
# preceded by READING THE SCREEN back — but deliberately not unified with
# either: this dialog is a bare two-option Yes/No menu with none of their
# anatomy.
#
# Newer Claude Code builds (observed live 2026-07-18, after the v2.1.214
# measurements) no longer always apply `/effort <level>` / `/model <arg>`
# outright: when switching would invalidate the conversation's prompt cache
# the TUI opens a numbered are-you-sure menu ("Change effort level?" …
# "❯ 1. Yes, switch to low / 2. No, go back") and the command does NOTHING
# until it is answered — so a web quick-command click looked dead. The web
# button IS the user's consent, so the server presses the menu's own Yes.
#
# Detection is by SHAPE, not header text: a ❯-cursored numbered option list
# in the screen TAIL whose labels lead with Yes/No. Anchoring on the measured
# effort header would silently miss the model variant (unmeasured wording) —
# and the cursor-on-a-numbered-row + Yes-and-No pair never matches scrollback
# prose or the bare composer prompt (a column-0 `❯` with no `N.` after it).
import re
import time

POLL_S = 0.15          # screen re-read beat while waiting for a menu state
OPEN_TIMEOUT_S = 4.0   # paste delivered → menu visible (slash-cmd latency);
#                        no menu inside this window = the switch applied
#                        silently (same level, no cache — a clean non-event)
STEP_TIMEOUT_S = 2.0   # Yes digit pressed → menu gone
TAIL_LINES = 20        # the live menu sits at the screen bottom; anything
#                        higher is scrollback and must not match

# option row: cursor mark? · digit. · label
_OPT = re.compile(r"^\s*(?P<cur>❯\s*)?(?P<digit>\d+)\.\s+(?P<label>.+?)\s*$")


class ConfirmError(Exception):
    """The confirm menu appeared but would not close after Yes. .step names
    the failed step for the audit row; the menu is left as-is (it is the
    user's decision surface — never Escape it away)."""

    def __init__(self, step, detail=""):
        super().__init__(step + ((": " + detail) if detail else ""))
        self.step = step


def find_menu(screen):
    """The Yes option's digit when the screen tail shows a switch-confirm
    menu, else None: numbered options with the ❯ cursor on one of them, one
    label leading with "Yes" and one with "No"."""
    opts, cursored = {}, False
    for ln in (screen or "").splitlines()[-TAIL_LINES:]:
        m = _OPT.match(ln)
        if not m:
            continue
        opts[m.group("label").lower()] = m.group("digit")
        cursored = cursored or bool(m.group("cur"))
    if not cursored:
        return None
    yes = next((d for l, d in opts.items() if l.startswith("yes")), None)
    no = next((d for l, d in opts.items() if l.startswith("no")), None)
    return yes if (yes and no) else None


def confirm(fe, win, sleep=time.sleep):
    """Watch window `win` for the switch-confirm menu a just-pasted /model or
    /effort may open; press its own Yes digit, verified. Returns
    {"dialog": False} when no menu appeared (the switch applied outright) or
    {"dialog": True, "digit": d} once the answered menu closes; raises
    ConfirmError when the menu stays open after Yes."""
    deadline = time.monotonic() + OPEN_TIMEOUT_S
    while True:
        digit = find_menu(fe.get_text(win) or "")
        if digit:
            break
        if time.monotonic() >= deadline:
            return {"dialog": False}
        sleep(POLL_S)
    fe.send_key(win, digit)
    deadline = time.monotonic() + STEP_TIMEOUT_S
    while find_menu(fe.get_text(win) or ""):
        if time.monotonic() >= deadline:
            raise ConfirmError("close", "confirm menu still open after Yes")
        sleep(POLL_S)
    return {"dialog": True, "digit": digit}
