# Copyright (c) 2026 Zhambyl Yermagambet
r"""Key EVENTS as the bytes a terminal sends for them.

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

from types import MappingProxyType

from terminal.models.input import TextInputMode
from terminal.paste import pasted

NAMED_KEYS = MappingProxyType({
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
})


def encoded(key: str) -> bytes | None:
    """Return the encoded.

    The bytes for one key event, or None when this terminal cannot send it.

    Returns:
        Encoded.

    """
    name = key.strip().lower()
    named_key = NAMED_KEYS.get(name)
    if named_key is not None:
        return named_key
    if name.startswith("ctrl+"):
        letter = name.removeprefix("ctrl+")
        if len(letter) == 1 and letter.isalpha():
            return bytes([ord(letter) - ord("a") + 1])
    return None


def chord(keys: str) -> bytes | None:
    """Return the chord.

    A space-separated sequence ("ctrl+x ctrl+b"), as one payload.

    Returns:
        Chord.

    """
    payload = b""
    for key in keys.split():
        encoding = encoded(key)
        if encoding is None:
            return None
        payload += encoding
    return payload


def payload(text: str, text_input_mode: TextInputMode) -> bytes:
    """Return the bytes that one text input writes.

    Returns:
        One paste, or the text as it is typed.

    """
    return pasted(text) if text_input_mode == TextInputMode.PASTE else text.encode("utf-8")
