# Copyright (c) 2026 Zhambyl Yermagambet
"""Send native hooks to a private host, change the card package, and open a session in a headless browser."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from playwright.sync_api import Page, sync_playwright

from tests import control_effect_native_payload as native_payload, terminal_pty_waits
from tests.extension_host import card_package_fixture as cards, lifecycle_http_fixture as lifecycle

if TYPE_CHECKING:
    from contextlib import ExitStack

    from sdk.client import BaqylauClient

OWNER = cards.OWNER
SESSION = "session-one"
HOOK_HEADERS = (("Content-Type", "application/json"), ("X-Baqylau", "1"))


def post_hook(client: BaqylauClient, event_name: str, event_id: str) -> None:
    """Send one native hook, then wait until the host has a verdict for every raw event."""
    reply = client.transport.client.post(
        "/api/harnesses/claude_code/hooks", content=native_payload.hook_payload(event_name, event_id),
        headers=HOOK_HEADERS,
    )
    assert reply.is_success
    terminal_pty_waits.wait_until(lambda: client.diagnostics.checkpoint().pending_raw_event_count == 0)


def change(client: BaqylauClient, action: Literal["enable", "disable"], request_id: str) -> None:
    """Change the card package and wait for success."""
    request = lifecycle.lifecycle_request(client, OWNER, action, request_id)
    admitted = client.extensions.lifecycle.change(OWNER, request)
    assert lifecycle.wait_operation(client, admitted.operation.operation_id).status == "succeeded"


def session_page(cleanup: ExitStack, client: BaqylauClient) -> Page:
    """Open the session's feed in a headless browser.

    Returns:
        The page.

    """
    browser = cleanup.enter_context(sync_playwright()).chromium.launch()
    cleanup.callback(browser.close)
    page = browser.new_page()
    host_url = str(client.transport.client.base_url)
    page.goto(f"{host_url}#/s/{SESSION}")
    return page
