# Copyright (c) 2026 Zhambyl Yermagambet
"""Publish extension pane requests and the views that the pane selector offers."""

from typing import Annotated

from baqylau_extension_api.models.base import ExtensionId, Identifier, WireModel
from pydantic import Field


class ExtensionPaneRequest(WireModel):
    """Open one extension view in a pane beside a terminal window."""

    extension_id: ExtensionId
    view_id: Identifier
    scope: Annotated[str, Field(min_length=1, description="JSON-encoded ExtensionScope")]
    window_id: Annotated[str, Field(min_length=1)]
    working_directory: str = ""


class ExtensionPaneResponse(WireModel):
    """Tell whether a new pane opened, or an existing pane took focus."""

    opened: bool
    focused: bool
    window_id: str | None = None
    reason: str | None = None


class AvailableViewResponse(WireModel):
    """Name one view that the pane selector can open, with the scope it opens in."""

    extension_id: ExtensionId
    view_id: Identifier
    title: str
    scope: str


class AvailableViewsResponse(WireModel):
    """List the views that the pane selector offers for one window."""

    views: tuple[AvailableViewResponse, ...]
