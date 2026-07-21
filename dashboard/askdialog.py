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
# Empirical dialog facts this encodes. The KEY MODEL was re-measured live for
# v2.1.215 (2026-07-19) — Claude Code overhauled the dialog and the old
# digit-driven model (v2.1.214) broke every web answer with "question N never
# became current"; the full before/after experiment log is in docs/dashboard.md:
#   - anatomy: a header-chip bar (`←  ☐ Pets  ☒ Drink  ✔ Submit  →`, one chip
#     per question keyed off the `header` field; ☒ once answered), the current
#     question's text, numbered option rows (`❯ 1. Apple`; multiSelect adds a
#     `[ ]`/`[✔]` checkbox), a numbered "Type something" free-text row,
#     multiSelect adds an UNNUMBERED advance row labelled "Next" (or "Submit"
#     on the last/only question), then a "Chat about this" row below a rule,
#     and a footer containing "Enter to select";
#   - TWO layouts: with no option `preview`, options carry an indented
#     description line and "Chat about this" is NUMBERED; when ANY option has a
#     `preview`, the dialog switches to a side-by-side layout — a box drawn to
#     the RIGHT of the option rows (its text bleeds onto the option lines, so
#     `rows()` strips it), a "Notes: press n" hint row, and an UNNUMBERED
#     "Chat about this". Both keep "Enter to select"; the driver is
#     layout-agnostic because it navigates by cursor + Enter, never by digit;
#   - SELECTION IS CURSOR + ENTER, NOT digits (digits are inert now): move the
#     ❯ cursor with up/down onto a row and press Enter. single-select Enter
#     SELECTS and auto-advances (the sole question of a one-question ask
#     submits outright — no review pane); multiSelect Enter TOGGLES the
#     cursored checkbox (cursor stays), and `right` then moves to the next tab;
#   - free text: cursor ONTO the "Type something" row, then send_text (types
#     the text + a CR): single-select commits it and auto-advances; multiSelect
#     commits + checks the custom row (verified, with a fallback Enter);
#   - the review pane ("Review your answers" · `1. Submit answers/2. Cancel`)
#     appears after the last question unless the ask was a single
#     single-select question; cursor onto "Submit answers" + Enter submits;
#   - `left`/`right`/Tab switch questions; `left` at the first is a NO-OP, so
#     `left`×len(questions) is a deterministic normalize-to-start from any tab;
#   - Esc ANYWHERE (and Enter on an EMPTY "Type something") declines the whole
#     question set — which is why this driver never presses Escape.
import re
import time

POLL_S = 0.15          # screen re-read beat while waiting for a dialog state
STEP_TIMEOUT_S = 2.5   # a key press → its screen effect visible
KEY_GAP_S = 0.12       # beat between successive blind key presses
SUBMIT_TIMEOUT_S = 4.0  # final submit → dialog gone (the tool round-trips)
NAV_STEPS = 24         # max up/down presses to walk the cursor to a target row

FOOT = "Enter to select"                 # question-pane open detector
REVIEW = "Review your answers"           # review-pane detector
CHAT_LABEL = "Chat about this"
TYPE_LABEL = "Type something"
SUBMIT_LABEL = "Submit answers"          # the review pane's submit row

# option row: cursor mark? · digit. · multiSelect checkbox? · label. The label
# capture stops before a preview side-box (2+ spaces then a box-drawing char,
# U+2500–U+257F) that the side-by-side layout bleeds onto the option line.
_ROW = re.compile(r"^\s*(?P<cur>❯\s+)?(?P<digit>\d+)\.\s+"
                  r"(?:\[(?P<check>[ ✔x])\]\s*)?"
                  r"(?P<label>.+?)"
                  r"(?:\s{2,}[─-╿].*)?\s*$")
# an UNNUMBERED action row: the multiSelect "Next"/"Submit" advance row and the
# side-by-side layout's un-numbered "Chat about this"
_ACTION_ROW = re.compile(r"^\s*(?P<cur>❯\s+)?"
                         r"(?P<label>Next|Submit|Chat about this)\s*$")


class AskError(Exception):
    """A step's expected screen state never appeared. .step names it for the
    audit row. The dialog is left EXACTLY as it was — never Escape-closed,
    because Escape declines the whole question set (the opposite of
    rewindmenu's bail); a re-answer from the web normalizes and retries.

    `.screen` carries the last screen capture the failing step saw (truncated
    by the caller into the audit `errors` context): the bail records only its
    OUTCOME, so without the pixels a step:open ("no dialog") can't be told
    apart after the fact — dialog-too-tall vs a footer-string drift after a
    Claude Code upgrade vs a blank/partial capture all look identical."""

    def __init__(self, step, detail="", screen=None):
        super().__init__(step + ((": " + detail) if detail else ""))
        self.step = step
        self.screen = screen


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
    """Every CURSOR-NAVIGABLE row of the question pane, in screen order:
    [{digit, label, cursor, check(None|bool)}]. Numbered option/Type/Chat rows
    (their preview side-box, if any, is stripped from the label) plus the
    UNNUMBERED action rows — the multiSelect "Next"/"Submit" advance row and the
    side-by-side layout's un-numbered "Chat about this" — carry digit "".
    Indented description lines and the "Notes: press n" hint don't match, so
    they drop out (they are not cursor stops)."""
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
        m = _ACTION_ROW.match(ln)
        if m:
            out.append({"digit": "", "label": m.group("label"),
                        "cursor": bool(m.group("cur")), "check": None})
    return out


