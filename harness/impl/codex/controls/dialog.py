# harness/impl/codex/controls/dialog.py — drive codex's request_user_input dialog from the web.
#
# The codex twin of harness/impl/claude_code/controls/askdialog.py, and the same philosophy: the dialog
# exists only as live TUI pixels (the pending questions are known from the
# rollout — harness/impl/codex/read.pending_dialog — but there is no API to answer
# them), so the one way to submit an answer is the dialog itself, every step
# verified by READING THE SCREEN back. Deliberately NOT a reuse of Claude's
# askdialog: codex's dialog is a DIFFERENT anatomy (a `Question N/M` header, a `›`
# cursor, an `enter to submit answer` footer), so Claude's region() returns "" on
# a codex screen and its key model does not apply.
#
# It lives in the PLUGIN because codex's `ask` HostControl gesture
# (harness/impl/codex/hostctl.py) drives it and the layering rule forbids a plugin
# importing the dashboard — so the whole gesture, screen driver included, sits
# behind HostControl and the dashboard only calls host.ask. Claude Code's
# drivers followed the same argument into ITS plugin in P2; the shared
# skeleton they used to reach in dashboard/ moved down to core/screendrive.py.
#
# request_user_input is model-nondeterministic (the model sometimes answers in
# prose instead of raising the tool), so this is reached rarely and is
# best-effort by design: a step that never verifies raises CodexAskError with the
# dialog LEFT OPEN (never Escape-closed — codex's Esc ABORTS the turn, the
# opposite of a decline), for a retry from the card.
#
# Empirical dialog geometry (docs/codex.md, re-measured live 2026-07-31 against
# codex-cli 0.146.0):
#   - a header line `Question N/M (K unanswered)` (N/M 1-based);
#   - numbered option rows `  N. <label>  <description>`, a `›` cursor on the
#     current option (codex renders `›`; `❯` is tolerated for version drift);
#   - a row codex APPENDS after the model's own options, `None of the above`
#     ("Optionally, add details in notes (tab)."). It is codex's, not the tool
#     call's, so it is invisible to read.pending_dialog — and it is THE free-text
#     answer: picking it and typing a note submits
#     `["None of the above", "user_note: <text>"]`;
#   - a footer `tab to add notes | enter to submit answer | esc to interrupt`
#     (a MULTI-question dialog reads `enter to submit all` on the last question,
#     and adds `←/→ to navigate questions`);
#   - DOWN/UP walk the `›` cursor, ENTER submits the current question's answer and
#     advances to the next (forward-only, like Claude's build), RIGHT/LEFT move
#     between questions WITHOUT answering, `tab` opens a free-text notes field
#     whose footer reads `tab or esc to clear notes`.
#
# THE CURSOR IS NOT THE SELECTION, but every submitting key TAKES it: with the
# header still reading `(1 unanswered)`, both ENTER and TAB select whatever row
# the `›` sits on. That is the whole of the bug this module shipped for its first
# life — a free-text answer pressed `tab` from the cursor's resting place (row 1)
# and typed, so codex recorded `answers: ["<option 1>", "user_note: <what you
# actually wrote>"]`: the FIRST option was submitted as the answer and the user's
# real one demoted to a footnote (measured in a live rollout, 2026-07-31). Hence
# `_cursor_to` runs FIRST on every path, `None of the above` included.
#
# Submitting with a question left unanswered raises a confirmation step of its
# own (`Submit with unanswered questions?` → `Proceed` / `Go back`), which is how
# `decline` (the card's "chat about this") gets as close to "I am not answering
# these" as codex allows — see its docstring for the one answer codex still
# forces.
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from domain.ids import WindowId
from harness.contract import ComposerDriver

Driver = ComposerDriver

POLL_S = 0.15           # screen re-read beat while waiting for a dialog state
STEP_TIMEOUT_S = 2.5    # a key press → its screen effect visible
NAV_STEPS = 24          # max up/down presses to walk the cursor to a target row

# codex dialog footer detector. A single-question dialog reads
# "enter to submit answer"; on a MULTI-question dialog the LAST question's footer
# switches to "enter to submit all" — matching the common "to submit" stem keeps
# `dialog_open` True through that switch (a verified live bug: keyed on the exact
# "submit answer" the driver bailed on the final question, leaving it unanswered),
# and stays disjoint from the plan/model picker footer ("to confirm").
FOOT = "to submit"
# the notes field's own footer, which REPLACES the one above once `tab` opens it
# — so it is also part of "the dialog is still open" (a question mid-note has no
# "to submit" line at all).
NOTES_FOOT = "to clear notes"
# codex's appended free-text row, and the prefix a narrow pane can truncate it to
NONE_LABEL = "None of the above"
NONE_PREFIX = "None of the"
# the submit-with-holes confirmation and its GO-AHEAD row (`Go back` is the other)
CONFIRM_HEAD = "Submit with unanswered questions?"
PROCEED_LABEL = "Proceed"
_HEADER = re.compile(r"Question\s+(\d+)\s*/\s*(\d+)")
# option row: cursor mark? · number. · label (a 2+-space run starts the dim
# description, which is not part of the label).
_OPT = re.compile(r"^\s*(?P<cur>[›❯]\s+)?(?P<num>\d+)\.\s+"
                  r"(?P<label>.+?)(?:\s{2,}.*)?\s*$")


