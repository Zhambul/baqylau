# Copyright (c) 2026 Zhambyl Yermagambet
"""Build target queries from service input and host-selected settings."""

from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.operations import OperationBinding
from baqylau_extension_api.models.queries import QueryRequest
from baqylau_extension_api.models.services import ServiceQueryRequest


def target_query_request(
    request: ServiceQueryRequest, settings_revision: int, settings: EncodedDocument | None = None,
) -> QueryRequest:
    """Reuse the public query contract without accepting peer settings from a caller.

    Returns:
        A target-owned query whose scope, arguments, and page remain unchanged.

    """
    return QueryRequest(
        binding=OperationBinding(
            extension_id=request.binding.owner, operation_id=request.query_id, scope=request.binding.scope,
            runtime_revision=request.service_revision.runtime_revision, call_id=request.binding.call_id,
        ), arguments=request.arguments, settings_revision=settings_revision, settings=settings,
        snapshot=request.snapshot, page=request.page, limit=request.limit,
    )
