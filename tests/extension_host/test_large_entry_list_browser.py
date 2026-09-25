# Copyright (c) 2026 Zhambyl Yermagambet
"""A large list of extension entries is paged in the session feed, and its oldest entry loads on request (P06-T06).

The feed pages by canonical fact. One fact's entries come in one page, and the
SDK bounds them (at most 1,000 rows for each projection), so a large list is
many facts: here, one card for each of many finished turns.
"""

from __future__ import annotations

from contextlib import ExitStack, suppress
from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import Error as PlaywrightError, Page, expect

from tests.extension_api import session_card_example as example
from tests.extension_host import card_package_fixture as cards, process_fixture, session_browser_fixture as browsing

if TYPE_CHECKING:
    from pathlib import Path

TURNS = 120
TEST_TIMEOUT_SECONDS = 180
LOAD_READS = 100
LOAD_MILLISECONDS = 30_000
SCROLL_MILLISECONDS = 2_000


def load_all_older(page: Page) -> None:
    """Scroll to the feed's load sentinel until no older page is left.

    Raises:
        AssertionError: If older pages are still left after the read bound.

    """
    for _ in range(LOAD_READS):
        sentinel = page.locator(".load-sentinel")
        if not sentinel.count():
            return
        # The sentinel leaves the page while a page loads; the next read finds the new one.
        with suppress(PlaywrightError):
            sentinel.scroll_into_view_if_needed(timeout=SCROLL_MILLISECONDS)
        expect(page.locator(".feed-loader")).to_have_count(0, timeout=LOAD_MILLISECONDS)
    message = "the feed still has an older page"
    raise AssertionError(message)


@pytest.mark.timeout(TEST_TIMEOUT_SECONDS)
def test_large_entry_list_is_paged(tmp_path: Path, runtime_wheels: Path) -> None:
    """Many finished turns give many cards; the page shows the newest without all of them, and loads the oldest."""
    cards.install_cards(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client, ExitStack() as cleanup:
        browsing.change(client, "enable", "enable-cards")
        browsing.post_hook(client, "SessionStart", "warm-up")
        for turn in range(TURNS):
            browsing.post_hook(client, "Stop", f"stop-{turn}")
        page = browsing.session_page(cleanup, client)
        expect(page.get_by_text(example.SUMMARY).last).to_be_visible()

        assert page.get_by_text(example.SUMMARY).count() < TURNS
        load_all_older(page)
        expect(page.get_by_text(example.SUMMARY)).to_have_count(TURNS)
