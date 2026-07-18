# dashboard/rewindmenu.py — drive Claude Code's OWN rewind menu from the web.
#
# The dashboard's full rewind support (docs/dashboard.md, *Web rewind*)
# deliberately REUSES Claude Code's interactive checkpoint menu instead of
# re-implementing restore: conversation state lives only inside the live TUI
# process (a rewind writes NOTHING to the transcript until the next send forks
# it by parentUuid — measured 2026-07-18), so the one sanctioned way to restore
# it is the menu itself. This module types `/rewind`, walks the checkpoint
# list with key EVENTS, and picks the restore option — verifying every step by
# READING THE SCREEN back (Frontend.get_text), so a mis-count or a menu that
# never opened bails out with Escape instead of pressing keys blind.
#
# Empirical menu facts this encodes (all measured live, 2026-07-18, v2.1.214):
#   - typed `/rewind` opens the menu 100% (synthesized double-Esc was ~2/3);
#   - the checkpoint list is one entry per LIVE-BRANCH user prompt, oldest
#     first, cursor starting on the trailing "(current)" entry — so the k-th
#     prompt from the end is k `up` presses away;
#   - an entry shows the prompt's FIRST LINE, truncated to pane width with
#     a trailing " …" — hence the truncation-aware prefix match below;
#   - Enter opens a numbered confirm menu whose NUMBERING SHIFTS with content
#     (with code changes: 1. Restore code and conversation / 2. Restore
#     conversation / 3. Restore code / …; without: 1. Restore conversation /
#     …) — so the digit is resolved from the parsed LABELS, never hard-coded;
#   - a digit key selects immediately (no Enter);
#   - Escape closes either menu cleanly back to the composer.
#
# Menu-region parsing: get_text returns the whole visible screen, where
# scrollback prompt echoes also start with "❯" — but at column 0; menu lines
# are indented, so only "  ❯ "-prefixed lines inside the region after the last
# "Rewind" header are cursor lines.
import re
import time

# One entry per selectable restore option: the requested `mode` (the POST
# body's vocabulary) → the menu label it must match. Labels are matched
# case-insensitively on the parsed confirm menu, so a menu that lacks the
# option (e.g. "code" at a checkpoint with no code changes) is a clean bail,
# not a wrong digit.
MODE_LABELS = {
    "both": "restore code and conversation",
    "conversation": "restore conversation",
    "code": "restore code",
}

MENU_HEADER = "Rewind"                       # first-menu region anchor
MENU_FOOT = "Enter to continue"              # first-menu open detector
CONFIRM_HEADER = "Confirm you want to restore"   # second-menu open detector
CODE_UNCHANGED = "The code will be unchanged."   # confirm-menu line when the
#                  checkpoint has no code changes — the verifiable reason the
#                  code-restoring options are absent (vs "will be restored…")

POLL_S = 0.15          # screen re-read beat while waiting for a menu state
OPEN_TIMEOUT_S = 4.0   # /rewind typed → menu visible (slash command latency)
STEP_TIMEOUT_S = 2.0   # a key press → its screen effect visible
SCAN_MAX = 100         # hard step bound — Claude Code caps checkpoints at 100
KEY_GAP_S = 0.05       # beat between blind repeated `up` presses


def first_line(text):
    """The menu's view of a prompt: its first non-empty line, stripped."""
    for ln in (text or "").splitlines():
        if ln.strip():
            return ln.strip()
    return ""


def entry_matches(entry, target):
    """True when a checkpoint-menu entry names `target` (a full prompt text).
    The entry is the prompt's first line, possibly truncated with a trailing
    ellipsis — so compare the truncation as a PREFIX of the target's first
    line; an untruncated entry must match it exactly."""
    e = (entry or "").strip()
    t = first_line(target)
    if not e or not t:
        return False
    if e.endswith("…"):
        return t.startswith(e[:-1].rstrip())
    return e == t


def menu_region(screen):
    """The visible text from the LAST checkpoint-menu header down, or "" when
    no menu is on screen. Anchoring at the last header skips scrollback (old
    prompt echoes, even a previously captured menu) above the live one."""
    if not screen:
        return ""
    # the \n-prefix lets a header on the very first screen row still anchor
    i = ("\n" + screen).rfind("\n  %s\n" % MENU_HEADER)
    return screen[max(0, i - 1):] if i >= 0 else ""


def menu_open(screen):
    """True when the checkpoint list (first menu) is on screen."""
    region = menu_region(screen)
    return bool(region) and MENU_FOOT in region and CONFIRM_HEADER not in region


def confirm_open(screen):
    """True when the numbered confirm menu (second menu) is on screen."""
    return CONFIRM_HEADER in menu_region(screen)


def cursor_entry(screen):
    """The text of the ❯-cursor line inside the menu region ("" when absent).
    Menu cursor lines are INDENTED ("  ❯ …"); scrollback prompt echoes start
    at column 0, so they never match."""
    m = re.findall(r"^\s+❯\s+(.*)$", menu_region(screen), re.M)
    return m[-1].strip() if m else ""


def confirm_options(screen):
    """The confirm menu's numbered options as {label-lowercased: digit-str}.
    Tolerates the cursor mark and the scroll indicators (↑/↓) the TUI puts
    before a boundary row."""
    out = {}
    for digit, label in re.findall(
            r"^\s*(?:[❯↑↓]\s*)*(\d+)\.\s+(.*?)\s*$", menu_region(screen), re.M):
        out[label.lower()] = digit
    return out


