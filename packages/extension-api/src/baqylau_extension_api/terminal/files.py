# Copyright (c) 2026 Zhambyl Yermagambet
"""Describe files and diffs without file-system or command access."""

from typing import Annotated, Literal, Self

from pydantic import AfterValidator, Field, model_validator

from baqylau_extension_api.models.base import Identifier, WireModel
from baqylau_extension_api.terminal.blocks import MAX_BLOCK_ITEMS
from baqylau_extension_api.terminal.text import DisplayText, Tone, require_display_text

MAX_TREE_DEPTH = 32
MAX_DIFF_CHARACTERS = 262_144


class FileTreeItem(WireModel):
    """Describe one visible row in a preorder file tree."""

    item_id: Identifier
    label: DisplayText
    depth: Annotated[int, Field(ge=0, le=MAX_TREE_DEPTH)] = 0
    node_kind: Literal["file", "directory"] = "file"
    tone: Tone = "text"
    action_id: Identifier | None = None


class FileTreeBlock(WireModel):
    """Show visible tree rows while the extension owns expansion state."""

    kind: Literal["file_tree"] = "file_tree"
    block_id: Identifier
    nodes: Annotated[tuple[FileTreeItem, ...], Field(max_length=MAX_BLOCK_ITEMS)]

    @model_validator(mode="after")
    def validate_depth(self) -> Self:
        """Require a directory before its child rows.

        Returns:
            The checked visible tree.

        Raises:
            ValueError: If preorder rows skip a parent or descend into a file.

        """
        allowed_depth = 0
        for node in self.nodes:
            if node.depth > allowed_depth:
                message = "terminal tree rows require a preceding directory parent"
                raise ValueError(message)
            allowed_depth = node.depth + int(node.node_kind == "directory")
        return self


class DiffBlock(WireModel):
    """Supply unified diff text for the client's existing diff renderer."""

    kind: Literal["diff"] = "diff"
    block_id: Identifier
    old_path: DisplayText | None = None
    new_path: DisplayText | None = None
    unified_diff: Annotated[
        str, Field(max_length=MAX_DIFF_CHARACTERS), AfterValidator(require_display_text),
    ]
