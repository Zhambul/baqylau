# dashboard/askdialog.py — drive Claude Code's AskUserQuestion dialog from the
# web (docs/dashboard.md, *Web ask*). Sibling of rewindmenu.py and the same
# philosophy: the dialog exists only as live TUI pixels (the pending questions
# are known from the PreToolUse stash — plugins/claude_code/ask_fmt.py — but
# there is no API to answer them), so the one way to submit an answer is the
# dialog itself, and every step is verified by READING THE SCREEN back.
# Deliberately NOT unified with rewindmenu: the two dialogs share nothing but
# the philosophy — different anatomy, different keys, and opposite bail
# semantics (rewind bails by pressing Escape; here Escape DECLINES the whole
# question set, so a failed step must leave the dialog exactly as it is).
#
# Empirical dialog facts this encodes (all measured live, 2026-07-18,
# v2.1.214 — the full experiment log is in docs/dashboard.md):
#   - anatomy: a header-chip bar (`←  ☐ Pets  ☒ Drink  ✔ Submit  →`; a lone
#     ` ☐ Fruit ` for one question), the current question's text, numbered
#     option rows (`❯ 1. Apple` + indented description; multiSelect adds
#     `[ ]`/`[✔]` checkboxes), a "Type something" free-text row, multiSelect
#     also an unnumbered "Submit" row, then "N. Chat about this" below a
#     rule, and the footer "Enter to select · ↑/↓ to navigate · Esc to
#     cancel";
#   - single-select: a DIGIT answers and auto-advances (the sole question of
#     a one-question ask submits the tool outright — no review pane);
#   - multiSelect: a digit TOGGLES its checkbox (cursor stays put); `right`
#     from a non-edit row moves to the next tab (next question, or Submit);
#   - free text: arrow ONTO the "Type something" row (never its digit in
#     multiSelect — it toggles), type — the text replaces the label inline —
#     then Enter: single-select submits/advances, multiSelect toggles the
#     custom row checked. On the EDIT row arrow left/right move the text
#     cursor, so leave it with up/down before any tab navigation;
#   - the review pane ("Review your answers" · `1. Submit answers/2. Cancel`)
#     appears after the last question unless the ask was a single
#     single-select question; digit `1` submits;
#   - `left` at the first question is a NO-OP — `left`×len(questions) is a
#     deterministic normalize-to-start whatever tab the dialog sits on;
#   - Esc ANYWHERE (and Enter on an EMPTY "Type something") declines the
#     whole question set — which is why this driver never presses Escape.
import re
import time

POLL_S = 0.15          # screen re-read beat while waiting for a dialog state
STEP_TIMEOUT_S = 2.5   # a key press → its screen effect visible
KEY_GAP_S = 0.12       # beat between successive blind key presses
SUBMIT_TIMEOUT_S = 4.0  # final submit → dialog gone (the tool round-trips)

FOOT = "Enter to select"                 # question-pane open detector
REVIEW = "Review your answers"           # review-pane detector
CHAT_LABEL = "Chat about this"
TYPE_LABEL = "Type something"

# option row: cursor mark? · digit. · multiSelect checkbox? · label
_ROW = re.compile(r"^\s*(?P<cur>❯\s+)?(?P<digit>\d+)\.\s+"
                  r"(?:\[(?P<check>[ ✔x])\]\s*)?(?P<label>.+?)\s*$")
_SUBMIT_ROW = re.compile(r"^\s*(?P<cur>❯\s+)?Submit\s*$")


class AskError(Exception):
    """A step's expected screen state never appeared. .step names it for the
    audit row. The dialog is left EXACTLY as it was — never Escape-closed,
    because Escape declines the whole question set (the opposite of
    rewindmenu's bail); a re-answer from the web normalizes and retries."""

    def __init__(self, step, detail=""):
        super().__init__(step + ((": " + detail) if detail else ""))
        self.step = step


def region(screen):
    """The dialog region: from the LAST header-chip bar (the only ☐/☒ on a
    kitty screen) to the end. "" when no dialog is on screen."""
    if not screen:
        return ""
    lines = screen.splitlines()
    at = None
    for i, ln in enumerate(lines):
        if "☐" in ln or "☒" in ln:
            at = i
    return "\n".join(lines[at:]) if at is not None else ""


def dialog_open(screen):
    return FOOT in region(screen)


def review_open(screen):
    return REVIEW in region(screen)


def rows(screen):
    """The numbered rows of the question pane, in screen order:
    [{digit, label, cursor, check(None|bool)}] + the unnumbered multiSelect
    Submit row as {digit: "", label: "Submit", …}. Indented description lines
    don't match _ROW (no `N.`), so they drop out."""
    out = []
    for ln in region(screen).splitlines():
        m = _ROW.match(ln)
        if m:
            out.append({"digit": m.group("digit"),
                        "label": m.group("label").strip(),
                        "cursor": bool(m.group("cur")),
                        "check": (None if m.group("check") is None
                                  else m.group("check") != " ")})
            continue
        m = _SUBMIT_ROW.match(ln)
        if m:
            out.append({"digit": "", "label": "Submit",
                        "cursor": bool(m.group("cur")), "check": None})
    return out


def current_question(screen, questions):
    """Which of the ask's questions the dialog currently shows (its text is a
    line of the region), or None (e.g. the review pane)."""
    lines = {ln.strip() for ln in region(screen).splitlines()}
    for i, q in enumerate(questions):
        if (q.get("question") or "").strip() in lines:
            return i
    return None


def _wait(fe, win, pred, timeout, sleep):
    deadline = time.monotonic() + timeout
    screen = fe.get_text(win) or ""
    while not pred(screen):
        if time.monotonic() >= deadline:
            return screen, False
        sleep(POLL_S)
        screen = fe.get_text(win) or ""
    return screen, True


