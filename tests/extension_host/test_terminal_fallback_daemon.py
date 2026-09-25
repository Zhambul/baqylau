# Copyright (c) 2026 Zhambyl Yermagambet
"""A package changes its terminal layout without a host build, and a failed presenter keeps the pane (P07-T04)."""

from __future__ import annotations

import hashlib
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING

from tests import test_client_loading
from tests.extension_host import package_fixture, process_fixture, test_terminal_view_daemon as terminal_daemon

if TYPE_CHECKING:
    import pytest

    from sdk.client import BaqylauClient

BACKEND = "terminal_backend.py"
OLD_TITLE = b'title="Extension prototype"'
PRESENT_LINE = b"    def present(self, terminal_request: TerminalViewRequest) -> TerminalView:\n"
FAILING_PRESENT = b"""    def present(self, terminal_request: TerminalViewRequest) -> TerminalView:
        raise RuntimeError("private presenter failure")
"""
ROOT = Path(__file__).resolve().parents[2]
HOST_SOURCES = (
    "api", "app", "audit", "client", "core", "dashboard", "domain", "engine", "extensions", "harness", "notify",
    "repository", "terminal",
)
# The built dashboard is the host's web artifact; `node_modules` is a tool cache, not host output.
SKIPPED_PARTS = frozenset(("__pycache__", "node_modules"))
pane = test_client_loading.load_shared("_extension_pane")


def host_digest() -> str:
    """Hash every host source file and the built dashboard, so the test proves that no host file changes.

    Returns:
        One digest of the file paths and bytes.

    """
    digest = hashlib.sha256()
    for path in sorted(host_files()):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def host_files() -> list[Path]:
    """List the host's source files and built dashboard files.

    Returns:
        The file paths.

    """
    found = (path for name in HOST_SOURCES for path in (ROOT / name).rglob("*"))
    return [path for path in found if path.is_file() and SKIPPED_PARTS.isdisjoint(path.parts)]


def replace_backend(client: BaqylauClient, package: Path, old: bytes, new: bytes) -> None:
    """Change the package's own presenter source, then rescan and reload the package."""
    source = (package / BACKEND).read_bytes()
    assert old in source
    package_fixture.write_file(package, BACKEND, source.replace(old, new))
    client.extensions.rescan(client.extensions.catalog().revision)
    request_id = hashlib.sha256(new).hexdigest()
    terminal_daemon.change(client, "reload", request_id)


def pane_output(client: BaqylauClient, capsys: pytest.CaptureFixture[str]) -> str:
    """Paint the pane once against the real daemon.

    Returns:
        The painted text.

    """
    port = client.transport.client.base_url.port
    target = pane.ExtensionPaneTarget(
        "127.0.0.1", port, terminal_daemon.OWNER, terminal_daemon.VIEW_ID, terminal_daemon.INSTALLATION,
    )
    capsys.readouterr()
    assert pane.paint_once(target, pane.PaneState())
    return capsys.readouterr().out


def test_layout_update_needs_no_host_build(
    tmp_path: Path, runtime_wheels: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """A reload of the package's own presenter shows the new layout, and no host file changes (C28)."""
    package = terminal_daemon.install_terminal_package(tmp_path, runtime_wheels)
    before = host_digest()
    with process_fixture.running_catalog(tmp_path) as client:
        terminal_daemon.change(client, "enable")
        assert "Extension prototype" in pane_output(client, capsys)
        replace_backend(client, package, OLD_TITLE, b'title="Updated layout"')
        assert "Updated layout" in pane_output(client, capsys)
    assert host_digest() == before


def test_failed_presenter_keeps_the_pane(
    tmp_path: Path, runtime_wheels: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """A failing presenter gives 503 with a host message, and the pane shows a failure line."""
    package = terminal_daemon.install_terminal_package(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client:
        terminal_daemon.change(client, "enable")
        replace_backend(client, package, PRESENT_LINE, FAILING_PRESENT)
        reply = client.transport.client.get(terminal_daemon.VIEW_PATH)
        assert reply.status_code == HTTPStatus.SERVICE_UNAVAILABLE
        assert "private presenter failure" not in reply.text
        assert pane.FAILED in pane_output(client, capsys)
