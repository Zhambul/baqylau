# Copyright (c) 2026 Zhambyl Yermagambet
"""Run a presented view's action as its registered command, and refuse a write under read-only policy (P07-T03)."""

from __future__ import annotations

import hashlib
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from baqylau_extension_api.manifest import contributions, operations, views
from baqylau_extension_api.models.documents import SchemaDefinition, SchemaRef
from baqylau_extension_api.models.scopes import InstallationScope

from tests import terminal_pty_waits
from tests.extension_api import terminal_action_example as example
from tests.extension_host import (
    environment_fixture,
    lifecycle_http_fixture as lifecycle,
    package_fixture,
    process_fixture,
)

if TYPE_CHECKING:
    from sdk.client import BaqylauClient

OWNER = package_fixture.OWNER
VIEW_ID = f"{OWNER}.files"
INSTALLATION = InstallationScope()


def install(directory: Path, wheels: Path) -> None:
    """Install a real worker whose list entries name a read command and a write command."""
    package = environment_fixture.write_package(directory, wheels)
    manifest = package_fixture.read_manifest(package)
    assert manifest.backend is not None
    digest = hashlib.sha256(example.TEXT_SCHEMA.encode()).hexdigest()
    schema = SchemaDefinition(
        reference=SchemaRef(owner=OWNER, name="text", version=1, digest=digest), json_text=example.TEXT_SCHEMA,
    )
    commands = (
        operations.CommandDefinition(
            name=f"{OWNER}.show", scopes=("installation",), arguments=schema.reference, result=schema.reference,
            effect="read", reconciliation=False,
        ),
        operations.CommandDefinition(
            name=f"{OWNER}.write", scopes=("installation",), arguments=schema.reference, result=schema.reference,
            effect="write", reconciliation=True,
        ),
    )
    package_fixture.save_manifest(package, manifest.model_copy(update={
        "backend": manifest.backend.model_copy(update={"module": "terminal_backend"}),
        "capabilities": ("lifecycle", "terminal", "commands"),
        "schemas": (schema,),
        "contributions": contributions.Contributions(commands=commands, terminal=(views.TerminalView(
            view_id=VIEW_ID, title="Files", scopes=("installation",), pane="files",
        ),)),
    }))
    package_fixture.write_file(package, "terminal_backend.py", Path(example.__file__).read_bytes())


def act(client: BaqylauClient, action_id: str, item_id: str) -> tuple[int, object]:
    """Run one action of the focused item.

    Returns:
        The HTTP status and reply body.

    """
    reply = client.transport.client.post("/api/extension-terminal/actions", json={
        "extension_id": OWNER, "view_id": VIEW_ID, "action_id": action_id,
        "scope": INSTALLATION.model_dump_json(), "columns": 80, "rows": 24,
        "block_id": "files", "item_id": item_id, "request_key": f"key-{action_id}",
    })
    return reply.status_code, reply.json()


def enable(client: BaqylauClient, action: Literal["enable"] = "enable") -> None:
    """Enable the package and wait for the operation."""
    request = lifecycle.lifecycle_request(client, OWNER, action, "action-enable")
    admitted = client.extensions.lifecycle.change(OWNER, request)
    assert lifecycle.wait_operation(client, admitted.operation.operation_id).status == "succeeded"


def restored(client: BaqylauClient) -> bool:
    """Check that the restarted daemon has restored the enabled package.

    Returns:
        True when the package is active again.

    """
    directory = client.extensions.lifecycle.state().directory
    entries = () if directory is None else directory.entries
    enabled = [entry.extension_info.extension_id for entry in entries if entry.state == "enabled"]
    return OWNER in enabled


def test_action_runs_its_command(tmp_path: Path, runtime_wheels: Path) -> None:
    """The host finds the action in the view it presents, runs its command, and refuses an unknown action."""
    install(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client:
        enable(client)
        status, job = act(client, "show", "readme")
        assert status == HTTPStatus.ACCEPTED
        assert isinstance(job, dict)
        job_id = str(job["job_id"])
        terminal_pty_waits.wait_until(
            lambda: client.extensions.jobs.read(OWNER, job_id, INSTALLATION).state == "succeeded",
        )
        assert act(client, "missing", "readme")[0] == HTTPStatus.NOT_FOUND
    with process_fixture.running_catalog(tmp_path, read_only=True) as client:
        terminal_pty_waits.wait_until(lambda: restored(client))
        assert act(client, "write", "notes")[0] == HTTPStatus.FORBIDDEN
        assert act(client, "show", "readme")[0] == HTTPStatus.ACCEPTED
