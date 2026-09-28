# Copyright (c) 2026 Zhambyl Yermagambet
"""Bind terminal presentation to an exact view and data revision."""

from typing import Annotated, Literal

from pydantic import Field

from baqylau_extension_api.models.base import ExtensionId, Identifier, Revision, WireModel
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.scopes import SnapshotCursor
from baqylau_extension_api.terminal.layout import MAX_SECTION_CHILDREN, TerminalBlock
from baqylau_extension_api.terminal.text import DisplayText

MAX_VIEW_ACTIONS = 256
MAX_VIEWPORT_SIZE = 512


class TerminalBinding(WireModel):
    """Keep presentation results attached to one accepted request."""

    extension_id: ExtensionId
    view_id: Identifier
    runtime_revision: Identifier
    settings_revision: Revision
    snapshot: SnapshotCursor


class TerminalViewport(WireModel):
    """Describe available terminal cells, not pixel sizes."""

    columns: Annotated[int, Field(ge=1, le=MAX_VIEWPORT_SIZE)]
    rows: Annotated[int, Field(ge=1, le=MAX_VIEWPORT_SIZE)]


class TerminalSelection(WireModel):
    """Select one block and item without treating navigation as a command."""

    block_id: Identifier
    item_id: Identifier | None = None


class TerminalViewInput(WireModel):
    """Give a view's query the pane's focus, so the query can read details of the focused item."""

    selection: TerminalSelection | None = None


class TerminalViewRequest(WireModel):
    """Pass a recorded snapshot and current display choices to a presenter."""

    binding: TerminalBinding
    viewport: TerminalViewport
    theme: Literal["dark", "light"] = "dark"
    selection: TerminalSelection | None = None
    state: EncodedDocument | None = None
    settings: EncodedDocument | None = None
    document: EncodedDocument | None = None


class TerminalAction(WireModel):
    """Reference a registered command instead of executable shell text."""

    action_id: Identifier
    command_id: Identifier
    label: DisplayText
    arguments: EncodedDocument
    expected_state_revision: Identifier | None = None


class TerminalView(WireModel):
    """Return layout data for one exact host-selected view revision."""

    binding: TerminalBinding
    title: DisplayText
    blocks: Annotated[tuple[TerminalBlock, ...], Field(max_length=MAX_SECTION_CHILDREN)]
    actions: Annotated[tuple[TerminalAction, ...], Field(max_length=MAX_VIEW_ACTIONS)] = ()
