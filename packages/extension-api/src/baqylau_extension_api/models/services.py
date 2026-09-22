# Copyright (c) 2026 Zhambyl Yermagambet
"""Describe versioned peer reads without exposing host settings or job authority."""

from typing import Annotated, Literal

from pydantic import Field

from baqylau_extension_api.manifest.operations import QueryDefinition
from baqylau_extension_api.models.base import ExtensionId, Identifier, Revision, WireModel
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.operations import QueryPageCursor, QuerySnapshot
from baqylau_extension_api.models.queries import DEFAULT_QUERY_LIMIT, MAX_QUERY_LIMIT, QueryResult
from baqylau_extension_api.models.scopes import ExtensionScope
from baqylau_extension_api.versions import PackageVersion


class ServiceBinding(WireModel):
    """Identify the requested peer service, scope, and caller-selected call key."""

    owner: ExtensionId
    service_id: Identifier
    scope: ExtensionScope
    call_id: Identifier


class ServiceRevision(WireModel):
    """Capture service and package versions from one active target worker."""

    package_version: PackageVersion
    service_version: PackageVersion
    runtime_revision: Identifier


class ServiceResolveRequest(WireModel):
    """Resolve only a service already named in the consumer manifest."""

    binding: ServiceBinding


class ServiceResolved(WireModel):
    """Expose the active service's read declarations for the selected scope."""

    status: Literal["available"] = "available"
    binding: ServiceBinding
    service_revision: ServiceRevision
    queries: Annotated[tuple[QueryDefinition, ...], Field(max_length=100)]


class ServiceUnavailable(WireModel):
    """Keep missing or changed peers distinct from a valid empty query result."""

    status: Literal["unavailable"] = "unavailable"
    binding: ServiceBinding
    reason: Literal["not_installed", "not_enabled", "not_provided", "incompatible", "stale_handle"]


class ServiceQueryRequest(ServiceResolveRequest):
    """Request a public read without choosing another package's settings."""

    service_revision: ServiceRevision
    query_id: Identifier
    arguments: EncodedDocument
    snapshot: QuerySnapshot | None = None
    page: QueryPageCursor | None = None
    limit: Annotated[int, Field(ge=1, le=MAX_QUERY_LIMIT)] = DEFAULT_QUERY_LIMIT


class ServiceQueryResponse(WireModel):
    """Return the checked target query result under its exact service handle."""

    status: Literal["available"] = "available"
    binding: ServiceBinding
    service_revision: ServiceRevision
    settings_revision: Revision
    result: QueryResult


type ServiceResolveResult = Annotated[ServiceResolved | ServiceUnavailable, Field(discriminator="status")]
type ServiceQueryResult = Annotated[ServiceQueryResponse | ServiceUnavailable, Field(discriminator="status")]
