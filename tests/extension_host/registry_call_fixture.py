# Copyright (c) 2026 Zhambyl Yermagambet
"""Hold a local protocol read with explicit events for registry concurrency tests."""

from dataclasses import dataclass, field, replace
from threading import Event

from baqylau_extension_api.contracts.operations import ExtensionQueries
from baqylau_extension_api.models.queries import QueryRequest, QueryResult
from baqylau_extension_api.models.scopes import InstallationScope

from extensions.impl.process.plugin import ProcessExtensionPlugin
from extensions.registry_package import RegistryPackage
from extensions.registry_services import RegistryServiceAccess
from tests.extension_api import service_checks, service_samples as peers
from tests.extension_host import registry_fixture as fixtures
from tests.extension_host.registry_memory_fixture import MemoryRegistry


@dataclass
class HeldQueries(ExtensionQueries):
    """Hold a read with explicit test events, not timing assumptions."""

    started: Event = field(default_factory=Event)
    released: Event = field(default_factory=Event)
    echo: service_checks.EchoQueries = field(default_factory=service_checks.EchoQueries)

    def query(self, query_request: QueryRequest) -> QueryResult:
        """Return only after the test has attempted a concurrent registry change.

        Returns:
            A checked provider reply after the hold is released.

        """
        self.started.set()
        assert self.released.wait(3)
        return self.echo.query(query_request)


def held_peer(held: HeldQueries) -> RegistryPackage:
    """Replace only the fixture's query capability with the held test read.

    Returns:
        A local protocol double, not a managed worker.

    """
    target = fixtures.peer(peers.BETA)
    assert target.plugin is not None
    plugin = ProcessExtensionPlugin(target.entry.extension_info, replace(target.plugin.capabilities, queries=held))
    return replace(target, plugin=plugin)


def authorized_read(access: RegistryServiceAccess) -> str:
    """Give the selected caller a real root grant for the local peer query.

    Returns:
        The typed service status after the query returns.

    """
    with access.calls.root(access.caller.environment, InstallationScope(), 3):
        return access.query_service(peers.service_query()).status


def held_access(registry: MemoryRegistry, held: HeldQueries) -> RegistryServiceAccess:
    """Publish the consumer and held provider for the concurrency test.

    Returns:
        The real registry adapter bound to the active consumer.

    """
    caller = fixtures.peer(peers.ALPHA)
    registry.publish_snapshot(0, fixtures.snapshot(caller, held_peer(held)))
    return fixtures.service_access(registry, caller)
