# Copyright (c) 2026 Zhambyl Yermagambet
"""Publish extension terminal views and pane requests."""

from enum import StrEnum
from typing import Annotated

from baqylau_extension_api.models.base import ExtensionId, Identifier, WireModel
from baqylau_extension_api.terminal.models import MAX_VIEWPORT_SIZE, TerminalView
from pydantic import Field


class TerminalViewResponse(WireModel):
    """Return one checked terminal view for the pane client to paint."""

    view: TerminalView


class PaneSectionsResponse(WireModel):
    """Return the extension sections of one core pane and the titles of views that failed."""

    views: tuple[TerminalView, ...]
    unavailable: tuple[str, ...]


class CorePane(StrEnum):
    """Name a core pane that shows extension sections."""

    MIRROR = "mirror"
    SCOREBOARD = "scoreboard"


class ExtensionActionRequest(WireModel):
    """Run one action of a presented view; the host finds the registered command itself."""

    extension_id: ExtensionId
    view_id: Identifier
    action_id: Identifier
    scope: Annotated[str, Field(min_length=1, description="JSON-encoded ExtensionScope")]
    columns: Annotated[int, Field(ge=1, le=MAX_VIEWPORT_SIZE)]
    rows: Annotated[int, Field(ge=1, le=MAX_VIEWPORT_SIZE)]
    block_id: Identifier | None = None
    item_id: Identifier | None = None
    request_key: Annotated[str, Field(min_length=1)]
