# harness/impl/claude_code/controls/tui.py — how text reaches Claude Code's `❯` input box.
#
# The ONE delivery channel every gesture that puts a `/…` SLASH COMMAND in front
# of Claude Code goes through (the compact / model / effort quick commands, a
# live `/rename <name>`, the argless auto-rename, `/rewind` — including the one
# rewindmenu.drive types itself). It lives with the host that owns the box: it
# encodes two facts about Claude Code's TUI and nothing else's, and a plugin may
# not import the dashboard, where it used to sit (dashboard/control/launch.
# type_command, moved here byte-identically when the gestures did).
import time
from collections.abc import Callable

from domain.ids import WindowId

from harness.impl.claude_code import suggestion
from harness.impl.claude_code.controls import clipboard_image
from harness.impl.claude_code.controls.screen_driver import ScreenDriver, poll_until

# after the line-kill that clears whatever the box held, settle before pasting —
# a paste into a just-cleared input drops leading bytes (measured; the mangle).
CLEAR_GAP_S = 0.15
CLEAR_EFFECT_TIMEOUT_S = 1.5
CLEAR_LINES_MAX = 50    # ceiling on the per-line kill loop: a corrupt/huge
#                         stash must not become an unbounded keystroke storm.
COMPOSER_READY_TIMEOUT_S = 3.0


def type_command(
    screen_driver: ScreenDriver,
    win: WindowId,
    text: str,
    *,
    ensure_submit: bool = False,
) -> tuple[bool, bool]:
    """Put a SLASH COMMAND into a session's input box and submit it. Returns
    (ok, cleared_clipboard_image).

    THE one way to do that — raw keystrokes are NOT SAFE in that box. With
    `editorMode: vim` the input is MODAL, and anything that pressed Escape first
    (the interrupt presses up to `INTERRUPT_TRIES`) leaves it in NORMAL mode,
    where the characters are vim COMMANDS rather than text: `/` opens reverse
    history search, and Claude Code's own hint spells out the workaround —
    *"press Esc then i then / to open the command menu instead"*. Measured
    2026-07-25: a web rewind ~14s after a web interrupt typed `/rewind` into a
    NORMAL-mode box, the checkpoint menu never opened, and the tail of the
    keystrokes was submitted into the conversation as the message `nd` (the
    first `web-rewind-to` `step: "open"` failure in the audit; the identical `nd`
    artifact recorded earlier in the Esc-gesture comment was blamed on a racing
    Escape, which now looks like the wrong diagnosis).

    A BRACKETED PASTE is mode-proof — Claude Code takes it as content, never as
    keystrokes — and it is already how the quick commands (`/compact`,
    `/model`, `/effort`) reach the TUI, which is why those kept working where
    the typed `/rewind` did not. The Enter rides outside the paste
    (the terminal's own submit convention), so it still submits.

    The clipboard-image guard comes with it: a bracketed paste can make Claude
    Code attach an unrelated image from the board. No caller can paste before
    this one delivery owner clears that accidental input.

    DELIVERY IS VERIFIED, not assumed. Even with the CR as its own delayed
    keystroke, the submit is swallowed intermittently (measured 2026-08-15,
    session 4597c616: a ~50-char web send pasted fine and sat UNSUBMITTED in the
    box while the control audited `acknowledged`). So after the paste the box
    itself is read back: if the message is still sitting there, Enter is
    re-sent with backoff, and a message that never leaves the draft is a FAILED
    delivery — the caller reports indeterminate instead of lying. Multi-line
    pastes collapse into Claude Code's placeholder. Attachment delivery uses
    `ensure_submit`, which sends the bounded Enter retry budget for that case."""
    _screen, ready = poll_until(
        screen_driver,
        win,
        suggestion.input_box_visible,
        COMPOSER_READY_TIMEOUT_S,
    )
    if not ready:
        return False, False
    clip = clipboard_image.clear_image()
    if not screen_driver.paste_text(win, text):
        return False, clip
    marker = _submission_marker(text)
    time.sleep(SUBMIT_SETTLE_S)
    if ensure_submit:
        for delay in SUBMIT_RETRY_DELAYS_S:
            if not screen_driver.send_key(win, "enter"):
                return False, clip
            time.sleep(delay)
    if not marker:
        return True, clip
    for delay in SUBMIT_RETRY_DELAYS_S:
        if not _submission_pending(screen_driver, win, marker):
            return True, clip
        screen_driver.send_key(win, "enter")
        time.sleep(delay)
    return not _submission_pending(screen_driver, win, marker), clip


