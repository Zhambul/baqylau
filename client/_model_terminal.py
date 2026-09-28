# Copyright (c) 2026 Zhambyl Yermagambet
"""Read the typed terminal view of an extension; the pane never imports the extension SDK."""

from __future__ import annotations

from typing import Annotated, Literal

from _model_base import WireModel
from _model_terminal_items import FileTreeBlockDocument, ListBlockDocument, TableBlockDocument
from _model_terminal_text import DEFAULT_TONE, TextLineDocument, TextSpanDocument, Tone
from pydantic import Field


class TextBlockDocument(WireModel):
    kind: Literal["text"]
    block_id: str
    content: TextLineDocument


class SectionBlockDocument(WireModel):
    kind: Literal["section"]
    block_id: str
    title: str
    children: tuple[BlockDocument, ...] = ()


class DiffBlockDocument(WireModel):
    kind: Literal["diff"]
    block_id: str
    old_path: str | None = None
    new_path: str | None = None
    unified_diff: str = ""


class StatusBlockDocument(WireModel):
    kind: Literal["status"]
    block_id: str
    label: str
    tone: Tone = DEFAULT_TONE
    detail: TextSpanDocument | None = None


type BlockDocument = Annotated[
    TextBlockDocument | SectionBlockDocument | TableBlockDocument | FileTreeBlockDocument
    | DiffBlockDocument | StatusBlockDocument | ListBlockDocument,
    Field(discriminator="kind"),
]


class TerminalViewDocument(WireModel):
    title: str
    blocks: tuple[BlockDocument, ...] = ()


class TerminalViewReply(WireModel):
    view: TerminalViewDocument


class PaneSectionsReply(WireModel):
    views: tuple[TerminalViewDocument, ...] = ()
    unavailable: tuple[str, ...] = ()