class MenuError(Exception):
    """A step's expected screen state never appeared. .step names it for the
    audit row; the driver has already pressed Escape to close any open menu."""

    def __init__(self, step, detail=""):
        super().__init__(step + ((": " + detail) if detail else ""))
        self.step = step


def _wait(fe, win, pred, timeout, sleep):
    """Poll the window's screen until pred(screen) or timeout; returns the
    last screen either way plus whether pred held."""
    deadline = time.monotonic() + timeout
    screen = fe.get_text(win) or ""
    while not pred(screen):
        if time.monotonic() >= deadline:
            return screen, False
        sleep(POLL_S)
        screen = fe.get_text(win) or ""
    return screen, True


def _bail(fe, win, sleep):
    """Close whatever menu is open — Escape once per open level, verified."""
    for _ in range(2):
        screen = fe.get_text(win) or ""
        if not menu_region(screen):
            return
        fe.send_key(win, "escape")
        sleep(POLL_S)


def _scan(fe, win, target, key, sleep):
    """Walk the checkpoint list one `key` ("up"/"down") press at a time until
    the cursor entry matches `target`, the cursor stops moving (list edge),
    the menu vanishes, or SCAN_MAX. Returns (matched, steps_taken)."""
    steps = 0
    while steps <= SCAN_MAX:
        screen = fe.get_text(win) or ""
        entry = cursor_entry(screen)
        if entry_matches(entry, target):
            return True, steps
        if not menu_open(screen):
            return False, steps
        fe.send_key(win, key)
        steps += 1
        sleep(POLL_S)
        if cursor_entry(fe.get_text(win) or "") == entry:   # edge — stopped
            return False, steps
    return False, steps


def drive(fe, win, target, mode, ups=0, sleep=time.sleep):
    """Rewind session window `win` to the checkpoint for prompt `target`
    (full text), restoring per `mode` (a MODE_LABELS key). `ups` is the
    page's jump hint — the target's `up`-press distance from the cursor's
    "(current)" start (newer prompts + 1) — burst blind before verifying; a
    stale page (e.g. after a kitty-side rewind the web never saw) just means
    the hint lands elsewhere and the verify scan walks the list — up to the
    top, then back down through everything — to find the entry by TEXT. Returns {"steps": .., "digit": ..} on
    success; raises MenuError (menus already closed) on any step that
    didn't verify."""
    if mode not in MODE_LABELS:
        raise MenuError("bad-mode", mode)
    # the input line may hold a draft — /rewind appended to it would send
    # garbage instead of the command; kill line both ways first (harmless
    # when empty — the composer-send clear_draft precedent)
    fe.send_key(win, "ctrl+u")
    fe.send_key(win, "ctrl+k")
    sleep(POLL_S)
    if not fe.send_text(win, "/rewind"):
        raise MenuError("send", "/rewind not delivered")
    screen, ok = _wait(fe, win, menu_open, OPEN_TIMEOUT_S, sleep)
    if not ok:
        _bail(fe, win, sleep)
        raise MenuError("open", "checkpoint menu never appeared")
    # burst the hinted distance blind, then verify by text: scan up to the
    # top, and if the hint overshot (the page counted dead-branch bubbles the
    # menu doesn't list), come back down through the whole list
    for _ in range(max(0, min(int(ups), SCAN_MAX))):
        fe.send_key(win, "up")
        sleep(KEY_GAP_S)
    sleep(POLL_S)
    matched, steps = _scan(fe, win, target, "up", sleep)
    if not matched:
        found, down = _scan(fe, win, target, "down", sleep)
        steps += down
        if not found:
            _bail(fe, win, sleep)
            raise MenuError("find", "checkpoint not found: %r" %
                            first_line(target)[:80])
    fe.send_key(win, "enter")
    screen, ok = _wait(fe, win, confirm_open, STEP_TIMEOUT_S, sleep)
    if not ok:
        _bail(fe, win, sleep)
        raise MenuError("confirm", "confirm menu never appeared")
    opts = confirm_options(screen)
    unchanged = CODE_UNCHANGED in menu_region(screen)
    digit = opts.get(MODE_LABELS[mode])
    degraded = False
    if not digit and mode == "both" and unchanged:
        # no code changes since that checkpoint ⇒ the code is ALREADY in the
        # target state and Claude Code omits the code-restoring options as
        # no-ops — a conversation restore IS "both" here, so degrade to it
        # instead of failing the request (reported live: "restore code and
        # conversation" on a no-change checkpoint bailed as an error)
        digit = opts.get(MODE_LABELS["conversation"])
        degraded = bool(digit)
    if not digit:
        _bail(fe, win, sleep)
        raise MenuError("option", "%r not offered here%s" % (
            MODE_LABELS[mode],
            " — no code changes to revert at that checkpoint"
            if unchanged else ""))
    fe.send_key(win, digit)
    screen, ok = _wait(fe, win, lambda s: not menu_region(s),
                       STEP_TIMEOUT_S, sleep)
    if not ok:
        _bail(fe, win, sleep)
        raise MenuError("close", "menu still open after selecting")
    return {"steps": steps, "digit": digit, "degraded": degraded}
