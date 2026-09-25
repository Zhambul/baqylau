# Copyright (c) 2026 Zhambyl Yermagambet
"""Publish the active web views and their immutable asset URLs."""

from typing import Annotated

from baqylau_extension_api.manifest.data import ScopeKind
from baqylau_extension_api.manifest.views import WebSlot, WebViewMode
from baqylau_extension_api.models.base import Digest, ExtensionId, Identifier, NonemptyText, WireModel
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.scopes import ExtensionScope, RepositoryScope
from pydantic import Field

MAX_WEB_VIEWS = 10_000


class WebViewResponse(WireModel):
    """Describe one active view and where its module and styles load from."""

    extension_id: ExtensionId
    package_digest: Digest
    view_id: Identifier
    title: NonemptyText
    slot: WebSlot
    scopes: tuple[ScopeKind, ...]
    mode: WebViewMode
    target: Identifier | None = None
    order: int
    module_url: str
    style_urls: tuple[str, ...] = ()


class WebViewsResponse(WireModel):
    """List the active views of one published runtime."""

    runtime_revision: Identifier
    views: Annotated[tuple[WebViewResponse, ...], Field(max_length=MAX_WEB_VIEWS)]


class ViewSettingsResponse(WireModel):
    """Return the settings that one view receives for its scope in the published runtime."""

    settings_revision: int
    settings: EncodedDocument | None


class RepositoryScopeResponse(WireModel):
    """Give the repository scope of a directory, or none."""

    scope: RepositoryScope | None


class RelatedScopesResponse(WireModel):
    """Name the scopes that one scope relates to, most specific first."""

    scopes: tuple[ExtensionScope, ...]
