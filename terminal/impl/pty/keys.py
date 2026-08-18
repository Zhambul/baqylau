"""Key EVENTS as the bytes a terminal sends for them.

The translation a terminal application does inside itself, done here because a
pty has no one to do it: the program reads bytes, and a key name is not one. Only the keys the
product actually sends are here — a name with no encoding is refused rather than
guessed at, because a guess arrives as some other keystroke and reads exactly
like a program that ignored the gesture.

The escape sequences are the NORMAL (cursor-key) mode ones. A program that has
switched the keypad into application mode expects SS3 (`\\x1bOA`) instead, and
this does not track that mode — the honest limit of driving a pty from outside,
where the terminal that owns the screen knows because it is the emulator.
"""

from __future__ import annotations

NAMED_KEYS = {
    "enter": b"\r",
    "return": b"\r",
    "escape": b"\x1b",
    "esc": b"\x1b",
    "tab": b"\t",
    "backspace": b"\x7f",
    "space": b" ",
    "up": b"\x1b[A",
    "down": b"\x1b[B",
    "right": b"\x1b[C",
    "left": b"\x1b[D",
    "home": b"\x1b[H",
    "end": b"\x1b[F",
    "page_up": b"\x1b[5~",
    "page_down": b"\x1b[6~",
}

BRACKETED_PASTE_START = b"\x1b[200~"
BRACKETED_PASTE_END = b"\x1b[201~"


def encoded(key: str) -> bytes | None:
    """The bytes for one key event, or None when this terminal cannot send it."""
    name = key.strip().lower()
    if name in NAMED_KEYS:
        return NAMED_KEYS[name]
    if name.startswith("ctrl+"):
        letter = name.removeprefix("ctrl+")
        if len(letter) == 1 and letter.isalpha():
            return bytes([ord(letter) - ord("a") + 1])
    return None


def chord(keys: str) -> bytes | None:
    """A space-separated sequence ("ctrl+x ctrl+b"), as one payload."""
    payload = b""
    for key in keys.split():
        encoding = encoded(key)
        if encoding is None:
            return None
        payload += encoding
    return payload
