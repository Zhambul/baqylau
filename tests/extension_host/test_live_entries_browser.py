# Copyright (c) 2026 Zhambyl Yermagambet
"""An open session page shows a projector's feed cards when they are committed (C13, C14, P08-T03).

A projected entry keeps the canonical cursor of its fact, and the core entry of
the same fact has already moved the client's stream cursor to that value. The
stream also follows the entry rows, so it sends the late entry.
"""

from __future__ import annotations

from contextlib import ExitStack
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from tests import terminal_pty_waits
from tests.extension_api import session_card_example as example
from tests.extension_host import (
    card_package_fixture as cards,
    lifecycle_daemon_fixture as fixture,
    process_fixture,
    session_browser_fixture as stored_entries,
)

if TYPE_CHECKING:
    from pathlib import Path

    from sdk.client import BaqylauClient

JSON_HEADERS = (("Content-Type", "application/json"),)
TEST_TIMEOUT_SECONDS = 180
LIVE_SECONDS_MS = 10000
STREAM_OPEN_MS = 2000


def finished_session(client: BaqylauClient) -> None:
    """Record a whole session with one finished turn."""
    for name, identity in (("SessionStart", "start-one"), ("Stop", "stop-one"), ("SessionEnd", "end-one")):
        fixture.deliver_hook(client, fixture.hook(name, identity))
    terminal_pty_waits.wait_until(lambda: client.diagnostics.checkpoint().pending_raw_event_count == 0)


def ready_candidate(client: BaqylauClient) -> str:
    """Replay the session into a candidate history with the active runtime, and wait until it is ready.

    Returns:
        The candidate's history revision.

    """
    http = client.transport.client
    started = http.post(f"/api/history/sessions/{fixture.SESSION_ID}/reprocess", headers=JSON_HEADERS)
    assert started.status_code == HTTPStatus.ACCEPTED, started.text
    revision = str(started.json()["history_revision"])
    path = f"/api/history/candidates/{revision}"
    terminal_pty_waits.wait_until(lambda: candidate_state(client, path) == "ready")
    return revision


def candidate_state(client: BaqylauClient, path: str) -> str:
    """Read a candidate history's state.

    Returns:
        The state.

    """
    return str(client.transport.client.get(path).json()["state"])


def session_page(cleanup: ExitStack, client: BaqylauClient) -> Page:
    """Open the session's feed in a headless browser.

    Returns:
        The page.

    """
    browser = cleanup.enter_context(sync_playwright()).chromium.launch()
    cleanup.callback(browser.close)
    page = browser.new_page()
    host_url = str(client.transport.client.base_url)
    page.goto(f"{host_url}#/s/{fixture.SESSION_ID}")
    return page


@pytest.mark.timeout(TEST_TIMEOUT_SECONDS)
def test_open_page_shows_a_new_card(tmp_path: Path, runtime_wheels: Path) -> None:
    """A card for a turn that finishes while the page is open shows without a reload."""
    cards.install_cards(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client, ExitStack() as cleanup:
        stored_entries.change(client, "enable", "enable-cards")
        # A new projector starts at the canonical head on its first pass, so this hook only starts it.
        stored_entries.post_hook(client, "SessionStart", "warm-up")
        page = session_page(cleanup, client)
        expect(page.get_by_role("button", name=fixture.SESSION_ID)).to_be_visible()
        # The page has no signal for an open stream; this wait lets it connect before the turn finishes.
        page.wait_for_timeout(STREAM_OPEN_MS)
        stored_entries.post_hook(client, "Stop", "stop-one")
        expect(page.get_by_text(example.SUMMARY)).to_be_visible(timeout=LIVE_SECONDS_MS)


@pytest.mark.timeout(TEST_TIMEOUT_SECONDS)
def test_open_page_follows_the_switch(tmp_path: Path, runtime_wheels: Path) -> None:
    """The open page shows no card before the switch, and the replayed session's card after it.

    The card projector is enabled after the session finished, so it starts at the canonical head. The switch
    resets the session's projector cursors, so the replayed session's card comes after the switch.
    """
    cards.install_cards(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client, ExitStack() as cleanup:
        finished_session(client)
        stored_entries.change(client, "enable", "enable-cards")
        revision = ready_candidate(client)
        page = session_page(cleanup, client)
        expect(page.get_by_text(example.SUMMARY)).to_have_count(0)
        switched = client.transport.client.post(f"/api/history/candidates/{revision}/activate", headers=JSON_HEADERS)
        assert switched.status_code == HTTPStatus.ACCEPTED, switched.text
        expect(page.get_by_text(example.SUMMARY)).to_be_visible(timeout=LIVE_SECONDS_MS)
