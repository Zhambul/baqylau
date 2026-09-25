# Copyright (c) 2026 Zhambyl Yermagambet
"""Declare extension-owned web and terminal presentation."""

from typing import Annotated, Literal

from pydantic import Field

from baqylau_extension_api.manifest.data import ScopeKinds
from baqylau_extension_api.models.base import Identifier, NonemptyText, WireModel
from baqylau_extension_api.paths import RelativePath

type WebSlot = Literal[
    "feed", "session_tab", "workspace_page", "toolbar", "status", "settings", "mirror", "scoreboard",
]
type WebViewMode = Literal["add", "replace"]


class WebView(WireModel):
    """Mount an independent module in one supported host slot."""

    view_id: Identifier
    title: NonemptyText
    slot: WebSlot
    scopes: ScopeKinds
    module: RelativePath
    styles: Annotated[tuple[RelativePath, ...], Field(max_length=100)] = ()
    mode: WebViewMode = "add"
    target: Identifier | None = None
    order: Annotated[int, Field(ge=-1000, le=1000)] = 0


class TerminalView(WireModel):
    """Register a worker-owned block layout in a named terminal pane.

    A view that names one of the package's queries gets that query's result as the presenter's document.
    The host runs the query with a `TerminalViewInput` for each presentation, because a pure presenter
    cannot read live data.
    """

    view_id: Identifier
    title: NonemptyText
    scopes: ScopeKinds
    pane: Identifier
    query: Identifier | None = None
