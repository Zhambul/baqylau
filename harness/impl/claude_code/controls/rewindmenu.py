# harness/impl/claude_code/controls/rewindmenu.py — drive Claude Code's OWN rewind menu from
# the web.
#
# The dashboard's full rewind support
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
#   - the numbered rows use cursor navigation and Enter;
#   - Escape closes either menu cleanly back to the composer.
#
# Menu-region parsing: get_text returns the whole visible screen, where
# scrollback prompt echoes also start with "❯" — but at column 0; menu lines
# are indented, so only "  ❯ "-prefixed lines inside the region after the last
# "Rewind" header are cursor lines.
import re
import time
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from domain.ids import WindowId

from harness.impl.claude_code.controls import numberedmenu
from harness.impl.claude_code.controls import screen_driver as screendrive
from harness.impl.claude_code.controls import tui
from harness.impl.claude_code.controls.screen_driver import ScreenDriver

# One entry per selectable restore option: the requested `mode` (the POST
# body's vocabulary) → the menu label it must match. Labels are matched
# case-insensitively on the parsed confirm menu, so a menu that lacks the
# option (e.g. "code" at a checkpoint with no code changes) is a clean bail,
# not a wrong digit.
class ClaudeCodeRewindMode(StrEnum):
    BOTH = "both"
    CONVERSATION = "conversation"
    CODE = "code"


MODE_LABELS: Mapping[ClaudeCodeRewindMode, str] = {
    ClaudeCodeRewindMode.BOTH: "restore code and conversation",
    ClaudeCodeRewindMode.CONVERSATION: "restore conversation",
    ClaudeCodeRewindMode.CODE: "restore code",
}


def _mode_label(mode: str) -> str | None:
    try:
        return MODE_LABELS.get(ClaudeCodeRewindMode(mode))
    except ValueError:
        return None

MENU_HEADER = "Rewind"                       # first-menu region anchor
# The list introduction and its selected row are visible even when a small
# terminal clips the footer. Claude Code 2.1.241 can omit the footer from
# get_text while it keeps the list open. Keep the old footer marker for older
# layouts, but do not require it.
MENU_INTRO = "Restore the code and/or conversation to the point before"
MENU_FOOT = "to continue"
CONFIRM_HEADER = "Confirm you want to restore"   # second-menu open detector
#                  (a JSX literal, unlike the footer — safe to match whole)
CODE_UNCHANGED = "The code will be unchanged."   # confirm-menu line when the
#                  checkpoint has no code changes — the verifiable reason the
#                  code-restoring options are absent (vs "will be restored…")

POLL_S = 0.15          # screen re-read beat while waiting for a menu state
OPEN_TIMEOUT_S = 4.0   # /rewind typed → menu visible (slash command latency)
STEP_TIMEOUT_S = 2.0   # a key press → its screen effect visible
SCAN_MAX = 100         # hard step bound — Claude Code caps checkpoints at 100
CONFIRM_SCAN_MAX = 10  # restore menu has four rows; leave room for later rows
KEY_GAP_S = 0.05       # beat between blind repeated `up` presses
DIALOG_MIN_LINES = 40  # the full confirm card and its restore rows


def first_line(text: str) -> str:
    """The menu's view of a prompt: its first non-empty line, stripped."""
    for ln in (text or "").splitlines():
        if ln.strip():
            return ln.strip()
    return ""


def entry_matches(entry: str, target: str) -> bool:
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


def menu_region(screen: str) -> str:
    """The visible text from the LAST checkpoint-menu header down, or "" when
    no menu is on screen. Anchoring at the last header skips scrollback (old
    prompt echoes, even a previously captured menu) above the live one.

    The header line is whitespace-tolerant. Claude Code changes its leading
    indent with the menu layout and can add trailing styled padding. A fixed
    two-space anchor therefore misses an open menu."""
    if not screen:
        return ""
    # the \n-prefix lets a header on the very first screen row still anchor
    hits = list(
        re.finditer(
            r"\n[ \t]+%s[ \t]*\n" % re.escape(MENU_HEADER),
            "\n" + screen,
        )
    )
    return screen[max(0, hits[-1].start() - 1):] if hits else ""


def menu_open(screen: str) -> bool:
    """True when the checkpoint list (first menu) is on screen."""
    region = menu_region(screen)
    marker_visible = MENU_INTRO in region or MENU_FOOT in region.lower()
    return (
        bool(region)
        and marker_visible
        and bool(cursor_entry(screen))
        and CONFIRM_HEADER not in region
    )


def confirm_open(screen: str) -> bool:
    """True when the numbered confirm menu (second menu) is on screen."""
    return CONFIRM_HEADER in menu_region(screen)


def cursor_entry(screen: str) -> str:
    """The text of the ❯-cursor line inside the menu region ("" when absent).
    Menu cursor lines are INDENTED ("  ❯ …"); scrollback prompt echoes start
    at column 0, so they never match."""
    m = re.findall(r"^\s+❯\s+(.*)$", menu_region(screen), re.M)
    return m[-1].strip() if m else ""


