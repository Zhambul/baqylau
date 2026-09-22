# Copyright (c) 2026 Zhambyl Yermagambet
"""Define terminal text, table, list, and status blocks."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from baqylau_extension_api.models.base import Identifier, WireModel
from baqylau_extension_api.terminal.text import DisplayText, TextLine, TextSpan, Tone

MAX_BLOCK_ITEMS = 512
MAX_TABLE_COLUMNS = 16


class TextBlock(WireModel):
    """Render wrapped text with semantic styles."""

    kind: Literal["text"] = "text"
    block_id: Identifier
    content: TextLine


class TableRow(WireModel):
    """Keep one row's cells and optional action together."""

    item_id: Identifier
    cells: Annotated[tuple[TextLine, ...], Field(min_length=1, max_length=MAX_TABLE_COLUMNS)]
    action_id: Identifier | None = None


class TableBlock(WireModel):
    """Let the client fit a bounded table to its current width."""

    kind: Literal["table"] = "table"
    block_id: Identifier
    columns: Annotated[tuple[DisplayText, ...], Field(min_length=1, max_length=MAX_TABLE_COLUMNS)]
    rows: Annotated[tuple[TableRow, ...], Field(max_length=MAX_BLOCK_ITEMS)]

    @model_validator(mode="after")
    def validate_cells(self) -> Self:
        """Require one cell for each declared column.

        Returns:
            The checked table.

        Raises:
            ValueError: If a row has a different number of cells.

        """
        column_count = len(self.columns)
        if any(len(row.cells) != column_count for row in self.rows):
            message = "terminal table rows must match the declared columns"
            raise ValueError(message)
        return self


class ListItem(WireModel):
    """Describe an item that can be selected without running a shell."""

    item_id: Identifier
    label: TextLine
    detail: TextLine | None = None
    action_id: Identifier | None = None


class ListBlock(WireModel):
    """Show a selectable list, such as recorded thread messages."""

    kind: Literal["list"] = "list"
    block_id: Identifier
    entries: Annotated[tuple[ListItem, ...], Field(max_length=MAX_BLOCK_ITEMS)]
    selected_id: Identifier | None = None


class StatusBlock(WireModel):
    """Show a short status message with a semantic tone."""

    kind: Literal["status"] = "status"
    block_id: Identifier
    label: DisplayText
    tone: Tone = "text"
    detail: TextSpan | None = None
