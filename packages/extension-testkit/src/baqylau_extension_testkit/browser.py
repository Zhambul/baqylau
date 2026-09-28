# Copyright (c) 2026 Zhambyl Yermagambet
"""Open dashboard routes of extension views and find a mounted view by its stable ID.

The routes are the dashboard's own hash routes. The web SDK marks each view's
root with `data-extension-view`, and a view ID starts with its owner, so the
locator does not depend on a title or on package markup. Playwright locators
see into the view's open shadow root.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


def workspace_view_url(host_url: str, workspace_id: str, extension_id: str, view_id: str) -> str:
    """Build the route of one workspace page view.

    Returns:
        The page URL.

    """
    return f"{host_url}/#/w/{_segment(workspace_id)}{_view_path(extension_id, view_id)}"


def repository_view_url(host_url: str, directory: str, extension_id: str, view_id: str) -> str:
    """Build the route of one repository page view; the page asks the host for the directory's repository.

    Returns:
        The page URL.

    """
    return f"{host_url}/#/repo/{_segment(directory)}{_view_path(extension_id, view_id)}"


def session_view_url(host_url: str, session_id: str, extension_id: str, view_id: str) -> str:
    """Build the route of one session tab view.

    Returns:
        The page URL.

    """
    return f"{host_url}/#/s/{_segment(session_id)}{_view_path(extension_id, view_id)}"


def settings_url(host_url: str, extension_id: str) -> str:
    """Build the route of one extension's settings page.

    Returns:
        The page URL.

    """
    return f"{host_url}/#/settings/extensions/{_segment(extension_id)}"


def extension_view(page: Page, view_id: str) -> Locator:
    """Find the mounted root of one extension view.

    Returns:
        The locator of the view's root.

    """
    return page.locator(f'[data-extension-view="{view_id}"]')


def _view_path(extension_id: str, view_id: str) -> str:
    return f"/x/{_segment(extension_id)}/{_segment(view_id)}"


def _segment(name: str) -> str:
    return quote(name, safe="")
