# Copyright (c) 2026 Zhambyl Yermagambet
"""Run one declared extension query through the HTTP route."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from baqylau_extension_api.models.scopes import InstallationScope

from app import provider_extension_registry
from extensions.registry_contract import RegistryRead
from tests import http_test_assets, http_test_controls, http_test_server_runtime
from tests.extension_api import service_samples
from tests.extension_host import registry_fixture

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.http_test_pane_models import RunningDaemon

OWNER = service_samples.ALPHA
QUERY_ID = f"{OWNER}.read"
ARGUMENTS = '"fixture"'
SCOPE = InstallationScope()
REGISTRY_REVISION = 1
OK_STATUS = 200
BAD_REQUEST_STATUS = 400


class FakeRegistry:
    """Borrow one prepared registry read for the request path."""

    def __init__(self, read: RegistryRead) -> None:
        """Store the prepared read."""
        self.read = read

    def read_snapshot(self) -> contextlib.AbstractContextManager[RegistryRead]:
        """Borrow the prepared read.

        Returns:
            A context manager yielding the prepared read.

        """
        return contextlib.nullcontext(self.read)


@contextlib.contextmanager
def query_server() -> Iterator[RunningDaemon]:
    """Run the test server with one active package that declares queries.

    Yields:
        The running server.

    """
    package = registry_fixture.peer(OWNER)
    registry = FakeRegistry(RegistryRead(revision=REGISTRY_REVISION, snapshot=registry_fixture.snapshot(package)))
    with http_test_assets.running_server(
        http_test_server_runtime.application(), (provider_extension_registry.extension_registry, registry),
    ) as server:
        yield server


def test_query_route_returns_typed_result() -> None:
    """The query route resolves the active package and returns its typed result."""
    body: dict[str, http_test_server_runtime.JsonValue] = {
        "scope": SCOPE.model_dump_json(), "arguments": ARGUMENTS,
    }

    with query_server() as server:
        status, response = http_test_controls.post(server, f"/api/extensions/{OWNER}/queries/{QUERY_ID}", body)

    document = response.json
    assert status == OK_STATUS
    assert document["status"] == "ready"
    assert document["binding"]["extension_id"] == OWNER


def test_query_route_rejects_an_undeclared_query() -> None:
    """A query id the active package never declared is a client error."""
    body: dict[str, http_test_server_runtime.JsonValue] = {
        "scope": SCOPE.model_dump_json(), "arguments": ARGUMENTS,
    }

    with query_server() as server:
        status, _ = http_test_controls.post(server, f"/api/extensions/{OWNER}/queries/{OWNER}.missing", body)

    assert status == BAD_REQUEST_STATUS