def current_question(screen, questions):
    """Which of the ask's questions the dialog currently shows, or None.
    Long question text WRAPS across screen lines (a 555-char question never
    matched the old exact line-set lookup — the live `question 1 never
    became current` bail, 2026-07-18), and a wrap can land mid-word (e.g.
    at a hyphen in a path), so ALL whitespace is stripped from both sides
    before the substring match. The review pane must answer None
    explicitly: its answer recap repeats every question's text."""
    reg = region(screen)
    if REVIEW in reg:
        return None
    flat = "".join(reg.split())
    # LONGEST match wins, not the first: only ONE question is on screen at a
    # time, but if question i's stripped text is a substring of question j's
    # (e.g. "Pickacolor" ⊂ "Pickacolorscheme"), then while j is showing, flat
    # contains j's text — which contains i's — and a first-match scan would
    # wrongly return i, so `drive`'s wait for j never resolves. The most
    # specific (longest) matching question is the one actually displayed.
    best, best_len = None, -1
    for i, q in enumerate(questions):
        text = "".join((q.get("question") or "").split())
        if text and text in flat and len(text) > best_len:
            best, best_len = i, len(text)
    return best


def _wait(fe, win, pred, timeout, sleep):
    deadline = time.monotonic() + timeout
    screen = fe.get_text(win) or ""
    while not pred(screen):
        if time.monotonic() >= deadline:
            return screen, False
        sleep(POLL_S)
        screen = fe.get_text(win) or ""
    return screen, True


def _cursor_row(screen):
    return next((r for r in rows(screen) if r["cursor"]), None)


def _cursor_to(fe, win, pred, sleep, what):
    """Move the ❯ cursor onto the row matching `pred(row_dict)`. Normalizes to
    the top (up is a no-op there) then walks DOWN, screen-verified each step.
    Deliberately walk-based, NOT index arithmetic: the v2.1.215 dialog has rows
    the parser skips (indented descriptions, the "Notes: press n" hint, preview
    box lines), and if the cursor ever parks on one, `rows()` reports no cursor
    row — the walk just steps past it, where index math would desync.

    A row is "reached" when ANY cursored row matches `pred`, not just the first
    (`_cursor_row`). The preview layout bleeds the last option's ❯ onto the
    "Chat about this" row below it: with the cursor genuinely ON Chat, BOTH the
    last option AND Chat render ❯ (verified live 2026-07-20 — down from the last
    option lands on Chat, showing two ❯). `_cursor_row` returned the FIRST mark
    (the option), so the walk never recognized it reached Chat and dead-looped
    (`cursor never reached Chat row`). Checking every cursored row fixes it
    WITHOUT breaking option targeting: the down-from-top walk stops at option N
    (clean, single ❯) before it ever descends into the ambiguous two-❯ state."""
    for _ in range(NAV_STEPS):               # normalize to the first row
        fe.send_key(win, "up")
        sleep(POLL_S)
        if (_cursor_row(fe.get_text(win) or "") or {}).get("digit") == "1":
            break
    for _ in range(NAV_STEPS):
        screen = fe.get_text(win) or ""
        if any(r["cursor"] and pred(r) for r in rows(screen)):
            return screen
        fe.send_key(win, "down")
        sleep(POLL_S)
    raise AskError("cursor", "cursor never reached %s" % what)


def _by_digit(d):
    return lambda r: r["digit"] == d


def _require_type_row(fe, win, type_digit):
    """Guard the typed-answer ('other') path: the free-text 'Type something'
    row is digit len(options)+1 in the PLAIN dialog, but the PREVIEW
    side-by-side layout omits it entirely (measured — a single-select
    question whose options carry `preview` renders options + 'Chat about
    this' and no numbered Type row). A typed answer is then undeliverable, so
    fail FAST with a clear reason instead of walking the cursor NAV_STEPS
    times looking for a row that never appears (the observed 'cursor never
    reached Type row' dead-walk, 2026-07-19). The web ask card routes typed
    answers on preview questions through 'Chat about this' instead, so the
    driver should not normally reach here (docs/dashboard.md, *Web ask*)."""
    if not any(r["digit"] == type_digit for r in rows(fe.get_text(win) or "")):
        raise AskError("type", "no typed-answer row (preview-layout dialog) — "
                       "answer via 'Chat about this'")


