# Copyright (c) 2026 Zhambyl Yermagambet
"""Read the item blocks of an extension terminal view: tables, file trees, and lists."""

from __future__ import annotations

from typing import Literal

from _model_base import WireModel
from _model_terminal_text import DEFAULT_TONE, TextLineDocument, Tone


class TableRowDocument(WireModel):
    item_id: str
    cells: tuple[TextLineDocument, ...]
    action_id: str | None = None


class TableBlockDocument(WireModel):
    kind: Literal["table"]
    block_id: str
    columns: tuple[str, ...]
    rows: tuple[TableRowDocument, ...] = ()


class FileTreeItemDocument(WireModel):
    item_id: str
    label: str
    depth: int = 0
    node_kind: Literal["file", "directory"] = "file"
    tone: Tone = DEFAULT_TONE
    action_id: str | None = None


class FileTreeBlockDocument(WireModel):
    kind: Literal["file_tree"]
    block_id: str
    nodes: tuple[FileTreeItemDocument, ...] = ()


class ListItemDocument(WireModel):
    item_id: str
    label: TextLineDocument
    detail: TextLineDocument | None = None
    action_id: str | None = None


class ListBlockDocument(WireModel):
    kind: Literal["list"]
    block_id: str
    entries: tuple[ListItemDocument, ...] = ()
    selected_id: str | None = None
