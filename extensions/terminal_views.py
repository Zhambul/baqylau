# Copyright (c) 2026 Zhambyl Yermagambet
"""Find the terminal views that active packages declare for a scope."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from baqylau_extension_api.models.scopes import ExtensionScope

if TYPE_CHECKING:
    from baqylau_extension_api.manifest.views import TerminalView

    from extensions.registry_package import RegistryPackage


class TerminalViewNotFoundError(LookupError):
    """Reject a view that no active package declares for the scope kind."""


class TerminalViewFailedError(RuntimeError):
    """Report that the presenter of a declared view failed or gave a view that is not valid."""


@dataclass(frozen=True)
class AvailableView:
    """Name one view that the pane selector can open in one scope."""

    extension_id: str
    view_id: str
    title: str
    scope: ExtensionScope


def declared_view(package: RegistryPackage, view_id: str) -> TerminalView | None:
    """Find one terminal view of a package manifest.

    Returns:
        The declaration, or None.

    """
    views = package.manifest.contributions.terminal
    return next((view for view in views if view.view_id == view_id), None)


def declaring_view(
    packages: tuple[RegistryPackage, ...], extension_id: str, view_id: str, scope_kind: str,
) -> tuple[RegistryPackage, TerminalView]:
    """Find the active package that declares a view for a scope kind, and the declaration.

    Returns:
        The package and the view.

    Raises:
        TerminalViewNotFoundError: If no active package declares it for the scope kind.

    """
    for package in packages:
        view = declared_view(package, view_id) if package.manifest.extension_id == extension_id else None
        if view is not None and scope_kind in view.scopes:
            return package, view
    message = "no active extension declares this terminal view for the scope"
    raise TerminalViewNotFoundError(message)


def available_views(
    packages: tuple[RegistryPackage, ...], scopes: tuple[ExtensionScope, ...],
) -> tuple[AvailableView, ...]:
    """List every active view that declares one of the scopes, in package and view order.

    Returns:
        The views with the scope each one opens in.

    """
    return tuple(found for package in packages for found in _package_views(package, scopes))


def _package_views(package: RegistryPackage, scopes: tuple[ExtensionScope, ...]) -> tuple[AvailableView, ...]:
    owner = package.manifest.extension_id
    return tuple(
        AvailableView(owner, view.view_id, view.title, scope)
        for view in package.manifest.contributions.terminal
        for scope in scopes
        if scope.kind in view.scopes
    )
