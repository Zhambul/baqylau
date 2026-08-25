# harness/impl/claude_code/controls/plandialog.py — drive Claude Code's ExitPlanMode plan-approval
# dialog from the web. Third sibling of
# rewindmenu.py / askdialog.py: the pending plan is known from the PreToolUse
# stash (harness/impl/claude_code/ask_fmt.py, kv `plan-pending`), but the dialog's
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
from collections.abc import Callable
from dataclasses import dataclass

from domain.ids import WindowId

from harness.impl.claude_code.controls import numberedmenu
from harness.impl.claude_code.controls import screen_driver as screendrive
from harness.impl.claude_code.controls.screen_driver import ScreenDriver

POLL_S = 0.15
STEP_TIMEOUT_S = 2.5
SUBMIT_TIMEOUT_S = 4.0   # a decision → dialog gone (the tool round-trips)

PROCEED = "Would you like to proceed?"       # dialog-open anchor
FEEDBACK_LABEL = "Tell Claude what to change"

_ROW = re.compile(r"^\s*(?P<cur>❯\s+)?(?P<digit>\d+)\.\s+(?P<label>.+?)\s*$")


@dataclass(frozen=True)
class Row:
    """One numbered decision row as parsed off the screen (see rows())."""

    digit: str
    label: str
    cursor: bool
    feedback: bool


@dataclass(frozen=True)
class Option:
    """One decision option as offered to the page — a `Row` without the
    cursor position, which the page has no use for."""

    digit: str
    label: str
    feedback: bool


@dataclass(frozen=True)
class Decided:
    decided: str


@dataclass(frozen=True)
class Fedback:
    feedback: bool


@dataclass(frozen=True)
class Dismissed:
    dismissed: bool


class PlanError(screendrive.StepError):
    """A step's expected screen state never appeared. .step names it for the
    audit row. The dialog is left EXACTLY as it was — never Escape-closed
    (Escape REJECTS the plan)."""


def region(screen: str) -> str:
    """The decision region: from the LAST "Would you like to proceed?" down.
    "" when no plan dialog is on screen."""
    if not screen:
        return ""
    i = screen.rfind(PROCEED)
    return screen[i:] if i >= 0 else ""


def dialog_open(screen: str) -> bool:
    return bool(region(screen))


def rows(screen: str) -> list[Row]:
    """The numbered decision rows: [{digit, label, cursor, feedback}]."""
    out: list[Row] = []
    for ln in region(screen).splitlines():
        m = _ROW.match(ln)
        if m:
            label = m.group("label").strip()
            out.append(
                Row(
                    m.group("digit"),
                    label,
                    bool(m.group("cur")),
                    label.startswith(FEEDBACK_LABEL),
                )
            )
    return out


def _numbered_rows(screen_driver: ScreenDriver, win: WindowId) -> tuple[numberedmenu.Row, ...]:
    return tuple(
        numberedmenu.Row(row.digit, row.label, row.cursor)
        for row in _open_rows(screen_driver, win)
    )


def _open_rows(screen_driver: ScreenDriver, win: WindowId) -> list[Row]:
    screen = screen_driver.get_text(win) or ""
    if not dialog_open(screen):
        raise PlanError("open", "no plan dialog on screen")
    rs = rows(screen)
    if not rs:
        raise PlanError("open", "plan dialog has no option rows")
    return rs


def options(screen_driver: ScreenDriver, win: WindowId) -> list[Option]:
    """The live decision options, for the page's buttons — labels vary with
    the session's permission mode, so they can only come from the screen."""
    return [Option(row.digit, row.label, row.feedback) for row in _open_rows(screen_driver, win)]


def decide(
    screen_driver: ScreenDriver, win: WindowId, digit: str, label: str, sleep: Callable[[float], None] = time.sleep,
) -> Decided:
    """Press decision row `digit` after verifying the screen still shows
    `label` on it (the dialog may have been replaced since the page fetched
    its options). Feedback rows are refused — use feedback()."""
    rs = _open_rows(screen_driver, win)
    row = next((row for row in rs if row.digit == str(digit)), None)
    if row is None or row.label != label:
        raise PlanError("option", "row %s is not %r any more" % (digit, label))
    if row.feedback:
        raise PlanError("option", "the feedback row takes text, not a click")
    try:
        numberedmenu.select(
            screen_driver,
            win,
            lambda: _numbered_rows(screen_driver, win),
            str(digit),
            sleep=sleep,
            key_gap=POLL_S,
        )
    except numberedmenu.SelectionError as error:
        raise PlanError("option", str(error)) from error
    _, ok = screendrive.poll_until(
        screen_driver, win, lambda s: not dialog_open(s), SUBMIT_TIMEOUT_S, sleep)
    if not ok:
        raise PlanError("submit", "dialog still open after the decision")
    return Decided(label)


def feedback(
    screen_driver: ScreenDriver, win: WindowId, text: str, sleep: Callable[[float], None] = time.sleep,
) -> Fedback:
    """Reject the plan with feedback through the verified feedback row."""
    text = " ".join((text or "").split())
    if not text:
        raise PlanError("feedback", "empty feedback")
    rs = _open_rows(screen_driver, win)
    row = next((row for row in rs if row.feedback), None)
    if row is None:
        raise PlanError("feedback", "no feedback row on screen")
    try:
        numberedmenu.select(
            screen_driver,
            win,
            lambda: _numbered_rows(screen_driver, win),
            row.digit,
            sleep=sleep,
            key_gap=POLL_S,
        )
    except numberedmenu.SelectionError as error:
        raise PlanError("feedback", str(error)) from error
    if not dialog_open(screen_driver.get_text(win) or ""):
        raise PlanError("feedback", "feedback row closed the plan dialog")
    if not screen_driver.send_text(win, text):
        raise PlanError("feedback", "text not delivered")
    _, ok = screendrive.poll_until(
        screen_driver, win, lambda s: not dialog_open(s), SUBMIT_TIMEOUT_S, sleep)
    if not ok:
        raise PlanError("submit", "dialog still open after the feedback")
    return Fedback(True)


def dismiss(screen_driver: ScreenDriver, win: WindowId, sleep: Callable[[float], None] = time.sleep) -> Dismissed:
    """Esc — reject the plan and keep planning (the TUI's own dismiss)."""
    _open_rows(screen_driver, win)
    screen_driver.send_key(win, "escape")
    _, ok = screendrive.poll_until(
        screen_driver, win, lambda s: not dialog_open(s), STEP_TIMEOUT_S, sleep)
    if not ok:
        raise PlanError("submit", "dialog still open after Escape")
    return Dismissed(True)
