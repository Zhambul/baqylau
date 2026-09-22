# Copyright (c) 2026 Zhambyl Yermagambet
"""Declare independent peers and service requests without importing their code."""

from baqylau_extension_api.manifest.contributions import Contributions
from baqylau_extension_api.manifest.data import ProcessingSelection
from baqylau_extension_api.manifest.metadata import BackendEntry, PackageDependency
from baqylau_extension_api.manifest.operations import PublicService, QueryDefinition, ServiceRequirement
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.documents import EncodedDocument, SchemaDefinition
from baqylau_extension_api.models.environment import ExtensionEnvironment
from baqylau_extension_api.models.operations import OperationBinding
from baqylau_extension_api.models.queries import QueryRequest
from baqylau_extension_api.models.scopes import InstallationScope
from baqylau_extension_api.models.services import (
    ServiceBinding,
    ServiceQueryRequest,
    ServiceResolveRequest,
    ServiceRevision,
)

from tests.extension_api import manifest_samples, samples

ALPHA = "test.alpha"
BETA = "test.beta"
PEER_VALUE = "test.beta:echo"


def schema(owner: str) -> SchemaDefinition:
    """Own a simple text schema in each separate feature package.

    Returns:
        Registered bytes with the selected package's exact schema identity.

    """
    definition = samples.schema_definition()
    reference = definition.reference.model_copy(update={"owner": owner})
    return definition.model_copy(update={"reference": reference})


def environment(owner: str) -> ExtensionEnvironment:
    """Select the worker identity through the host environment.

    Returns:
        A complete environment, with no feature-selected caller identity.

    """
    original = samples.worker_environment()
    identity = original.extension_info.model_copy(update={"extension_id": owner})
    return original.model_copy(update={"extension_info": identity})


def manifest(owner: str, *, cycle: bool = False) -> ExtensionManifest:
    """Declare public and private reads with optional service consumption.

    Returns:
        A package-local manifest; active-set validation is a separate host step.

    """
    peer = BETA if owner == ALPHA else ALPHA
    consume = owner == ALPHA or cycle
    declarations = tuple(QueryDefinition(
        name=f"{owner}.{suffix}", scopes=("installation",),
        arguments=schema(owner).reference, result=schema(owner).reference,
    ) for suffix in ("read", "private"))
    return manifest_samples.backend_manifest(owner).model_copy(update={
        "backend": BackendEntry(module="peer_backend"), "schemas": (schema(owner),),
        "capabilities": ("lifecycle", "queries", "raw_transformer"),
        "dependencies": (PackageDependency(
            extension_id=peer, version_range=">=1,<2", required=False,
        ),) if consume else (),
        "contributions": Contributions(
            queries=declarations,
            services=(PublicService(name=f"{owner}.service", version="1.0.0", queries=(f"{owner}.read",)),),
            consumes=(ServiceRequirement(
                owner=peer, name=f"{peer}.service", version_range=">=1,<2", required=False,
            ),) if consume else (),
            processing=(ProcessingSelection(
                capability="raw_transformer", scopes=("session",), input_types=("test.record",),
            ),),
        ),
    })


def resolve_request() -> ServiceResolveRequest:
    """Ask alpha's host service for beta's public read metadata.

    Returns:
        A declared service selection with no caller authority fields.

    """
    return ServiceResolveRequest(binding=ServiceBinding(
        owner=BETA, service_id=f"{BETA}.service", scope=InstallationScope(), call_id="peer-call-1",
    ))


def service_query() -> ServiceQueryRequest:
    """Capture beta's expected version for one external peer read.

    Returns:
        A query that cannot supply beta's effective settings.

    """
    return ServiceQueryRequest(
        binding=resolve_request().binding, query_id=f"{BETA}.read",
        service_revision=ServiceRevision(
            package_version="1.0.0", service_version="1.0.0", runtime_revision=samples.RUNTIME_REVISION,
        ), arguments=EncodedDocument(schema_ref=schema(BETA).reference, json_text='"echo"'),
    )


def query_request(owner: str, mode: str = "peer") -> QueryRequest:
    """Call one package through the ordinary public query capability.

    Returns:
        The host's root read request before any service callback occurs.

    """
    return QueryRequest(
        binding=OperationBinding(
            extension_id=owner, operation_id=f"{owner}.read", scope=InstallationScope(),
            runtime_revision=samples.RUNTIME_REVISION, call_id="root-query",
        ), settings_revision=0, arguments=EncodedDocument(schema_ref=schema(owner).reference, json_text=f'"{mode}"'),
    )
