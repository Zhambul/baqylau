# Copyright (c) 2026 Zhambyl Yermagambet
"""Split a message into pastes that Claude Code keeps as the person's own text.

Claude Code 2.1.294 turns one paste into a `[Pasted text #N]` placeholder when the
paste has more than 800 characters or more than two line breaks. At submit, it
puts the text of that placeholder in `<pasted_content>` tags, and its model is
told not to follow instructions inside pasted content. A message from the
dashboard was then refused (measured: "This pasted block asks me to enter plan
mode ... You didn't write it ..."). A smaller paste goes into the box as plain
text, so a message that is pasted in such pieces stays the person's own text.
A paste is still used, and not typed keys, because the box can be in vim normal
mode, where typed keys are commands.
"""

import re

PIECE_CHARACTERS = 800
PIECE_LINE_BREAKS = 2
# Claude Code counts a line break with this pattern.
LINE_BREAK = re.compile(r"\r\n|\r|\n")


def paste_pieces(text: str) -> tuple[str, ...]:
    """Split text into pastes that are each under Claude Code's placeholder limit.

    Returns:
        The pieces in order. Together they are the text, without a change.

    """
    pieces: list[str] = []
    piece = ""
    for part in _parts(text):
        joined = piece + part
        if piece and (len(joined) > PIECE_CHARACTERS or _line_breaks(joined) > PIECE_LINE_BREAKS):
            pieces.append(piece)
            joined = part
        piece = joined
    pieces.append(piece)
    return tuple(pieces)


def _parts(text: str) -> list[str]:
    """Return each line with its line break, with a long line cut into slices that fit one piece.

    Returns:
        The parts in order.

    """
    lines = text.splitlines(keepends=True) or [text]
    return [
        line[start:start + PIECE_CHARACTERS]
        for line in lines
        for start in range(0, max(len(line), 1), PIECE_CHARACTERS)
    ]


def _line_breaks(text: str) -> int:
    """Count line breaks as Claude Code counts them: CR LF is one break.

    Returns:
        The number of line breaks.

    """
    return len(LINE_BREAK.findall(text))