@dataclass(frozen=True)
class PromptChoice:
    """One option of a pending question, as the pending_dialog stash carries it."""

    label: str
    description: str


@dataclass(frozen=True)
class Prompt:
    """One pending question from the pending_dialog stash (verbatim)."""

    id: str = ""
    header: str = ""
    question: str = ""
    options: tuple[PromptChoice, ...] = ()


@dataclass(frozen=True)
class OptionRow:
    """One numbered option row as read off the live screen."""

    num: str
    label: str
    cursor: bool


@dataclass(frozen=True)
class Answer:
    """One question's answer, as the control API's `AnswerQuestion.answers`
    JSON decodes to — `{selected: [labels...], other: text}` per question,
    aligned by position with the pending_dialog `Prompt` list."""

    selected: tuple[str, ...] = ()
    other: str = ""


class DialogOutcome(StrEnum):
    SUBMITTED = "submitted"


class CodexAskError(Exception):
    """A step's expected screen state never appeared. `.step` names it for the
    audit row; the dialog is left EXACTLY as it was (never Escape-closed — codex's
    Esc aborts the turn), so a re-answer from the card normalizes and retries."""

    def __init__(self, step: str, detail: str = "") -> None:
        super().__init__(step + ((": " + detail) if detail else ""))
        self.step = step
        self.detail = detail


def _poll(
    driver: Driver,
    win: WindowId,
    pred: Callable[[str], bool],
    timeout: float,
    sleep: Callable[[float], None],
) -> tuple[str, bool]:
    """Poll `win`'s screen until pred(screen) or `timeout`; (screen, held)."""
    deadline = time.monotonic() + timeout
    screen = driver.get_text(win) or ""
    while not pred(screen):
        if time.monotonic() >= deadline:
            return screen, False
        sleep(POLL_S)
        screen = driver.get_text(win) or ""
    return screen, True


def dialog_open(screen: str) -> bool:
    """Is codex's question dialog on screen — its footer visible. Either footer
    counts: `tab to add notes | enter to submit …`, or the notes field's own
    `tab or esc to clear notes`, which replaces it while a note is being typed."""
    s = screen or ""
    return FOOT in s or NOTES_FOOT in s


def notes_open(screen: str) -> bool:
    """Is the `tab`-opened notes field focused — its footer visible. The one
    proof that typing will land in the note rather than in codex's composer."""
    return NOTES_FOOT in (screen or "")


def confirm_open(screen: str) -> bool:
    """Is the `Submit with unanswered questions?` confirmation on screen. Raised
    by a submit that leaves any question unanswered (decline's normal ending, and
    a step `drive` resolves too rather than leaving the dialog hanging)."""
    return CONFIRM_HEAD in (screen or "")


