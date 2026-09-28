# Copyright (c) 2026 Zhambyl Yermagambet
"""Read the styled text of an extension terminal view."""

from __future__ import annotations

from typing import Final, Literal

from _model_base import WireModel

type Tone = Literal["text", "muted", "accent", "success", "warning", "error"]
DEFAULT_TONE: Final = "text"


class TextSpanDocument(WireModel):
    text: str
    tone: Tone = DEFAULT_TONE
    bold: bool = False


class TextLineDocument(WireModel):
    spans: tuple[TextSpanDocument, ...] = ()
