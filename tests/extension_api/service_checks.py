# Copyright (c) 2026 Zhambyl Yermagambet
"""Build checked host service adapters with observable local query providers."""

from dataclasses import dataclass, field

from baqylau_extension_api.contracts.operations import ExtensionQueries
from baqylau_extension_api.models.operations import QuerySnapshot
from baqylau_extension_api.models.queries import QueryReady, QueryRequest, QueryResult
from baqylau_extension_api.runtime.call_grants import HostCallLedger
from baqylau_extension_api.runtime.service_dispatch import HostServiceAccess
from baqylau_extension_api.runtime.service_provider import ServiceProvider
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest
from baqylau_extension_api.schemas import SchemaSet

from tests.extension_api import service_providers, service_samples as fixtures


@dataclass
class EchoQueries(ExtensionQueries):
    """Record exact target input so tests can prove rejected calls did not run."""

    received: list[QueryRequest] = field(default_factory=list)
    wrong_document: bool = False

    def query(self, query_request: QueryRequest) -> QueryResult:
        """Return the original typed arguments under an explicit read snapshot.

        Returns:
            A valid target-owned query reply.

        """
        self.received.append(query_request)
        document = query_request.arguments
        if self.wrong_document:
            document = document.model_copy(update={"json_text": "42"})
        return QueryReady(
            binding=query_request.binding, document=document,
            snapshot=QuerySnapshot(state_revision="test-state"),
        )


@dataclass(frozen=True)
class ServiceTestHost:
    """Expose test evidence without adding state access to the public SDK service."""

    access: HostServiceAccess
    target: EchoQueries
    providers: service_providers.FixtureProviders


def test_host() -> ServiceTestHost:
    """Create one declared consumer and an active peer with a read counter.

    Returns:
        A checked service facade and its independent host fixture state.

    """
    target = EchoQueries()
    providers = service_providers.FixtureProviders((ServiceProvider(
        manifest=fixtures.manifest(fixtures.BETA),
        schemas=SchemaSet((fixtures.schema(fixtures.BETA),)),
        environment=fixtures.environment(fixtures.BETA), queries=target,
    ),))
    caller = WorkerLoadRequest(
        manifest=fixtures.manifest(fixtures.ALPHA), environment=fixtures.environment(fixtures.ALPHA),
    )
    return ServiceTestHost(HostServiceAccess(caller, providers, HostCallLedger()), target, providers)