def current_question(screen: str) -> tuple[int, int] | None:
    """The (n, m) of the `Question N/M` header (1-based), or None."""
    m = _HEADER.search(screen or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def rows(screen: str) -> list[OptionRow]:
    """The numbered option rows in screen order: [{num, label, cursor}]."""
    out: list[OptionRow] = []
    for ln in (screen or "").splitlines():
        m = _OPT.match(ln)
        if m:
            out.append(OptionRow(m.group("num"), m.group("label").strip(), bool(m.group("cur"))))
    return out


def _row_num(screen: str, label: str, prefix: str = "") -> str:
    """The number of the row labelled `label` (exact), else the first row whose
    label starts with `prefix` (a narrow pane truncates the dim tail), else ""."""
    rs = rows(screen)
    for r in rs:
        if r.label == label:
            return r.num
    if prefix:
        for r in rs:
            if r.label.startswith(prefix):
                return r.num
    return ""


def none_row(screen: str, prompt: Prompt) -> str:
    """The number of codex's appended `None of the above` row — the free-text
    answer's target. Matched by LABEL first; failing that (a truncated pane, a
    reworded row) by POSITION, which codex fixes: exactly one row more than the
    tool call's own options, and last. Returns "" when neither holds, so a codex
    that stops appending the row fails the step LOUDLY rather than submitting one
    of the model's real options as if the user had chosen it."""
    num = _row_num(screen, NONE_LABEL, NONE_PREFIX)
    if num:
        return num
    n = len(prompt.options)
    rs = rows(screen)
    if n and len(rs) == n + 1 and rs[-1].num == str(n + 1):
        return rs[-1].num
    return ""


def _cursor_row(screen: str) -> OptionRow | None:
    return next((r for r in rows(screen) if r.cursor), None)


class _NoPreviousCursor:
    """A sentinel distinct from every `OptionRow["num"]` value AND from None
    (the "no cursor row visible" reading) — `_cursor_to` below needs a THIRD
    state, "haven't looked yet", so its first comparison can never fire."""


_NO_PREVIOUS_CURSOR = _NoPreviousCursor()


def _cursor_to(driver: Driver, win: WindowId, num: str, sleep: Callable[[float], None]) -> None:
    """Move the `›` cursor onto option `num`: normalize UP to option 1 (up is a
    no-op there), then walk DOWN, screen-verified each step. Bail if `up` stops
    making progress (a trapped/edit row)."""
    prev: str | None | _NoPreviousCursor = _NO_PREVIOUS_CURSOR
    for _ in range(NAV_STEPS):
        cur = _cursor_row(driver.get_text(win) or "")
        if cur is not None and cur.num == "1":
            break
        key = None if cur is None else cur.num
        if key == prev:
            break
        prev = key
        driver.send_key(win, "up")
        sleep(POLL_S)
    for _ in range(NAV_STEPS):
        cur = _cursor_row(driver.get_text(win) or "")
        if cur is not None and cur.num == num:
            return
        driver.send_key(win, "down")
        sleep(POLL_S)
    raise CodexAskError("cursor", "cursor never reached option %s" % num)


def _note(driver: Driver, win: WindowId, text: str, sleep: Callable[[float], None]) -> None:
    """`tab` into the notes field and paste `text` as one input operation.

    The field exists only after the tab state change. A fast typed write can lose
    all text at that boundary. Bracketed paste delivers one atomic value, and its
    separate Enter submits the question.
    """
    driver.send_key(win, "tab")
    _, ok = _poll(driver, win, notes_open, STEP_TIMEOUT_S, sleep)
    if not ok:
        raise CodexAskError("notes", "notes field never opened")
    if not driver.paste_text(win, text):
        raise CodexAskError("notes", "notes not delivered")


def _answer_one(
    driver: Driver,
    win: WindowId,
    prompt: Prompt,
    answer: Answer,
    sleep: Callable[[float], None],
) -> None:
    """Apply one question's answer to the CURRENT pane. The cursor is moved onto
    the target row FIRST in every case, because both submitting keys take it:

      · a chosen option        → cursor onto it, then ENTER;
      · a free-text answer     → cursor onto codex's `None of the above` row,
                                 then `tab` + the text (which is submitted as
                                 `user_note:` beside it);
      · an option AND text     → the same, on the chosen option — codex's dialog
                                 natively carries a note beside a pick.
    """
    labels = [option.label for option in prompt.options]
    selected = [selection for selection in answer.selected if selection in labels]
    other = answer.other.strip()
    if selected:
        num = str(1 + labels.index(selected[0]))
    elif other:
        num = none_row(driver.get_text(win) or "", prompt)
        if not num:
            raise CodexAskError("noneof",
                                "no %r row for a free-text answer" % NONE_LABEL)
    else:
        raise CodexAskError("options",
                            "no answer for %r" % prompt.question[:60])
    _cursor_to(driver, win, num, sleep)
    if other:
        _note(driver, win, other, sleep)
    else:
        driver.send_key(win, "enter")          # submit this question + advance


def _confirm(driver: Driver, win: WindowId, sleep: Callable[[float], None]) -> None:
    """Resolve the `Submit with unanswered questions?` step if it is up: cursor
    onto `Proceed` and ENTER. A no-op when no confirmation appeared (every
    question answered), so both callers can end with it."""
    screen = driver.get_text(win) or ""
    if not confirm_open(screen):
        return
    num = _row_num(screen, PROCEED_LABEL) or "1"
    _cursor_to(driver, win, num, sleep)
    driver.send_key(win, "enter")
    _, ok = _poll(driver, win, lambda s: not confirm_open(s), STEP_TIMEOUT_S, sleep)
    if not ok:
        raise CodexAskError("confirm",
                            "the unanswered-questions confirm stayed up")


def drive(
    driver: Driver,
    win: WindowId,
    questions: list[Prompt],
    answers: list[Answer],
    sleep: Callable[[float], None] = time.sleep,
) -> DialogOutcome:
    """Answer codex's OPEN request_user_input dialog in window `win`. `questions`
    is the pending_dialog stash ([{id, header, question, options[{label,
    description}]}], verbatim); `answers` aligns with it ([{selected: [labels…],
    other: text}] per question). Answers whatever question is CURRENTLY shown, in
    order (forward-only), letting each answer advance the pane. Raises
    CodexAskError with the dialog LEFT OPEN on any unverified step; returns
    {"submitted": True}."""
    screen, ok = _poll(driver, win, dialog_open, STEP_TIMEOUT_S, sleep)
    if not ok:
        raise CodexAskError("open", "no question dialog on screen")
    if len(answers) != len(questions):
        raise CodexAskError("answers", "expected %d answers, got %d"
                            % (len(questions), len(answers)))
    last = -1
    for _ in range(len(questions) + 1):     # bounded; each pass advances one q
        screen = driver.get_text(win) or ""
        if confirm_open(screen) or not dialog_open(screen):
            break                            # submitted out
        cur = current_question(screen)
        if cur is None:
            raise CodexAskError("question", "no current question on screen")
        n = cur[0]
        i = n - 1
        if i <= last:                        # answered but the pane didn't move
            raise CodexAskError("advance",
                                "dialog did not advance past question %d" % n)
        if not (0 <= i < len(answers)):
            raise CodexAskError("answers", "no answer for question %d" % n)
        _answer_one(driver, win, questions[i], answers[i], sleep)
        # confirm the answer advanced the pane (or closed the dialog) before
        # looking for the next question
        def answer_landed(s: str, n: int = n) -> bool:
            return ((current_question(s) or (0,))[0] != n
                    or not dialog_open(s) or confirm_open(s))
        _, ok = _poll(driver, win, answer_landed, STEP_TIMEOUT_S, sleep)
        if not ok:
            raise CodexAskError("advance",
                                "dialog did not advance past question %d" % n)
        last = i
    # every question was answered, so this is normally a no-op — but a codex that
    # counts an answer differently must not leave the confirmation hanging with
    # the user's whole submission stuck behind it.
    _confirm(driver, win, sleep)
    return DialogOutcome.SUBMITTED


def decline(
    driver: Driver,
    win: WindowId,
    questions: list[Prompt],
    message: str = "",
    sleep: Callable[[float], None] = time.sleep,
) -> DialogOutcome:
    """The card's "chat about this" on codex: submit the dialog with as little
    answered as codex permits, so the turn resumes and the composer is yours.

    codex has no decline ROW (its Esc aborts the whole turn, the opposite), but it
    does have the next best thing: a submit that leaves questions unanswered
    raises `Submit with unanswered questions?`, and `Proceed` sends those as
    `answers: []`. What it does NOT have is a zero-answer submit — the submitting
    key takes the cursor, so the question you submit FROM is always answered
    (measured: ENTER on a fresh single-question dialog recorded option 1). So the
    honest maximum is: navigate to the LAST question, answer only that one, and
    with codex's own least-committal row — `None of the above`. Every earlier
    question goes through unanswered.

    Codex requires a note to submit its forced `None of the above` answer.
    `message` supplies that note. The controller sends the actual discussion
    as a new prompt after this function confirms that the dialog closed.

    Raises CodexAskError with the dialog LEFT OPEN on any unverified step; returns
    {"submitted": True, "unanswered": <count>}."""
    screen, ok = _poll(driver, win, dialog_open, STEP_TIMEOUT_S, sleep)
    if not ok:
        raise CodexAskError("open", "no question dialog on screen")
    cur = current_question(screen)
    if cur is None:
        raise CodexAskError("question", "no current question on screen")
    n, m = cur
    # RIGHT walks questions WITHOUT answering them — the whole point here.
    for _ in range(m):
        if n >= m:
            break
        driver.send_key(win, "right")
        def moved_on(s: str, n: int = n) -> bool:
            return (current_question(s) or (n,))[0] != n
        screen, ok = _poll(driver, win, moved_on, STEP_TIMEOUT_S, sleep)
        if not ok:
            raise CodexAskError("navigate",
                                "dialog did not move past question %d" % n)
        n = (current_question(screen) or (n,))[0]
    if n != m:
        raise CodexAskError("navigate",
                            "never reached question %d of %d" % (m, m))
    q = questions[m - 1] if 0 <= m - 1 < len(questions) else Prompt()
    num = none_row(driver.get_text(win) or "", q)
    if not num:
        raise CodexAskError("noneof", "no %r row to decline with" % NONE_LABEL)
    _cursor_to(driver, win, num, sleep)
    note = (message or "Continue in chat.").strip()
    _note(driver, win, note, sleep)
    _, closed = _poll(driver, win, lambda screen: not dialog_open(screen), STEP_TIMEOUT_S, sleep)
    if not closed:
        raise CodexAskError("submit", "question dialog stayed open after decline")
    return DialogOutcome.SUBMITTED
