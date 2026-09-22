# Copyright (c) 2026 Zhambyl Yermagambet
"""Carry display text without terminal control sequences."""

from typing import Annotated, Literal

from pydantic import AfterValidator, Field

from baqylau_extension_api.models.base import WireModel

MAX_DISPLAY_TEXT = 8192
MAX_SPANS = 64
CONTROL_START = 32
CONTROL_END = 160
DELETE_CHARACTER = 127
BIDI_CONTROLS = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def require_display_text(text: str) -> str:
    """Reject terminal commands and hidden directional overrides.

    Returns:
        Text with tabs and newlines but no other terminal controls.

    Raises:
        ValueError: If a display string contains a forbidden control.

    """
    for character in text:
        forbidden = ord(character) < CONTROL_START and character not in {"\n", "\t"}
        if forbidden or DELETE_CHARACTER <= ord(character) < CONTROL_END or character in BIDI_CONTROLS:
            message = "terminal display text must not contain control sequences"
            raise ValueError(message)
    return text


DisplayText = Annotated[str, Field(max_length=MAX_DISPLAY_TEXT), AfterValidator(require_display_text)]
Tone = Literal["text", "muted", "accent", "success", "warning", "error"]


class TextSpan(WireModel):
    """Select semantic emphasis without supplying an escape sequence."""

    text: DisplayText
    tone: Tone = "text"
    bold: bool = False


class TextLine(WireModel):
    """Group bounded spans for a table cell, list item, or heading."""

    spans: Annotated[tuple[TextSpan, ...], Field(max_length=MAX_SPANS)]
