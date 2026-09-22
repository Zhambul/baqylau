# Copyright (c) 2026 Zhambyl Yermagambet
"""Apply complete projection validation in contract and process tests."""

from baqylau_extension_api.models.projections import ProjectionRequest, ProjectionResult
from baqylau_extension_api.projection import inputs, results
from baqylau_extension_api.schemas import SchemaSet

from tests.extension_api import projection_samples


def validate_request(request: ProjectionRequest) -> ProjectionRequest:
    """Check input against the fixture's exact registration.

    Returns:
        The complete checked request.

    """
    manifest = projection_samples.manifest()
    return inputs.validate_projection_request(manifest, SchemaSet(manifest.schemas), request)


def validate_result(request: ProjectionRequest, response: ProjectionResult) -> ProjectionResult:
    """Check input, binding, writes, and every output schema together.

    Returns:
        A complete proposal with no mutation of captured input.

    """
    manifest = projection_samples.manifest()
    checked_request = validate_request(request)
    checked = results.validate_projection_result(checked_request, response)
    results.validate_projection_documents(manifest, SchemaSet(manifest.schemas), checked)
    return checked
