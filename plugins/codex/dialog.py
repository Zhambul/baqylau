# plugins/codex/dialog.py — drive codex's request_user_input dialog from the web.
#
# The codex twin of plugins/claude_code/askdialog.py, and the same philosophy: the dialog
# exists only as live TUI pixels (the pending questions are known from the
# rollout — plugins/codex/read.pending_dialog — but there is no API to answer
# them), so the one way to submit an answer is the dialog itself, every step
# verified by READING THE SCREEN back. Deliberately NOT a reuse of Claude's
# askdialog: codex's dialog is a DIFFERENT anatomy (a `Question N/M` header, a `›`
# cursor, an `enter to submit answer` footer), so Claude's region() returns "" on
# a codex screen and its key model does not apply.
#
# It lives in the PLUGIN because codex's `ask` HostControl gesture
# (plugins/codex/hostctl.py) drives it and the layering rule forbids a plugin
# importing the dashboard — so the whole gesture, screen driver included, sits
# behind HostControl and the dashboard only calls host.ask. Claude Code's
# drivers followed the same argument into ITS plugin in P2; the shared
# skeleton they used to reach in dashboard/ moved down to core/screendrive.py.
#
# request_user_input is PLAN-MODE-ONLY and model-nondeterministic (the model
# sometimes answers in prose instead of raising the tool), so this is reached
# rarely and is best-effort by design: a step that never verifies raises
# CodexAskError with the dialog LEFT OPEN (never Escape-closed — codex's Esc
# ABORTS the turn, the opposite of a decline), for a retry from the card.
#
# Empirical dialog geometry (docs/codex.md):
#   - a header line `Question N/M (K unanswered)` (N/M 1-based);
#   - numbered option rows `  N. <label>  <description>`, a `›` cursor on the
#     current option (codex renders `›`; `❯` is tolerated for version drift);
#   - a footer `tab to add notes | enter to submit answer | esc to interrupt`
#     (a MULTI-question dialog reads `enter to submit all` on the last question,
#     and adds `←/→ to navigate questions`);
#   - DOWN/UP walk the `›` cursor, ENTER submits the current question's answer and
#     advances to the next (forward-only, like Claude's build), `tab` opens a
#     free-text notes field.
import re
import time

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
_HEADER = re.compile(r"Question\s+(\d+)\s*/\s*(\d+)")
# option row: cursor mark? · number. · label (a 2+-space run starts the dim
# description, which is not part of the label).
_OPT = re.compile(r"^\s*(?P<cur>[›❯]\s+)?(?P<num>\d+)\.\s+"
                  r"(?P<label>.+?)(?:\s{2,}.*)?\s*$")


class CodexAskError(Exception):
    """A step's expected screen state never appeared. `.step` names it for the
    audit row; the dialog is left EXACTLY as it was (never Escape-closed — codex's
    Esc aborts the turn), so a re-answer from the card normalizes and retries."""

    def __init__(self, step, detail=""):
        super().__init__(step + ((": " + detail) if detail else ""))
        self.step = step
        self.detail = detail


def _poll(fe, win, pred, timeout, sleep):
    """Poll `win`'s screen until pred(screen) or `timeout`; (screen, held)."""
    deadline = time.monotonic() + timeout
    screen = fe.get_text(win) or ""
    while not pred(screen):
        if time.monotonic() >= deadline:
            return screen, False
        sleep(POLL_S)
        screen = fe.get_text(win) or ""
    return screen, True


def dialog_open(screen):
    """Is codex's question dialog on screen — its footer visible."""
    return FOOT in (screen or "")


def current_question(screen):
    """The (n, m) of the `Question N/M` header (1-based), or None."""
    m = _HEADER.search(screen or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def rows(screen):
    """The numbered option rows in screen order: [{num, label, cursor}]."""
    out = []
    for ln in (screen or "").splitlines():
        m = _OPT.match(ln)
        if m:
            out.append({"num": m.group("num"),
                        "label": m.group("label").strip(),
                        "cursor": bool(m.group("cur"))})
    return out


def _cursor_row(screen):
    return next((r for r in rows(screen) if r["cursor"]), None)


def _cursor_to(fe, win, num, sleep):
    """Move the `›` cursor onto option `num`: normalize UP to option 1 (up is a
    no-op there), then walk DOWN, screen-verified each step. Bail if `up` stops
    making progress (a trapped/edit row)."""
    prev = object()
    for _ in range(NAV_STEPS):
        cur = _cursor_row(fe.get_text(win) or "")
        if cur is not None and cur["num"] == "1":
            break
        key = None if cur is None else cur["num"]
        if key == prev:
            break
        prev = key
        fe.send_key(win, "up")
        sleep(POLL_S)
    for _ in range(NAV_STEPS):
        cur = _cursor_row(fe.get_text(win) or "")
        if cur is not None and cur["num"] == num:
            return
        fe.send_key(win, "down")
        sleep(POLL_S)
    raise CodexAskError("cursor", "cursor never reached option %s" % num)


def _answer_one(fe, win, question, ans, sleep):
    """Apply one question's answer to the CURRENT pane: cursor onto the chosen
    option + ENTER; or, for a free-text `other`, `tab` into the notes field and
    type it (best-effort — codex's notes flow is version-fragile)."""
    labels = [o.get("label") or "" for o in (question.get("options") or [])]
    selected = [s for s in (ans.get("selected") or []) if s in labels]
    other = (ans.get("other") or "").strip()
    if selected:
        num = str(1 + labels.index(selected[0]))
        _cursor_to(fe, win, num, sleep)
        fe.send_key(win, "enter")          # submit this question + advance
        return
    if other:
        fe.send_key(win, "tab")            # 'tab to add notes'
        sleep(POLL_S)
        if not fe.send_text(win, other):   # types the note + Enter
            raise CodexAskError("notes", "notes not delivered")
        return
    raise CodexAskError("options",
                        "no answer for %r" % (question.get("question") or "")[:60])


def drive(fe, win, questions, answers, sleep=time.sleep):
    """Answer codex's OPEN request_user_input dialog in window `win`. `questions`
    is the pending_dialog stash ([{id, header, question, options[{label,
    description}]}], verbatim); `answers` aligns with it ([{selected: [labels…],
    other: text}] per question). Answers whatever question is CURRENTLY shown, in
    order (forward-only), letting each answer advance the pane. Raises
    CodexAskError with the dialog LEFT OPEN on any unverified step; returns
    {"submitted": True}."""
    screen, ok = _poll(fe, win, dialog_open, STEP_TIMEOUT_S, sleep)
    if not ok:
        raise CodexAskError("open", "no question dialog on screen")
    if len(answers) != len(questions):
        raise CodexAskError("answers", "expected %d answers, got %d"
                            % (len(questions), len(answers)))
    last = -1
    for _ in range(len(questions) + 1):     # bounded; each pass advances one q
        screen = fe.get_text(win) or ""
        if not dialog_open(screen):
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
        _answer_one(fe, win, questions[i], answers[i], sleep)
        # confirm the answer advanced the pane (or closed the dialog) before
        # looking for the next question
        _, ok = _poll(fe, win,
                      lambda s, n=n: (current_question(s) or (0,))[0] != n
                      or not dialog_open(s),
                      STEP_TIMEOUT_S, sleep)
        if not ok:
            raise CodexAskError("advance",
                                "dialog did not advance past question %d" % n)
        last = i
    return {"submitted": True}