def _cursor_to(fe, win, digit, sleep):
    """Move the ❯ cursor to the row NUMBERED `digit` with up/down presses,
    screen-verified each step. By number, not label: the Type row is always
    len(options)+1 but its label mutates to whatever was last typed into it,
    so a retry after a half-driven attempt still finds it."""
    for _ in range(24):                     # options ≤ ~6 rows; generous
        screen = fe.get_text(win) or ""
        rs = rows(screen)
        at = next((i for i, r in enumerate(rs) if r["cursor"]), None)
        to = next((i for i, r in enumerate(rs) if r["digit"] == digit), None)
        if at is None or to is None:
            raise AskError("cursor", "row %s not on screen" % digit)
        if at == to:
            return
        fe.send_key(win, "down" if to > at else "up")
        sleep(POLL_S)
    raise AskError("cursor", "cursor never reached row %s" % digit)


def _answer_question(fe, win, q, ans, sleep):
    """Apply one question's answer to the CURRENT pane. Leaves the dialog on
    the next tab (single-select auto-advances; multiSelect is advanced with
    `right` from a non-edit row)."""
    screen = fe.get_text(win) or ""
    rs = rows(screen)
    labels = [o.get("label") or "" for o in (q.get("options") or [])]
    selected = [s for s in (ans.get("selected") or []) if s in labels]
    other = (ans.get("other") or "").strip()
    if q.get("multiSelect"):
        # digits TOGGLE — diff each option's desired state against the
        # checkbox the screen actually shows (the user may have pre-toggled
        # some in the terminal)
        for i, label in enumerate(labels):
            row = next((r for r in rs if r["digit"] == str(i + 1)), None)
            if row is None:
                raise AskError("options", "row %d not on screen" % (i + 1))
            if bool(row["check"]) != (label in selected):
                fe.send_key(win, str(i + 1))
                sleep(KEY_GAP_S)
        if other:
            _cursor_to(fe, win, str(len(labels) + 1), sleep)
            if not fe.send_text(win, other):     # types inline + CR commits
                raise AskError("type", "other text not delivered")
            sleep(POLL_S)
            fe.send_key(win, "enter")            # check the custom row
            sleep(POLL_S)
            screen, ok = _wait(
                fe, win,
                lambda s: any(r["check"] for r in rows(s)
                              if r["label"].startswith(other[:24])),
                STEP_TIMEOUT_S, sleep)
            if not ok:
                raise AskError("type", "custom option never checked")
            fe.send_key(win, "up")               # leave the edit row (left/
            sleep(POLL_S)                        # right would move the text
        fe.send_key(win, "right")                # cursor there) → next tab
        return
    if other:
        _cursor_to(fe, win, str(len(labels) + 1), sleep)
        if not fe.send_text(win, other):         # CR selects + advances
            raise AskError("type", "other text not delivered")
        return
    if not selected:
        raise AskError("options", "no answer for %r"
                       % (q.get("question") or "")[:60])
    fe.send_key(win, str(1 + labels.index(selected[0])))


def drive(fe, win, questions, answers, chat=False, sleep=time.sleep):
    """Answer the OPEN AskUserQuestion dialog in window `win`. `questions` is
    the ask-pending stash (the PreToolUse tool_input.questions, verbatim);
    `answers` aligns with it: [{"selected": [labels…], "other": "text"}] per
    question. `chat: True` presses the dialog's own "Chat about this" instead
    (declines + tells Claude to discuss — the page then focuses its
    composer). Raises AskError with the dialog LEFT OPEN on any unverified
    step; returns {"submitted": True} / {"chat": True}."""
    screen = fe.get_text(win) or ""
    if not dialog_open(screen) and not review_open(screen):
        raise AskError("open", "no question dialog on screen")
    if chat:
        rs = rows(screen)
        row = next((r for r in rs if r["label"] == CHAT_LABEL), None)
        if row is None:
            raise AskError("chat", "no 'Chat about this' row on screen")
        fe.send_key(win, row["digit"])
        _, ok = _wait(fe, win,
                      lambda s: not dialog_open(s) and not review_open(s),
                      STEP_TIMEOUT_S, sleep)
        if not ok:
            raise AskError("chat", "dialog still open")
        return {"chat": True}
    if len(answers) != len(questions):
        raise AskError("answers", "expected %d answers, got %d"
                       % (len(questions), len(answers)))
    # normalize to the first question — `left` is a no-op there (measured),
    # so len(questions) presses land on tab 1 from anywhere (incl. review)
    for _ in range(len(questions)):
        fe.send_key(win, "left")
        sleep(KEY_GAP_S)
    for i, (q, ans) in enumerate(zip(questions, answers)):
        screen, ok = _wait(fe, win,
                           lambda s, i=i: current_question(s, questions) == i,
                           STEP_TIMEOUT_S, sleep)
        if not ok:
            raise AskError("question", "question %d never became current: %r"
                           % (i + 1, (q.get("question") or "")[:60]))
        _answer_question(fe, win, q, ans, sleep)
    # a single single-select question submits outright; everything else lands
    # on the review pane, where digit 1 = "Submit answers"
    screen, ok = _wait(fe, win,
                       lambda s: review_open(s)
                       or (not dialog_open(s) and not review_open(s)),
                       STEP_TIMEOUT_S, sleep)
    if not ok:
        raise AskError("review", "neither review pane nor submit happened")
    if review_open(screen):
        fe.send_key(win, "1")
        _, ok = _wait(fe, win,
                      lambda s: not dialog_open(s) and not review_open(s),
                      SUBMIT_TIMEOUT_S, sleep)
        if not ok:
            raise AskError("submit", "dialog still open after Submit answers")
    return {"submitted": True}
