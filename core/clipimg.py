# core/clipimg.py — the macOS clipboard IMAGE probe/wipe, one owner.
#
# The MECHANISM (ask the pasteboard whether it holds an image flavor; empty it)
# is OS-level and tool-agnostic. The POLICY that needs it is not: Claude Code's
# TUI auto-attaches whatever image the clipboard holds to a message on ANY
# bracketed paste (and on an argv-prompt startup), so every web send/launch into
# a Claude Code window empties an image clipboard first — while codex does not
# do this and must NOT pay the wipe (`HostControl.paste_grabs_clipboard_image`
# is where a host DECLARES it, docs/dashboard.md *Clipboard-image guard*).
#
# It lives in core because BOTH tiers reach it: plugins/claude_code's slash-
# command channel (plugins/claude_code/tui.py) and its `send` gesture, and the
# dashboard's own new-session launch path (dashboard/control/launch.py keeps the
# thin `clear_clipboard_image()` name its callers and tests already use). A
# plugin may not import the dashboard, so the shared half had to move down here
# rather than be spelled twice — the exact re-encoding docs/styleguide.md's
# single-owner table bans.
#
# Distinct from dashboard/clipboard.py, which reads the pasteboard's FILE
# flavors (pyobjc) to resolve a pasted file's path — a different question about
# the same board, deliberately not unified.
import subprocess
import sys

# macOS clipboard "flavor" codes that mean an IMAGE is on the board. Proven
# live: a web send with a screenshot on the clipboard arrived as "text[Image #1]"
# with the PNG, though baqylau attached nothing. There is no Claude Code opt-out,
# so the guard is to empty the board (the user chose auto-clear; a text-only
# clipboard is left alone).
_CLIP_IMAGE_FLAVORS = ("PNGf", "TIFF", "8BPS", "jp2", "GIF", "JPEG", "picture")


def has_image():
    """True when the macOS clipboard currently holds an image flavor. Best-effort
    (`osascript -e 'clipboard info'`); False off macOS / on any failure / on a
    text-only clipboard — so we never clear a clipboard that has no image."""
    if sys.platform != "darwin":
        return False
    try:
        info = subprocess.run(["osascript", "-e", "clipboard info"],
                              capture_output=True, text=True, timeout=2).stdout or ""
    except Exception:
        return False
    return any(f in info for f in _CLIP_IMAGE_FLAVORS)


def clear_image():
    """If the macOS clipboard holds an IMAGE, empty it — so a host TUI can't
    auto-attach it to a web-delivered message (docs/dashboard.md *Clipboard-image
    guard*). Returns True iff it cleared. No-op (False) off macOS or on a
    text-only clipboard, so a plain text clipboard is preserved; best-effort,
    never raises into the caller."""
    if not has_image():
        return False
    try:
        subprocess.run(["osascript", "-e", 'set the clipboard to ""'],
                       capture_output=True, timeout=2)
        return True
    except Exception:
        return False
