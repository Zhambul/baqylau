# Copyright (c) 2026 Zhambyl Yermagambet
"""Present a worker's terminal view through the daemon, and refuse it after a disable (P07-T02)."""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from baqylau_extension_api.manifest.contributions import Contributions
from baqylau_extension_api.manifest.views import TerminalView

from tests import test_client_loading
from tests.extension_api import terminal_example
from tests.extension_host import (
    environment_fixture,
    lifecycle_http_fixture as lifecycle,
    package_fixture,
    process_fixture,
)

if TYPE_CHECKING:
    from sdk.client import BaqylauClient

OWNER = package_fixture.OWNER
VIEW_ID = f"{OWNER}.terminal"
PANE_COLUMNS = 80
INSTALLATION = '{"kind":"installation"}'
VIEW_PATH = f"/api/extension-terminal/views/{OWNER}/{VIEW_ID}?scope={INSTALLATION}&columns={PANE_COLUMNS}&rows=24"
terminal_model = test_client_loading.load_shared("_model_terminal")
blocks = test_client_loading.load_shared("_render_extension_blocks")


def install_terminal_package(directory: Path, wheels: Path) -> Path:
    """Install a real worker package whose backend presents every block kind.

    Returns:
        The package directory.

    """
    package = environment_fixture.write_package(directory, wheels)
    manifest = package_fixture.read_manifest(package)
    assert manifest.backend is not None
    package_fixture.save_manifest(package, manifest.model_copy(update={
        "backend": manifest.backend.model_copy(update={"module": "terminal_backend"}),
        "capabilities": ("lifecycle", "terminal"),
        "contributions": Contributions(terminal=(TerminalView(
            view_id=VIEW_ID, title="Example", scopes=("installation",), pane="example",
        ),)),
    }))
    package_fixture.write_file(package, "terminal_backend.py", Path(terminal_example.__file__).read_bytes())
    return package


def change(client: BaqylauClient, action: Literal["enable", "disable", "reload"], request_id: str = "") -> None:
    """Enable, disable, or reload the package and wait for the operation."""
    request = lifecycle.lifecycle_request(client, OWNER, action, request_id or f"terminal-{action}")
    admitted = client.extensions.lifecycle.change(OWNER, request)
    assert lifecycle.wait_operation(client, admitted.operation.operation_id).status == "succeeded"


def test_view_presented_until_disable(tmp_path: Path, runtime_wheels: Path) -> None:
    """The pane client paints the worker's view; after a disable the host answers 404, which ends the pane."""
    install_terminal_package(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client:
        change(client, "enable")
        reply = client.transport.client.get(VIEW_PATH)
        assert reply.status_code == HTTPStatus.OK
        view = terminal_model.TerminalViewReply.model_validate_json(reply.content).view
        assert view.title == "Extension prototype"
        assert any("Recorded data" in row for row in blocks.view_rows(view, PANE_COLUMNS))
        offered = client.transport.client.get("/api/extension-terminal/views").json()["views"]
        pairs = [(entry["view_id"], entry["scope"]) for entry in offered]
        assert pairs == [(VIEW_ID, INSTALLATION)]
        change(client, "disable")
        assert client.transport.client.get(VIEW_PATH).status_code == HTTPStatus.NOT_FOUND
