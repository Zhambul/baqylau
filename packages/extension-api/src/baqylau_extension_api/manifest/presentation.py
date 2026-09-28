# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate view slots and immutable asset references."""

from baqylau_extension_api.core.entry_registry import CORE_ENTRY_MODELS
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import rules
from baqylau_extension_api.manifest.metadata import PackageAsset
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.manifest.views import TerminalView, WebView


def validate_views(manifest: ExtensionManifest) -> None:
    """Keep feature modules and styles fully inside declared package assets."""
    views: tuple[WebView | TerminalView, ...] = (*manifest.contributions.web, *manifest.contributions.terminal)
    rules.require_unique((view.view_id for view in views), "view IDs")
    rules.require_owned((view.view_id for view in views), manifest.extension_id)
    for view in views:
        rules.require_unique(view.scopes, "view scopes")
    for web_view in manifest.contributions.web:
        _validate_web_view(web_view, manifest)
    for terminal_view in manifest.contributions.terminal:
        _validate_view_query(terminal_view, manifest)


def _validate_view_query(view: TerminalView, manifest: ExtensionManifest) -> None:
    if view.query is None:
        return
    view_scopes = frozenset(view.scopes)
    for query in manifest.contributions.queries:
        if query.name == view.query and view_scopes <= frozenset(query.scopes):
            return
    message = "a terminal view query must be a declared query for every view scope"
    raise ExtensionContractError(message)


def _validate_web_view(view: WebView, manifest: ExtensionManifest) -> None:
    _require_asset(view.module, manifest.assets, ("text/javascript", "application/javascript"))
    rules.require_unique(view.styles, "view style paths")
    for path in view.styles:
        _require_asset(path, manifest.assets, ("text/css",))
    valid_target = view.slot == "feed" and view.target is not None
    if view.mode == "replace" and not valid_target:
        message = "replacement views require a named feed target"
        raise ExtensionContractError(message)
    own_entries = {definition.name for definition in manifest.contributions.entry_types}
    if view.mode == "replace" and view.target not in CORE_ENTRY_MODELS.keys() | own_entries:
        message = "a feed replacement target must be a core entry kind or an entry type of the package"
        raise ExtensionContractError(message)
    if view.slot == "settings" and manifest.settings is None:
        message = "settings views require a settings definition"
        raise ExtensionContractError(message)


def _require_asset(
    path: str, assets: tuple[PackageAsset, ...], media_types: tuple[str, ...],
) -> None:
    for asset in assets:
        if asset.path == path and asset.media_type in media_types:
            return
    message = "view module or style is not a declared asset with the required media type"
    raise ExtensionContractError(message)
