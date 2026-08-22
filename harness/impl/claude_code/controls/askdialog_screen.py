# harness/impl/claude_code/controls/askdialog_screen.py — READ Claude Code's
# AskUserQuestion dialog off a terminal screen capture. This half is pure parsing:
# where the dialog is, what its rows say, which question is showing. It presses no
# key and reaches for no terminal — askdialog.py drives, and calls in here to see.
#
# Empirical dialog facts this encodes. The KEY MODEL was re-measured live for
# v2.1.215 (2026-07-19) — Claude Code overhauled the dialog and the old
# digit-driven model (v2.1.214) broke every web answer with "question N never
# became current". What that overhaul changed, measured before and after:
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
#   - THE FLOW IS FORWARD-ONLY: `left`/`right`/Tab do NOT switch questions in
#     this build — they are inert (or, on a focused "Type something"/custom
#     row, caret movement), verified live 2026-07-22 (session 3fd325d9: a
#     `left`/`right`/`Tab` from every row left the same question showing).
#     up/down still move the row cursor, EXCEPT a filled custom-text row traps
#     upward movement (edit focus);
#   - the review pane ("Review your answers" · `1. Submit answers/2. Cancel`)
#     appears after the last question unless the ask was a single
#     single-select question.
import re
from dataclasses import dataclass

from harness.impl.claude_code.canonical import records

FOOT = "Enter to select"                 # question-pane open detector
REVIEW = "Review your answers"           # review-pane detector
CHAT_LABEL = "Chat about this"
SUBMIT_LABEL = "Submit answers"          # the review pane's submit row
# NB the free-text row has no label constant on purpose: it is located by its
# DIGIT (len(options)+1, `type_digit`), because the row's text becomes the user's
# own typed answer the moment anything is entered — matching "Type something"
# would stop working exactly when it mattered. A dead TYPE_LABEL constant sat
# here from the label-matching era; don't re-add one.

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


@dataclass(frozen=True)
class Row:
    """One cursor-navigable dialog row as parsed off the screen (see rows())."""

    digit: str
    label: str
    cursor: bool
    check: bool | None


def region(screen: str) -> str:
    """The dialog region: from the LAST header-chip bar (the only ☐/☒ on a
    terminal screen) to the end. "" when no dialog is on screen.

    FALLBACK: on a NARROW/SHORT window a tall dialog (several options with
    wrapped multi-line descriptions) overflows the visible viewport and the
    ☐/☒ chip bar scrolls off the TOP while the footer survives at the bottom
    — get_text only returns the visible screen, so the chip bar is simply
    absent. A chip-bar-only anchor then returns "" and the driver false-bails
    step:open on a genuinely-open dialog (session 819627e5, 2026-07-23). So
    when there is no chip bar but a dialog FOOTER is on screen (FOOT/REVIEW),
    anchor from the screen top instead. The row/question parsers over this
    wider region tolerate the extra transcript lines above (the numbered-option
    and action-row patterns rarely match prose), and the chip-bar path stays
    primary — it cleanly excludes the transcript whenever the bar IS visible."""
    if not screen:
        return ""
    lines = screen.splitlines()
    at = None
    for i, ln in enumerate(lines):
        if "☐" in ln or "☒" in ln:
            at = i
    if at is not None:
        return "\n".join(lines[at:])
    if FOOT in screen or REVIEW in screen:
        return screen
    return ""


def dialog_open(screen: str) -> bool:
    return FOOT in region(screen)


def review_open(screen: str) -> bool:
    return REVIEW in region(screen)


def rows(screen: str) -> list[Row]:
    """Every CURSOR-NAVIGABLE row of the question pane, in screen order:
    [{digit, label, cursor, check(None|bool)}]. Numbered option/Type/Chat rows
    (their preview side-box, if any, is stripped from the label) plus the
    UNNUMBERED action rows — the multiSelect "Next"/"Submit" advance row and the
    side-by-side layout's un-numbered "Chat about this" — carry digit "".
    Indented description lines and the "Notes: press n" hint don't match, so
    they drop out (they are not cursor stops)."""
    out: list[Row] = []
    for ln in region(screen).splitlines():
        m = _ROW.match(ln)
        if m:
            out.append(
                Row(
                    m.group("digit"),
                    m.group("label").strip(),
                    bool(m.group("cur")),
                    None if m.group("check") is None else m.group("check") != " ",
                )
            )
            continue
        m = _ACTION_ROW.match(ln)
        if m:
            out.append(Row("", m.group("label"), bool(m.group("cur")), None))
    return out


def current_question(
    screen: str,
    questions: list[records.Question],
) -> int | None:
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
    best: int | None = None
    best_len = -1
    for i, q in enumerate(questions):
        text = "".join((q.question or "").split())
        if text and text in flat and len(text) > best_len:
            best, best_len = i, len(text)
    return best


def cursor_row(screen: str) -> Row | None:
    return next((row for row in rows(screen) if row.cursor), None)
