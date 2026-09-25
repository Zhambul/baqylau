# Copyright (c) 2026 Zhambyl Yermagambet
"""A terminal view that names a query shows the query's result, read with the pane's focus (P10-T02)."""

from __future__ import annotations

import hashlib
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING

from baqylau_extension_api.manifest import contributions as manifest_contributions, operations, views
from baqylau_extension_api.models.documents import SchemaDefinition, SchemaRef

from tests import test_client_loading
from tests.extension_api import terminal_query_example as example
from tests.extension_host import (
    environment_fixture,
    lifecycle_http_fixture as lifecycle,
    package_fixture,
    process_fixture,
)

if TYPE_CHECKING:
    from baqylau_extension_api.manifest.data import ScopeKinds

    from sdk.client import BaqylauClient

OWNER = package_fixture.OWNER
VIEW_ID = f"{OWNER}.focus"
QUERY_ID = f"{OWNER}.focus"
SCOPES: ScopeKinds = ("installation",)
COLUMNS = 80
VIEW_PATH = f'/api/extension-terminal/views/{OWNER}/{VIEW_ID}?scope={{"kind":"installation"}}&columns={COLUMNS}&rows=24'
terminal_model = test_client_loading.load_shared("_model_terminal")
blocks = test_client_loading.load_shared("_render_extension_blocks")


def schema(name: str, encoded: str) -> SchemaDefinition:
    """Declare one package schema with the digest of its bytes.

    Returns:
        The schema.

    """
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    reference = SchemaRef(owner=OWNER, name=name, version=1, digest=digest)
    return SchemaDefinition(reference=reference, json_text=encoded)


ARGUMENTS = schema("view-input", example.ARGUMENTS_SCHEMA)
RESULT = schema(example.RESULT_NAME, example.RESULT_SCHEMA)


def declarations() -> manifest_contributions.Contributions:
    """Declare the focus query and the terminal view that names it.

    Returns:
        The contributions.

    """
    arguments, result = ARGUMENTS.reference, RESULT.reference
    query = operations.QueryDefinition(name=QUERY_ID, scopes=SCOPES, arguments=arguments, result=result)
    view = views.TerminalView(
        view_id=VIEW_ID, title="Focus", scopes=SCOPES, pane="focus", query=QUERY_ID,
    )
    return manifest_contributions.Contributions(queries=(query,), terminal=(view,))


def install(directory: Path, wheels: Path) -> None:
    """Install a real worker whose terminal view names its focus query."""
    package = environment_fixture.write_package(directory, wheels)
    manifest = package_fixture.read_manifest(package)
    assert manifest.backend is not None
    package_fixture.save_manifest(package, manifest.model_copy(update={
        "backend": manifest.backend.model_copy(update={"module": "terminal_query_backend"}),
        "capabilities": ("lifecycle", "queries", "terminal"),
        "schemas": (*manifest.schemas, ARGUMENTS, RESULT),
        "contributions": declarations(),
    }))
    package_fixture.write_file(package, "terminal_query_backend.py", Path(example.__file__).read_bytes())


def enable(client: BaqylauClient) -> None:
    """Enable the package and wait for the operation."""
    request = lifecycle.lifecycle_request(client, OWNER, "enable", "query-view-enable")
    admitted = client.extensions.lifecycle.change(OWNER, request)
    assert lifecycle.wait_operation(client, admitted.operation.operation_id).status == "succeeded"


def view_rows(client: BaqylauClient, focus: str = "") -> list[str]:
    """Read the view through the daemon, with an optional focus query.

    Returns:
        The painted rows.

    """
    reply = client.transport.client.get(VIEW_PATH + focus)
    assert reply.status_code == HTTPStatus.OK, reply.text
    view = terminal_model.TerminalViewReply.model_validate_json(reply.content).view
    return list(blocks.view_rows(view, COLUMNS))


def test_view_shows_its_query_result(tmp_path: Path, runtime_wheels: Path) -> None:
    """The presenter gets the query's document; a new focus reads the query again."""
    install(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client:
        enable(client)

        unfocused = view_rows(client)
        focused = view_rows(client, "&block=list&item=second")

        assert any('document "none"' in row for row in unfocused)
        assert any('document "second"' in row for row in focused)
