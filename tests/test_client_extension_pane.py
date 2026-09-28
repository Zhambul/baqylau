# Copyright (c) 2026 Zhambyl Yermagambet
"""The extension pane paints its view, waits for a stopped daemon, and ends when the view is gone (P07-T02)."""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import TYPE_CHECKING

from tests import client_test_servers, test_client_loading

if TYPE_CHECKING:
    import pytest

pane = test_client_loading.load_shared("_extension_pane")
daemon = client_test_servers.daemon
VIEW_JSON = json.dumps({"view": {"title": "Deployments", "blocks": [
    {"kind": "status", "block_id": "s", "label": "Healthy", "tone": "success"},
]}}).encode()


def target(port: int) -> object:
    """Name the fixture view on one daemon port.

    Returns:
        The pane target.

    """
    return pane.ExtensionPaneTarget("127.0.0.1", port, "test.deploy", "test.deploy.main", '{"kind":"installation"}')


def test_active_view_is_painted(daemon: client_test_servers._Capture, capsys: pytest.CaptureFixture[str]) -> None:
    """A 200 reply paints the view and keeps the pane running."""
    daemon.replies["/api/extension-terminal/views/"] = VIEW_JSON

    assert pane.paint_once(target(daemon.port), pane.PaneState())
    assert "Deployments" in capsys.readouterr().out
    assert "scope=%7B%22kind%22" in daemon.delivery("/api/extension-terminal/views/").path


def test_stopped_daemon_keeps_the_pane(capsys: pytest.CaptureFixture[str]) -> None:
    """With no daemon the pane shows that it waits and keeps running."""
    assert pane.paint_once(target(client_test_servers.free_port()), pane.PaneState())
    assert pane.WAITING in capsys.readouterr().out


def test_removed_view_ends_the_pane(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """A 404 means the view is no longer active: the pane says so and ends, so the terminal closes it."""
    monkeypatch.setattr(pane, "_fetch", lambda *_: (HTTPStatus.NOT_FOUND, b""))

    assert not pane.paint_once(target(1), pane.PaneState())
    assert pane.REMOVED in capsys.readouterr().out


def test_failed_view_keeps_the_pane(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """A 503 means the presenter failed: the pane says so and stays to try again (P07-T04)."""
    monkeypatch.setattr(pane, "_fetch", lambda *_: (HTTPStatus.SERVICE_UNAVAILABLE, b""))

    assert pane.paint_once(target(1), pane.PaneState())
    assert pane.FAILED in capsys.readouterr().out