@dataclass(frozen=True)
class ConfirmOption:
    label: str
    digit: str
    cursor: bool


def confirm_options(screen: str) -> tuple[ConfirmOption, ...]:
    """The confirm menu's numbered options as {label-lowercased: digit-str}.
    Tolerates the cursor mark and the scroll indicators (↑/↓) the TUI puts
    before a boundary row."""
    return tuple(
        ConfirmOption(row.label.lower(), row.digit, row.cursor)
        for row in numberedmenu.rows(menu_region(screen))
    )


def confirm_ready(screen: str, requested_label: str, mode: str) -> bool:
    """True when the confirm menu has rendered enough to decide the request."""
    if not confirm_open(screen):
        return False
    options = confirm_options(screen)
    if any(option.label == requested_label for option in options):
        return True
    return (
        mode in {ClaudeCodeRewindMode.BOTH, ClaudeCodeRewindMode.CODE}
        and bool(options)
        and CODE_UNCHANGED in menu_region(screen)
    )


def _scan_confirm(
    screen_driver: ScreenDriver,
    win: WindowId,
    requested_label: str,
    mode: str,
    key: str,
    sleep: Callable[[float], None],
) -> tuple[str, bool]:
    """Reveal a restore option that is below a small terminal's viewport."""
    screen = screen_driver.get_text(win) or ""
    for _ in range(CONFIRM_SCAN_MAX + 1):
        if confirm_ready(screen, requested_label, mode):
            return screen, True
        if not confirm_open(screen):
            return screen, False
        screen_driver.send_key(win, key)
        sleep(POLL_S)
        screen = screen_driver.get_text(win) or ""
    return screen, False


class MenuError(screendrive.StepError):
    """A step's expected screen state never appeared. .step names it for the
    audit row; the driver has already pressed Escape to close any open menu."""


@dataclass(frozen=True)
class RewindOutcome:
    """What a successful drive() reports back to its caller."""

    steps: int
    digit: str
    degraded: bool


