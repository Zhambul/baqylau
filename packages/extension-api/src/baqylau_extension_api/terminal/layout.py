# Copyright (c) 2026 Zhambyl Yermagambet
"""Compose bounded terminal sections from public display blocks."""

from typing import Annotated, Literal

from pydantic import Field

from baqylau_extension_api.models.base import Identifier, WireModel
from baqylau_extension_api.terminal.blocks import ListBlock, StatusBlock, TableBlock, TextBlock
from baqylau_extension_api.terminal.files import DiffBlock, FileTreeBlock
from baqylau_extension_api.terminal.text import DisplayText

MAX_SECTION_CHILDREN = 64


class SectionBlock(WireModel):
    """Group feature-owned blocks under one heading."""

    kind: Literal["section"] = "section"
    block_id: Identifier
    title: DisplayText
    children: Annotated[tuple["TerminalBlock", ...], Field(max_length=MAX_SECTION_CHILDREN)]


TerminalBlock = Annotated[
    TextBlock | TableBlock | ListBlock | StatusBlock | FileTreeBlock | DiffBlock | SectionBlock,
    Field(discriminator="kind"),
]

SectionBlock.model_rebuild()
