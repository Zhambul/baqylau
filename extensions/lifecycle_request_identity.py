# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep user request identity stable without exposing manager or runtime selection."""

from hashlib import sha256

from baqylau_extension_api.models.base import Identifier

from extensions.lifecycle_control_contract import LifecycleConflictError
from extensions.models.lifecycle_operations import LifecycleOperation
from extensions.models.management_requests import ManagementRequestOrigin


def request_operation_id(request_id: Identifier) -> Identifier:
    """Reserve a separate stable namespace for user requests of bounded length.

    Returns:
        An operation identity independent of catalog changes and daemon restarts.

    """
    digest = sha256(request_id.encode()).hexdigest()
    return f"request-{digest}"


def require_same_request(operation: LifecycleOperation, origin: ManagementRequestOrigin) -> None:
    """Reject another body or package owner for an existing request key.

    Raises:
        LifecycleConflictError: If the key already belongs to another request.

    """
    if operation.proposal.request_origin != origin:
        message = "request ID already belongs to a different extension lifecycle request"
        raise LifecycleConflictError(message)
