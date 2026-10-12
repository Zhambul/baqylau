# Copyright (c) 2026 Zhambyl Yermagambet
"""Run the test server with one active package that declares queries."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from baqylau_extension_api.models.scopes import InstallationScope

from app import provider_extension_registry
from extensions.registry_contract import RegistryRead
from tests import http_test_assets, http_test_server_runtime
from tests.extension_api import service_samples
from tests.extension_host import registry_fixture

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.http_test_pane_models import RunningDaemon
    from tests.provider_graph import ProviderGraph

OWNER = service_samples.ALPHA
QUERY_ID = f"{OWNER}.read"
QUERY_PATH = f"/api/extensions/{OWNER}/queries/{QUERY_ID}"
ARGUMENTS = '"fixture"'
SCOPE = InstallationScope()
REGISTRY_REVISION = 1


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


def query_body() -> dict[str, http_test_server_runtime.JsonValue]:
    """Select the fixture query in the installation scope.

    Returns:
        The request body.

    """
    return {"scope": SCOPE.model_dump_json(), "arguments": ARGUMENTS}


@contextlib.contextmanager
def query_server(application: ProviderGraph | None = None) -> Iterator[RunningDaemon]:
    """Run the test server with one active package that declares queries.

    Yields:
        The running server.

    """
    package = registry_fixture.peer(OWNER)
    registry = FakeRegistry(RegistryRead(revision=REGISTRY_REVISION, snapshot=registry_fixture.snapshot(package)))
    with http_test_assets.running_server(
        application or http_test_server_runtime.application(),
        (provider_extension_registry.extension_registry, registry),
    ) as server:
        yield server
