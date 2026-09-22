# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate complete source plans and exact release acknowledgments."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import rules
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.source_results import SourcePlan, SourceReleaseResult
from baqylau_extension_api.models.sources import SourceContext, SourceReleaseRequest
from baqylau_extension_api.schemas import SchemaSet
from baqylau_extension_api.sources.registration import validate_source_descriptor

MAX_SOURCE_PLAN_BYTES = 1_048_576


def validate_source_plan(request: SourceContext, response: SourcePlan) -> SourcePlan:
    """Reject a stale or duplicate plan before the host changes its watches.

    Returns:
        One bounded complete source plan.

    Raises:
        ExtensionContractError: If the binding or encoded size is invalid.

    """
    checked = SourcePlan.model_validate(response)
    if checked.binding != SourceContext.model_validate(request).binding:
        message = "source plan does not match its requested call"
        raise ExtensionContractError(message)
    if len(checked.model_dump_json().encode("utf-8")) > MAX_SOURCE_PLAN_BYTES:
        message = "source plan exceeds its encoded size limit"
        raise ExtensionContractError(message)
    rules.require_unique((source.source_identity for source in checked.sources), "source identities")
    return checked


def validate_plan_documents(manifest: ExtensionManifest, schemas: SchemaSet, response: SourcePlan) -> None:
    """Validate every descriptor before accepting any watch or deadline."""
    for source in response.sources:
        validate_source_descriptor(manifest, schemas, response.binding, source)


def validate_source_release(request: SourceReleaseRequest, response: SourceReleaseResult) -> SourceReleaseResult:
    """Do not release a different source, scope, or runtime from a late reply.

    Returns:
        The checked release state, including an explicit pending result.

    Raises:
        ExtensionContractError: If a reply changes its binding or selected source.

    """
    checked_request = SourceReleaseRequest.model_validate(request)
    checked = SourceReleaseResult.model_validate(response)
    if checked.binding != checked_request.binding or checked.source_identity != checked_request.source_identity:
        message = "source release does not match its requested source and call"
        raise ExtensionContractError(message)
    return checked
