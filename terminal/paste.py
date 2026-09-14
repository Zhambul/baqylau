# Copyright (c) 2026 Zhambyl Yermagambet
"""Write the bytes of one paste.

Every terminal writes the same two marks, so a text that arrives as a paste in
one terminal arrives as one paste in the other. A terminal that wraps the text
itself can add a SECOND, empty paste, and a native program that reads its own
clipboard when a paste carries nothing then puts the clipboard of the person in
the prompt.
"""

from __future__ import annotations

START = b"\x1b[200~"
END = b"\x1b[201~"


def pasted(text: str) -> bytes:
    """Return the bytes of one paste.

    Returns:
        The text between the marks of one paste.

    """
    return START + text.encode("utf-8") + END
