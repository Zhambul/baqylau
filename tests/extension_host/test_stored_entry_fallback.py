# Copyright (c) 2026 Zhambyl Yermagambet
"""A disabled extension's stored feed entries still show, with no worker code (C14, P08-T03)."""

from __future__ import annotations

from contextlib import ExitStack
from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import expect

from tests.extension_api import session_card_example as example
from tests.extension_host import card_package_fixture as cards, process_fixture, session_browser_fixture as browsing

if TYPE_CHECKING:
    from pathlib import Path

TEST_TIMEOUT_SECONDS = 180


@pytest.mark.timeout(TEST_TIMEOUT_SECONDS)
def test_stored_card_shows_after_disable(tmp_path: Path, runtime_wheels: Path) -> None:
    """The session feed shows the card while enabled, and the same stored card after the package is disabled."""
    cards.install_cards(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client, ExitStack() as cleanup:
        browsing.change(client, "enable", "enable-cards")
        # A new projector starts at the canonical head on its first pass, so this hook only starts it.
        browsing.post_hook(client, "SessionStart", "warm-up")
        browsing.post_hook(client, "Stop", "stop-one")
        page = browsing.session_page(cleanup, client)
        expect(page.get_by_text(example.SUMMARY)).to_be_visible()
        browsing.change(client, "disable", "disable-cards")
        page.reload()
        expect(page.get_by_text(example.SUMMARY)).to_be_visible()
