# Copyright (c) 2026 Zhambyl Yermagambet
"""Open an extension pane in a real Kitty window, resize it, reopen it, reload it, and disable it (C20).

The test opens its own tab in the running Kitty, and closes only that tab.
It needs `CLAUDE_E2E_KITTY=1` and a Kitty remote-control socket.
"""

from __future__ import annotations

import dataclasses
from contextlib import ExitStack
from http import HTTPStatus
from typing import TYPE_CHECKING, Literal

import pytest

from api.extensions.terminal_pane_models import ExtensionPaneResponse
from sdk.client import BaqylauClient
from tests.e2e.testkit.process import ApplicationProcess
from tests.extension_host import (
    kitty_fixture as kitty,
    lifecycle_http_fixture as lifecycle,
    process_fixture,
    test_terminal_query_daemon as query_view,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

CASE_SECONDS = 240
pytestmark = [pytest.mark.kitty, pytest.mark.timeout(CASE_SECONDS)]

OWNER = query_view.OWNER
DRAWN = 'document "none"'
anchor_window = kitty.anchor_window


@pytest.fixture
def kitty_host(tmp_path: Path, runtime_wheels: Path) -> Iterator[BaqylauClient]:
    """Start a private host that uses the running Kitty, with the query view package installed.

    Yields:
        The client.

    """
    kitty.require_kitty()
    query_view.install(tmp_path, runtime_wheels)
    config = dataclasses.replace(process_fixture.application_config(tmp_path), terminal="kitty")
    process = ApplicationProcess.start(config)
    with ExitStack() as cleanup:
        cleanup.callback(process.stop)
        client = BaqylauClient(process.endpoint.url)
        cleanup.callback(client.close)
        client.application.wait_until_ready()
        yield client


def open_pane(client: BaqylauClient, anchor: str) -> ExtensionPaneResponse:
    """Ask the host to open the view beside the anchor window.

    Returns:
        The reply.

    """
    body = {
        "extension_id": OWNER, "view_id": query_view.VIEW_ID, "scope": '{"kind":"installation"}', "window_id": anchor,
    }
    reply = client.transport.client.post("/api/extension-terminal/panes", json=body)
    assert reply.status_code == HTTPStatus.OK, reply.text
    return ExtensionPaneResponse.model_validate_json(reply.content)


def changed(client: BaqylauClient, action: Literal["reload", "disable"]) -> None:
    """Change the package's lifecycle, and require that the change succeeds."""
    request = lifecycle.lifecycle_request(client, OWNER, action, f"c20-{action}")
    operation = client.extensions.lifecycle.change(OWNER, request).operation
    assert lifecycle.wait_operation(client, operation.operation_id).status == "succeeded"


def wait_drawn(pane: str, what: str) -> None:
    """Wait until the pane shows the view's document."""
    kitty.wait_for_text(pane, lambda text: DRAWN in text, what)


def test_extension_pane_in_kitty(kitty_host: BaqylauClient, anchor_window: str) -> None:
    """The pane draws the view, redraws after a resize, takes focus on a reopen, and follows reload and disable."""
    query_view.enable(kitty_host)

    opened = open_pane(kitty_host, anchor_window)
    assert opened.opened, opened
    pane = str(opened.window_id)
    wait_drawn(pane, "the pane did not draw the view")
    kitty.kitten("resize-window", "--match", f"id:{pane}", "--increment", "-10", "--axis", "horizontal")
    wait_drawn(pane, "the pane did not draw again after a resize")

    reopened = open_pane(kitty_host, anchor_window)
    assert (reopened.opened, reopened.focused) == (False, True)
    changed(kitty_host, "reload")
    wait_drawn(pane, "the pane did not draw after a reload")

    changed(kitty_host, "disable")
    kitty.wait_for_text(pane, lambda text: DRAWN not in text, "the pane still drew the disabled view")