def select_confirm_option(
    screen_driver: ScreenDriver,
    win: WindowId,
    digit: str,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Select one verified confirmation row with cursor keys and Enter."""
    def current_rows() -> tuple[numberedmenu.Row, ...]:
        return numberedmenu.rows(menu_region(screen_driver.get_text(win) or ""))

    try:
        numberedmenu.select(
            screen_driver,
            win,
            current_rows,
            digit,
            sleep=sleep,
            key_gap=POLL_S,
        )
    except numberedmenu.SelectionError as error:
        raise MenuError("select", str(error)) from error


def _bail(screen_driver: ScreenDriver, win: WindowId, sleep: Callable[[float], None]) -> None:
    """Close whatever menu is open — Escape once per open level, verified."""
    for _ in range(2):
        screen = screen_driver.get_text(win) or ""
        if not menu_region(screen):
            return
        screen_driver.send_key(win, "escape")
        sleep(POLL_S)


def _scan(
    screen_driver: ScreenDriver,
    win: WindowId,
    target: str,
    key: str,
    sleep: Callable[[float], None],
) -> tuple[bool, int]:
    """Walk the checkpoint list one `key` ("up"/"down") press at a time until
    the cursor entry matches `target`, the cursor stops moving (list edge),
    the menu vanishes, or SCAN_MAX. Returns (matched, steps_taken)."""
    steps = 0
    while steps <= SCAN_MAX:
        screen = screen_driver.get_text(win) or ""
        entry = cursor_entry(screen)
        if entry_matches(entry, target):
            return True, steps
        if not menu_open(screen):
            return False, steps
        screen_driver.send_key(win, key)
        steps += 1
        sleep(POLL_S)
        if cursor_entry(screen_driver.get_text(win) or "") == entry:   # edge — stopped
            return False, steps
    return False, steps


def _drive_menu(
    screen_driver: ScreenDriver,
    win: WindowId,
    target: str,
    mode: str,
    ups: int = 0,
    sleep: Callable[[float], None] = time.sleep,
) -> RewindOutcome:
    """Rewind session window `win` to the checkpoint for prompt `target`
    (full text), restoring per `mode` (a MODE_LABELS key). `ups` is the
    page's jump hint — the target's `up`-press distance from the cursor's
    "(current)" start (newer prompts + 1) — burst blind before verifying; a
    stale page (e.g. after a terminal-side rewind the web never saw) just means
    the hint lands elsewhere and the verify scan walks the list — up to the
    top, then back down through everything — to find the entry by TEXT. Returns {"steps": .., "digit": ..} on
    success; raises MenuError (menus already closed) on any step that
    didn't verify."""
    requested_label = _mode_label(mode)
    if requested_label is None:
        raise MenuError("bad-mode", mode)
    # the input line may hold a draft — /rewind appended to it would send
    # garbage instead of the command; kill line both ways first (harmless
    # when empty — the composer-send clear_draft precedent)
    screen_driver.send_key(win, "ctrl+u")
    screen_driver.send_key(win, "ctrl+k")
    sleep(POLL_S)
    # a bracketed paste, never raw keystrokes: with editorMode vim the box is
    # MODAL and the Escapes that preceded this (an interrupt, a bail) leave it
    # in NORMAL mode, where "/rewind" is vim COMMANDS — the menu never opens and
    # the tail lands in the conversation (tui.type_command has the measurement)
    ok, _clip = tui.type_command(screen_driver, win, "/rewind")
    if not ok:
        raise MenuError("send", "/rewind not delivered")
    screen, ok = screendrive.poll_until(screen_driver, win, menu_open, OPEN_TIMEOUT_S, sleep)
    if not ok:
        _bail(screen_driver, win, sleep)
        # carry the SCREEN we gave up on (StepError.screen, as askdialog does):
        # "the menu never appeared" is indistinguishable, from the audit alone,
        # between a menu that truly never opened and one that opened while our
        # detector missed it — which is exactly the marker drift that cost three
        # rounds on 2026-07-25. The capture makes the next one a single look.
        raise MenuError("open", "checkpoint menu never appeared", screen)
    # burst the hinted distance blind, then verify by text: scan up to the
    # top, and if the hint overshot (the page counted dead-branch bubbles the
    # menu doesn't list), come back down through the whole list
    for _ in range(max(0, min(int(ups), SCAN_MAX))):
        screen_driver.send_key(win, "up")
        sleep(KEY_GAP_S)
    sleep(POLL_S)
    matched, steps = _scan(screen_driver, win, target, "up", sleep)
    if not matched:
        found, down = _scan(screen_driver, win, target, "down", sleep)
        steps += down
        if not found:
            failure_screen = screen_driver.get_text(win) or ""
            _bail(screen_driver, win, sleep)
            raise MenuError(
                "find",
                "checkpoint not found: %r" % first_line(target)[:80],
                failure_screen,
            )
    screen_driver.send_key(win, "enter")
    screen, ok = screendrive.poll_until(
        screen_driver,
        win,
        confirm_open,
        STEP_TIMEOUT_S,
        sleep,
    )
    if ok:
        screen, ok = _scan_confirm(
            screen_driver,
            win,
            requested_label,
            mode,
            "down",
            sleep,
        )
    if not ok and confirm_open(screen):
        screen, ok = _scan_confirm(
            screen_driver,
            win,
            requested_label,
            mode,
            "up",
            sleep,
        )
    if not ok:
        _bail(screen_driver, win, sleep)
        raise MenuError("confirm", "requested restore option never appeared", screen)
    opts = confirm_options(screen)
    unchanged = CODE_UNCHANGED in menu_region(screen)
    digit = next((option.digit for option in opts if option.label == requested_label), None)
    degraded = False
    if not digit and mode == "both" and unchanged:
        # no code changes since that checkpoint ⇒ the code is ALREADY in the
        # target state and Claude Code omits the code-restoring options as
        # no-ops — a conversation restore IS "both" here, so degrade to it
        # instead of failing the request (reported live: "restore code and
        # conversation" on a no-change checkpoint bailed as an error)
        conversation_label = _mode_label("conversation") or ""
        digit = next(
            (option.digit for option in opts if option.label == conversation_label),
            None,
        )
        degraded = bool(digit)
    if not digit:
        _bail(screen_driver, win, sleep)
        raise MenuError(
            "option",
            "%r not offered here%s"
            % (
                requested_label,
                " — no code changes to revert at that checkpoint"
                if unchanged
                else "",
            ),
            screen,
        )
    select_confirm_option(screen_driver, win, digit, sleep)
    screen, ok = screendrive.poll_until(screen_driver, win, lambda s: not menu_region(s),
                       STEP_TIMEOUT_S, sleep)
    if not ok:
        _bail(screen_driver, win, sleep)
        raise MenuError("close", "menu still open after selecting", screen)
    return RewindOutcome(steps, digit, degraded)


def drive(
    screen_driver: ScreenDriver,
    win: WindowId,
    target: str,
    mode: str,
    ups: int = 0,
    sleep: Callable[[float], None] = time.sleep,
) -> RewindOutcome:
    """Drive one native rewind dialog inside a temporary usable viewport."""
    current_lines = screen_driver.lines(win)
    growth = max(0, DIALOG_MIN_LINES - current_lines) if current_lines is not None else 0
    grown = growth > 0 and screen_driver.resize_lines(win, growth)
    if grown:
        sleep(POLL_S)
    try:
        return _drive_menu(screen_driver, win, target, mode, ups, sleep)
    finally:
        if grown:
            screen_driver.resize_lines(win, -growth)
