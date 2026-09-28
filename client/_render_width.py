# Copyright (c) 2026 Zhambyl Yermagambet
"""Measure text in terminal columns, not in code points."""

from __future__ import annotations

import unicodedata

ZERO_WIDTH_CATEGORIES = frozenset(("Mn", "Me", "Cf"))
WIDE_CLASSES = frozenset(("W", "F"))


def char_width(char: str) -> int:
    """Count the columns of one character.

    Returns:
        0 for a combining or format character, 2 for a wide East Asian character, else 1.

    """
    if unicodedata.category(char) in ZERO_WIDTH_CATEGORIES:
        return 0
    return 2 if unicodedata.east_asian_width(char) in WIDE_CLASSES else 1


def text_width(text: str) -> int:
    """Count the columns of a text.

    Returns:
        The sum of the character widths.

    """
    return sum(char_width(char) for char in text)


def cut(text: str, columns: int) -> int:
    """Find how many characters fit in the columns.

    Returns:
        The length of the longest prefix that is at most `columns` wide.

    """
    used = 0
    for index, char in enumerate(text):
        used += char_width(char)
        if used > columns:
            return index
    return len(text)