# After the paste's own CR, give the TUI a beat before reading the box back; then
# each retried Enter gets a longer beat to take effect.
SUBMIT_SETTLE_S = 0.2
SUBMIT_RETRY_DELAYS_S = (0.4, 0.8)
# The box shows the START of a long message (the tail is ellipsized), so the
# verification marker is the head of its first line.
SUBMISSION_MARKER_LENGTH = 24


def _submission_marker(text: str) -> str:
    lines = str(text).strip().splitlines()
    if len(lines) != 1:
        return ""
    return lines[0][:SUBMISSION_MARKER_LENGTH].strip()


def _submission_pending(screen_driver: ScreenDriver, win: WindowId, marker: str) -> bool:
    """Is the message still sitting in the input box? Unreadable = assume sent."""
    try:
        from harness.impl.claude_code.probe import ClaudeCodeTerminalProbe  # noqa: PLC0415 — probe is optional; unreadable means assume sent

        state = ClaudeCodeTerminalProbe().input_state(screen_driver.terminal.viewport, win)
    except Exception:
        try:
            from audit import record  # noqa: PLC0415 — audit fallback inside the failure path

            record.error("", "type_command (submit verification)", f"window={win}")
        except Exception:
            pass
        return False
    typed = (state.typed_text or "") if state is not None else ""
    return marker in typed


def clear_input(
    screen_driver: ScreenDriver,
    win: WindowId,
    prev_text: str = "",
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Clear a draft and verify each removed logical line on the screen.

    Ctrl+U and Ctrl+K clear one logical line. A backspace then consumes the
    preceding newline. The terminal probe normalizes line breaks, so a stored
    line count is not reliable for a restored multiline prompt. Read the box
    after each removal and stop when it is empty or when the visible text no
    longer changes. `prev_text` is only a fallback when the screen is not
    readable."""
    fallback_lines = min(prev_text.count("\n") + 1 if prev_text else 1, CLEAR_LINES_MAX)
    before = _input_text(screen_driver, win)
    killed = 0
    for _ in range(CLEAR_LINES_MAX):
        screen_driver.send_key(win, "ctrl+u")
        screen_driver.send_key(win, "ctrl+k")
        killed += 1
        after = _wait_for_input_change(screen_driver, win, before, sleep)
        if after == "":
            return killed
        if after is not None and after == before:
            return killed
        if after is None and killed >= fallback_lines:
            return killed
        screen_driver.send_key(win, "backspace")
        joined = _wait_for_input_change(screen_driver, win, after, sleep)
        before = joined if joined is not None else after
    return killed


def _wait_for_input_change(
    screen_driver: ScreenDriver,
    win: WindowId,
    before: str | None,
    sleep: Callable[[float], None],
) -> str | None:
    deadline = time.monotonic() + CLEAR_EFFECT_TIMEOUT_S
    while True:
        current = _input_text(screen_driver, win)
        if current != before:
            return current
        if time.monotonic() >= deadline:
            return current
        sleep(CLEAR_GAP_S)


def _input_text(screen_driver: ScreenDriver, win: WindowId) -> str | None:
    """Read real composer text, with a plain-screen fallback for PTY tests."""
    for ansi in (True, False):
        screen = screen_driver.get_text(win, ansi=ansi)
        if screen is None:
            continue
        if not suggestion.input_box_visible(screen):
            return None
        return suggestion.typed(screen) or ""
    return None
