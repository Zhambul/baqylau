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

from harness.impl.claude_code.controls import clipboard_image

# after the line-kill that clears whatever the box held, settle before pasting —
# a paste into a just-cleared input drops leading bytes (measured; the mangle).
CLEAR_GAP_S = 0.15
CLEAR_LINES_MAX = 50    # ceiling on the per-line kill loop: a corrupt/huge
#                         stash must not become an unbounded keystroke storm.


def type_command(fe, win, text):
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

    The clipboard-image guard comes with it: a bracketed paste makes Claude Code
    attach whatever IMAGE is on the board (docs/dashboard.md *Clipboard-image
    guard*), so no caller may paste without it — folding the two together here is
    the point of the single owner. THIS host declares
    `paste_grabs_clipboard_image`; a host that doesn't pays no osascript.

    DELIVERY IS VERIFIED, not assumed. Even with the CR as its own delayed
    keystroke, the submit is swallowed intermittently (measured 2026-08-15,
    session 4597c616: a ~50-char web send pasted fine and sat UNSUBMITTED in the
    box while the control audited `acknowledged`). So after the paste the box
    itself is read back: if the message is still sitting there, Enter is
    re-sent with backoff, and a message that never leaves the draft is a FAILED
    delivery — the caller reports indeterminate instead of lying. Multi-line
    pastes collapse into Claude Code's placeholder and cannot be verified; they
    keep the optimistic contract."""
    clip = clipboard_image.clear_image()
    if not fe.paste_text(win, text):
        return False, clip
    marker = _submission_marker(text)
    if not marker:
        return True, clip
    time.sleep(SUBMIT_SETTLE_S)
    for delay in SUBMIT_RETRY_DELAYS_S:
        if not _submission_pending(fe, win, marker):
            return True, clip
        fe.send_key(win, "enter")
        time.sleep(delay)
    return not _submission_pending(fe, win, marker), clip


# After the paste's own CR, give the TUI a beat before reading the box back; then
# each retried Enter gets a longer beat to take effect.
SUBMIT_SETTLE_S = 0.2
SUBMIT_RETRY_DELAYS_S = (0.4, 0.8)
# The box shows the START of a long message (the tail is ellipsized), so the
# verification marker is the head of its first line.
SUBMISSION_MARKER_LENGTH = 24


def _submission_marker(text) -> str:
    lines = str(text).strip().splitlines()
    if len(lines) != 1:
        return ""
    return lines[0][:SUBMISSION_MARKER_LENGTH].strip()


def _submission_pending(fe, win, marker) -> bool:
    """Is the message still sitting in the input box? Unreadable = assume sent."""
    try:
        from harness.impl.claude_code.probe import ClaudeCodeTerminalProbe

        state = ClaudeCodeTerminalProbe().input_state(fe.terminal, str(win))
    except Exception:
        try:
            from core import audit

            audit.error("", "type_command (submit verification)", {"window": str(win)})
        except Exception:
            pass
        return False
    typed = (state.typed_text or "") if state is not None else ""
    return marker in typed


def clear_input(fe, win, prev_text="", sleep=time.sleep):
    """Kill whatever is in the input box, so the paste that follows REPLACES it
    instead of gluing onto it. Returns the number of lines killed.

    Ctrl+U (to line start) + Ctrl+K (to line end) clear ONE line, and the text
    the web left there can be MULTI-LINE (session 8b9f870b, 2026-07-29: a 3-line
    take-back came back, only its last line died, and the resend glued onto the
    two survivors) — so `prev_text` (the stash, when we have it) drives the loop:
    one kill per newline, with a backspace between kills consuming the newline to
    hop up a line. The cursor sits on the LAST line after a restore. With no
    stash the historical single-line kill stands, and the cursor position within
    a line never mattered."""
    lines = prev_text.count("\n") + 1 if prev_text else 1
    for i in range(min(lines, CLEAR_LINES_MAX)):
        if i:
            fe.send_key(win, "backspace")
        fe.send_key(win, "ctrl+u")
        fe.send_key(win, "ctrl+k")
    sleep(CLEAR_GAP_S)
    return lines
