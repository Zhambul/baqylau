# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide the tui module."""

# harness/impl/claude_code/controls/tui.py — how text reaches Claude Code's `>` input box.
#
# The ONE delivery channel every gesture that puts a `/…` SLASH COMMAND in front
# of Claude Code goes through (the compact / model / effort quick commands, a
# live `/rename <name>`, the argless auto-rename, `/rewind` — including the one
# rewindmenu.drive types itself). It lives with the host that owns the box: it
# encodes two facts about Claude Code's TUI and nothing else's, and a plugin may
# not import the dashboard, where it used to sit (dashboard/control/launch.
# type_command, moved here byte-identically when the gestures did).
import time

from domain.ids import WindowId
from harness.impl.claude_code import suggestion_screen
from harness.impl.claude_code.composer_state import read_composer_state
from harness.impl.claude_code.controls import clipboard_image
from harness.impl.claude_code.controls.paste_pieces import paste_pieces
from harness.impl.claude_code.controls.screen_driver import poll_until
from harness.impl.claude_code.controls.screen_protocols import CommandScreenDriver, ScreenDriver

# after the line-kill that clears whatever the box held, settle before pasting —
# a paste into a just-cleared input drops leading bytes (measured; the mangle).
# Canonical turn completion can precede the native TUI restoring its composer.
# Real Claude Code sessions exceed three seconds under concurrent live load;
# keep the wait bounded, but allow the verified command path to reach the box.
COMPOSER_READY_TIMEOUT_S = 10.0


def type_command(
    command_screen_driver: CommandScreenDriver,
    win: WindowId,
    text: str,
    *,
    ensure_submit: bool = False,
) -> tuple[bool, bool]:
    """Return the type command.

    Put a SLASH COMMAND into a session's input box and submit it. Returns
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
        delivery — the caller reports indeterminate instead of lying. A long or
        multi-line message is pasted in pieces (paste_pieces), so that Claude
        Code keeps it as plain text and not as untrusted pasted content.
        Attachment delivery uses `ensure_submit`, which sends the bounded Enter
        retry budget for that case.

    Returns:
        Type command.

    """
    _screen, ready = poll_until(
        command_screen_driver,
        win,
        suggestion_screen.composer_visible,
        COMPOSER_READY_TIMEOUT_S,
    )
    if not ready:
        return False, False
    clip = clipboard_image.clear_image()
    if not _paste_and_submit(command_screen_driver, win, text):
        return False, clip
    marker = _submission_marker(text)
    time.sleep(SUBMIT_SETTLE_S)
    if ensure_submit and not _send_submit_retries(command_screen_driver, win):
        return False, clip
    return _verified_submission(command_screen_driver, win, marker), clip


def _paste_and_submit(command_screen_driver: CommandScreenDriver, window_id: WindowId, text: str) -> bool:
    """Paste the text in pieces that Claude Code keeps as plain text, and submit the last piece.

    Returns:
        True if the terminal took each piece and the submit.

    """
    *leading, last = paste_pieces(text)
    for piece in leading:
        if not command_screen_driver.insert_text(window_id, piece, paste=True):
            return False
        time.sleep(PASTE_PIECE_SETTLE_S)
    return command_screen_driver.submit_text(window_id, last, paste=True)


def _send_submit_retries(command_screen_driver: CommandScreenDriver, window_id: WindowId) -> bool:
    for delay in SUBMIT_RETRY_DELAYS_S:
        if not command_screen_driver.send_key(window_id, "enter"):
            return False
        time.sleep(delay)
    return True


def _verified_submission(command_screen_driver: CommandScreenDriver, window_id: WindowId, marker: str) -> bool:
    if not marker:
        return True
    for delay in SUBMIT_RETRY_DELAYS_S:
        if not _submission_pending(command_screen_driver, window_id, marker):
            return True
        command_screen_driver.send_key(window_id, "enter")
        time.sleep(delay)
    return not _submission_pending(command_screen_driver, window_id, marker)


# Give the TUI a beat to take one paste before the next piece comes.
PASTE_PIECE_SETTLE_S = 0.05
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
    """Is the message still sitting in the input box? Unreadable = assume sent.

    Returns:
        True when the stated condition is met; otherwise, false.

    """
    state = read_composer_state(screen_driver, win)
    typed = "" if state is None else (state.typed_text or "")
    return marker in typed
