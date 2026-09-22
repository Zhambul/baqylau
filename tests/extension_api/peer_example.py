# Copyright (c) 2026 Zhambyl Yermagambet
"""Own a cooperating feature which never imports the other feature package."""

from dataclasses import dataclass

from baqylau_extension_api.contracts import lifecycle, operations, plugin, processing
from baqylau_extension_api.contracts.services import ExtensionHostServices
from baqylau_extension_api.models import lifecycle as lifecycle_models, queries, raw_transforms, services, transforms
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.operations import QuerySnapshot
from baqylau_extension_api.models.scopes import InstallationScope
from baqylau_extension_api.runtime.models import ExtensionTransportError
from pydantic import TypeAdapter


def peer_selection(host: ExtensionHostServices) -> services.ServiceResolveRequest:
    """Select the fixture's declared peer from package-owned feature rules.

    Returns:
        A public service request, not a host implementation reference.

    """
    owner = host.environment.extension_info.extension_id
    peer = "test.beta" if owner == "test.alpha" else "test.alpha"
    return services.ServiceResolveRequest(binding=services.ServiceBinding(
        owner=peer, service_id=f"{peer}.service", scope=InstallationScope(), call_id="feature-service-call",
    ))


def peer_read(host: ExtensionHostServices, mode: str) -> str:
    """Resolve and call the peer using only its declared query metadata.

    Returns:
        Peer data or the explicit unavailable reason.

    """
    access = host.service_access
    assert access is not None
    selected = access.resolve_service(peer_selection(host))
    if selected.status == "unavailable":
        return selected.reason
    query = selected.queries[0]
    forwarded = "cycle" if mode == "cycle" else "echo"
    response = access.query_service(services.ServiceQueryRequest(
        binding=selected.binding, service_revision=selected.service_revision, query_id=query.name,
        arguments=EncodedDocument(
            schema_ref=query.arguments, json_text=TypeAdapter(str).dump_json(forwarded).decode(),
        ),
    ))
    if response.status == "unavailable":
        return response.reason
    assert response.result.status == "ready"
    return TypeAdapter(str).validate_json(response.result.document.json_text)


@dataclass(frozen=True)
class PeerRaw(processing.ExtensionRawTransformer):
    """Attempt a forbidden live callback to test the existing pure-work guard."""

    host: ExtensionHostServices

    def transform(self, request: transforms.RawTransformRequest) -> raw_transforms.RawTransformResult:
        """Report the exact pure guard, or keep input when the service is absent.

        Returns:
            A recorded test decision only when the pure guard rejects the call.

        """
        if self.host.service_access is not None:
            try:
                self.host.service_access.resolve_service(peer_selection(self.host))
            except ExtensionTransportError as exc:
                return raw_transforms.RawTransformResult(operations=tuple(
                    transforms.Drop(input_id=source.input_id, reason=str(exc))
                    for source in request.inputs
                ))
        return raw_transforms.RawTransformResult(operations=tuple(
            transforms.Keep(input_id=source.input_id) for source in request.inputs
        ))


@dataclass(frozen=True)
class PeerExample(plugin.ExtensionPlugin, lifecycle.ExtensionLifecycle, operations.ExtensionQueries):
    """Expose feature-owned read behavior and a factory-time peer observation."""

    host: ExtensionHostServices
    factory_state: str

    @property
    def extension_info(self) -> lifecycle_models.ExtensionInfo:
        """The package identity fixed by the worker host."""
        return self.host.environment.extension_info

    @property
    def capabilities(self) -> plugin.ExtensionCapabilities:
        """The feature's declared lifecycle, read, and pure transform capabilities."""
        return plugin.ExtensionCapabilities(lifecycle=self, queries=self, raw_transformer=PeerRaw(self.host))

    def activate(self, request: lifecycle_models.ActivationRequest) -> lifecycle_models.ActivationResult:
        """Prepare this small fixture without changing external systems.

        Returns:
            The requested readiness revision.

        """
        return lifecycle_models.ActivationReady(runtime_revision=request.runtime_revision)

    def deactivate(self, request: lifecycle_models.DeactivationRequest) -> lifecycle_models.DeactivationResult:
        """Stop after the fixture's calls have completed.

        Returns:
            The selected stop revision.

        """
        return lifecycle_models.DeactivationResult(runtime_revision=request.runtime_revision)

    def query(self, query_request: queries.QueryRequest) -> queries.QueryResult:
        """Read through the host service or return this package's own value.

        Returns:
            A typed document whose schema remains owned by this package.

        """
        mode = TypeAdapter(str).validate_json(query_request.arguments.json_text)
        owner = self.extension_info.extension_id
        text = f"{owner}:{mode}"
        if mode in {"peer", "cycle"}:
            text = peer_read(self.host, mode)
        if mode == "factory":
            text = self.factory_state
        document = query_request.arguments.model_copy(update={
            "json_text": TypeAdapter(str).dump_json(text).decode(),
        })
        return queries.QueryReady(
            binding=query_request.binding, document=document, snapshot=QuerySnapshot(state_revision="fixture-state"),
        )


def build_extension(services: ExtensionHostServices) -> plugin.ExtensionPlugin:
    """Read declared service metadata during factory preparation, without live query authority.

    Returns:
        A feature which owns its peer-selection and view data rules.

    """
    state = "not_declared"
    if services.service_access is not None:
        selected = services.service_access.resolve_service(peer_selection(services))
        state = selected.reason if selected.status == "unavailable" else "available"
    return PeerExample(services, state)


FACTORY: plugin.ExtensionFactory = build_extension
