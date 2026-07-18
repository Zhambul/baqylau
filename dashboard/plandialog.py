# dashboard/plandialog.py — drive Claude Code's ExitPlanMode plan-approval
# dialog from the web (docs/dashboard.md, *Web plan mode*). Third sibling of
# rewindmenu.py / askdialog.py: the pending plan is known from the PreToolUse
# stash (plugins/claude_code/ask_fmt.py, kv `plan-pending`), but the dialog's
# OPTION LABELS are not — they vary with the session's permission mode ("Yes,
# and bypass permissions" vs "Yes, and auto-accept edits", measured
# 2026-07-18) — so the page fetches them from the live screen (options())
# and every decision is screen-verified before its key is pressed.
#
# Empirical dialog facts this encodes (measured live, 2026-07-18, v2.1.214):
#   - anatomy: "Ready to code?" + the rendered plan, then "Claude has written
#     up a plan and is ready to execute. Would you like to proceed?" and
#     numbered rows (`❯ 1. Yes, and bypass permissions` …); the "Tell Claude
#     what to change" row is an EDITABLE feedback row (its sub-line "shift+tab
#     to approve with this feedback" is unnumbered and drops out of parsing);
#   - a digit on a decision row selects IMMEDIATELY (approve fired PostToolUse
#     and executed; mode flips per the chosen option); a digit on the feedback
#     row only FOCUSES it — typed text goes inline, Enter submits the
#     rejection-with-feedback;
#   - Esc dismisses = REJECTS the plan (the transcript gains the rejection
#     tool_result "The user doesn't want to proceed…"); like every decline it
#     fires NO closing hook — the ask_fmt turn-boundary clears own the stash;
#   - bail semantics: like askdialog, a failed step LEAVES the dialog as-is
#     (Escape here would reject the plan the user may still want to approve).
import re
import time

POLL_S = 0.15
STEP_TIMEOUT_S = 2.5
SUBMIT_TIMEOUT_S = 4.0   # a decision → dialog gone (the tool round-trips)

PROCEED = "Would you like to proceed?"       # dialog-open anchor
FEEDBACK_LABEL = "Tell Claude what to change"

_ROW = re.compile(r"^\s*(?P<cur>❯\s+)?(?P<digit>\d+)\.\s+(?P<label>.+?)\s*$")


class PlanError(Exception):
    """A step's expected screen state never appeared. .step names it for the
    audit row. The dialog is left EXACTLY as it was — never Escape-closed
    (Escape REJECTS the plan)."""

    def __init__(self, step, detail=""):
        super().__init__(step + ((": " + detail) if detail else ""))
        self.step = step


def region(screen):
    """The decision region: from the LAST "Would you like to proceed?" down.
    "" when no plan dialog is on screen."""
    if not screen:
        return ""
    i = screen.rfind(PROCEED)
    return screen[i:] if i >= 0 else ""


def dialog_open(screen):
    return bool(region(screen))


def rows(screen):
    """The numbered decision rows: [{digit, label, cursor, feedback}]."""
    out = []
    for ln in region(screen).splitlines():
        m = _ROW.match(ln)
        if m:
            label = m.group("label").strip()
            out.append({"digit": m.group("digit"), "label": label,
                        "cursor": bool(m.group("cur")),
                        "feedback": label.startswith(FEEDBACK_LABEL)})
    return out


def _wait(fe, win, pred, timeout, sleep):
    deadline = time.monotonic() + timeout
    screen = fe.get_text(win) or ""
    while not pred(screen):
        if time.monotonic() >= deadline:
            return screen, False
        sleep(POLL_S)
        screen = fe.get_text(win) or ""
    return screen, True


def _open_rows(fe, win):
    screen = fe.get_text(win) or ""
    if not dialog_open(screen):
        raise PlanError("open", "no plan dialog on screen")
    rs = rows(screen)
    if not rs:
        raise PlanError("open", "plan dialog has no option rows")
    return rs


def options(fe, win):
    """The live decision options, for the page's buttons — labels vary with
    the session's permission mode, so they can only come from the screen."""
    return [{"digit": r["digit"], "label": r["label"],
             "feedback": r["feedback"]} for r in _open_rows(fe, win)]


def decide(fe, win, digit, label, sleep=time.sleep):
    """Press decision row `digit` after verifying the screen still shows
    `label` on it (the dialog may have been replaced since the page fetched
    its options). Feedback rows are refused — use feedback()."""
    rs = _open_rows(fe, win)
    row = next((r for r in rs if r["digit"] == str(digit)), None)
    if row is None or row["label"] != label:
        raise PlanError("option", "row %s is not %r any more" % (digit, label))
    if row["feedback"]:
        raise PlanError("option", "the feedback row takes text, not a click")
    fe.send_key(win, str(digit))
    _, ok = _wait(fe, win, lambda s: not dialog_open(s), SUBMIT_TIMEOUT_S,
                  sleep)
    if not ok:
        raise PlanError("submit", "dialog still open after the decision")
    return {"decided": label}


def feedback(fe, win, text, sleep=time.sleep):
    """Reject the plan with feedback: focus the "Tell Claude what to change"
    row (its digit only focuses — measured), type the text inline, Enter
    submits. Newlines collapse to spaces (the row is a single-line editor;
    a raw CR mid-text would submit early)."""
    text = " ".join((text or "").split())
    if not text:
        raise PlanError("feedback", "empty feedback")
    rs = _open_rows(fe, win)
    row = next((r for r in rs if r["feedback"]), None)
    if row is None:
        raise PlanError("feedback", "no feedback row on screen")
    fe.send_key(win, row["digit"])
    sleep(POLL_S)
    if not fe.send_text(win, text):
        raise PlanError("feedback", "text not delivered")
    _, ok = _wait(fe, win, lambda s: not dialog_open(s), SUBMIT_TIMEOUT_S,
                  sleep)
    if not ok:
        raise PlanError("submit", "dialog still open after the feedback")
    return {"feedback": True}


def dismiss(fe, win, sleep=time.sleep):
    """Esc — reject the plan and keep planning (the TUI's own dismiss)."""
    _open_rows(fe, win)
    fe.send_key(win, "escape")
    _, ok = _wait(fe, win, lambda s: not dialog_open(s), STEP_TIMEOUT_S,
                  sleep)
    if not ok:
        raise PlanError("submit", "dialog still open after Escape")
    return {"dismissed": True}
