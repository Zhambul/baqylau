# Copyright (c) 2026 Zhambyl Yermagambet
"""A package decorates the feed of a real session, and a disable while the page is open removes it (P06-T03, C19).

The package also declares a replacement for the finished-turn row. A session made
only of hooks has no displayed core row, because its transcript is read only in
a terminal window, so the replacement row belongs to the live-harness browser
suite; here the catalog must still accept both views together.
"""

from __future__ import annotations

import hashlib
from contextlib import ExitStack
from typing import TYPE_CHECKING, Literal

import pytest
from baqylau_extension_api.manifest.metadata import PackageAsset
from baqylau_extension_api.manifest.views import WebView
from playwright.sync_api import expect

from tests.extension_host import (
    package_fixture,
    process_fixture,
    session_browser_fixture as stored_entries,
    test_live_entries_browser as live_entries,
)

if TYPE_CHECKING:
    from pathlib import Path

OWNER = package_fixture.OWNER
MODULE_PATH = "web/feed.js"
REPLACED = "Replaced turn row"
DECORATION = "Feed decoration"
MODULE_TEXT = """export function mount(target, context) {
  const line = target.ownerDocument.createElement('p');
  line.textContent = context.viewId.endsWith('.turn') ? 'REPLACED' : 'DECORATION';
  target.append(line);
  return { update() {}, dispose() { line.remove(); } };
}
"""
MODULE_SOURCE = MODULE_TEXT.replace("REPLACED", REPLACED).replace("DECORATION", DECORATION).encode()
MOUNT_MS = 15000
# An open page learns of a disable at the web view catalog's 15-second refresh.
REFRESH_MS = 30000
TEST_TIMEOUT_SECONDS = 180


def feed_view(
    name: str, mode: Literal["add", "replace"] = "add", target: str | None = None,
) -> WebView:
    """Declare one session feed view of the package's module.

    Returns:
        The view.

    """
    return WebView(
        view_id=f"{OWNER}.{name}", title=name.title(), slot="feed", scopes=("session",),
        module=MODULE_PATH, mode=mode, target=target,
    )


def install_feed_views(directory: Path) -> None:
    """Write a web-only package with a replacement for the finished-turn row and one feed decoration."""
    package = package_fixture.write_package(directory / "packages", OWNER, web=True)
    manifest = package_fixture.read_manifest(package)
    views = (feed_view("turn", mode="replace", target="turn_finished"), feed_view("decoration"))
    package_fixture.save_manifest(package, manifest.model_copy(update={
        "assets": (PackageAsset(
            path=MODULE_PATH, digest=hashlib.sha256(MODULE_SOURCE).hexdigest(), media_type="text/javascript",
        ),),
        "contributions": manifest.contributions.model_copy(update={"web": views}),
    }))
    package_fixture.write_file(package, MODULE_PATH, MODULE_SOURCE)


@pytest.mark.timeout(TEST_TIMEOUT_SECONDS)
def test_feed_views_follow_the_package(tmp_path: Path) -> None:
    """The feed shows the decoration; after a disable the open page removes it without a reload."""
    install_feed_views(tmp_path)
    with process_fixture.running_catalog(tmp_path) as client, ExitStack() as cleanup:
        stored_entries.change(client, "enable", "enable-feed")
        stored_entries.post_hook(client, "SessionStart", "start-one")
        stored_entries.post_hook(client, "Stop", "stop-one")
        page = live_entries.session_page(cleanup, client)
        expect(page.get_by_text(DECORATION)).to_be_visible(timeout=MOUNT_MS)
        stored_entries.change(client, "disable", "disable-feed")
        expect(page.get_by_text(DECORATION)).to_have_count(0, timeout=REFRESH_MS)