def _answer_question(fe, win, q, ans, sleep):
    """Apply one question's answer to the CURRENT pane. Leaves the dialog on
    the next tab: single-select Enter auto-advances; multiSelect toggles each
    box (Enter) then `right` advances. Digits are inert in v2.1.215 — every
    selection is cursor-to-the-row + Enter."""
    labels = [o.get("label") or "" for o in (q.get("options") or [])]
    selected = [s for s in (ans.get("selected") or []) if s in labels]
    other = (ans.get("other") or "").strip()
    type_digit = str(len(labels) + 1)
    if q.get("multiSelect"):
        # Enter TOGGLES the cursored box — diff each option's desired state
        # against the checkbox the screen actually shows (the user may have
        # pre-toggled some in the terminal), and only flip the ones that differ
        for i, label in enumerate(labels):
            row = next((r for r in rows(fe.get_text(win) or "")
                        if r["digit"] == str(i + 1)), None)
            if row is None:
                raise AskError("options", "row %d not on screen" % (i + 1))
            if bool(row["check"]) != (label in selected):
                _cursor_to(fe, win, _by_digit(str(i + 1)), sleep,
                           "option %d" % (i + 1))
                fe.send_key(win, "enter")        # toggle
                sleep(KEY_GAP_S)
        if other:
            _require_type_row(fe, win, type_digit)
            _cursor_to(fe, win, _by_digit(type_digit), sleep, "Type row")
            if not fe.send_text(win, other):     # types inline + CR
                raise AskError("type", "other text not delivered")
            sleep(POLL_S)
            # the CR may or may not have checked the custom row; ensure it is
            checked = any(r["check"] for r in rows(fe.get_text(win) or "")
                          if r["label"].startswith(other[:24]))
            if not checked:
                fe.send_key(win, "enter")
            _, ok = _wait(
                fe, win,
                lambda s: any(r["check"] for r in rows(s)
                              if r["label"].startswith(other[:24])),
                STEP_TIMEOUT_S, sleep)
            if not ok:
                raise AskError("type", "custom option never checked")
        fe.send_key(win, "right")                # non-edit → next tab
        return
    if other:
        _require_type_row(fe, win, type_digit)
        _cursor_to(fe, win, _by_digit(type_digit), sleep, "Type row")
        if not fe.send_text(win, other):         # type + CR selects + advances
            raise AskError("type", "other text not delivered")
        return
    if not selected:
        raise AskError("options", "no answer for %r"
                       % (q.get("question") or "")[:60])
    tgt = str(1 + labels.index(selected[0]))
    _cursor_to(fe, win, _by_digit(tgt), sleep, "option " + tgt)
    fe.send_key(win, "enter")                    # select + auto-advance


def drive(fe, win, questions, answers, chat=False, sleep=time.sleep):
    """Answer the OPEN AskUserQuestion dialog in window `win`. `questions` is
    the ask-pending stash (the PreToolUse tool_input.questions, verbatim);
    `answers` aligns with it: [{"selected": [labels…], "other": "text"}] per
    question. `chat: True` presses the dialog's own "Chat about this" instead
    (declines + tells Claude to discuss — the page then focuses its
    composer). Raises AskError with the dialog LEFT OPEN on any unverified
    step; returns {"submitted": True} / {"chat": True}."""
    # Poll for the dialog like every other step does (via _wait) — this was
    # the ONE step that read the screen ONCE with no retry, so a capture taken
    # while the dialog was still rendering (e.g. right after a --resume) or a
    # transient blank/partial get_text bailed immediately with step:open even
    # though the dialog was genuinely up (session 0247ebb2, 2026-07-21: a still
    # -open, never-answered ask failed here on a freshly-resumed window).
    screen, ok = _wait(fe, win,
                       lambda s: dialog_open(s) or review_open(s),
                       STEP_TIMEOUT_S, sleep)
    if not ok:
        raise AskError("open", "no question dialog on screen", screen=screen)
    if chat:
        if not any(r["label"] == CHAT_LABEL for r in rows(screen)):
            raise AskError("chat", "no 'Chat about this' row on screen")
        _cursor_to(fe, win, lambda r: r["label"] == CHAT_LABEL, sleep,
                   "Chat row")
        fe.send_key(win, "enter")
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
                           % (i + 1, (q.get("question") or "")[:60]),
                           screen=screen)
        _answer_question(fe, win, q, ans, sleep)
    # a single single-select question submits outright; everything else lands
    # on the review pane, where "Submit answers" is the cursored top row. The
    # review pane appears locally-fast, but the outright submit is the dialog
    # going away because the tool ROUND-TRIPS — the same event the review-pane
    # Submit below budgets SUBMIT_TIMEOUT_S for, so this dual-purpose wait needs
    # that longer budget too (a single single-select whose round-trip took
    # 2.5-4.0 s spuriously raised "neither review pane nor submit happened").
    screen, ok = _wait(fe, win,
                       lambda s: review_open(s)
                       or (not dialog_open(s) and not review_open(s)),
                       SUBMIT_TIMEOUT_S, sleep)
    if not ok:
        raise AskError("review", "neither review pane nor submit happened",
                       screen=screen)
    if review_open(screen):
        _cursor_to(fe, win, lambda r: r["label"] == SUBMIT_LABEL, sleep,
                   "Submit answers")
        fe.send_key(win, "enter")
        _, ok = _wait(fe, win,
                      lambda s: not dialog_open(s) and not review_open(s),
                      SUBMIT_TIMEOUT_S, sleep)
        if not ok:
            raise AskError("submit", "dialog still open after Submit answers")
    return {"submitted": True}
