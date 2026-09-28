# Copyright (c) 2026 Zhambyl Yermagambet
"""The kit's browser and terminal helpers find an extension's views by stable IDs (P08-T02)."""

from __future__ import annotations

from contextlib import ExitStack
from typing import TYPE_CHECKING

import pytest
from baqylau_extension_testkit.browser import extension_view, workspace_view_url
from baqylau_extension_testkit.isolation import PrivateRoots
from baqylau_extension_testkit.lifecycle import change
from baqylau_extension_testkit.terminal_panes import PaneRequest, terminal_view
from playwright.sync_api import expect, sync_playwright

from tests.extension_host import test_terminal_view_daemon as terminal_daemon, testkit_host_fixture as hosts
from tests.extension_web import view_server

if TYPE_CHECKING:
    from pathlib import Path

TEST_TIMEOUT_SECONDS = 180
PAGE_VIEW = f"{view_server.OWNER}.main"


@pytest.mark.timeout(TEST_TIMEOUT_SECONDS)
def test_browser_finds_the_mounted_view(tmp_path: Path) -> None:
    """A headless browser opens the workspace route and finds the package's view by its ID."""
    roots = PrivateRoots(tmp_path / "web")
    roots.create()
    view_server.write_view_package(roots.packages)
    with ExitStack() as cleanup:
        host = hosts.started(cleanup, roots)
        assert change(host.client, view_server.OWNER, "enable", "kit-web").status == "succeeded"
        browser = cleanup.enter_context(sync_playwright()).chromium.launch()
        cleanup.callback(browser.close)
        page = browser.new_page()
        page.goto(workspace_view_url(host.process.url, "workspace-one", view_server.OWNER, PAGE_VIEW))
        expect(extension_view(page, PAGE_VIEW)).to_contain_text(f"Hello from {view_server.OWNER} in workspace")


@pytest.mark.timeout(TEST_TIMEOUT_SECONDS)
def test_terminal_view_is_the_checked_view(tmp_path: Path, runtime_wheels: Path) -> None:
    """The kit reads the view that a pane paints from a real worker."""
    roots = PrivateRoots(tmp_path / "terminal")
    terminal_daemon.install_terminal_package(roots.root, runtime_wheels)
    request = PaneRequest(
        extension_id=terminal_daemon.OWNER, view_id=terminal_daemon.VIEW_ID,
        scope=terminal_daemon.INSTALLATION, window_id="not-used",
    )
    with ExitStack() as cleanup:
        client = hosts.started(cleanup, roots).client
        assert change(client, terminal_daemon.OWNER, "enable", "kit-terminal").status == "succeeded"
        assert terminal_view(client, request).title == "Extension prototype"
