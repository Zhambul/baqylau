# Copyright (c) 2026 Zhambyl Yermagambet
"""List the web views of the enabled packages in one published runtime, in a stable order."""

from dataclasses import dataclass

from baqylau_extension_api.manifest.views import WebView

from extensions.registry_snapshot import RuntimeSnapshot


@dataclass(frozen=True)
class ActiveWebView:
    """Bind one declared web view to its package identity."""

    extension_id: str
    package_digest: str
    view: WebView


def active_web_views(snapshot: RuntimeSnapshot) -> tuple[ActiveWebView, ...]:
    """Select the views of enabled packages, ordered by slot, order, owner, and view ID.

    Returns:
        The active web views.

    """
    views = (
        ActiveWebView(package.manifest.extension_id, package.entry.extension_info.package_digest, view)
        for package in snapshot.packages if package.entry.state == "enabled"
        for view in package.manifest.contributions.web
    )
    return tuple(sorted(views, key=_order))


def _order(active: ActiveWebView) -> tuple[str, int, str, str]:
    return (active.view.slot, active.view.order, active.extension_id, active.view.view_id)
